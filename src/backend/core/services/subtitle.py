"""Service for managing subtitle agents in LiveKit rooms."""

import asyncio
import time
from datetime import datetime
from logging import getLogger
from uuid import uuid4

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from asgiref.sync import async_to_sync, sync_to_async
from livekit.api import TwirpError
from livekit.protocol.agent_dispatch import CreateAgentDispatchRequest

from core import utils
from core.models import Room

logger = getLogger(__name__)


class SubtitleException(Exception):
    """Exception raised when subtitle operations fail."""

    def __init__(self, message, *, status_code=500):
        super().__init__(message)
        self.public_message = message
        self.status_code = status_code


class SubtitleConflict(SubtitleException):
    """Raised when a provider change needs an explicit stop first."""

    def __init__(self):
        super().__init__(
            "Subtitle provider transition requires an explicit stop", status_code=409
        )


class SubtitleService:
    """Manage one durable, idempotent subtitle dispatch per room."""

    INACTIVE = "inactive"
    STARTING = "starting"
    LIVE = "live"
    RECONNECTING = "reconnecting"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"
    STOPPING = "stopping"
    STOPPED = "stopped"

    _ACTIVE_STATES = {
        STARTING,
        LIVE,
        RECONNECTING,
        DEGRADED,
        UNAVAILABLE,
        STOPPING,
    }

    @async_to_sync
    async def start_subtitle(self, room, *, started_by=None):
        """Start or recover the room's configured subtitle agent."""

        room_id = str(room.pk)
        agent_name, provider = self._agent_configuration(room)
        claim = await sync_to_async(self._claim_start)(
            room_id, agent_name, provider, started_by
        )
        if not claim["claimed"]:
            if claim.get("conflict"):
                raise SubtitleConflict()
            if claim["state"]["state"] == self.STARTING:
                return await self._wait_for_start_completion(
                    room_id, claim["state"]["attemptId"]
                )
            return claim["state"]

        attempt_id = claim["attemptId"]
        client = None
        dispatch_id = None
        deadline = self._start_deadline()
        try:
            client = utils.create_livekit_client()
            dispatch = await self._find_dispatch(client, room_id, agent_name, deadline)
            if dispatch is None:
                response = await self._create_dispatch(
                    client, room_id, agent_name, deadline
                )
                dispatch_id = self._dispatch_id(response)
                if not dispatch_id:
                    dispatch = await self._find_dispatch(
                        client, room_id, agent_name, deadline
                    )
                    dispatch_id = self._dispatch_id(dispatch)
            else:
                dispatch_id = self._dispatch_id(dispatch)

            if not dispatch_id:
                raise SubtitleException("LiveKit did not return a dispatch ID")

            result = await sync_to_async(self._finalize_start)(
                room_id, attempt_id, dispatch_id, agent_name, provider
            )
            if result["cleanup"]:
                await self._cleanup_start_result(
                    client,
                    room_id,
                    dispatch_id,
                    agent_name,
                    result,
                    deadline,
                )
                return (
                    result["state"]
                    if result["superseded"]
                    else await sync_to_async(self._mark_stopped_after_start)(
                        room_id, attempt_id
                    )
                )
            return result["state"]
        except SubtitleException as error:
            await self._best_effort_cleanup_start(
                client, room_id, attempt_id, agent_name, dispatch_id, deadline
            )
            await sync_to_async(self._mark_start_failure)(
                room_id, attempt_id, dispatch_id, error.public_message
            )
            raise
        except Exception as error:  # pylint: disable=broad-exception-caught
            logger.exception("Failed to start subtitle agent for room %s", room_id)
            await self._best_effort_cleanup_start(
                client, room_id, attempt_id, agent_name, dispatch_id, deadline
            )
            await sync_to_async(self._mark_start_failure)(
                room_id, attempt_id, dispatch_id, "Failed to create subtitle agent"
            )
            raise SubtitleException("Failed to create subtitle agent") from error
        finally:
            if client is not None:
                await client.aclose()

    @async_to_sync
    async def stop_subtitle(self, room):
        """Delete all known room dispatches and confirm bounded provider drain."""

        room_id = str(room.pk)
        claim = await sync_to_async(self._claim_stop)(room_id)
        if not claim["claimed"]:
            return claim["state"]

        state = claim["state"]
        attempt_id = state["attemptId"]
        client = None
        deadline = time.monotonic() + settings.ROOM_SUBTITLE_DRAIN_TIMEOUT_SECONDS
        try:
            client = utils.create_livekit_client()
            dispatch_ids = await self._delete_dispatches(
                client,
                room_id,
                dispatch_ids={state["dispatchId"]} if state["dispatchId"] else set(),
                agent_names=self._known_agent_names(state),
                deadline=deadline,
            )
            drained = await self._wait_for_drain(
                client, room_id, dispatch_ids, deadline
            )
            if not drained:
                message = "Timed out waiting for subtitle dispatch drain"
                await sync_to_async(self._mark_degraded)(
                    room_id, attempt_id, state["dispatchId"], message
                )
                raise SubtitleException(message, status_code=504)

            return await sync_to_async(self._mark_stopped)(room_id, attempt_id)
        except SubtitleException:
            raise
        except asyncio.TimeoutError as error:
            logger.exception("Timed out stopping subtitle agent for room %s", room_id)
            await sync_to_async(self._mark_degraded)(
                room_id,
                attempt_id,
                state["dispatchId"],
                "Timed out waiting for subtitle dispatch drain",
            )
            raise SubtitleException(
                "Timed out waiting for subtitle dispatch drain", status_code=504
            ) from error
        except Exception as error:  # pylint: disable=broad-exception-caught
            logger.exception("Failed to stop subtitle agent for room %s", room_id)
            await sync_to_async(self._mark_degraded)(
                room_id,
                attempt_id,
                state["dispatchId"],
                "Failed to stop subtitle agent",
            )
            raise SubtitleException("Failed to stop subtitle agent") from error
        finally:
            if client is not None:
                await client.aclose()

    @async_to_sync
    async def get_status(self, room):
        """Return the normalized persisted subtitle state for a room."""

        return await sync_to_async(self._read_state)(str(room.pk))

    async def _wait_for_start_completion(self, room_id, attempt_id):
        """Let concurrent callers observe the durable start result."""
        deadline = time.monotonic() + settings.ROOM_SUBTITLE_START_TIMEOUT_SECONDS
        while True:
            state = await sync_to_async(self._read_state)(room_id)
            if state["attemptId"] != attempt_id or state["state"] != self.STARTING:
                return state
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return state
            await asyncio.sleep(min(0.05, remaining))

    @staticmethod
    def _agent_configuration(room):
        """Resolve provider, falling back to the existing agent outside the canary."""

        if not settings.LIVE_STT_OPENAI_ENABLED:
            return settings.ROOM_SUBTITLE_AGENT_NAME, "livekit"

        allowlist = {
            item.strip()
            for item in settings.LIVE_STT_OPENAI_ROOM_ALLOWLIST.split(",")
            if item.strip()
        }
        room_identifiers = {str(room.pk)}
        if room.slug:
            room_identifiers.add(str(room.slug))
        if not allowlist.intersection(room_identifiers):
            return settings.ROOM_SUBTITLE_AGENT_NAME, "livekit"
        return settings.LIVE_STT_OPENAI_AGENT_NAME, "openai"

    @staticmethod
    def _dispatch_id(dispatch):
        return getattr(dispatch, "id", None) if dispatch is not None else None

    @classmethod
    async def _create_dispatch(cls, client, room_id, agent_name, deadline):
        """Create one dispatch with the LiveKit v1.2 contract."""

        return await asyncio.wait_for(
            client.agent_dispatch.create_dispatch(
                CreateAgentDispatchRequest(agent_name=agent_name, room=room_id)
            ),
            timeout=cls._remaining(deadline),
        )

    @classmethod
    async def _find_dispatch(cls, client, room_id, agent_name, deadline):
        dispatches = await asyncio.wait_for(
            client.agent_dispatch.list_dispatch(room_id),
            timeout=cls._remaining(deadline),
        )
        return next(
            (
                dispatch
                for dispatch in dispatches
                if getattr(dispatch, "agent_name", None) == agent_name
            ),
            None,
        )

    @classmethod
    async def _wait_for_drain(cls, client, room_id, dispatch_ids, deadline):
        if not dispatch_ids:
            return True
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            dispatches = await asyncio.wait_for(
                client.agent_dispatch.list_dispatch(room_id), timeout=remaining
            )
            if not any(
                cls._dispatch_id(dispatch) in dispatch_ids for dispatch in dispatches
            ):
                return True
            await asyncio.sleep(min(0.1, max(0, deadline - time.monotonic())))

    @classmethod
    def _known_agent_names(cls, state=None):
        names = {
            settings.ROOM_SUBTITLE_AGENT_NAME,
            settings.LIVE_STT_OPENAI_AGENT_NAME,
        }
        if state and state.get("agentName"):
            names.add(state["agentName"])
        return names

    @staticmethod
    def _start_deadline():
        """Reserve one bounded budget for all provider calls in a start."""

        return time.monotonic() + settings.ROOM_SUBTITLE_START_TIMEOUT_SECONDS * 0.95

    @staticmethod
    def _remaining(deadline):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise asyncio.TimeoutError
        return remaining

    @classmethod
    # pylint: disable=too-many-arguments
    async def _delete_dispatches(
        cls, client, room_id, *, dispatch_ids, agent_names, deadline
    ):
        dispatches = await asyncio.wait_for(
            client.agent_dispatch.list_dispatch(room_id),
            timeout=cls._remaining(deadline),
        )
        target_ids = set(dispatch_ids)
        target_ids.update(
            cls._dispatch_id(dispatch)
            for dispatch in dispatches
            if getattr(dispatch, "agent_name", None) in agent_names
        )
        target_ids.discard(None)
        for dispatch_id in target_ids:
            try:
                await asyncio.wait_for(
                    client.agent_dispatch.delete_dispatch(dispatch_id, room_id),
                    timeout=cls._remaining(deadline),
                )
            except TwirpError as error:
                if error.code != "not_found":
                    raise
        return target_ids

    # pylint: disable=too-many-arguments,too-many-positional-arguments
    async def _cleanup_start_result(  # noqa: PLR0913,PLR0917
        self, client, room_id, dispatch_id, agent_name, result, deadline
    ):
        if result["superseded"]:
            current_dispatch_id = result["dispatchId"]
            dispatch_ids = (
                {dispatch_id}
                if dispatch_id and dispatch_id != current_dispatch_id
                else set()
            )
            agent_names = set()
        else:
            dispatch_ids = {dispatch_id} if dispatch_id else set()
            agent_names = {agent_name}
        await self._delete_dispatches(
            client,
            room_id,
            dispatch_ids=dispatch_ids,
            agent_names=agent_names,
            deadline=deadline,
        )

    @classmethod
    def _start_cleanup_scope(cls, room_id, attempt_id, dispatch_id, agent_name):
        """Fence rollback cleanup against a newer persisted attempt."""

        with transaction.atomic():
            room = Room.objects.select_for_update().get(pk=room_id)
            state = cls._normalize_state(room.subtitle_state)
            current_dispatch_id = state["dispatchId"]
            return {
                "dispatch_ids": (
                    {dispatch_id}
                    if dispatch_id and dispatch_id != current_dispatch_id
                    else set()
                ),
                "agent_names": (
                    {agent_name} if state["attemptId"] == attempt_id else set()
                ),
            }

    # pylint: disable=too-many-arguments,too-many-positional-arguments
    async def _best_effort_cleanup_start(  # noqa: PLR0913,PLR0917
        self, client, room_id, attempt_id, agent_name, dispatch_id, deadline
    ):
        if client is None:
            return
        try:
            cleanup_scope = await sync_to_async(self._start_cleanup_scope)(
                room_id, attempt_id, dispatch_id, agent_name
            )
            await self._delete_dispatches(
                client,
                room_id,
                dispatch_ids=cleanup_scope["dispatch_ids"],
                agent_names=cleanup_scope["agent_names"],
                deadline=deadline,
            )
        except Exception:  # pylint: disable=broad-exception-caught
            logger.exception(
                "Failed to clean up subtitle dispatch for room %s", room_id
            )

    @classmethod
    def _claim_start(cls, room_id, agent_name, provider, started_by):
        with transaction.atomic():
            room = Room.objects.select_for_update().get(pk=room_id)
            state = cls._normalize_state(room.subtitle_state)
            if state["state"] == cls.STOPPING and not cls._stopping_is_stale(state):
                return {"claimed": False, "state": state}
            if state["dispatchId"] and state["state"] in cls._ACTIVE_STATES:
                provider_changed = (
                    state["agentName"] and state["agentName"] != agent_name
                ) or (state["provider"] and state["provider"] != provider)
                if provider_changed:
                    return {"claimed": False, "conflict": True, "state": state}
                if not (
                    state["state"] == cls.STOPPING and cls._stopping_is_stale(state)
                ):
                    return {"claimed": False, "state": state}
            if state["state"] == cls.STARTING and cls._start_is_fresh(state):
                return {"claimed": False, "state": state}

            now = timezone.now().isoformat()
            attempt_id = str(uuid4())
            state.update(
                {
                    "state": cls.STARTING,
                    "provider": provider,
                    "agentName": agent_name,
                    "startedBy": started_by,
                    "startedAt": now,
                    "stoppedAt": None,
                    "stoppingAt": None,
                    "attemptId": attempt_id,
                    "stopRequested": False,
                    "lastError": None,
                }
            )
            room.subtitle_state = state
            room.save(update_fields=["subtitle_state"])
            return {"claimed": True, "attemptId": attempt_id, "state": state}

    @classmethod
    def _claim_stop(cls, room_id):
        with transaction.atomic():
            room = Room.objects.select_for_update().get(pk=room_id)
            state = cls._normalize_state(room.subtitle_state)
            if not state["dispatchId"]:
                if state["state"] == cls.STARTING:
                    state.update(
                        {
                            "state": cls.STOPPING,
                            "stopRequested": True,
                            "stoppingAt": timezone.now().isoformat(),
                            "lastError": None,
                        }
                    )
                    room.subtitle_state = state
                    room.save(update_fields=["subtitle_state"])
                    return {"claimed": False, "state": state}
                state.update(
                    {
                        "state": cls.STOPPED,
                        "stoppedAt": timezone.now().isoformat(),
                        "stopRequested": False,
                        "lastError": None,
                    }
                )
                room.subtitle_state = state
                room.save(update_fields=["subtitle_state"])
                return {"claimed": False, "state": state}
            if state["state"] == cls.STOPPING and not cls._stopping_is_stale(state):
                return {"claimed": False, "state": state}

            state["state"] = cls.STOPPING
            state["stoppingAt"] = timezone.now().isoformat()
            state["stopRequested"] = True
            state["lastError"] = None
            room.subtitle_state = state
            room.save(update_fields=["subtitle_state"])
            return {"claimed": True, "state": state}

    @classmethod
    def _finalize_start(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        cls, room_id, attempt_id, dispatch_id, agent_name, provider
    ):
        with transaction.atomic():
            room = Room.objects.select_for_update().get(pk=room_id)
            state = cls._normalize_state(room.subtitle_state)
            if (
                state["attemptId"] != attempt_id
                or state["state"] != cls.STARTING
                or state["stopRequested"]
            ):
                return {
                    "cleanup": True,
                    "superseded": state["attemptId"] != attempt_id,
                    "dispatchId": state["dispatchId"],
                    "state": state,
                }
            state.update(
                {
                    "state": cls.LIVE,
                    "provider": provider,
                    "agentName": agent_name,
                    "dispatchId": dispatch_id,
                    "lastError": None,
                }
            )
            room.subtitle_state = state
            room.save(update_fields=["subtitle_state"])
            return {
                "cleanup": False,
                "superseded": False,
                "dispatchId": dispatch_id,
                "state": state,
            }

    @classmethod
    def _mark_start_failure(  # pylint: disable=too-many-arguments
        cls, room_id, attempt_id, dispatch_id, error
    ):
        with transaction.atomic():
            room = Room.objects.select_for_update().get(pk=room_id)
            state = cls._normalize_state(room.subtitle_state)
            if state["attemptId"] != attempt_id:
                return state
            if state["stopRequested"] or state["state"] == cls.STOPPING:
                changes = {
                    "state": cls.DEGRADED if dispatch_id else cls.STOPPED,
                    "dispatchId": dispatch_id,
                    "stoppedAt": timezone.now().isoformat()
                    if not dispatch_id
                    else state["stoppedAt"],
                    "lastError": "Failed to stop subtitle agent"
                    if dispatch_id
                    else None,
                }
            else:
                changes = {
                    "state": cls.UNAVAILABLE,
                    "dispatchId": dispatch_id,
                    "lastError": error[:500],
                }
            state.update(changes)
            room.subtitle_state = state
            room.save(update_fields=["subtitle_state"])
            return state

    @classmethod
    def _mark_degraded(cls, room_id, attempt_id, dispatch_id, error):
        return cls._update_state(
            room_id,
            {
                "state": cls.DEGRADED,
                "dispatchId": dispatch_id,
                "lastError": error[:500],
            },
            expected_attempt_id=attempt_id,
        )

    @classmethod
    def _mark_stopped(cls, room_id, attempt_id):
        return cls._update_state(
            room_id,
            {
                "state": cls.STOPPED,
                "dispatchId": None,
                "stoppedAt": timezone.now().isoformat(),
                "stopRequested": False,
                "lastError": None,
            },
            expected_attempt_id=attempt_id,
        )

    @classmethod
    def _mark_stopped_after_start(cls, room_id, attempt_id):
        return cls._mark_stopped(room_id, attempt_id)

    @classmethod
    def _update_state(cls, room_id, changes, *, expected_attempt_id=None):
        with transaction.atomic():
            room = Room.objects.select_for_update().get(pk=room_id)
            state = cls._normalize_state(room.subtitle_state)
            if (
                expected_attempt_id is not None
                and state["attemptId"] != expected_attempt_id
            ):
                return state
            state.update(changes)
            room.subtitle_state = state
            room.save(update_fields=["subtitle_state"])
            return state

    @classmethod
    def _read_state(cls, room_id):
        return cls._normalize_state(Room.objects.get(pk=room_id).subtitle_state)

    @classmethod
    def _start_is_fresh(cls, state):
        started_at = state["startedAt"]
        if not started_at:
            return False
        try:
            age = (timezone.now() - datetime.fromisoformat(started_at)).total_seconds()
        except (TypeError, ValueError):
            return False
        return age < settings.ROOM_SUBTITLE_START_TIMEOUT_SECONDS

    @staticmethod
    def _stopping_is_stale(state):
        """A stop outlives delete plus drain only if its process died."""
        stopping_at = state.get("stoppingAt")
        if not stopping_at:
            return True
        try:
            age = (timezone.now() - datetime.fromisoformat(stopping_at)).total_seconds()
        except (TypeError, ValueError):
            return True
        return age >= 3 * settings.ROOM_SUBTITLE_DRAIN_TIMEOUT_SECONDS

    @classmethod
    def _normalize_state(cls, value):
        defaults = {
            "state": cls.INACTIVE,
            "provider": None,
            "agentName": None,
            "dispatchId": None,
            "startedBy": None,
            "startedAt": None,
            "stoppedAt": None,
            "stoppingAt": None,
            "attemptId": None,
            "stopRequested": False,
            "lastError": None,
        }
        if not isinstance(value, dict):
            return defaults
        state = defaults | {key: value[key] for key in defaults if key in value}
        if state["state"] not in {
            cls.INACTIVE,
            cls.STARTING,
            cls.LIVE,
            cls.RECONNECTING,
            cls.DEGRADED,
            cls.UNAVAILABLE,
            cls.STOPPING,
            cls.STOPPED,
        }:
            state["state"] = cls.UNAVAILABLE
        return state
