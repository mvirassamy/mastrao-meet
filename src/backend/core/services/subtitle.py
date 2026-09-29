"""Service for managing subtitle agents in LiveKit rooms."""
# pylint: disable=no-member

import time
from logging import getLogger

from django.conf import settings
from django.utils import timezone

from asgiref.sync import async_to_sync, sync_to_async
from livekit import api

from core import models, utils
from core.mastrao_room_lifecycle import is_mastrao_room_closed
from core.services.subtitle_control import (
    SubtitleControlConflict,
    compare_and_set_subtitle_control,
    ensure_subtitle_control,
)
from core.services.subtitle_reconciliation import (
    SubtitleConvergenceBusy,
    SubtitleProviderActive,
    dispatch_provider,
    is_subtitle_agent_dispatch,
    reconcile_subtitle_control,
    request_subtitle_stop,
    schedule_subtitle_reconciliation,
    subtitle_provider,
)

logger = getLogger(__name__)
MAX_START_CAS_ATTEMPTS = 3


class SubtitleException(Exception):
    """Exception raised when subtitle operations fail."""


class SubtitleAlreadyActive(SubtitleException):
    """A provider subtitle agent is already active for the room."""

    status_code = 409


def _read_current_subtitle_control(room_sid):
    """Read the current control row after a compare-and-set conflict."""
    return (
        models.RoomSubtitleControl.objects.filter(
            room_sid=room_sid,
            is_current=True,
        )
        .select_related("room")
        .first()
    )


async def _resolve_subtitle_room(room):
    """Resolve the provider room SID and current subtitle dispatches."""
    lkapi = utils.create_livekit_client()
    try:
        room_response = await lkapi.room.list_rooms(
            api.ListRoomsRequest(names=[str(room.id)])
        )
        if not room_response.rooms:
            raise SubtitleException("LiveKit room does not exist")
        room_sid = room_response.rooms[0].sid
        dispatches = await lkapi.agent_dispatch.list_dispatch(str(room.id))
        active = [
            dispatch for dispatch in dispatches if is_subtitle_agent_dispatch(dispatch)
        ]
        return room_sid, active
    except SubtitleException:
        raise
    except Exception as error:
        logger.exception("Failed to create agent dispatch for room %s", room.id)
        raise SubtitleException("Failed to create subtitle agent") from error
    finally:
        await lkapi.aclose()


def _persist_start_intent(room, room_sid, requested_provider):
    """Persist ON with short Room -> Control transactions only."""

    control = ensure_subtitle_control(room, room_sid=room_sid)
    for attempt in range(MAX_START_CAS_ATTEMPTS):
        if control.room_finished_at is not None or is_mastrao_room_closed(room):
            raise SubtitleException("Subtitles cannot restart a finished room.")
        if (
            control.provider not in (None, requested_provider)
            and control.desired_state == models.RoomSubtitleControl.DesiredState.ON
        ):
            raise SubtitleAlreadyActive(
                "A different subtitle provider is already active for this room."
            )
        needs_intent = (
            control.desired_state != models.RoomSubtitleControl.DesiredState.ON
        )
        needs_contract_fields = (
            control.provider != requested_provider
            or control.agent_name != settings.ROOM_SUBTITLE_AGENT_NAME
        )
        if not needs_intent and not needs_contract_fields:
            return control
        try:
            return compare_and_set_subtitle_control(
                control.room_sid,
                expected_control_generation=control.control_generation,
                expected_state_version=control.state_version,
                new_intent=needs_intent,
                desired_state=models.RoomSubtitleControl.DesiredState.ON,
                public_state=(
                    models.RoomSubtitleControl.PublicState.STARTING
                    if needs_intent
                    else control.public_state
                ),
                reason_code=None,
                pending_since=timezone.now(),
                attempts=0,
                next_retry_at=None,
                provider=requested_provider,
                agent_name=settings.ROOM_SUBTITLE_AGENT_NAME,
                current_only=True,
            )
        except SubtitleControlConflict:
            if attempt == MAX_START_CAS_ATTEMPTS - 1:
                raise SubtitleException(
                    "Subtitle start intent changed during reconciliation."
                ) from None
            control = _read_current_subtitle_control(room_sid)
            if control is None:
                control = ensure_subtitle_control(room, room_sid=room_sid)
    return control


class SubtitleService:
    """Service for managing subtitle agents in LiveKit rooms."""

    @async_to_sync
    async def start_subtitle(self, room):
        """Persist an ON intent and reconcile one provider subtitle agent."""

        room_sid, active = await _resolve_subtitle_room(room)

        requested_provider = subtitle_provider()
        active_provider_conflict = any(
            dispatch_provider(dispatch) not in (None, requested_provider)
            for dispatch in active
        )
        if active_provider_conflict:
            raise SubtitleAlreadyActive(
                "A different subtitle provider is already active for this room."
            )
        control = await sync_to_async(
            _persist_start_intent,
            thread_sensitive=True,
        )(room, room_sid, requested_provider)
        await sync_to_async(schedule_subtitle_reconciliation)(control.room_sid)
        if settings.CELERY_ENABLED:
            return control

        deadline = time.monotonic() + settings.ROOM_SUBTITLE_SYNC_BUDGET_SECONDS
        try:
            return await sync_to_async(
                reconcile_subtitle_control,
                thread_sensitive=True,
            )(control.room_sid, deadline=deadline)
        except SubtitleConvergenceBusy:
            return control
        except SubtitleProviderActive:
            return control
        except SubtitleException:
            raise
        except Exception as error:
            logger.exception("Failed to reconcile subtitle start for room %s", room.id)
            raise SubtitleException("Failed to reconcile subtitle agent") from error

    @async_to_sync
    async def stop_subtitle(self, room, *, room_sid=None, reason_code=None):
        """Persist an OFF intent and reconcile provider cleanup."""

        control = await sync_to_async(request_subtitle_stop, thread_sensitive=True)(
            room,
            room_sid=room_sid,
            reason_code=reason_code,
        )
        if control is None:
            return None
        await sync_to_async(schedule_subtitle_reconciliation)(control.room_sid)
        if settings.CELERY_ENABLED:
            return control
        deadline = time.monotonic() + settings.ROOM_SUBTITLE_SYNC_BUDGET_SECONDS
        try:
            return await sync_to_async(
                reconcile_subtitle_control,
                thread_sensitive=True,
            )(control.room_sid, deadline=deadline)
        except SubtitleConvergenceBusy:
            return control
        except Exception as error:
            logger.exception("Failed to reconcile subtitle stop for room %s", room.id)
            raise SubtitleException("Failed to stop subtitle agent") from error
