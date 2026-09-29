"""Durable LiveKit subtitle-agent observation and reconciliation."""
# pylint: disable=cyclic-import,no-member

import asyncio
import json
import time
from dataclasses import dataclass
from datetime import timedelta
from logging import getLogger

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from asgiref.sync import async_to_sync
from livekit import api
from livekit.protocol.agent_dispatch import CreateAgentDispatchRequest

from core import models, utils

from .subtitle_control import (
    SubtitleControlConflict,
    SubtitleControlContractError,
    _serialize_control,
    compare_and_set_subtitle_control,
    compare_and_set_subtitle_control_locked,
    lock_room_and_control,
)
from .subtitle_lock import SubtitleLockUnavailable, try_subtitle_convergence_lock

logger = getLogger(__name__)

SUBTITLE_STATUS_TOPIC = "mastrao.transcription.state.v1"
DEFAULT_SUBTITLE_PROVIDER = "legacy"
PROVIDER_TIMEOUT_SECONDS = 8
CLEANUP_TIMEOUT_SECONDS = 3
MAX_RECONCILIATION_ATTEMPTS = 3
RETRY_DELAYS_SECONDS = (1, 5, 30)
PACKET_RETRY_DELAYS_SECONDS = (1, 5, 30)
MAX_PACKET_RETRIES = len(PACKET_RETRY_DELAYS_SECONDS)


class SubtitleReconciliationAmbiguous(RuntimeError):
    """The provider state is not safe to project as settled."""

    def __init__(
        self,
        message,
        *,
        list_succeeded=False,
        no_dispatch_confirmed=False,
        create_timeout=False,
    ):
        super().__init__(message)
        self.list_succeeded = list_succeeded
        self.no_dispatch_confirmed = no_dispatch_confirmed
        self.create_timeout = create_timeout


class SubtitleProviderActive(RuntimeError):
    """The provider already has a live subtitle dispatch."""

    status_code = 409


class SubtitleConvergenceBusy(RuntimeError):
    """Another room-scoped convergence currently owns the provider lock."""


@dataclass(frozen=True)
class _ProviderResult:
    dispatch_ids: list[str]
    list_succeeded: bool = True
    no_dispatch_confirmed: bool = False
    had_dispatches: bool = False


def subtitle_provider():
    """Return the configured provider identity used by new subtitle intents."""
    return getattr(settings, "ROOM_SUBTITLE_PROVIDER", DEFAULT_SUBTITLE_PROVIDER)


def subtitle_agent_identity(room_name):
    """Return the exact LiveKit participant identity for the room agent."""
    return f"{settings.ROOM_SUBTITLE_AGENT_NAME}-{room_name}"


def _dispatch_metadata(dispatch):
    raw_metadata = getattr(dispatch, "metadata", None)
    if not raw_metadata:
        return None
    try:
        metadata = json.loads(raw_metadata)
    except (TypeError, ValueError):
        return None
    return metadata if isinstance(metadata, dict) else None


def _dispatch_generation(dispatch):
    metadata = _dispatch_metadata(dispatch)
    generation = metadata.get("generation") if metadata else None
    if isinstance(generation, bool) or not isinstance(generation, int):
        return None
    return generation


def _is_subtitle_dispatch(dispatch):
    return getattr(dispatch, "agent_name", None) == settings.ROOM_SUBTITLE_AGENT_NAME


def is_subtitle_agent_dispatch(dispatch):
    """Return whether a provider dispatch belongs to the subtitle agent."""
    return _is_subtitle_dispatch(dispatch)


def dispatch_provider(dispatch):
    """Return provider metadata from a dispatch, if present."""
    metadata = _dispatch_metadata(dispatch)
    return metadata.get("provider") if metadata else None


def _is_room_dispatch(dispatch, room_sid):
    if not _is_subtitle_dispatch(dispatch):
        return False
    raw_metadata = getattr(dispatch, "metadata", None)
    if not raw_metadata:
        return True
    metadata = _dispatch_metadata(dispatch)
    return metadata is None or metadata.get("roomSid") == room_sid


def _dispatch_ids(dispatches):
    return [
        str(dispatch.id) for dispatch in dispatches if getattr(dispatch, "id", None)
    ]


