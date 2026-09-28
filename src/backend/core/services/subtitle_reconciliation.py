"""Durable LiveKit subtitle-agent observation and reconciliation."""
# pylint: disable=cyclic-import,no-member

import asyncio
import json
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
)

logger = getLogger(__name__)

SUBTITLE_STATUS_TOPIC = "mastrao.subtitle.status"
PROVIDER_TIMEOUT_SECONDS = 8
CLEANUP_TIMEOUT_SECONDS = 3
MAX_RECONCILIATION_ATTEMPTS = 3
RETRY_DELAYS_SECONDS = (1, 5, 30)


class SubtitleReconciliationAmbiguous(RuntimeError):
    """The provider state is not safe to project as settled."""


class SubtitleProviderActive(RuntimeError):
    """The provider already has a live subtitle dispatch."""

    status_code = 409


@dataclass(frozen=True)
class _ProviderResult:
    dispatch_ids: list[str]


def _is_subtitle_dispatch(dispatch) -> bool:
    return getattr(dispatch, "agent_name", None) == settings.ROOM_SUBTITLE_AGENT_NAME


def _dispatch_ids(dispatches) -> list[str]:
    return [
        str(dispatch.id) for dispatch in dispatches if getattr(dispatch, "id", None)
    ]


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


async def _reconcile_provider(room_name, desired_state):
    client = utils.create_livekit_client()
    try:
        dispatches = [
            dispatch
            for dispatch in await _list_dispatches(client, room_name)
            if _is_subtitle_dispatch(dispatch)
        ]

        if desired_state == models.RoomSubtitleControl.DesiredState.OFF:
            for dispatch in dispatches:
                await _delete_dispatch(client, str(dispatch.id), room_name)
            remaining = [
                dispatch
                for dispatch in await _list_dispatches(client, room_name)
                if _is_subtitle_dispatch(dispatch)
            ]
            if remaining:
                raise SubtitleReconciliationAmbiguous(
                    "LiveKit subtitle dispatch cleanup is ambiguous."
                )
            return _ProviderResult(dispatch_ids=[])

        if len(dispatches) > 1:
            keep = dispatches[0]
            for dispatch in dispatches:
                if dispatch.id != keep.id:
                    await _delete_dispatch(client, str(dispatch.id), room_name)
            remaining = [
                dispatch
                for dispatch in await _list_dispatches(client, room_name)
                if _is_subtitle_dispatch(dispatch)
            ]
            if len(remaining) != 1:
                raise SubtitleReconciliationAmbiguous(
                    "LiveKit subtitle dispatch cleanup is ambiguous."
                )
            return _ProviderResult(dispatch_ids=_dispatch_ids(remaining))

        if dispatches:
            return _ProviderResult(dispatch_ids=_dispatch_ids(dispatches))

        try:
            created = await asyncio.wait_for(
                client.agent_dispatch.create_dispatch(
                    CreateAgentDispatchRequest(
                        agent_name=settings.ROOM_SUBTITLE_AGENT_NAME,
                        room=room_name,
                    )
                ),
                timeout=PROVIDER_TIMEOUT_SECONDS,
            )
        except api.TwirpError as error:
            if error.code == "already_exists":
                raise SubtitleProviderActive(
                    "A subtitle agent is already active in LiveKit."
                ) from error
            raise
        dispatch_id = getattr(created, "id", None)
        if not dispatch_id:
            raise SubtitleReconciliationAmbiguous(
                "LiveKit did not return a subtitle dispatch ID."
            )
        return _ProviderResult(dispatch_ids=[str(dispatch_id)])
    finally:
        await client.aclose()


def _provider_reconcile(room_name, desired_state):
    return async_to_sync(_reconcile_provider)(room_name, desired_state)


async def _publish_snapshot(room_name, snapshot):
    client = utils.create_livekit_client()
    try:
        await client.room.send_data(
            api.SendDataRequest(
                room=room_name,
                data=json.dumps(
                    {
                        "type": "status",
                        "schemaVersion": 1,
                        "status": snapshot["state"],
                        "stateVersion": snapshot["stateVersion"],
                        "sessionId": snapshot["sessionId"],
                        "reason": snapshot["reason"],
                        "desired": snapshot["desired"],
                    }
                ).encode("utf-8"),
                kind="RELIABLE",
                topic=SUBTITLE_STATUS_TOPIC,
            )
        )
    finally:
        await client.aclose()


def publish_subtitle_snapshot(room_id):
    """Publish the latest committed snapshot without changing database state."""
    control = (
        models.RoomSubtitleControl.objects.filter(room_id=room_id, is_current=True)
        .select_related("room")
        .first()
    )
    snapshot = _serialize_control(control)
    room_name = str(control.room_id) if control is not None else str(room_id)
    try:
        async_to_sync(_publish_snapshot)(room_name, snapshot)
    except Exception:  # pylint: disable=broad-exception-caught
        logger.exception("Failed to publish subtitle snapshot for room %s", room_id)


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


