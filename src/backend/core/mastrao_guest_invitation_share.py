"""Issue a safe guest link for the current canonical-room host."""

import re
from urllib.parse import urlencode, urlparse
from uuid import uuid4

from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.http import require_POST

from core import models
from core.mastrao_guest_grant import CANONICAL_ROOM_SLUG
from core.mastrao_host_grant import active_host_grant
from core.mastrao_platform_facade import (
    PlatformFacadeError,
    request_platform,
    share_link_path,
)

SHARE_REF = re.compile(r"^share_[A-Za-z0-9_-]{32}$")
EXTERNAL_ID = re.compile(r"^[A-Za-z0-9._:-]{1,200}$")


def _meeting_origin():
    configured = settings.APPLICATION_BASE_URL
    parsed = urlparse(configured if isinstance(configured, str) else "")
    loopback = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    if (
        not parsed.hostname
        or parsed.scheme not in ({"http", "https"} if loopback else {"https"})
        or any(
            (
                parsed.username,
                parsed.password,
                parsed.params,
                parsed.query,
                parsed.fragment,
            )
        )
        or parsed.path not in {"", "/"}
    ):
        raise PlatformFacadeError()
    return f"{parsed.scheme}://{parsed.netloc}"


def _positive_epoch(value):
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _invite_url(body, binding):
    expected_fields = {
        "version",
        "organization_external_id",
        "meeting_ref",
        "room_ref",
        "invitation_ref",
        "share_ref",
        "state",
        "link_generated_at",
        "start_deadline_at",
        "first_started_at",
    }
    if (
        set(body) != expected_fields
        or not _positive_epoch(body["version"])
        or body["version"] != 1
    ):
        raise PlatformFacadeError()
    if (
        body["meeting_ref"] != binding.meeting_ref
        or body["room_ref"] != binding.room_ref
        or body["state"] != "issued"
    ):
        raise PlatformFacadeError()
    for field, pattern in (
        ("share_ref", SHARE_REF),
        ("organization_external_id", EXTERNAL_ID),
        ("invitation_ref", re.compile(r"[A-Za-z0-9_-]{16,160}")),
    ):
        if not isinstance(body[field], str) or not pattern.fullmatch(body[field]):
            raise PlatformFacadeError()
    if (
        not _positive_epoch(body["link_generated_at"])
        or not _positive_epoch(body["start_deadline_at"])
        or body["start_deadline_at"] != body["link_generated_at"] + 86400
    ):
        raise PlatformFacadeError()
    if body["first_started_at"] is not None and not _positive_epoch(
        body["first_started_at"]
    ):
        raise PlatformFacadeError()
    fragment = urlencode(
        {"organization": body["organization_external_id"], "share": body["share_ref"]}
    )
    # Fragments never reach proxy access logs or HTTP Referer headers.
    return f"{_meeting_origin()}/guest#{fragment}"


def _canonical_host_binding(request, room_ref):
    if not CANONICAL_ROOM_SLUG.fullmatch(room_ref):
        return None
    room = (
        models.Room.objects.select_related("mastrao_binding")
        .filter(slug=room_ref)
        .first()
    )
    if room is None or not hasattr(room, "mastrao_binding"):
        return None
    if active_host_grant(request, room) is None:
        return None
    return room.mastrao_binding


@require_POST
def create_guest_invitation_share(request, room_ref):
    """Return a guest URL only to the exact active host browser session."""

    binding = _canonical_host_binding(request, room_ref)
    if binding is None:
        return JsonResponse(
            {"message": "Invitation indisponible"},
            status=404,
            headers={
                "Cache-Control": "private, no-store",
                "Referrer-Policy": "no-referrer",
            },
        )
    try:
        invite_url = guest_invitation_url(request, binding)
    except PlatformFacadeError as error:
        return JsonResponse(
            {"message": "Invitation indisponible"},
            status=error.status,
            headers={
                "Cache-Control": "private, no-store",
                "Referrer-Policy": "no-referrer",
            },
        )
    return JsonResponse(
        {"invite_url": invite_url},
        headers={
            "Cache-Control": "private, no-store",
            "Referrer-Policy": "no-referrer",
        },
    )


def guest_invitation_url(request, binding):
    """Reuse the host's canonical share link for dialog and personal emails."""

    keys = request.session.get("mastrao_share_creation_keys", {})
    key = keys.get(binding.meeting_ref)
    if key is None:
        key = uuid4().hex
        keys[binding.meeting_ref] = key
        request.session["mastrao_share_creation_keys"] = keys
        request.session.save()
    body, _ = request_platform(
        request,
        "POST",
        share_link_path(binding.meeting_ref),
        accepted_statuses={201},
        options={"idempotency_key": key},
    )
    return _invite_url(body, binding)
