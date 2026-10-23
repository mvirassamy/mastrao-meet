"""OIDC-only bridge for private, explicitly approved note enrichment."""

import json

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_http_methods

from core.mastrao_meeting_assistant import (
    _fields,
    _integer,
    _reference,
    _text,
    _unique_fields,
    _valid_citation,
    _valid_coverage,
)
from core.mastrao_personal_notes import (
    PersonalNoteRefused,
    _history_guest_participant,
    _history_host_participant,
)
from core.mastrao_platform_facade import (
    PlatformFacadeError,
    meeting_path,
    request_platform,
)

MAX_COMMAND_BYTES = 60_000
MAX_RESPONSE_BYTES = 480_000
TIMEOUT_SECONDS = (5, 40)
COMMAND_FIELDS = {"version", "meeting_ref", "command_id", "action"}
STATE_FIELDS = {
    "version",
    "meeting_ref",
    "state_version",
    "eligible",
    "reason",
    "proposal",
    "last_applied",
    "receipt",
}


def _command(request, meeting_ref):
    declared = request.META.get("CONTENT_LENGTH")
    if declared and (
        len(declared) > 20
        or not declared.isdecimal()
        or int(declared) > MAX_COMMAND_BYTES
    ):
        raise PlatformFacadeError(status=422)
    if request.GET or request.content_type != "application/json":
        raise PlatformFacadeError(status=422)
    raw = request.read(MAX_COMMAND_BYTES + 1)
    if len(raw) > MAX_COMMAND_BYTES:
        raise PlatformFacadeError(status=422)
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_fields)
    except (UnicodeError, ValueError, RecursionError) as error:
        raise PlatformFacadeError(status=422) from error
    if not isinstance(value, dict):
        raise PlatformFacadeError(status=422)
    return _validate_command(value, meeting_ref)


def _validate_command(value, meeting_ref):
    action = value.get("action")
    fields = COMMAND_FIELDS | {"expected_state_version"}
    match action:
        case "generate":
            fields |= {"expected_revision"}
        case "apply":
            fields |= {"expected_revision", "proposal_id", "additions"}
        case "undo":
            fields |= {"expected_revision", "proposal_id"}
        case "discard":
            fields |= {"proposal_id"}
        case _:
            raise PlatformFacadeError(status=422)
    if not _fields(value, fields):
        raise PlatformFacadeError(status=422)
    if (
        not _integer(value["version"])
        or value["version"] != 1
        or value["meeting_ref"] != meeting_ref
    ):
        raise PlatformFacadeError(status=422)
    if not _reference(value["command_id"]) or not _integer(
        value["expected_state_version"]
    ):
        raise PlatformFacadeError(status=422)
    if "expected_revision" in value and not _integer(value["expected_revision"]):
        raise PlatformFacadeError(status=422)
    if "proposal_id" in value and not _reference(value["proposal_id"]):
        raise PlatformFacadeError(status=422)
    if action == "apply" and (
        not _text(value["additions"], 8000)
        or not value["additions"].strip()
        or "\x00" in value["additions"]
    ):
        raise PlatformFacadeError(status=422)
    return value


def _digest(value):
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _valid_evidence(value, meeting_ref):
    if not _fields(value, {"citation", "text", "speaker", "status"}):
        return False
    return (
        _valid_citation(value["citation"], meeting_ref)
        and value["citation"]["source_kind"] != "published_brief"
        and _text(value["text"], 8000)
        and value["status"] == "transcript"
        and (value["speaker"] is None or _text(value["speaker"], 160, minimum=0))
    )


def _valid_proposal(value, meeting_ref):
    if not _fields(
        value,
        {
            "id",
            "command_id",
            "status",
            "base_revision",
            "base_digest",
            "text",
            "evidence",
            "coverage",
        },
    ):
        return False
    return (
        _reference(value["id"])
        and _reference(value["command_id"])
        and value["status"]
        in ("generating", "ready", "empty", "failed", "unknown", "stale")
        and _integer(value["base_revision"])
        and _digest(value["base_digest"])
        and _text(value["text"], 8000, minimum=0)
        and isinstance(value["evidence"], list)
        and len(value["evidence"]) <= 32
        and all(_valid_evidence(item, meeting_ref) for item in value["evidence"])
        and (value["coverage"] is None or _valid_coverage(value["coverage"]))
    )


def _valid_applied(value):
    return _fields(value, {"proposal_id", "revision", "digest", "undo_available"}) and (
        _reference(value["proposal_id"])
        and _integer(value["revision"])
        and _digest(value["digest"])
        and isinstance(value["undo_available"], bool)
    )


def _valid_receipt(value):
    return _fields(
        value, {"command_id", "action", "revision", "digest", "idempotent_replay"}
    ) and (
        _reference(value["command_id"])
        and value["action"] in ("apply", "undo")
        and _integer(value["revision"])
        and _digest(value["digest"])
        and isinstance(value["idempotent_replay"], bool)
    )


def _validate_state(value, meeting_ref):
    if not _fields(value, STATE_FIELDS):
        raise PlatformFacadeError()
    if (
        not _integer(value["version"])
        or value["version"] != 1
        or value["meeting_ref"] != meeting_ref
    ):
        raise PlatformFacadeError()
    if (
        not _integer(value["state_version"])
        or not isinstance(value["eligible"], bool)
        or value["reason"]
        not in (
            None,
            "empty_note",
            "meeting_active",
            "sources_unavailable",
            "note_changed",
            "input_too_large",
            "operation_in_progress",
        )
    ):
        raise PlatformFacadeError()
    if value["proposal"] is not None and not _valid_proposal(
        value["proposal"], meeting_ref
    ):
        raise PlatformFacadeError()
    if value["last_applied"] is not None and not _valid_applied(value["last_applied"]):
        raise PlatformFacadeError()
    if value["receipt"] is not None and not _valid_receipt(value["receipt"]):
        raise PlatformFacadeError()


def _require_account_note_author(request, meeting_ref):
    """Match the author selected by history notes before using OIDC authority."""
    try:
        if _history_host_participant(request, meeting_ref) is not None:
            return
        if _history_guest_participant(request, meeting_ref) is not None:
            raise PlatformFacadeError(status=403)
    except PersonalNoteRefused as error:
        raise PlatformFacadeError(status=error.status) from error


def _enrichment(request, meeting_ref):
    if request.GET:
        raise PlatformFacadeError(status=422)
    options = {
        "timeout": TIMEOUT_SECONDS,
        "max_response_bytes": MAX_RESPONSE_BYTES,
        "error_statuses": {401, 403, 404, 409, 422, 429},
    }
    if request.method == "POST":
        options["json"] = _command(request, meeting_ref)
    _require_account_note_author(request, meeting_ref)
    value, status = request_platform(
        request,
        request.method,
        meeting_path(meeting_ref, "personal-note/enrichment"),
        accepted_statuses={200},
        options=options,
    )
    _validate_state(value, meeting_ref)
    return value, status


@csrf_protect
@require_http_methods(["GET", "POST"])
def personal_note_enrichment(request, meeting_ref):
    """No guest grant path and no private content in public diagnostics."""
    try:
        value, status = _enrichment(request, meeting_ref)
    except PlatformFacadeError as error:
        value = {"message": "Enrichissement des notes indisponible."}
        status = error.status
    return JsonResponse(
        value,
        status=status,
        headers={
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )
