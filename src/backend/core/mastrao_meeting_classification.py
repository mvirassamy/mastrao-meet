"""OIDC-only classification bridge; Core owns matters, rights and commands."""

import json
import re
from urllib.parse import urlencode

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_http_methods

from core.mastrao_platform_facade import (
    CURSOR,
    MEETING_REF,
    PlatformFacadeError,
    meeting_path,
    request_platform,
)

MAX_COMMAND_BYTES = 4096
MAX_SAFE_INTEGER = 9_007_199_254_740_991
UUID = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-"
    r"[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$"
)
# Platform's projectMeetingClassification uses the typed TanStack matter route.
MATTER_PATH = re.compile(
    r"/orgs/(?P<organization_slug>[a-z0-9]+(?:-[a-z0-9]+)*)/matters/"
    r"(?P<matter_ref>[A-Za-z0-9_-]{16,160})"
)
CLASSIFICATION_FIELDS = {
    "version",
    "meeting_ref",
    "matter_ref",
    "matter_path",
    "classification_version",
    "can_classify",
}
CLASSIFY_COMMAND_FIELDS = {
    "version",
    "meeting_ref",
    "matter_ref",
    "expected_classification_version",
    "command_id",
}
MATTER_FIELDS = {"version", "matter_ref", "display_label", "metadata_version"}
MATTER_COMMAND_FIELDS = {"version", "command_id", "display_label"}
PUBLIC_ERROR_STATUSES = {400, 401, 403, 404, 409, 422, 429}


def _safe_integer(value, minimum=0):
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and minimum <= value <= MAX_SAFE_INTEGER
    )


def _version_one(value):
    return _safe_integer(value) and value == 1


def _reference(value):
    return isinstance(value, str) and MEETING_REF.fullmatch(value) is not None


def _label(value):
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= 200
        or value != value.strip()
    ):
        return False
    return all(
        ord(character) > 0x1F
        and not 0x7F <= ord(character) <= 0x9F
        and not 0xD800 <= ord(character) <= 0xDFFF
        and ord(character) not in {0x2028, 0x2029}
        for character in value
    )


def _unique_json_fields(pairs):
    body = {}
    for field, value in pairs:
        if field in body:
            raise ValueError("Duplicate JSON field")
        body[field] = value
    return body


def _read_command(request, fields):
    declared = request.META.get("CONTENT_LENGTH")
    if declared and (
        len(declared) > 20
        or not declared.isdecimal()
        or int(declared) > MAX_COMMAND_BYTES
    ):
        raise PlatformFacadeError(status=422)
    if request.content_type != "application/json":
        raise PlatformFacadeError(status=422)
    raw = request.read(MAX_COMMAND_BYTES + 1)
    if len(raw) > MAX_COMMAND_BYTES:
        raise PlatformFacadeError(status=422)
    try:
        body = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_json_fields)
    except (UnicodeError, ValueError, RecursionError) as error:
        raise PlatformFacadeError(status=422) from error
    if (
        not isinstance(body, dict)
        or set(body) != fields
        or not _version_one(body.get("version"))
    ):
        raise PlatformFacadeError(status=422)
    return body


