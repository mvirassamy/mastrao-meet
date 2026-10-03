"""Close canonical meetings after a verified ten-minute empty-room grace period."""

import hashlib
import re
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse, urlunparse

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from celery import current_app

from core import models
from core.mastrao_core_http import post_core_json
from core.mastrao_room_close_contract import RoomCloseRefused, sign_idle_meeting_close
from core.services.room_management import RoomManagement, RoomManagementException

IDLE_CLOSE_SECONDS = 600
EVENT_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
ROOM_SID = re.compile(r"^RM_[A-Za-z0-9_-]{1,124}$")


def _valid_room_finished_event(room_sid, event_id, event_time):
    return (
        isinstance(room_sid, str)
        and ROOM_SID.fullmatch(room_sid)
        and isinstance(event_id, str)
        and EVENT_ID.fullmatch(event_id)
        and isinstance(event_time, int)
        and event_time > 0
    )


def _organization_for(binding):
    host = (
        binding.host_grants.exclude(organization_external_id__isnull=True)
        .exclude(organization_external_id="")
        .order_by("-consumed_at")
        .first()
    )
    if host is not None:
        return host.organization_external_id
    recording = getattr(binding, "recording_binding", None)
    return recording.organization_external_id if recording is not None else None


def idle_close_blocks_restart(room_id):
    """Return whether the provider's ten-minute departure timeout has elapsed."""

    return models.MastraoIdleCloseCandidate.objects.filter(
        room_binding__room_id=room_id,
        state__in=[
            models.MastraoIdleCloseCandidate.State.PENDING,
            models.MastraoIdleCloseCandidate.State.DELIVERING,
        ],
    ).exists()


def _verified_empty_since(binding, room_sid):
    """Return the last verified departure only when every observed peer left."""

    connections = models.MastraoRtcConnection.objects.filter(
        room_binding=binding,
        room_sid=room_sid,
    )
    if not connections.exists() or connections.filter(ended=False).exists():
        return None
    last_departure = (
        models.MastraoRtcObservation.objects.filter(
            room_binding=binding,
            room_sid=room_sid,
            event_type="participant_left",
        )
        .order_by("-event_time_seconds", "-event_id")
        .first()
    )
    if last_departure is None:
        return None
    return datetime.fromtimestamp(last_departure.event_time_seconds, tz=UTC)


@transaction.atomic
def observe_room_finished(event):
    """Persist and enqueue one signed LiveKit room-finished observation."""

    room_sid = getattr(event.room, "sid", None)
    departure_timeout = getattr(event.room, "departure_timeout", None)
    event_id = getattr(event, "id", None)
    event_time = getattr(event, "created_at", None)
    if departure_timeout != IDLE_CLOSE_SECONDS or not _valid_room_finished_event(
        room_sid, event_id, event_time
    ):
        return None
    try:
        room_id = event.room.name
        binding = models.MastraoRoomBinding.objects.select_for_update(of=("self",)).get(
            room_id=room_id,
            closing_at__isnull=True,
            closure__isnull=True,
        )
    except (models.MastraoRoomBinding.DoesNotExist, ValueError):
        return None
    organization_external_id = _organization_for(binding)
    if not organization_external_id:
        return None
    finished_at = datetime.fromtimestamp(event_time, tz=UTC)
    empty_since = _verified_empty_since(binding, room_sid)
    if empty_since is None or finished_at < empty_since + timedelta(
        seconds=IDLE_CLOSE_SECONDS
    ):
        return None
    close_request_id = (
        "idleclose_"
        + hashlib.sha256(
            f"{binding.room_ref}:{room_sid}:{event_id}".encode()
        ).hexdigest()[:32]
    )
    candidate, _created = models.MastraoIdleCloseCandidate.objects.get_or_create(
        room_finished_event_id=event_id,
        defaults={
            "room_binding": binding,
            "organization_external_id": organization_external_id,
            "close_request_id": close_request_id,
            "room_sid": room_sid,
            "room_finished_at": finished_at,
        },
    )
    if candidate.state == models.MastraoIdleCloseCandidate.State.PENDING:
        if settings.CELERY_ENABLED:
            transaction.on_commit(
                lambda: current_app.send_task(
                    "core.tasks.idle_close.process_idle_close",
                    args=[str(candidate.pk)],
                )
            )
    return candidate