def _dispatch_metadata_json(room_sid, generation, provider):
    return json.dumps(
        {
            "roomSid": room_sid,
            "generation": generation,
            "provider": provider,
            "agentName": settings.ROOM_SUBTITLE_AGENT_NAME,
        },
        separators=(",", ":"),
        sort_keys=True,
    )


async def _list_dispatches(client, room_name):
    return list(
        await asyncio.wait_for(
            client.agent_dispatch.list_dispatch(room_name),
            timeout=PROVIDER_TIMEOUT_SECONDS,
        )
    )


async def _delete_dispatch(client, dispatch_id, room_name):
    await asyncio.wait_for(
        client.agent_dispatch.delete_dispatch(
            dispatch_id=dispatch_id,
            room_name=room_name,
        ),
        timeout=CLEANUP_TIMEOUT_SECONDS,
    )


def _lower_generation_dispatches(dispatches, room_sid, generation):
    return [
        dispatch
        for dispatch in dispatches
        if _is_room_dispatch(dispatch, room_sid)
        and _dispatch_generation(dispatch) is not None
        and _dispatch_generation(dispatch) < generation
    ]


def _dispatch_sort_key(dispatch):
    """Use provider creation time, then ID, for a stable duplicate winner."""

    state = getattr(dispatch, "state", None)
    created_at = getattr(state, "created_at", None) or getattr(
        dispatch, "created_at", None
    )
    if created_at is not None:
        seconds = getattr(created_at, "seconds", None)
        nanos = getattr(created_at, "nanos", None)
        if seconds is not None:
            try:
                seconds = int(seconds)
                nanos = int(nanos or 0)
            except (TypeError, ValueError):
                seconds = None
            if seconds is not None:
                return (0, seconds, nanos, str(getattr(dispatch, "id", "")))
        return (1, 0, 0, str(getattr(dispatch, "id", "")))
    return (1, 0, 0, str(getattr(dispatch, "id", "")))


def _dispatch_is_malformed(dispatch, room_sid):
    """Identify an agent dispatch that cannot be safely adopted."""

    if not _is_room_dispatch(dispatch, room_sid):
        return False
    raw_metadata = getattr(dispatch, "metadata", None)
    if not raw_metadata:
        return False
    metadata = _dispatch_metadata(dispatch)
    if metadata is None or metadata.get("roomSid") != room_sid:
        return True
    generation = metadata.get("generation")
    return any(
        key not in metadata for key in ("agentName", "provider", "generation")
    ) or (
        metadata.get("agentName") != settings.ROOM_SUBTITLE_AGENT_NAME
        or not isinstance(metadata.get("provider"), str)
        or isinstance(generation, bool)
        or not isinstance(generation, int)
    )


def _dispatch_is_current(dispatch, room_sid, generation, provider):
    raw_metadata = getattr(dispatch, "metadata", None)
    if not raw_metadata:
        return True
    metadata = _dispatch_metadata(dispatch)
    return bool(
        metadata
        and metadata.get("roomSid") == room_sid
        and metadata.get("agentName") == settings.ROOM_SUBTITLE_AGENT_NAME
        and metadata.get("provider") == provider
        and metadata.get("generation") == generation
    )


async def _canonical_current_dispatch(  # noqa: PLR0913, PLR0917  # pylint: disable=too-many-arguments,too-many-positional-arguments
    client,
    room_name,
    room_sid,
    generation,
    provider,
    dispatches,
):
    """Adopt one legacy dispatch or deterministically remove duplicates."""

    targeted = [
        dispatch for dispatch in dispatches if _is_room_dispatch(dispatch, room_sid)
    ]
    if any(_dispatch_is_malformed(dispatch, room_sid) for dispatch in targeted):
        raise SubtitleReconciliationAmbiguous(
            "LiveKit subtitle dispatch metadata is malformed."
        )
    conflicting = [
        dispatch
        for dispatch in targeted
        if getattr(dispatch, "metadata", None)
        and _dispatch_metadata(dispatch).get("roomSid") == room_sid
        and _dispatch_metadata(dispatch).get("provider") not in (None, provider)
    ]
    if conflicting:
        raise SubtitleProviderActive(
            "A different subtitle provider is already active in LiveKit."
        )
    candidates = [
        dispatch
        for dispatch in targeted
        if _dispatch_is_current(dispatch, room_sid, generation, provider)
    ]
    if not candidates:
        return None
    candidates.sort(key=_dispatch_sort_key)
    winner = candidates[0]
    for duplicate in candidates[1:]:
        await _delete_dispatch(client, str(duplicate.id), room_name)
    if len(candidates) > 1:
        confirmed = [
            dispatch
            for dispatch in await _list_dispatches(client, room_name)
            if _is_room_dispatch(dispatch, room_sid)
        ]
        remaining = [
            dispatch
            for dispatch in confirmed
            if _dispatch_is_current(dispatch, room_sid, generation, provider)
        ]
        if len(remaining) != 1:
            raise SubtitleReconciliationAmbiguous(
                "LiveKit subtitle dispatch deduplication is ambiguous."
            )
        winner = remaining[0]
    return winner


