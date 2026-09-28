"""Service for managing subtitle agents in LiveKit rooms."""
# pylint: disable=no-member

from logging import getLogger

from django.conf import settings
from django.utils import timezone

from asgiref.sync import async_to_sync, sync_to_async
from livekit import api

from core import utils
from core.services.subtitle_control import (
    SubtitleControlConflict,
    compare_and_set_subtitle_control,
    ensure_subtitle_control,
)
from core.services.subtitle_reconciliation import (
    SubtitleProviderActive,
    reconcile_subtitle_control,
    request_subtitle_stop,
    schedule_subtitle_reconciliation,
)

logger = getLogger(__name__)


class SubtitleException(Exception):
    """Exception raised when subtitle operations fail."""


class SubtitleAlreadyActive(SubtitleException):
    """A provider subtitle agent is already active for the room."""

    status_code = 409


class SubtitleService:
    """Service for managing subtitle agents in LiveKit rooms."""

    @async_to_sync
    async def start_subtitle(self, room):
        """Persist an ON intent and reconcile one provider subtitle agent."""

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
                dispatch
                for dispatch in dispatches
                if getattr(dispatch, "agent_name", None)
                == settings.ROOM_SUBTITLE_AGENT_NAME
            ]
        except SubtitleException:
            raise
        except Exception as e:
            logger.exception("Failed to create agent dispatch for room %s", room.id)
            raise SubtitleException("Failed to create subtitle agent") from e
        finally:
            await lkapi.aclose()

        control = await sync_to_async(ensure_subtitle_control, thread_sensitive=True)(
            room, room_sid=room_sid
        )
        if active or control.desired_state == "ON":
            raise SubtitleAlreadyActive(
                "A subtitle agent is already active for this room."
            )
        try:
            control = await sync_to_async(
                compare_and_set_subtitle_control,
                thread_sensitive=True,
            )(
                control.room_sid,
                expected_control_generation=control.control_generation,
                expected_state_version=control.state_version,
                new_intent=True,
                desired_state="ON",
                public_state="starting",
                reason_code=None,
                pending_since=timezone.now(),
                attempts=0,
                next_retry_at=None,
                current_only=True,
            )
        except SubtitleControlConflict as error:
            raise SubtitleAlreadyActive(
                "A subtitle start intent is already active for this room."
            ) from error

        try:
            if settings.CELERY_ENABLED:
                await sync_to_async(schedule_subtitle_reconciliation)(control.room_sid)
            else:
                await sync_to_async(reconcile_subtitle_control, thread_sensitive=True)(
                    control.room_sid
                )
        except SubtitleProviderActive as error:
            raise SubtitleAlreadyActive(str(error)) from error
        except Exception as error:
            raise SubtitleException("Failed to reconcile subtitle agent") from error
        return control

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
        if settings.CELERY_ENABLED:
            await sync_to_async(schedule_subtitle_reconciliation)(control.room_sid)
        else:
            await sync_to_async(reconcile_subtitle_control, thread_sensitive=True)(
                control.room_sid
            )
        return control
