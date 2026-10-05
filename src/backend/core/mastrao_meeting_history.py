"""Authenticated Meet API backed by the Platform meeting facade."""

import json
import re

from django.http import JsonResponse
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from core.mastrao_host_contract import HostHandoffRefused
from core.mastrao_host_handoff import consume_host_handoff_for_oidc_session
from core.mastrao_platform_facade import (
    MEETING_CREATION_TIMEOUT_SECONDS,
    PlatformFacadeError,
    meeting_path,
    request_platform,
)

IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9_-]{16,128}$")
MAX_CREATION_BODY_BYTES = 8192
CREATION_FIELDS = {"title", "scheduled_start_at", "scheduled_end_at", "timezone"}
DAY_QUERY_FIELDS = {"day_start", "day_end", "cursor"}
UNIX_SECONDS = re.compile(r"^[0-9]{1,16}$")
MAX_SAFE_INTEGER = 9_007_199_254_740_991


def _reject_json_constant(_value):
    raise ValueError("Invalid JSON constant")


def _creation_body(request):
    """Bound the JSON envelope; Platform validates canonical schedule values."""

    declared = request.META.get("CONTENT_LENGTH")
    if declared and (
        not declared.isdecimal() or int(declared) > MAX_CREATION_BODY_BYTES
    ):
        raise PlatformFacadeError(status=422)
    raw = request.read(MAX_CREATION_BODY_BYTES + 1)
    if not raw:
        return None
    if request.content_type != "application/json" or len(raw) > MAX_CREATION_BODY_BYTES:
        raise PlatformFacadeError(status=422)
    try:
        body = json.loads(raw, parse_constant=_reject_json_constant)
    except (UnicodeDecodeError, ValueError, RecursionError) as error:
        raise PlatformFacadeError(status=422) from error
    if not isinstance(body, dict) or set(body) - CREATION_FIELDS:
        raise PlatformFacadeError(status=422)
    return body


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


def _day_list_options(request):
    """Validate the query envelope; Platform owns day filtering and access."""

    if set(request.GET) - DAY_QUERY_FIELDS or any(
        len(request.GET.getlist(field)) != 1 for field in request.GET
    ):
        raise PlatformFacadeError(status=422)
    options = {}
    for field in ("day_start", "day_end"):
        value = request.GET.get(field, "")
        if not UNIX_SECONDS.fullmatch(value) or int(value) > MAX_SAFE_INTEGER:
            raise PlatformFacadeError(status=422)
        options[field] = int(value)
    if options["day_end"] <= options["day_start"]:
        raise PlatformFacadeError(status=422)
    if "cursor" in request.GET:
        options["cursor"] = request.GET["cursor"]
    return options


@require_http_methods(["GET", "POST"])
def meeting_collection(request):
    """List canonical meetings for a day or use the existing creation flow."""

    if request.method == "POST":
        return create_meeting(request)
    return _response(
        lambda: request_platform(
            request,
            "GET",
            "/api/meet/meetings",
            accepted_statuses={200},
            options=_day_list_options(request),
        )
    )


@require_POST
def create_meeting(request):
    """Create a canonical meeting and bind its host grant to this session."""

    idempotency_key = request.headers.get("X-Idempotency-Key", "")
    if not IDEMPOTENCY_KEY.fullmatch(idempotency_key):
        return JsonResponse({"message": "Paramètres invalides"}, status=422)

    def create_and_bind():
        payload = _creation_body(request)
        options = {
            "idempotency_key": idempotency_key,
            "timeout": MEETING_CREATION_TIMEOUT_SECONDS,
        }
        if payload is not None:
            options["json"] = payload
        body, status = request_platform(
            request,
            "POST",
            "/api/meet/meetings",
            accepted_statuses={201},
            options=options,
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