async def _reconcile_provider_unlocked(  # noqa: PLR0911, PLR0912, PLR0913  # pylint: disable=too-many-arguments,too-many-positional-arguments,too-many-return-statements,too-many-branches
    room_name,
    room_sid,
    desired_state,
    generation,
    provider,
    *,
    robust=False,
):
    client = utils.create_livekit_client()
    try:
        first = await _list_dispatches(client, room_name)
        targeted = [
            dispatch for dispatch in first if _is_room_dispatch(dispatch, room_sid)
        ]

        if desired_state == models.RoomSubtitleControl.DesiredState.OFF:
            if robust and any(
                _dispatch_is_malformed(dispatch, room_sid) for dispatch in targeted
            ):
                raise SubtitleReconciliationAmbiguous(
                    "LiveKit subtitle dispatch metadata is malformed."
                )
            for dispatch in targeted:
                await _delete_dispatch(client, str(dispatch.id), room_name)
            second = [
                dispatch
                for dispatch in await _list_dispatches(client, room_name)
                if _is_room_dispatch(dispatch, room_sid)
            ]
            if second:
                raise SubtitleReconciliationAmbiguous(
                    "LiveKit subtitle dispatch cleanup is ambiguous."
                )
            return _ProviderResult(
                [],
                list_succeeded=True,
                no_dispatch_confirmed=True,
                had_dispatches=bool(targeted),
            )

        lower_generations = _lower_generation_dispatches(
            targeted,
            room_sid,
            generation,
        )
        if lower_generations:
            for dispatch in lower_generations:
                await _delete_dispatch(client, str(dispatch.id), room_name)
            targeted = [
                dispatch
                for dispatch in await _list_dispatches(client, room_name)
                if _is_room_dispatch(dispatch, room_sid)
            ]
            if _lower_generation_dispatches(targeted, room_sid, generation):
                raise SubtitleReconciliationAmbiguous(
                    "LiveKit stale subtitle dispatch cleanup is ambiguous."
                )

        if robust:
            canonical = await _canonical_current_dispatch(
                client,
                room_name,
                room_sid,
                generation,
                provider,
                targeted,
            )
            if canonical is not None:
                return _ProviderResult(
                    [str(canonical.id)],
                    had_dispatches=True,
                )
        elif targeted:
            return _ProviderResult(
                _dispatch_ids(targeted),
                had_dispatches=bool(targeted),
            )

        try:
            created = await asyncio.wait_for(
                client.agent_dispatch.create_dispatch(
                    CreateAgentDispatchRequest(
                        agent_name=settings.ROOM_SUBTITLE_AGENT_NAME,
                        room=room_name,
                        metadata=_dispatch_metadata_json(
                            room_sid,
                            generation,
                            provider,
                        ),
                    )
                ),
                timeout=PROVIDER_TIMEOUT_SECONDS,
            )
        except (asyncio.TimeoutError, TimeoutError) as error:
            raise SubtitleReconciliationAmbiguous(
                "LiveKit subtitle dispatch creation timed out.",
                list_succeeded=True,
                create_timeout=True,
            ) from error
        except api.TwirpError as error:
            if error.code == "already_exists":
                confirmed = [
                    dispatch
                    for dispatch in await _list_dispatches(client, room_name)
                    if _is_room_dispatch(dispatch, room_sid)
                ]
                if confirmed:
                    if robust:
                        canonical = await _canonical_current_dispatch(
                            client,
                            room_name,
                            room_sid,
                            generation,
                            provider,
                            confirmed,
                        )
                        if canonical is None:
                            raise SubtitleReconciliationAmbiguous(
                                "LiveKit did not confirm the created subtitle dispatch."
                            ) from None
                        return _ProviderResult(
                            [str(canonical.id)],
                            had_dispatches=True,
                        )
                    return _ProviderResult(
                        _dispatch_ids(confirmed),
                        had_dispatches=True,
                    )
                raise SubtitleProviderActive(
                    "A subtitle agent is already active in LiveKit."
                ) from error
            raise
        dispatch_id = getattr(created, "id", None)
        if not dispatch_id:
            raise SubtitleReconciliationAmbiguous(
                "LiveKit did not return a subtitle dispatch ID.",
                list_succeeded=True,
                create_timeout=True,
            )
        confirmed = [
            dispatch
            for dispatch in await _list_dispatches(client, room_name)
            if _is_room_dispatch(dispatch, room_sid)
        ]
        if robust:
            canonical = await _canonical_current_dispatch(
                client,
                room_name,
                room_sid,
                generation,
                provider,
                confirmed,
            )
            if canonical is None:
                raise SubtitleReconciliationAmbiguous(
                    "LiveKit did not confirm the created subtitle dispatch."
                )
            return _ProviderResult([str(canonical.id)], had_dispatches=True)
        return _ProviderResult(
            _dispatch_ids(confirmed) or [str(dispatch_id)],
            had_dispatches=True,
        )
    finally:
        await client.aclose()


