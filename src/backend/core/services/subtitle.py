"""Service for managing subtitle agents in LiveKit rooms."""

import asyncio
import threading
import time
from collections import defaultdict
from datetime import datetime
from logging import getLogger

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from asgiref.sync import async_to_sync, sync_to_async
from livekit.api import TwirpError
from livekit.protocol.agent_dispatch import CreateAgentDispatchRequest

from core import utils
from core.models import Room

logger = getLogger(__name__)

_ROOM_LOCKS = defaultdict(threading.RLock)


class SubtitleException(Exception):
    """Exception raised when subtitle operations fail."""


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

        with _ROOM_LOCKS[room_id]:
            claim = await sync_to_async(self._claim_start)(
                room_id, agent_name, provider, started_by
            )
            if not claim["claimed"]:
                return claim["state"]

            client = utils.create_livekit_client()
            dispatch_id = None
            try:
                dispatch = await self._find_dispatch(client, room_id, agent_name)
                if dispatch is None:
                    response = await self._create_dispatch(client, room_id, agent_name)
                    dispatch_id = self._dispatch_id(response)
                    if not dispatch_id:
                        dispatch = await self._find_dispatch(
                            client, room_id, agent_name
                        )
                        dispatch_id = self._dispatch_id(dispatch)
                else:
                    dispatch_id = self._dispatch_id(dispatch)

                if not dispatch_id:
                    raise SubtitleException(
                        f"LiveKit did not return a dispatch ID for room {room_id}"
                    )

                return await sync_to_async(self._mark_live)(
                    room_id, dispatch_id, agent_name, provider
                )
            except SubtitleException:
                await sync_to_async(self._mark_unavailable)(
                    room_id, dispatch_id, "Subtitle dispatch could not start"
                )
                raise
            except Exception as error:  # pylint: disable=broad-exception-caught
                logger.exception("Failed to start subtitle agent for room %s", room_id)
                await sync_to_async(self._mark_unavailable)(
                    room_id, dispatch_id, str(error)
                )
                raise SubtitleException("Failed to create subtitle agent") from error
            finally:
                await client.aclose()

    @async_to_sync
    async def stop_subtitle(self, room):
        """Delete the room dispatch and confirm bounded provider drain."""

        room_id = str(room.pk)
        with _ROOM_LOCKS[room_id]:
            claim = await sync_to_async(self._claim_stop)(room_id)
            if not claim["claimed"]:
                return claim["state"]

            dispatch_id = claim["state"]["dispatchId"]
            client = utils.create_livekit_client()
            try:
                try:
                    await asyncio.wait_for(
                        client.agent_dispatch.delete_dispatch(dispatch_id, room_id),
                        timeout=settings.ROOM_SUBTITLE_DRAIN_TIMEOUT_SECONDS,
                    )
                except TwirpError as error:
                    if error.code != "not_found":
                        raise

                drained = await self._wait_for_drain(client, room_id, dispatch_id)
                if not drained:
                    message = "Timed out waiting for subtitle dispatch drain"
                    await sync_to_async(self._mark_degraded)(
                        room_id, dispatch_id, message
                    )
                    raise SubtitleException(message)

                return await sync_to_async(self._mark_stopped)(room_id)
            except SubtitleException:
                raise
            except Exception as error:  # pylint: disable=broad-exception-caught
                logger.exception("Failed to stop subtitle agent for room %s", room_id)
                await sync_to_async(self._mark_unavailable)(
                    room_id, dispatch_id, str(error)
                )
                raise SubtitleException("Failed to stop subtitle agent") from error
            finally:
                await client.aclose()

    @async_to_sync
    async def get_status(self, room):
        """Return the normalized persisted subtitle state for a room."""

        return await sync_to_async(self._read_state)(str(room.pk))

    @staticmethod
    def _agent_configuration(room):
        """Resolve provider and agent name without changing the room rights model."""

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
            raise SubtitleException("OpenAI live transcription is not enabled for room")
        return settings.LIVE_STT_OPENAI_AGENT_NAME, "openai"

    @staticmethod
    def _dispatch_id(dispatch):
        return getattr(dispatch, "id", None) if dispatch is not None else None

    @staticmethod
    async def _create_dispatch(client, room_id, agent_name):
        """Create one dispatch with the LiveKit v1.2 contract."""

        return await asyncio.wait_for(
            client.agent_dispatch.create_dispatch(
                CreateAgentDispatchRequest(agent_name=agent_name, room=room_id)
            ),
            timeout=settings.ROOM_SUBTITLE_START_TIMEOUT_SECONDS,
        )

    @classmethod
    async def _find_dispatch(cls, client, room_id, agent_name):
        response = await asyncio.wait_for(
            client.agent_dispatch.list_dispatch(room_id),
            timeout=settings.ROOM_SUBTITLE_START_TIMEOUT_SECONDS,
        )
        return next(
            (
                dispatch
                for dispatch in response.agent_dispatches
                if dispatch.agent_name == agent_name
            ),
            None,
        )

    @classmethod
    async def _wait_for_drain(cls, client, room_id, dispatch_id):
        deadline = time.monotonic() + settings.ROOM_SUBTITLE_DRAIN_TIMEOUT_SECONDS
        while True:
            response = await asyncio.wait_for(
                client.agent_dispatch.list_dispatch(room_id),
                timeout=settings.ROOM_SUBTITLE_DRAIN_TIMEOUT_SECONDS,
            )
            if not any(
                cls._dispatch_id(dispatch) == dispatch_id
                for dispatch in response.agent_dispatches
            ):
                return True
            if time.monotonic() >= deadline:
                return False
            await asyncio.sleep(min(0.1, deadline - time.monotonic()))

    @classmethod
    def _claim_start(cls, room_id, agent_name, provider, started_by):
        with transaction.atomic():
            room = Room.objects.select_for_update().get(pk=room_id)
            state = cls._normalize_state(room.subtitle_state)
            if cls._start_is_already_claimed(state, agent_name):
                return {"claimed": False, "state": state}

            now = timezone.now().isoformat()
            state.update(
                {
                    "state": cls.STARTING,
                    "provider": provider,
                    "agentName": agent_name,
                    "startedBy": started_by,
                    "startedAt": now,
                    "stoppedAt": None,
                    "lastError": None,
                }
            )
            room.subtitle_state = state
            room.save(update_fields=["subtitle_state"])
            return {"claimed": True, "state": state}

    @classmethod
    def _claim_stop(cls, room_id):
        with transaction.atomic():
            room = Room.objects.select_for_update().get(pk=room_id)
            state = cls._normalize_state(room.subtitle_state)
            if not state["dispatchId"]:
                state.update(
                    {
                        "state": cls.STOPPED,
                        "stoppedAt": timezone.now().isoformat(),
                        "lastError": None,
                    }
                )
                room.subtitle_state = state
                room.save(update_fields=["subtitle_state"])
                return {"claimed": False, "state": state}
            if state["state"] == cls.STOPPING:
                return {"claimed": False, "state": state}

            state["state"] = cls.STOPPING
            state["lastError"] = None
            room.subtitle_state = state
            room.save(update_fields=["subtitle_state"])
            return {"claimed": True, "state": state}

    @classmethod
    def _mark_live(cls, room_id, dispatch_id, agent_name, provider):
        return cls._update_state(
            room_id,
            {
                "state": cls.LIVE,
                "provider": provider,
                "agentName": agent_name,
                "dispatchId": dispatch_id,
                "lastError": None,
            },
        )

    @classmethod
    def _mark_unavailable(cls, room_id, dispatch_id, error):
        return cls._update_state(
            room_id,
            {
                "state": cls.UNAVAILABLE,
                "dispatchId": dispatch_id,
                "lastError": error[:500],
            },
        )

    @classmethod
    def _mark_degraded(cls, room_id, dispatch_id, error):
        return cls._update_state(
            room_id,
            {
                "state": cls.DEGRADED,
                "dispatchId": dispatch_id,
                "lastError": error[:500],
            },
        )

    @classmethod
    def _mark_stopped(cls, room_id):
        return cls._update_state(
            room_id,
            {
                "state": cls.STOPPED,
                "dispatchId": None,
                "stoppedAt": timezone.now().isoformat(),
                "lastError": None,
            },
        )

    @classmethod
    def _update_state(cls, room_id, changes):
        with transaction.atomic():
            room = Room.objects.select_for_update().get(pk=room_id)
            state = cls._normalize_state(room.subtitle_state)
            state.update(changes)
            room.subtitle_state = state
            room.save(update_fields=["subtitle_state"])
            return state

    @classmethod
    def _read_state(cls, room_id):
        return cls._normalize_state(Room.objects.get(pk=room_id).subtitle_state)

    @classmethod
    def _start_is_already_claimed(cls, state, agent_name):
        claimed = (
            state["agentName"] == agent_name
            and state["state"] in cls._ACTIVE_STATES
            and bool(state["dispatchId"])
        )
        started_at = state["startedAt"]
        if not claimed and state["state"] == cls.STARTING and started_at:
            try:
                age = (
                    timezone.now() - datetime.fromisoformat(started_at)
                ).total_seconds()
            except ValueError:
                age = settings.ROOM_SUBTITLE_START_TIMEOUT_SECONDS
            claimed = age < settings.ROOM_SUBTITLE_START_TIMEOUT_SECONDS
        return claimed

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
            "lastError": None,
        }
        if not isinstance(value, dict):
            return defaults
        state = defaults | {key: value.get(key) for key in defaults}
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
