"""OIDC-only bridge for Core's private, grounded meeting assistant."""

import json
import re
from urllib.parse import urlencode

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_http_methods, require_POST

from core.mastrao_platform_facade import (
    MEETING_REF,
    PlatformFacadeError,
    meeting_path,
    request_platform,
)

MAX_COMMAND_BYTES = 12_000
# Platform bounds its Core assistant calls to 30 s, after OIDC userinfo (5 s).
ASSISTANT_TIMEOUT_SECONDS = (5, 40)
MAX_ASSISTANT_RESPONSE_BYTES = 480_000
PUBLIC_ERROR_STATUSES = {401, 403, 404, 409, 422, 429}
MAX_SAFE_INTEGER = 9_007_199_254_740_991
OPERATIONS = ("question", "summary", "actions", "email_draft")
SOURCE_KINDS = ("provider_run", "reviewed_revision", "native_epoch", "published_brief")
TURN_STATES = (
    "processing",
    "completed",
    "refused",
    "failed",
    "unknown",
    "sources_unavailable",
)
SEGMENT_STATUSES = (
    "transcript",
    "published_summary",
    "published_proposal",
    "confirmed_decision",
    "confirmed_action",
    "confirmed_deadline",
)
READ_FIELDS = {"version", "meeting_ref", "command_id"}
CITATION_FIELDS = {
    "citation_id",
    "meeting_ref",
    "source_kind",
    "source_ref",
    "source_version",
    "source_digest",
    "segment_id",
    "start_ms",
    "end_ms",
    "time_basis",
}
DIGEST = re.compile(r"^[a-f0-9]{64}$")
SEGMENT_ID = re.compile(r"^[A-Za-z0-9_-]{1,160}$")


def _fields(value, required, optional=()):
    return (
        isinstance(value, dict)
        and required <= set(value)
        and not set(value) - required - set(optional)
    )


def _integer(value, minimum=0):
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and minimum <= value <= MAX_SAFE_INTEGER
    )


def _version(value):
    return _integer(value) and value == 1


def _text(value, maximum, minimum=1):
    if not isinstance(value, str):
        return False
    try:
        value.encode("utf-8")
        # Zod string bounds count UTF-16 code units, including surrogate pairs.
        length = len(value.encode("utf-16-le")) // 2
    except UnicodeEncodeError:
        return False
    return minimum <= length <= maximum


def _reference(value):
    return isinstance(value, str) and MEETING_REF.fullmatch(value) is not None


def _valid_citation(value, meeting_ref):
    if not _fields(value, CITATION_FIELDS, {"epoch_ref"}):
        return False
    if (
        value["meeting_ref"] != meeting_ref
        or not _reference(value["citation_id"])
        or not _reference(value["source_ref"])
        or value["source_kind"] not in SOURCE_KINDS
    ):
        return False
    if (
        not _integer(value["source_version"])
        or not _text(value["source_digest"], 64)
        or not DIGEST.fullmatch(value["source_digest"])
        or not _text(value["segment_id"], 160)
        or not SEGMENT_ID.fullmatch(value["segment_id"])
    ):
        return False
    if (
        not _integer(value["start_ms"])
        or not _integer(value["end_ms"])
        or value["end_ms"] < value["start_ms"]
        or value["time_basis"] not in ("meeting_relative_ms", "source_relative_ms")
    ):
        return False
    return "epoch_ref" not in value or _reference(value["epoch_ref"])


def _unique_fields(pairs):
    value = {}
    for field, item in pairs:
        if field in value:
            raise ValueError("Duplicate JSON field")
        value[field] = item
    return value


def _read_command(request, meeting_ref, fields):
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
    if (
        not _fields(value, fields)
        or not _version(value["version"])
        or value["meeting_ref"] != meeting_ref
        or not _reference(value["command_id"])
    ):
        raise PlatformFacadeError(status=422)
    return value


def _send_command(request, meeting_ref):
    value = _read_command(
        request,
        meeting_ref,
        READ_FIELDS | {"operation", "question", "expected_thread_version"},
    )
    question = value["question"]
    if isinstance(question, str):
        question = question.strip()
    if (
        not isinstance(value["operation"], str)
        or value["operation"] not in OPERATIONS
        or not _integer(value["expected_thread_version"])
        or not _text(question, 1024)
    ):
        raise PlatformFacadeError(status=422)
    if not question or len(question.encode("utf-8")) > 4096:
        raise PlatformFacadeError(status=422)
    value["question"] = question
    return value


