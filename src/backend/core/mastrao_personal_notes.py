"""Private personal notes bridge; Cabinet Core owns content and authorization."""

import hashlib
import json

from django.conf import settings
from django.http import JsonResponse
from django.middleware.csrf import get_token
from django.utils import timezone
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_http_methods

from core import models
from core.mastrao_core_http import post_core_json
from core.mastrao_guest_contract import GuestHandoffRefused, verify_guest_bootstrap
from core.mastrao_guest_grant import (
    SESSION_GRANT_REF_KEY,
    SESSION_NONCE_KEY,
    active_guest_compact_grant,
    active_guest_lifecycle_grant_for_room_ref,
)
from core.mastrao_host_contract import (
    HostHandoffRefused,
    compact_digest,
    verify_host_grant,
)
from core.mastrao_host_grant import (
    SESSION_COMPACT_GRANTS_KEY,
    active_host_close_grant_for_room_ref,
    active_host_compact_grant,
)
from core.mastrao_platform_facade import (
    MEETING_REF,
    PlatformFacadeError,
    meeting_path,
    request_platform,
)

MAX_CONTENT_CHARACTERS = 100_000
# JSON escapes can use six bytes per character, plus the grant/write envelope.
MAX_WRITE_BODY_BYTES = 700_000
MAX_RESPONSE_BYTES = 700_000
MAX_SAFE_INTEGER = 9_007_199_254_740_991
NOTE_FIELDS = {"id", "content", "revision", "created_at", "updated_at"}
WRITE_FIELDS = {"content", "expected_revision"}


class PersonalNoteRefused(Exception):
    """Opaque boundary failure that never includes notes or credentials."""

    def __init__(self, status=404):
        self.status = status
        super().__init__("personal_note_unavailable")


def _safe_revision(value):
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and 0 <= value <= MAX_SAFE_INTEGER
    )


def _valid_content(value):
    if (
        not isinstance(value, str)
        or len(value) > MAX_CONTENT_CHARACTERS
        or "\x00" in value
    ):
        return False
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _unique_json_fields(pairs):
    body = {}
    for name, value in pairs:
        if name in body:
            raise ValueError("Duplicate JSON field")
        body[name] = value
    return body


def _write_body(request):
    declared = request.META.get("CONTENT_LENGTH")
    if declared and (not declared.isdecimal() or int(declared) > MAX_WRITE_BODY_BYTES):
        raise PersonalNoteRefused(status=422)
    if request.content_type != "application/json":
        raise PersonalNoteRefused(status=422)
    raw = request.read(MAX_WRITE_BODY_BYTES + 1)
    if len(raw) > MAX_WRITE_BODY_BYTES:
        raise PersonalNoteRefused(status=422)
    try:
        body = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_json_fields)
    except (UnicodeError, ValueError, RecursionError) as error:
        raise PersonalNoteRefused(status=422) from error
    if (
        not isinstance(body, dict)
        or set(body) != WRITE_FIELDS
        or not _valid_content(body["content"])
        or not _safe_revision(body["expected_revision"])
    ):
        raise PersonalNoteRefused(status=422)
    return body


def _verified_participant(request, grant, *, host=False):
    """Match a signed bearer to its exact session projection and room binding."""

    if grant is None:
        raise PersonalNoteRefused()
    if host:
        compact = active_host_compact_grant(request, grant)
        verifier = verify_host_grant
    else:
        compact = active_guest_compact_grant(request, grant)
        verifier = verify_guest_bootstrap
    try:
        if not compact or compact_digest(compact) != grant.grant_digest:
            raise PersonalNoteRefused()
        claims = verifier(compact)
    except (HostHandoffRefused, GuestHandoffRefused) as error:
        raise PersonalNoteRefused(status=error.status) from error
    binding = grant.room_binding
    expected = {
        "grant_ref": grant.grant_ref,
        "meeting_ref": binding.meeting_ref,
        "room_ref": binding.room_ref,
        "provider_binding_digest": binding.provider_binding_digest,
        "credential_digest": grant.credential_digest,
        "issued_at": int(grant.issued_at.timestamp()),
        "expires_at": int(grant.expires_at.timestamp()),
    }
    if host:
        expected["host_ref"] = grant.identity.host_ref
        expected["handoff_ref"] = grant.handoff_ref
        expected["platform_session_ref"] = grant.platform_session_ref
    else:
        expected["guest_ref"] = grant.guest_ref
        expected["organization_external_id"] = grant.organization_external_id
        expected["invitation_ref"] = grant.invitation_ref
        expected["redemption_id"] = grant.redemption_id
    if (
        grant.meeting_ref != binding.meeting_ref
        or grant.room_ref != binding.room_ref
        or grant.provider_binding_digest != binding.provider_binding_digest
        or any(claims.get(name) != value for name, value in expected.items())
    ):
        raise PersonalNoteRefused()
    return {
        "participant_grant": compact,
        "participant_session_digest": grant.session_nonce_digest,
    }


def _room_participant(request, room_ref):
    host = active_host_close_grant_for_room_ref(request, room_ref)
    if host is not None:
        return _verified_participant(request, host, host=True)
    guest = active_guest_lifecycle_grant_for_room_ref(request, room_ref)
    return _verified_participant(request, guest)


