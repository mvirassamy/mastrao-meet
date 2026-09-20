"""Session-bound native notice projection. Core remains the only decision owner."""

# Generated LiveKit protobuf members and fail-closed contract checks are intentional here.
# pylint: disable=too-many-boolean-expressions,unidiomatic-typecheck

import time
from uuid import uuid4

from django.conf import settings

from core.mastrao_core_http import post_core_json
from core.mastrao_recording_contract import (
    RecordingContractRefused,
    _sign,
    compact_digest,
)
from core.mastrao_recording_session import CAPTURE_STATES, _participant
from core.mastrao_room_contract import DIGEST, OPAQUE_REFERENCE, _sha256_canonical

NOTICE_PATH = "/internal/v1/meetings/capture/native/notice"
NOTICE_JOSE = "mastrao-native-preentry-notice-request+jws"
SNAPSHOT_FIELDS = {
    "policy_ref",
    "notice_version",
    "notice_digest",
    "purpose",
    "scope",
    "retention_expires_at",
}


def native_envelope(kind):
    """Fresh purpose-specific server assertion, never a browser signature."""
    now = int(time.time())
    return {
        "version": 1,
        "type": kind,
        "issuer": settings.MASTRAO_RECORDING_RECEIPT_ISSUER,
        "audience": settings.MASTRAO_RECORDING_RECEIPT_AUDIENCE,
        "issued_at": now,
        "expires_at": now + 30,
        "jti": "native_" + uuid4().hex,
    }


def _validate_projection(result):
    notice = result.get("notice")
    text = result.get("text")
    if (
        set(result) != {"version", "text", "notice", "decision", "capture_authorized"}
        or type(result["version"]) is not int
        or result["version"] != 1
        or result["capture_authorized"] is not False
        or not isinstance(text, str)
        or not 1 <= len(text.encode()) <= 4096
        or not isinstance(notice, dict)
        or set(notice) != SNAPSHOT_FIELDS
    ):
        raise RecordingContractRefused(status=503)
    if (
        any(
            not isinstance(notice[field], str)
            or not OPAQUE_REFERENCE.fullmatch(notice[field])
            for field in ("policy_ref", "notice_version")
        )
        or not isinstance(notice["notice_digest"], str)
        or not DIGEST.fullmatch(notice["notice_digest"])
        or _sha256_canonical({"text": text}) != notice["notice_digest"]
        or notice["purpose"] != "meeting_transcription_source_audio"
        or notice["scope"] != "consented_microphone_track_epoch"
        or type(notice["retention_expires_at"]) is not int
        or notice["retention_expires_at"] <= int(time.time())
    ):
        raise RecordingContractRefused(status=503)
    decision = result["decision"]
    if decision is not None and (
        not isinstance(decision, dict)
        or set(decision) != SNAPSHOT_FIELDS | {"decision", "decision_ref", "decided_at"}
        or any(decision[field] != notice[field] for field in SNAPSHOT_FIELDS)
        or decision["decision"] not in ("accepted", "refused")
        or not isinstance(decision["decision_ref"], str)
        or not OPAQUE_REFERENCE.fullmatch(decision["decision_ref"])
        or type(decision["decided_at"]) is not int
        or not 0 < decision["decided_at"] <= int(time.time())
    ):
        raise RecordingContractRefused(status=503)
    return result


def native_notice(request, room, action=None):
    """No client-supplied participant/grant/session/room authority is accepted."""
    if not settings.MASTRAO_NATIVE_PREENTRY_ENABLED or not hasattr(
        room, "mastrao_binding"
    ):
        raise RecordingContractRefused()
    participant = _participant(request, room)
    claims = participant["claims"]
    binding = room.mastrao_binding
    if (
        claims["room_ref"] != binding.room_ref
        or claims["meeting_ref"] != binding.meeting_ref
        or claims["provider_binding_digest"] != binding.provider_binding_digest
    ):
        raise RecordingContractRefused()
    assertion = {
        **native_envelope("mastrao.meet-native-preentry-notice-request"),
        **{
            field: claims[field]
            for field in (
                "organization_external_id",
                "meeting_ref",
                "room_ref",
                "provider_binding_digest",
                "grant_ref",
            )
        },
        "grant_digest": compact_digest(participant["compact"]),
        "session_nonce_digest": participant["session_digest"],
        "participant_kind": participant["kind"],
        "participant_ref": participant["ref"],
        "action": action or {"kind": "read"},
    }
    result = post_core_json(
        endpoint=settings.MASTRAO_CORE_NATIVE_NOTICE_ENDPOINT,
        expected_path=NOTICE_PATH,
        body={
            "request": _sign(assertion, NOTICE_JOSE),
            "participant_grant": participant["compact"],
        },
        timeout=settings.MASTRAO_CORE_RECORDING_TIMEOUT_SECONDS,
        refusal=RecordingContractRefused,
        passthrough_statuses={409},
    )
    return _validate_projection(result)


def native_notice_projection(request, room, recording_status):
    """Off means zero extra I/O; only the explicit native transcription mode offers a notice."""
    if (
        not settings.MASTRAO_NATIVE_PREENTRY_ENABLED
        or not recording_status
        or recording_status.get("mode") != "recorded"
        or recording_status.get("transcription_mode") != "transcribed"
        or recording_status.get("recording_state") not in CAPTURE_STATES
    ):
        return None
    return native_notice(request, room)


def native_media_allowed(projection):
    """A refusal forbids this native source, not previously consented legacy video."""
    return projection is None or projection["decision"] is not None