def _delete_command(request, meeting_ref):
    value = _read_command(
        request, meeting_ref, READ_FIELDS | {"expected_thread_version"}
    )
    if not _integer(value["expected_thread_version"]):
        raise PlatformFacadeError(status=422)
    return value


def _citation_command(request, meeting_ref):
    value = _read_command(request, meeting_ref, READ_FIELDS | {"citation"})
    if not _valid_citation(value["citation"], meeting_ref):
        raise PlatformFacadeError(status=422)
    return value


def _valid_coverage(value):
    if not _fields(
        value,
        {
            "state",
            "source_coverage",
            "selection",
            "total_segments",
            "selected_segments",
            "omitted_segments",
        },
    ):
        return False
    if (
        value["state"] not in ("full", "partial", "absent")
        or value["source_coverage"]
        not in ("reference_transcript", "partial_sources", "published_only", "absent")
        or value["selection"]
        not in ("complete", "relevant_passages", "distributed_passages", "none")
    ):
        return False
    if not all(
        _integer(value[field])
        for field in ("total_segments", "selected_segments", "omitted_segments")
    ):
        return False
    return (
        value["total_segments"]
        == value["selected_segments"] + value["omitted_segments"]
    )


def _valid_action(value):
    if not _fields(value, {"statement", "owner", "due_date", "citation_ids"}):
        return False
    if (
        not _text(value["statement"], 2000)
        or (value["owner"] is not None and not _text(value["owner"], 160))
        or (value["due_date"] is not None and not _text(value["due_date"], 160))
    ):
        return False
    citations = value["citation_ids"]
    return (
        isinstance(citations, list)
        and 1 <= len(citations) <= 16
        and all(_reference(item) for item in citations)
    )


def _valid_result(value, meeting_ref):
    if not _fields(
        value,
        {
            "state",
            "text",
            "rationale_category",
            "citations",
            "actions",
            "email_draft",
            "publication_state",
        },
    ):
        return False
    if (
        value["state"] not in ("answered", "abstained", "refused")
        or value["rationale_category"]
        not in ("supported", "insufficient", "contradictory", "policy")
        or value["publication_state"] != "private_proposal"
        or not _text(value["text"], 8000)
    ):
        return False
    citations = value["citations"]
    actions = value["actions"]
    if (
        not isinstance(citations, list)
        or len(citations) > 32
        or not all(_valid_citation(item, meeting_ref) for item in citations)
        or not isinstance(actions, list)
        or len(actions) > 20
    ):
        return False
    if not all(_valid_action(item) for item in actions):
        return False
    citation_ids = {item["citation_id"] for item in citations}
    if len(citation_ids) != len(citations) or any(
        set(item["citation_ids"]) - citation_ids for item in actions
    ):
        return False
    return _valid_email_draft(value["email_draft"])


def _valid_email_draft(value):
    if value is None:
        return True
    return (
        _fields(value, {"subject", "body"})
        and _text(value["subject"], 300)
        and _text(value["body"], 8000)
    )


def _valid_turn(value, meeting_ref):
    if not _fields(
        value,
        {
            "command_id",
            "operation",
            "question",
            "state",
            "result",
            "coverage",
            "created_at",
        },
    ):
        return False
    if (
        not _reference(value["command_id"])
        or value["operation"] not in OPERATIONS
        or value["state"] not in TURN_STATES
        or not _integer(value["created_at"], minimum=1)
    ):
        return False
    if value["question"] is not None and not _text(value["question"], 1024, minimum=0):
        return False
    if value["result"] is not None and not _valid_result(value["result"], meeting_ref):
        return False
    return value["coverage"] is None or _valid_coverage(value["coverage"])


def _validate_thread(value, meeting_ref):
    if not _fields(
        value,
        {
            "version",
            "meeting_ref",
            "thread_ref",
            "thread_version",
            "visibility",
            "deleted",
            "turns",
            "has_more",
            "next_cursor",
        },
    ):
        raise PlatformFacadeError()
    if (
        not _version(value["version"])
        or value["meeting_ref"] != meeting_ref
        or not _integer(value["thread_version"])
        or value["visibility"] != "private"
        or not isinstance(value["deleted"], bool)
    ):
        raise PlatformFacadeError()
    if value["thread_ref"] is not None and not _reference(value["thread_ref"]):
        raise PlatformFacadeError()
    if (
        not isinstance(value["turns"], list)
        or len(value["turns"]) > 20
        or not all(_valid_turn(item, meeting_ref) for item in value["turns"])
    ):
        raise PlatformFacadeError()
    if not isinstance(value["has_more"], bool) or (
        value["next_cursor"] is not None and not _reference(value["next_cursor"])
    ):
        raise PlatformFacadeError()


