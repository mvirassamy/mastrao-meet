"""Authenticated Meet API backed by the Platform meeting facade."""

import re

from django.http import JsonResponse
from django.views.decorators.http import require_GET, require_POST

from core.mastrao_host_contract import HostHandoffRefused
from core.mastrao_host_handoff import consume_host_handoff_for_oidc_session
from core.mastrao_platform_facade import (
    MEETING_CREATION_TIMEOUT_SECONDS,
    PlatformFacadeError,
    meeting_path,
    request_platform,
)

IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9_-]{16,128}$")


def _response(operation):
    try:
        body, status = operation()
        return JsonResponse(
            body,
            status=status,
            headers={"Cache-Control": "private, no-store"},
        )
    except PlatformFacadeError as error:
        return JsonResponse(
            {"message": "Ressource Meet indisponible"},
            status=error.status,
            headers={"Cache-Control": "private, no-store"},
        )


@require_POST
def create_meeting(request):
    """Create a canonical meeting and bind its host grant to this session."""

    idempotency_key = request.headers.get("X-Idempotency-Key", "")
    if not IDEMPOTENCY_KEY.fullmatch(idempotency_key):
        return JsonResponse({"message": "Paramètres invalides"}, status=422)

    def create_and_bind():
        body, status = request_platform(
            request,
            "POST",
            "/api/meet/meetings",
            accepted_statuses={201},
            options={
                "idempotency_key": idempotency_key,
                "timeout": MEETING_CREATION_TIMEOUT_SECONDS,
            },
        )
        handoff = body.pop("host_handoff", None)
        if not isinstance(handoff, str):
            raise PlatformFacadeError()
        try:
            binding = consume_host_handoff_for_oidc_session(request, handoff)
        except HostHandoffRefused as error:
            raise PlatformFacadeError(status=error.status) from error
        if binding.room.slug != body.get("room_ref"):
            raise PlatformFacadeError()
        return body, status

    return _response(create_and_bind)


@require_GET
def meeting_history(request):
    """Return the authenticated user's paginated meeting history."""

    if set(request.GET) - {"cursor"} or len(request.GET.getlist("cursor")) > 1:
        return JsonResponse({"message": "Paramètres invalides"}, status=422)
    return _response(
        lambda: request_platform(
            request,
            "GET",
            "/api/meet/meetings/history/",
            accepted_statuses={200},
            options={"cursor": request.GET.get("cursor")},
        )
    )


@require_GET
def meeting_history_detail(request, meeting_ref):
    """Return transcription and summary state for one owned meeting."""

    return _response(
        lambda: request_platform(
            request,
            "GET",
            meeting_path(meeting_ref),
            accepted_statuses={200},
        )
    )


@require_POST
def meeting_summary(request, meeting_ref):
    """Request or return the summary for one owned meeting."""

    return _response(
        lambda: request_platform(
            request,
            "POST",
            meeting_path(meeting_ref, "summary"),
            accepted_statuses={200, 202},
        )
    )