def _provider_reconcile(  # noqa: PLR0913  # pylint: disable=too-many-arguments,too-many-positional-arguments
    room_name,
    room_sid,
    desired_state,
    generation,
    provider,
    *,
    timeout=None,
):
    """Run provider convergence with the rollout flag and OFF safety rules."""

    robust = bool(settings.ROOM_SUBTITLE_CONVERGENCE_ENABLED)
    must_lock = robust or desired_state == models.RoomSubtitleControl.DesiredState.OFF
    if not must_lock:
        return async_to_sync(_reconcile_provider_unlocked)(
            room_name,
            room_sid,
            desired_state,
            generation,
            provider,
            robust=False,
        )
    try:
        with try_subtitle_convergence_lock(room_sid, timeout=timeout) as lock:
            if lock is None:
                raise SubtitleConvergenceBusy(room_sid)
            return async_to_sync(_reconcile_provider_unlocked)(
                room_name,
                room_sid,
                desired_state,
                generation,
                provider,
                robust=robust,
            )
    except SubtitleLockUnavailable as error:
        raise SubtitleConvergenceBusy(room_sid) from error


def _packet_payload(control):
    snapshot = _serialize_control(control)
    occurred_at = snapshot["updatedAt"] or timezone.now().isoformat()
    return {
        "schemaVersion": 1,
        "roomSid": control.room_sid,
        "state": snapshot["state"],
        "stateVersion": snapshot["stateVersion"],
        "sessionId": snapshot["sessionId"],
        "eventId": f"subtitle-state-{control.room_sid}-{snapshot['stateVersion']}",
        "occurredAt": occurred_at,
        "reason": snapshot["reason"],
    }


async def _publish_snapshot(room_name, payload):
    client = utils.create_livekit_client()
    try:
        await client.room.send_data(
            api.SendDataRequest(
                room=room_name,
                data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
                kind="RELIABLE",
                topic=SUBTITLE_STATUS_TOPIC,
            )
        )
    finally:
        await client.aclose()


def _schedule_snapshot_retry(room_id, attempt):
    if not settings.CELERY_ENABLED or attempt >= MAX_PACKET_RETRIES:
        return
    delay = PACKET_RETRY_DELAYS_SECONDS[attempt - 1]
    from core.tasks.subtitle import (  # noqa: PLC0415  # pylint: disable=import-outside-toplevel
        process_subtitle_snapshot_publication,
    )

    process_subtitle_snapshot_publication.apply_async(
        args=[room_id, attempt + 1],
        queue="mastrao-transcription",
        countdown=delay,
    )


def publish_subtitle_snapshot(room_id, attempt=1):
    """Publish the latest committed snapshot and retry delivery with a bound."""
    control = (
        models.RoomSubtitleControl.objects.filter(room_id=room_id, is_current=True)
        .select_related("room")
        .first()
    )
    if control is None:
        return
    try:
        async_to_sync(_publish_snapshot)(
            str(control.room_id),
            _packet_payload(control),
        )
    except Exception:  # pylint: disable=broad-exception-caught
        logger.exception("Failed to publish subtitle snapshot for room %s", room_id)
        _schedule_snapshot_retry(room_id, attempt)