def _validate_sent(value, meeting_ref, command):
    if not _fields(value, {"version", "thread", "turn", "idempotent_replay"}):
        raise PlatformFacadeError()
    if not _version(value["version"]) or not isinstance(
        value["idempotent_replay"], bool
    ):
        raise PlatformFacadeError()
    _validate_thread(value["thread"], meeting_ref)
    turn = value["turn"]
    if (
        not _valid_turn(turn, meeting_ref)
        or turn["command_id"] != command["command_id"]
        or turn["operation"] != command["operation"]
    ):
        raise PlatformFacadeError()


def _validate_deleted(value):
    if (
        not _fields(value, {"version", "deleted", "thread_version"})
        or not _version(value["version"])
        or value["deleted"] is not True
        or not _integer(value["thread_version"])
    ):
        raise PlatformFacadeError()


def _validate_opened(value, meeting_ref, citation):
    if not _fields(value, {"version", "segment"}) or not _version(value["version"]):
        raise PlatformFacadeError()
    segment = value["segment"]
    if not _fields(segment, {"citation", "text", "speaker", "status"}):
        raise PlatformFacadeError()
    if (
        not _valid_citation(segment["citation"], meeting_ref)
        or segment["citation"] != citation
        or not _text(segment["text"], 8000)
        or segment["status"] not in SEGMENT_STATUSES
    ):
        raise PlatformFacadeError()
    if segment["speaker"] is not None and not _text(segment["speaker"], 160, minimum=0):
        raise PlatformFacadeError()


def _response(operation):
    try:
        value, status = operation()
    except PlatformFacadeError as error:
        value = {"message": "Assistant de réunion indisponible."}
        status = error.status
    return JsonResponse(
        value,
        status=status,
        headers={
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


def _history_query(request):
    fields = {"before_command_id", "limit"}
    if set(request.GET) - fields or any(
        len(request.GET.getlist(field)) != 1 for field in request.GET
    ):
        raise PlatformFacadeError(status=422)
    query = request.GET.dict()
    if "before_command_id" in query and not _reference(query["before_command_id"]):
        raise PlatformFacadeError(status=422)
    limit = query.get("limit")
    if limit is not None and (
        not re.fullmatch(r"[0-9]{1,2}", limit) or not 1 <= int(limit) <= 20
    ):
        raise PlatformFacadeError(status=422)
    return urlencode(query)


def _request_assistant(request, path, command=None):
    options = {
        "timeout": ASSISTANT_TIMEOUT_SECONDS,
        "max_response_bytes": MAX_ASSISTANT_RESPONSE_BYTES,
        "error_statuses": PUBLIC_ERROR_STATUSES,
    }
    if command is not None:
        options["json"] = command
    return request_platform(
        request, request.method, path, accepted_statuses={200}, options=options
    )


def _assistant(request, meeting_ref):
    path = meeting_path(meeting_ref, "assistant")
    match request.method:
        case "GET":
            query = _history_query(request)
            if query:
                path = f"{path}?{query}"
            body, status = _request_assistant(request, path)
            _validate_thread(body, meeting_ref)
        case "POST":
            command = _send_command(request, meeting_ref)
            body, status = _request_assistant(request, path, command)
            _validate_sent(body, meeting_ref, command)
        case "DELETE":
            command = _delete_command(request, meeting_ref)
            body, status = _request_assistant(request, path, command)
            _validate_deleted(body)
        case _:
            raise PlatformFacadeError(status=405)
    return body, status


@csrf_protect
@require_http_methods(["GET", "POST", "DELETE"])
def meeting_assistant(request, meeting_ref):
    """Read, send or delete the current Core author's private meeting thread."""

    return _response(lambda: _assistant(request, meeting_ref))


def _open_citation(request, meeting_ref):
    path = meeting_path(meeting_ref, "assistant/citation")
    command = _citation_command(request, meeting_ref)
    body, status = _request_assistant(request, path, command)
    _validate_opened(body, meeting_ref, command["citation"])
    return body, status


@csrf_protect
@require_POST
def meeting_assistant_citation(request, meeting_ref):
    """Resolve a cited passage through Core's current source authorization."""

    return _response(lambda: _open_citation(request, meeting_ref))
