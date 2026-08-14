"""Temporary host client for the canonical Cabinet Core close transition."""

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from core import models
from core.mastrao_core_http import post_core_json
from core.mastrao_host_grant import active_host_close_grant, active_host_compact_grant
from core.mastrao_room_close_contract import (
    RoomCloseRefused,
    sign_meeting_close_request,
)
from core.mastrao_room_contract import OPAQUE_REFERENCE


def _validate_response(body, grant):
    # pylint: disable=too-many-boolean-expressions
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
        or body.get("meeting_ref") != grant.meeting_ref
        or body.get("room_ref") != grant.room_ref
        or body.get("state") not in {"ending", "ended"}
        or not isinstance(body.get("state_version"), int)
        or isinstance(body.get("state_version"), bool)
        or body["state_version"] < 1
        or not isinstance(body.get("requested_at"), int)
        or isinstance(body.get("requested_at"), bool)
    ):
        raise RoomCloseRefused(status=503)
    if body["state"] == "ending" and "ended_at" in body:
        raise RoomCloseRefused(status=503)
    if body["state"] == "ended" and (
        not isinstance(body.get("ended_at"), int)
        or isinstance(body.get("ended_at"), bool)
    ):
        raise RoomCloseRefused(status=503)
    for name in ("matter_ref", "meeting_ref", "room_ref"):
        if not isinstance(body.get(name), str) or not OPAQUE_REFERENCE.fullmatch(
            body[name]
        ):
            raise RoomCloseRefused(status=503)
    return body


@transaction.atomic
def _persist_closing_fence(grant):
    binding = models.MastraoRoomBinding.objects.select_for_update().get(
        pk=grant.room_binding_id
    )
    if binding.closing_at is None:
        binding.closing_at = timezone.now()
        binding.save(update_fields=["closing_at", "updated_at"])


def request_meeting_close(request, room, close_request_id):
    """Ask Core to commit an irreversible close for the exact active host grant."""

    if not settings.MASTRAO_MEETING_CLOSE_ENABLED:
        raise RoomCloseRefused()
    grant = active_host_close_grant(request, room)
    if grant is None:
        raise RoomCloseRefused()
    compact_host = active_host_compact_grant(request, grant)
    if not compact_host:
        raise RoomCloseRefused()
    assertion, _claims = sign_meeting_close_request(
        grant, compact_host, close_request_id
    )
    body = post_core_json(
        endpoint=settings.MASTRAO_CORE_MEETING_CLOSE_ENDPOINT,
        expected_path="/internal/v1/meetings/close",
        body={"host_grant": compact_host, "close_assertion": assertion},
        timeout=settings.MASTRAO_CORE_MEETING_CLOSE_TIMEOUT_SECONDS,
        refusal=RoomCloseRefused,
        passthrough_statuses={404, 409},
        client_error_status=None,
    )
    response = _validate_response(body, grant)
    _persist_closing_fence(grant)
    return response