def queue_subtitle_snapshot(room_id):
    """Publish only after the transaction containing the snapshot commits."""
    transaction.on_commit(lambda: publish_subtitle_snapshot(room_id))


def _current_control(room_sid):
    return (
        models.RoomSubtitleControl.objects.filter(
            room_sid=room_sid,
            is_current=True,
        )
        .select_related("room")
        .first()
    )


def _session_engaged(control):
    return bool(
        control.session_id
        or control.agent_present
        or control.worker_ready
        or control.observed_dispatch_ids
        or control.pending_since
    )


def _failure_state(control, error, *, stopping):
    if stopping:
        return models.RoomSubtitleControl.PublicState.STOPPING
    if (
        control.attempts + 1 >= MAX_RECONCILIATION_ATTEMPTS
        and getattr(error, "list_succeeded", False)
        and getattr(error, "no_dispatch_confirmed", False)
    ):
        return models.RoomSubtitleControl.PublicState.UNAVAILABLE
    if getattr(error, "create_timeout", False):
        return models.RoomSubtitleControl.PublicState.STARTING
    if control.public_state in {
        models.RoomSubtitleControl.PublicState.LIVE,
        models.RoomSubtitleControl.PublicState.RECONNECTING,
        models.RoomSubtitleControl.PublicState.DEGRADED,
    }:
        return control.public_state
    return models.RoomSubtitleControl.PublicState.STARTING


def _mark_failure(room_sid, *, stopping=False, error=None):
    now = timezone.now()
    with transaction.atomic():
        room_id = (
            models.RoomSubtitleControl.objects.filter(room_sid=room_sid)
            .values_list("room_id", flat=True)
            .first()
        )
        if room_id is None:
            return None
        locked_room, control = lock_room_and_control(
            models.Room(pk=room_id),
            room_sid=room_sid,
        )
        if control is None:
            return None
        attempts = min(control.attempts + 1, MAX_RECONCILIATION_ATTEMPTS)
        delay = RETRY_DELAYS_SECONDS[min(attempts - 1, len(RETRY_DELAYS_SECONDS) - 1)]
        changes = {
            "public_state": _failure_state(control, error, stopping=stopping),
            "reason_code": models.RoomSubtitleControl.ReasonCode.PROVIDER_UNAVAILABLE,
            "attempts": attempts,
            "next_retry_at": (
                now + timedelta(seconds=delay)
                if attempts < MAX_RECONCILIATION_ATTEMPTS
                else None
            ),
        }
        result = compare_and_set_subtitle_control_locked(
            locked_room,
            control,
            expected_control_generation=control.control_generation,
            expected_state_version=control.state_version,
            **changes,
        )
        if result.next_retry_at is not None:
            transaction.on_commit(
                lambda sid=result.room_sid, countdown=delay: (
                    schedule_subtitle_reconciliation(sid, countdown=countdown)
                )
            )
        return result


def _intent_changed(room_sid, generation, desired_state):
    latest = _current_control(room_sid)
    return latest is None or (
        latest.control_generation != generation or latest.desired_state != desired_state
    )


def _record_stale_provider_dispatches(room_sid, provider_result):
    """Retain evidence of a late provider create for the newer intent."""
    if not provider_result.dispatch_ids:
        return
    latest = _current_control(room_sid)
    if latest is None or not latest.observed_dispatch_ids:
        if latest is None:
            return
        compare_and_set_subtitle_control(
            latest.room_sid,
            expected_control_generation=latest.control_generation,
            expected_state_version=latest.state_version,
            current_only=True,
            observed_dispatch_ids=provider_result.dispatch_ids,
            pending_since=latest.pending_since or timezone.now(),
        )


