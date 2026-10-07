"""Automatic native transcription policy authorization at prejoin."""

# Generated LiveKit protobuf members and fail-closed contract checks are intentional here.
# pylint: disable=too-many-boolean-expressions,unidiomatic-typecheck

import json
import time
from uuid import uuid4

from django.conf import settings

from core.mastrao_core_http import post_core_json
from core.mastrao_recording_contract import (
    RecordingContractRefused,
    _sign,
    compact_digest,
)
from core.mastrao_recording_session import _participant
from core.mastrao_room_contract import DIGEST, OPAQUE_REFERENCE

AUTHORIZATION_PATH = "/internal/v1/meetings/capture/native/authorize"
AUTHORIZATION_JOSE = "mastrao-native-policy-authorization-request+jws"
AUTHORIZATION_SESSION_KEY = "mastrao_native_policy_authorizations"
AUTHORIZATION_FIELDS = {
    "policy_ref",
    "policy_version",
    "policy_digest",
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


def _validate_authorization(result):
    authorization = result.get("authorization")
    if (
        set(result) != {"version", "authorization", "capture_authorized"}
        or type(result["version"]) is not int
        or result["version"] != 1
        or result["capture_authorized"] is not False
        or not isinstance(authorization, dict)
        or set(authorization) != AUTHORIZATION_FIELDS
        or any(
            not isinstance(authorization[field], str)
            or not OPAQUE_REFERENCE.fullmatch(authorization[field])
            for field in ("policy_ref", "policy_version")
        )
        or not isinstance(authorization["policy_digest"], str)
        or not DIGEST.fullmatch(authorization["policy_digest"])
        or authorization["purpose"] != "meeting_transcription_source_audio"
        or authorization["scope"] != "authorized_microphone_track_epoch"
        or type(authorization["retention_expires_at"]) is not int
        or authorization["retention_expires_at"] <= int(time.time())
    ):
        raise RecordingContractRefused(status=503)
    return result


def native_policy_required(recording_status):
    """Only the canonical native post-transcription mode needs a policy seal."""
    return bool(
        settings.MASTRAO_NATIVE_PREENTRY_ENABLED
        and recording_status
        and recording_status.get("mode") == "recorded"
        and recording_status.get("transcription_mode") == "transcribed"
    )


def _authorization_fingerprint(participant, recording_status):
    return compact_digest(
        json.dumps(
            {
                "grant_digest": compact_digest(participant["compact"]),
                "session_nonce_digest": participant["session_digest"],
                "participant_kind": participant["kind"],
                "participant_ref": participant["ref"],
                "recording_ref": recording_status.get("recording_ref"),
                "transcription_notice_version": recording_status.get(
                    "transcription_notice_version"
                ),
                "transcription_notice_digest": recording_status.get(
                    "transcription_notice_digest"
                ),
                "retention_expires_at": recording_status.get("retention_expires_at"),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )


def _cached_authorization(request, room_ref, fingerprint):
    entries = request.session.get(AUTHORIZATION_SESSION_KEY)
    if not isinstance(entries, dict):
        return None
    entry = entries.get(room_ref)
    if not isinstance(entry, dict) or entry.get("fingerprint") != fingerprint:
        return None
    result = entry.get("result")
    if not isinstance(result, dict):
        return None
    try:
        return _validate_authorization(result)
    except RecordingContractRefused:
        return None


def _remember_authorization(request, room_ref, fingerprint, result):
    current = request.session.get(AUTHORIZATION_SESSION_KEY)
    entries = dict(current) if isinstance(current, dict) else {}
    entries[room_ref] = {"fingerprint": fingerprint, "result": result}
    request.session[AUTHORIZATION_SESSION_KEY] = entries


def authorize_native_policy(request, room, recording_status):
    """Seal Core's current policy while the exact compact grant is available."""
    if not native_policy_required(recording_status):
        return None
    if not hasattr(room, "mastrao_binding"):
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
    fingerprint = _authorization_fingerprint(participant, recording_status)
    cached = _cached_authorization(request, binding.room_ref, fingerprint)
    if cached is not None:
        return cached
    assertion = {
        **native_envelope("mastrao.meet-native-policy-authorization-request"),
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
    }
    result = post_core_json(
        endpoint=settings.MASTRAO_CORE_NATIVE_AUTHORIZATION_ENDPOINT,
        expected_path=AUTHORIZATION_PATH,
        body={
            "request": _sign(assertion, AUTHORIZATION_JOSE),
            "participant_grant": participant["compact"],
        },
        timeout=settings.MASTRAO_CORE_RECORDING_TIMEOUT_SECONDS,
        refusal=RecordingContractRefused,
    )
    result = _validate_authorization(result)
    _remember_authorization(request, binding.room_ref, fingerprint, result)
    return result
