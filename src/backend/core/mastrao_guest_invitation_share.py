"""Issue a safe guest link for the current canonical-room host."""

import re
from urllib.parse import urlencode, urlparse

from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.http import require_POST

from core import models
from core.mastrao_guest_grant import CANONICAL_ROOM_SLUG
from core.mastrao_host_grant import active_host_grant
from core.mastrao_platform_facade import (
    PlatformFacadeError,
    guest_invitation_path,
    request_platform,
)

COMPACT_JWS = re.compile(
    r"^[A-Za-z0-9_-]{1,4096}\.[A-Za-z0-9_-]{1,8192}\.[A-Za-z0-9_-]{1,4096}$"
)


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


def _invite_url(body, meeting_ref):
    if body.get("meeting_ref") != meeting_ref:
        raise PlatformFacadeError()
    credential = body.get("guest_invitation")
    if not isinstance(credential, str) or not COMPACT_JWS.fullmatch(credential):
        raise PlatformFacadeError()
    fragment = urlencode({"invite": credential})
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
            headers={"Cache-Control": "private, no-store"},
        )
    try:
        body, _ = request_platform(
            request,
            "POST",
            guest_invitation_path(binding.meeting_ref),
            accepted_statuses={200, 201},
        )
        invite_url = _invite_url(body, binding.meeting_ref)
    except PlatformFacadeError as error:
        return JsonResponse(
            {"message": "Invitation indisponible"},
            status=error.status,
            headers={"Cache-Control": "private, no-store"},
        )
    return JsonResponse(
        {"invite_url": invite_url},
        headers={"Cache-Control": "private, no-store"},
    )