def _project_provider_result(control, provider_result):
    desired_state = control.desired_state
    if desired_state == models.RoomSubtitleControl.DesiredState.ON:
        public_state = (
            models.RoomSubtitleControl.PublicState.LIVE
            if control.agent_present and control.worker_ready
            else models.RoomSubtitleControl.PublicState.STARTING
        )
        return {
            "observed_dispatch_ids": provider_result.dispatch_ids,
            "public_state": public_state,
            "reason_code": None,
            "pending_since": control.pending_since or timezone.now(),
            "attempts": 0,
            "next_retry_at": None,
            "provider": control.provider or subtitle_provider(),
            "agent_name": settings.ROOM_SUBTITLE_AGENT_NAME,
        }

    if not provider_result.list_succeeded or provider_result.dispatch_ids:
        public_state = models.RoomSubtitleControl.PublicState.STOPPING
    elif _session_engaged(control) or provider_result.had_dispatches:
        public_state = models.RoomSubtitleControl.PublicState.STOPPED
    else:
        public_state = models.RoomSubtitleControl.PublicState.INACTIVE
    return {
        "observed_dispatch_ids": provider_result.dispatch_ids,
        "public_state": public_state,
        "reason_code": control.reason_code,
        "pending_since": None,
        "attempts": 0,
        "next_retry_at": None,
        "agent_present": False,
        "worker_ready": False,
        "session_id": None,
    }


def reconcile_subtitle_control(  # noqa: PLR0912, PLR0915  # pylint: disable=too-many-branches,too-many-statements
    room_sid,
    *,
    deadline=None,
):
    """Converge one current control row without losing a newer intent."""
    for _ in range(max(1, settings.ROOM_SUBTITLE_CONVERGENCE_MAX_ATTEMPTS)):
        if deadline is not None and time.monotonic() >= deadline:
            latest = _current_control(room_sid)
            if latest is not None and settings.CELERY_ENABLED:
                schedule_subtitle_reconciliation(room_sid)
            return latest
        control = _current_control(room_sid)
        if control is None:
            return None

        if (
            control.room_finished_at is not None
            and control.desired_state == models.RoomSubtitleControl.DesiredState.ON
        ):
            control = compare_and_set_subtitle_control(
                control.room_sid,
                expected_control_generation=control.control_generation,
                expected_state_version=control.state_version,
                new_intent=True,
                desired_state=models.RoomSubtitleControl.DesiredState.OFF,
                public_state=models.RoomSubtitleControl.PublicState.STOPPING,
                reason_code=models.RoomSubtitleControl.ReasonCode.ROOM_FINISHED,
                pending_since=None,
                room_finished_at=control.room_finished_at,
            )
        elif not settings.ROOM_SUBTITLE_ENABLED and control.desired_state == "ON":
            control = compare_and_set_subtitle_control(
                control.room_sid,
                expected_control_generation=control.control_generation,
                expected_state_version=control.state_version,
                current_only=True,
                new_intent=True,
                desired_state=models.RoomSubtitleControl.DesiredState.OFF,
                public_state=models.RoomSubtitleControl.PublicState.STOPPING,
                reason_code=models.RoomSubtitleControl.ReasonCode.PROVIDER_UNAVAILABLE,
            )

        desired_state = control.desired_state
        generation = control.control_generation
        try:
            provider_kwargs = {}
            if deadline is not None:
                provider_kwargs["timeout"] = max(
                    0.0,
                    deadline - time.monotonic(),
                )
            provider_result = _provider_reconcile(
                str(control.room_id),
                control.room_sid,
                desired_state,
                generation,
                control.provider or subtitle_provider(),
                **provider_kwargs,
            )
        except SubtitleConvergenceBusy:
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining > 0:
                    time.sleep(min(0.05, remaining))
                    continue
            if settings.CELERY_ENABLED:
                schedule_subtitle_reconciliation(room_sid)
            return control
        except (SubtitleControlConflict, SubtitleControlContractError):
            raise
        except SubtitleProviderActive:
            if _intent_changed(room_sid, generation, desired_state):
                continue
            if desired_state == models.RoomSubtitleControl.DesiredState.ON:
                continue
            _mark_failure(room_sid, error=SubtitleProviderActive("active"))
            raise
        except Exception as error:  # pylint: disable=broad-exception-caught
            if _intent_changed(room_sid, generation, desired_state):
                continue
            _mark_failure(
                room_sid,
                stopping=desired_state == models.RoomSubtitleControl.DesiredState.OFF,
                error=error,
            )
            if isinstance(error, SubtitleReconciliationAmbiguous):
                raise
            raise SubtitleReconciliationAmbiguous(
                "LiveKit subtitle reconciliation is ambiguous."
            ) from error

        if _intent_changed(room_sid, generation, desired_state):
            _record_stale_provider_dispatches(room_sid, provider_result)
            continue

        latest = _current_control(room_sid)
        if latest is None:
            return None
        try:
            return compare_and_set_subtitle_control(
                latest.room_sid,
                expected_control_generation=latest.control_generation,
                expected_state_version=latest.state_version,
                current_only=True,
                **_project_provider_result(latest, provider_result),
            )
        except SubtitleControlConflict:
            continue

    latest = _current_control(room_sid)
    if latest is not None and settings.CELERY_ENABLED:
        schedule_subtitle_reconciliation(room_sid)
    return latest