def _response(operation):
    try:
        body, status = operation()
    except PlatformFacadeError as error:
        body = {"message": "Classement ou dossiers indisponibles."}
        status = error.status
    return JsonResponse(
        body,
        status=status,
        headers={
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


def _classification_result(body, meeting_ref, command):
    fields = CLASSIFICATION_FIELDS
    if command is not None:
        fields = fields | {"idempotent_replay"}
    if (
        set(body) - {"matter_display_label"} != fields
        or not _version_one(body.get("version"))
        or body.get("meeting_ref") != meeting_ref
    ):
        raise PlatformFacadeError()
    _validate_matter_path(body)
    label = body.get("matter_display_label")
    if label is not None and not _label(label):
        raise PlatformFacadeError()
    if (
        not _safe_integer(body.get("classification_version"))
        or not isinstance(body.get("can_classify"), bool)
        or (body.get("matter_ref") is not None and not _reference(body["matter_ref"]))
    ):
        raise PlatformFacadeError()
    if command is not None and (
        body["matter_ref"] != command["matter_ref"]
        or not isinstance(body.get("idempotent_replay"), bool)
    ):
        raise PlatformFacadeError()


def _validate_matter_path(body):
    """Keep Platform's authorized relative path bound to its current dossier."""

    path = body["matter_path"]
    label = body.get("matter_display_label")
    if path is None:
        if label is not None:
            raise PlatformFacadeError()
        return
    if not isinstance(path, str) or label is None or body["matter_ref"] is None:
        raise PlatformFacadeError()
    match = MATTER_PATH.fullmatch(path)
    if (
        match is None
        or len(match["organization_slug"]) > 100
        or match["matter_ref"] != body["matter_ref"]
    ):
        raise PlatformFacadeError()


def _classification(request, meeting_ref):
    path = meeting_path(meeting_ref, "classification")
    if request.GET:
        raise PlatformFacadeError(status=422)
    command = None
    options = {"error_statuses": PUBLIC_ERROR_STATUSES}
    if request.method == "POST":
        command = _read_command(request, CLASSIFY_COMMAND_FIELDS)
        if (
            command["meeting_ref"] != meeting_ref
            or not _reference(command["matter_ref"])
            or not _reference(command["command_id"])
            or not _safe_integer(command["expected_classification_version"])
        ):
            raise PlatformFacadeError(status=422)
        options["json"] = command
    body, status = request_platform(
        request,
        request.method,
        path,
        accepted_statuses={200},
        options=options,
    )
    _classification_result(body, meeting_ref, command)
    return body, status


@csrf_protect
@require_http_methods(["GET", "POST"])
def meeting_classification(request, meeting_ref):
    """Read or classify a meeting under the current Core business permissions."""

    return _response(lambda: _classification(request, meeting_ref))


def _matter_query(request):
    fields = {"query", "limit", "cursor", "writable_only"}
    if set(request.GET) - fields or any(
        len(request.GET.getlist(field)) != 1 for field in request.GET
    ):
        raise PlatformFacadeError(status=422)
    query = request.GET.dict()
    if "query" in query:
        query["query"] = query["query"].strip()
        if len(query["query"]) > 100:
            raise PlatformFacadeError(status=422)
    limit = query.get("limit")
    if limit is not None and (
        not re.fullmatch(r"[0-9]{1,2}", limit) or not 1 <= int(limit) <= 50
    ):
        raise PlatformFacadeError(status=422)
    if "cursor" in query and not CURSOR.fullmatch(query["cursor"]):
        raise PlatformFacadeError(status=422)
    if "writable_only" in query and query["writable_only"] not in {"true", "false"}:
        raise PlatformFacadeError(status=422)
    encoded = urlencode(query)
    return f"/api/meet/matters?{encoded}" if encoded else "/api/meet/matters"


def _matter_result(body, *, created=False):
    fields = MATTER_FIELDS
    if created:
        fields = fields | {"idempotent_replay"}
    if (
        not isinstance(body, dict)
        or set(body) != fields
        or not _version_one(body.get("version"))
    ):
        raise PlatformFacadeError()
    if (
        not _reference(body.get("matter_ref"))
        or not _label(body.get("display_label"))
        or not _safe_integer(body.get("metadata_version"), minimum=1)
        or (created and not isinstance(body.get("idempotent_replay"), bool))
    ):
        raise PlatformFacadeError()


def _matters(request):
    if request.method == "POST":
        if request.GET:
            raise PlatformFacadeError(status=422)
        command = _read_command(request, MATTER_COMMAND_FIELDS)
        if (
            not isinstance(command["command_id"], str)
            or not UUID.fullmatch(command["command_id"])
            or not _label(command["display_label"])
        ):
            raise PlatformFacadeError(status=422)
        body, status = request_platform(
            request,
            "POST",
            "/api/meet/matters",
            accepted_statuses={200, 201},
            options={"json": command, "error_statuses": PUBLIC_ERROR_STATUSES},
        )
        _matter_result(body, created=True)
        return body, status
    body, status = request_platform(
        request,
        "GET",
        _matter_query(request),
        accepted_statuses={200},
        options={"error_statuses": PUBLIC_ERROR_STATUSES},
    )
    if (
        set(body) != {"version", "items", "next_cursor"}
        or not _version_one(body.get("version"))
        or not isinstance(body.get("items"), list)
        or len(body["items"]) > 50
    ):
        raise PlatformFacadeError()
    if body["next_cursor"] is not None and (
        not isinstance(body["next_cursor"], str)
        or not CURSOR.fullmatch(body["next_cursor"])
    ):
        raise PlatformFacadeError()
    for item in body["items"]:
        _matter_result(item)
    return body, status


@csrf_protect
@require_http_methods(["GET", "POST"])
def meeting_matters(request):
    """List or create authorized matters without storing business data in Meet."""

    return _response(lambda: _matters(request))