def _history_host_participant(request, meeting_ref):
    """Prefer a verified host bound to this session and this meeting."""

    compact_grants = request.session.get(SESSION_COMPACT_GRANTS_KEY, {})
    if not isinstance(compact_grants, dict) or not compact_grants:
        return None
    room_ref = (
        models.MastraoHostGrant.objects.filter(
            grant_ref__in=tuple(compact_grants), meeting_ref=meeting_ref
        )
        .order_by("-expires_at")
        .values_list("room_ref", flat=True)
        .first()
    )
    if room_ref is None:
        return None
    host = active_host_close_grant_for_room_ref(request, room_ref)
    if host is None:
        return None
    if host.meeting_ref != meeting_ref:
        raise PersonalNoteRefused()
    return _verified_participant(request, host, host=True)


def _history_guest_participant(request, meeting_ref):
    """Preserve a matching guest author, including in a signed-in browser."""

    grant_ref = request.session.get(SESSION_GRANT_REF_KEY)
    if not isinstance(grant_ref, str):
        return None
    grant = (
        models.MastraoGuestGrant.objects.select_related("room_binding")
        .filter(grant_ref=grant_ref, meeting_ref=meeting_ref)
        .first()
    )
    if grant is None:
        return None
    nonce = request.session.get(SESSION_NONCE_KEY)
    if (
        not isinstance(nonce, str)
        or len(nonce) < 32
        or hashlib.sha256(nonce.encode()).hexdigest() != grant.session_nonce_digest
        or grant.expires_at <= timezone.now()
    ):
        raise PersonalNoteRefused()
    return _verified_participant(request, grant)


def _validate_note(body):
    """Keep the public response flat and reject malformed upstream documents."""

    if (
        not isinstance(body, dict)
        or set(body) != NOTE_FIELDS
        or not _valid_content(body["content"])
        or not _safe_revision(body["revision"])
    ):
        raise PersonalNoteRefused(status=503)
    if body["id"] is None:
        if body != {
            "id": None,
            "content": "",
            "revision": 0,
            "created_at": None,
            "updated_at": None,
        }:
            raise PersonalNoteRefused(status=503)
        return body
    if (
        not isinstance(body["id"], str)
        or not 1 <= len(body["id"]) <= 160
        or body["revision"] == 0
        or any(body[name] is None for name in ("created_at", "updated_at"))
    ):
        raise PersonalNoteRefused(status=503)
    return body


def _core_note(request, participant, payload):
    operation = "read"
    endpoint = settings.MASTRAO_CORE_PERSONAL_NOTES_READ_ENDPOINT
    if request.method == "PUT":
        operation = "write"
        endpoint = settings.MASTRAO_CORE_PERSONAL_NOTES_WRITE_ENDPOINT
    return post_core_json(
        endpoint=endpoint,
        expected_path=f"/internal/v1/meetings/personal-notes/{operation}",
        body={**participant, **payload},
        timeout=settings.MASTRAO_CORE_PERSONAL_NOTES_TIMEOUT_SECONDS,
        refusal=PersonalNoteRefused,
        expected_fields=NOTE_FIELDS,
        passthrough_statuses={409, 422},
        maximum_response_bytes=MAX_RESPONSE_BYTES,
    )


def _respond(request, operation):
    try:
        payload = {}
        if request.method == "PUT":
            payload = _write_body(request)
        else:
            get_token(request)
        body = _validate_note(operation(payload))
        status = 200
    except (PersonalNoteRefused, PlatformFacadeError) as error:
        body = {"message": "Notes personnelles indisponibles"}
        status = error.status
    return JsonResponse(
        body,
        status=status,
        headers={"Cache-Control": "private, no-store"},
        json_dumps_params={"ensure_ascii": False},
    )


@csrf_protect
def _write_response(request, operation):
    return _respond(request, operation)


def _dispatch(request, operation):
    if request.method == "PUT":
        return _write_response(request, operation)
    return _respond(request, operation)


@require_http_methods(["GET", "PUT"])
def room_personal_note(request, room_ref):
    """Read or replace this exact browser participant's note, including after close."""

    return _dispatch(
        request,
        lambda payload: _core_note(
            request, _room_participant(request, room_ref), payload
        ),
    )


@require_http_methods(["GET", "PUT"])
def history_personal_note(request, meeting_ref):
    """Use Platform's account authority or the exact session guest after a meeting."""

    def operation(payload):
        if not MEETING_REF.fullmatch(meeting_ref):
            raise PersonalNoteRefused()
        participant = _history_host_participant(request, meeting_ref)
        if participant is not None:
            return _core_note(request, participant, payload)
        participant = _history_guest_participant(request, meeting_ref)
        if participant is not None:
            return _core_note(request, participant, payload)
        options = {"json": payload} if request.method == "PUT" else None
        body, _status = request_platform(
            request,
            request.method,
            meeting_path(meeting_ref, "personal-note"),
            accepted_statuses={200},
            options=options,
        )
        return body

    return _dispatch(request, operation)