def _idle_close_endpoint():
    configured = urlparse(settings.MASTRAO_CORE_MEETING_CLOSE_ENDPOINT)
    return urlunparse(
        configured._replace(path="/internal/v1/meetings/idle-close", query="")
    )


def _validate_response(body, candidate):
    required = {
        "version",
        "matter_ref",
        "meeting_ref",
        "room_ref",
        "state",
        "state_version",
        "requested_at",
    }
    if not isinstance(body, dict) or set(body) not in (
        required,
        required | {"ended_at"},
    ):
        raise RoomCloseRefused(status=503)
    if (
        body.get("version") != 1
        or body.get("meeting_ref") != candidate.room_binding.meeting_ref
        or body.get("room_ref") != candidate.room_binding.room_ref
        or body.get("state") not in {"ending", "ended"}
    ):
        raise RoomCloseRefused(status=503)
    return body


def deliver_idle_close(candidate_pk):
    """Deliver one due candidate only while the provider room remains absent."""

    try:
        with transaction.atomic():
            candidate = (
                models.MastraoIdleCloseCandidate.objects.select_for_update()
                .select_related("room_binding", "room_binding__room")
                .get(pk=candidate_pk)
            )
            if candidate.state not in {
                models.MastraoIdleCloseCandidate.State.PENDING,
                models.MastraoIdleCloseCandidate.State.DELIVERING,
            }:
                return
            if candidate.room_finished_at > timezone.now():
                raise IdleCloseRetryable()
            if candidate.room_binding.closing_at is not None or hasattr(
                candidate.room_binding, "closure"
            ):
                candidate.state = models.MastraoIdleCloseCandidate.State.CANCELLED
                candidate.last_error = "meeting_already_closing"
                candidate.save(update_fields=["state", "last_error", "updated_at"])
                return
            if candidate.state == models.MastraoIdleCloseCandidate.State.PENDING:
                room_management = RoomManagement()
                current_room_sid = room_management.room_sid(
                    str(candidate.room_binding.room_id)
                )
                if current_room_sid == candidate.room_sid:
                    raise IdleCloseRetryable()
                if current_room_sid is not None:
                    room_management.delete_room(str(candidate.room_binding.room_id))
                candidate.state = models.MastraoIdleCloseCandidate.State.DELIVERING
                candidate.last_error = ""
                candidate.save(update_fields=["state", "last_error", "updated_at"])

        assertion, _claims = sign_idle_meeting_close(candidate)
        body = post_core_json(
            endpoint=_idle_close_endpoint(),
            expected_path="/internal/v1/meetings/idle-close",
            body={"idle_close_assertion": assertion},
            timeout=settings.MASTRAO_CORE_MEETING_CLOSE_TIMEOUT_SECONDS,
            refusal=RoomCloseRefused,
            passthrough_statuses={404, 409},
            client_error_status=None,
        )
        _validate_response(body, candidate)
    except RoomManagementException as error:
        _retry(candidate.pk, "provider_unavailable")
        raise IdleCloseRetryable() from error
    except RoomCloseRefused as error:
        if error.status == 503:
            _retry(candidate.pk, "core_unavailable")
            raise IdleCloseRetryable() from error
        _settle(candidate.pk, delivered=False, error="core_refused")
        return
    _settle(candidate.pk, delivered=True)


def _retry(candidate_pk, error):
    models.MastraoIdleCloseCandidate.objects.filter(
        pk=candidate_pk,
        state=models.MastraoIdleCloseCandidate.State.DELIVERING,
    ).update(
        last_error=error,
        updated_at=timezone.now(),
    )


def _settle(candidate_pk, *, delivered, error=""):
    updates = {
        "state": (
            models.MastraoIdleCloseCandidate.State.DELIVERED
            if delivered
            else models.MastraoIdleCloseCandidate.State.CANCELLED
        ),
        "last_error": error,
        "updated_at": timezone.now(),
    }
    if delivered:
        updates["delivered_at"] = timezone.now()
    models.MastraoIdleCloseCandidate.objects.filter(
        pk=candidate_pk,
        state=models.MastraoIdleCloseCandidate.State.DELIVERING,
    ).update(**updates)


class IdleCloseRetryable(RuntimeError):
    """A transient provider or Core failure suitable for Celery retry."""