@transaction.atomic
def observe_subtitle_agent(room_sid, *, participant_identity, present):
    """Project verified LiveKit agent presence into the current control row."""
    room_id = (
        models.RoomSubtitleControl.objects.filter(room_sid=room_sid)
        .values_list("room_id", flat=True)
        .first()
    )
    if room_id is None:
        return None
    locked_room, control = lock_room_and_control(
        models.Room(pk=room_id),
        room_sid=room_sid,
    )
    if control is None:
        return None
    if control.room_finished_at is not None:
        return control
    if not present and control.session_id not in (None, participant_identity):
        return control
    if control.desired_state == models.RoomSubtitleControl.DesiredState.OFF:
        if present:
            return control
        public_state = (
            models.RoomSubtitleControl.PublicState.STOPPING
            if control.public_state == models.RoomSubtitleControl.PublicState.STOPPING
            or _session_engaged(control)
            else models.RoomSubtitleControl.PublicState.INACTIVE
        )
    else:
        public_state = (
            models.RoomSubtitleControl.PublicState.LIVE
            if present
            else models.RoomSubtitleControl.PublicState.RECONNECTING
        )
    return compare_and_set_subtitle_control_locked(
        locked_room,
        control,
        expected_control_generation=control.control_generation,
        expected_state_version=control.state_version,
        agent_present=present,
        worker_ready=present,
        worker_observed_at=timezone.now(),
        session_id=participant_identity if present else None,
        public_state=public_state,
        reason_code=(
            None
            if present
            else (
                control.reason_code
                or models.RoomSubtitleControl.ReasonCode.AGENT_MISSING
            )
        ),
    )


@transaction.atomic
def request_subtitle_stop(room, *, room_sid=None, reason_code=None):
    """Persist an OFF intent before any provider cleanup is attempted."""
    locked_room, control = lock_room_and_control(room, room_sid=room_sid)
    if control is None or (room_sid is not None and control.room_sid != room_sid):
        return None
    if (
        control.desired_state == models.RoomSubtitleControl.DesiredState.OFF
        and reason_code != models.RoomSubtitleControl.ReasonCode.ROOM_FINISHED
    ):
        return control
    if (
        reason_code == models.RoomSubtitleControl.ReasonCode.ROOM_FINISHED
        and control.room_finished_at is not None
    ):
        return control
    changes = {
        "desired_state": models.RoomSubtitleControl.DesiredState.OFF,
        "public_state": models.RoomSubtitleControl.PublicState.STOPPING,
        "reason_code": reason_code,
        "pending_since": None,
    }
    if reason_code == models.RoomSubtitleControl.ReasonCode.ROOM_FINISHED:
        changes["room_finished_at"] = control.room_finished_at or timezone.now()
    return compare_and_set_subtitle_control_locked(
        locked_room,
        control,
        expected_control_generation=control.control_generation,
        expected_state_version=control.state_version,
        new_intent=True,
        **changes,
    )


def schedule_subtitle_reconciliation(room_sid, *, countdown=0):
    """Wake one bounded Celery reconciliation; no periodic beat is required."""
    if not settings.CELERY_ENABLED:
        return 0
    from core.tasks.subtitle import (  # noqa: PLC0415  # pylint: disable=import-outside-toplevel
        process_subtitle_reconciliation,
    )

    try:
        process_subtitle_reconciliation.apply_async(
            args=[room_sid],
            queue="mastrao-transcription",
            countdown=countdown,
        )
    except Exception:  # noqa: BLE001  # pylint: disable=broad-exception-caught
        logger.warning("Unable to schedule subtitle reconciliation for %s", room_sid)
        return 0
    return 1