def _mark_failure(room_sid, *, stopping=False):
    now = timezone.now()
    with transaction.atomic():
        control = (
            models.RoomSubtitleControl.objects.select_for_update()
            .filter(room_sid=room_sid, is_current=True)
            .first()
        )
        if control is None:
            return None
        attempts = min(control.attempts + 1, MAX_RECONCILIATION_ATTEMPTS)
        delay = RETRY_DELAYS_SECONDS[min(attempts - 1, len(RETRY_DELAYS_SECONDS) - 1)]
        changes = {
            "public_state": (
                models.RoomSubtitleControl.PublicState.STOPPING
                if stopping
                else models.RoomSubtitleControl.PublicState.DEGRADED
            ),
            "reason_code": models.RoomSubtitleControl.ReasonCode.PROVIDER_UNAVAILABLE,
            "attempts": attempts,
            "next_retry_at": (
                now + timedelta(seconds=delay)
                if attempts < MAX_RECONCILIATION_ATTEMPTS
                else None
            ),
        }
        result = compare_and_set_subtitle_control(
            control.room_sid,
            expected_control_generation=control.control_generation,
            expected_state_version=control.state_version,
            current_only=True,
            **changes,
        )
        if result.next_retry_at is not None:
            transaction.on_commit(
                lambda sid=result.room_sid, countdown=delay: (
                    schedule_subtitle_reconciliation(sid, countdown=countdown)
                )
            )
        return result


def reconcile_subtitle_control(room_sid):
    """Converge one current control row to the observed LiveKit state."""
    control = _current_control(room_sid)
    if control is None:
        return None

    if not settings.ROOM_SUBTITLE_ENABLED and control.desired_state == "ON":
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
    try:
        provider_result = _provider_reconcile(str(control.room_id), desired_state)
        latest = _current_control(room_sid)
        if latest is None or latest.desired_state != desired_state:
            raise SubtitleReconciliationAmbiguous(
                "Subtitle intent changed while provider reconciliation was running."
            )
        if desired_state == models.RoomSubtitleControl.DesiredState.ON:
            public_state = (
                models.RoomSubtitleControl.PublicState.LIVE
                if latest.agent_present
                else models.RoomSubtitleControl.PublicState.STARTING
            )
            reason_code = None
            pending_since = latest.pending_since or timezone.now()
        else:
            public_state = (
                models.RoomSubtitleControl.PublicState.STOPPED
                if latest.reason_code
                == models.RoomSubtitleControl.ReasonCode.ROOM_FINISHED
                else models.RoomSubtitleControl.PublicState.INACTIVE
            )
            reason_code = latest.reason_code
            pending_since = None
        return compare_and_set_subtitle_control(
            latest.room_sid,
            expected_control_generation=latest.control_generation,
            expected_state_version=latest.state_version,
            current_only=True,
            observed_dispatch_ids=provider_result.dispatch_ids,
            public_state=public_state,
            reason_code=reason_code,
            pending_since=pending_since,
            attempts=0,
            next_retry_at=None,
        )
    except (SubtitleControlConflict, SubtitleControlContractError):
        raise
    except SubtitleProviderActive:
        _mark_failure(room_sid)
        raise
    except Exception as error:
        _mark_failure(
            room_sid,
            stopping=desired_state == models.RoomSubtitleControl.DesiredState.OFF,
        )
        if isinstance(error, SubtitleReconciliationAmbiguous):
            raise
        raise SubtitleReconciliationAmbiguous(
            "LiveKit subtitle reconciliation is ambiguous."
        ) from error


@transaction.atomic
def observe_subtitle_agent(room_sid, *, participant_identity, present):
    """Project verified LiveKit agent join/leave events into the current row."""
    control = (
        models.RoomSubtitleControl.objects.select_for_update()
        .filter(room_sid=room_sid, is_current=True)
        .first()
    )
    if control is None:
        return None
    if not present and control.session_id not in (None, participant_identity):
        return control
    public_state = (
        models.RoomSubtitleControl.PublicState.LIVE
        if present
        and control.desired_state == models.RoomSubtitleControl.DesiredState.ON
        else (
            models.RoomSubtitleControl.PublicState.RECONNECTING
            if control.desired_state == models.RoomSubtitleControl.DesiredState.ON
            else models.RoomSubtitleControl.PublicState.INACTIVE
        )
    )
    return compare_and_set_subtitle_control(
        control.room_sid,
        expected_control_generation=control.control_generation,
        expected_state_version=control.state_version,
        current_only=True,
        agent_present=present,
        session_id=participant_identity if present else None,
        public_state=public_state,
        reason_code=None
        if present
        else models.RoomSubtitleControl.ReasonCode.AGENT_MISSING,
    )


@transaction.atomic
def request_subtitle_stop(room, *, room_sid=None, reason_code=None):
    """Persist an OFF intent before any provider cleanup is attempted."""
    control = (
        models.RoomSubtitleControl.objects.select_for_update()
        .filter(room=room, is_current=True)
        .first()
    )
    if control is None or (room_sid is not None and control.room_sid != room_sid):
        return None
    terminal = reason_code == models.RoomSubtitleControl.ReasonCode.ROOM_FINISHED
    return compare_and_set_subtitle_control(
        control.room_sid,
        expected_control_generation=control.control_generation,
        expected_state_version=control.state_version,
        current_only=True,
        new_intent=True,
        desired_state=models.RoomSubtitleControl.DesiredState.OFF,
        public_state=(
            models.RoomSubtitleControl.PublicState.STOPPED
            if terminal
            else models.RoomSubtitleControl.PublicState.STOPPING
        ),
        reason_code=reason_code,
        pending_since=None,
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
