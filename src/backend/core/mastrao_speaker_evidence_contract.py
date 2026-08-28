"""Strict signed contract for Mastrao speaker evidence capture."""

import time

from django.conf import settings

from core.mastrao_recording_contract import (
    EFFECT_BINDING_FIELDS,
    MAX_ASSERTION_SECONDS,
    RecordingContractRefused,
    _sign,
    _validate_common,
    _validate_ref,
    _validate_time,
    _verify,
)
from core.mastrao_room_contract import CONTRACT_VERSION, DIGEST, _sha256_canonical

PURPOSE = "meeting_speaker_evidence"
SCOPE = "recording_roster_vad_timeline"
CAPTURE_EFFECT_TYPE = "mastrao.core-meeting-speaker-evidence-capture-effect"
CAPTURE_EFFECT_JOSE_TYPE = "mastrao-meeting-speaker-evidence-capture-effect+jws"
CAPTURE_RECEIPT_TYPE = "mastrao.meeting-speaker-evidence-capture-receipt"
CAPTURE_RECEIPT_JOSE_TYPE = "mastrao-meeting-speaker-evidence-capture-receipt+jws"

CAPTURE_EFFECT_FIELDS = {
    "version",
    "type",
    "issuer",
    "audience",
    "operation",
    "operation_version",
    "organization_external_id",
    "meeting_ref",
    "room_ref",
    "recording_ref",
    "evidence_ref",
    "provider_binding_digest",
    "policy_ref",
    "notice_version",
    "notice_digest",
    "purpose",
    "scope",
    "retention_expires_at",
    "effect_key",
    "arguments_digest",
    "recording_started_at_ms",
    "issued_at",
    "expires_at",
    "jti",
}


def _effect_arguments(effect):
    return {
        "version": CONTRACT_VERSION,
        "operation": "capture_meeting_speaker_evidence",
        "operation_version": 1,
        "meeting_ref": effect["meeting_ref"],
        "room_ref": effect["room_ref"],
        "recording_ref": effect["recording_ref"],
        "evidence_ref": effect["evidence_ref"],
        "provider_binding_digest": effect["provider_binding_digest"],
        "purpose": PURPOSE,
        "scope": SCOPE,
    }


def verify_speaker_evidence_capture_effect(compact_jws):
    """Verify and return one exact speaker-evidence capture effect."""

    effect = _verify(compact_jws, CAPTURE_EFFECT_JOSE_TYPE, CAPTURE_EFFECT_FIELDS)
    _validate_time(effect)
    _validate_common(effect)
    if (
        effect.get("type") != CAPTURE_EFFECT_TYPE
        or effect.get("operation") != "capture_meeting_speaker_evidence"
        or effect.get("purpose") != PURPOSE
        or effect.get("scope") != SCOPE
    ):
        raise RecordingContractRefused()
    for name in ("evidence_ref", "policy_ref", "notice_version"):
        _validate_ref(effect, name)
    if not DIGEST.fullmatch(effect.get("notice_digest", "")):
        raise RecordingContractRefused()
    retention = effect.get("retention_expires_at")
    recording_started = effect.get("recording_started_at_ms")
    if (
        not isinstance(retention, int)
        or isinstance(retention, bool)
        or retention <= int(time.time())
        or not isinstance(recording_started, int)
        or isinstance(recording_started, bool)
        or recording_started < 0
    ):
        raise RecordingContractRefused()
    if effect["arguments_digest"] != _sha256_canonical(_effect_arguments(effect)):
        raise RecordingContractRefused()
    return effect


def build_capture_receipt_claims(effect, status):
    """Build strict capture receipt claims that mirror the Core effect."""

    if status not in {"accepted", "already_active"}:
        raise RecordingContractRefused()
    now = int(time.time())
    return {
        "version": CONTRACT_VERSION,
        "type": CAPTURE_RECEIPT_TYPE,
        "issuer": settings.MASTRAO_RECORDING_RECEIPT_ISSUER,
        "audience": settings.MASTRAO_RECORDING_RECEIPT_AUDIENCE,
        "operation": "confirm_meeting_speaker_evidence_capture",
        "operation_version": 1,
        "organization_external_id": effect["organization_external_id"],
        "meeting_ref": effect["meeting_ref"],
        "room_ref": effect["room_ref"],
        "recording_ref": effect["recording_ref"],
        "evidence_ref": effect["evidence_ref"],
        "provider_binding_digest": effect["provider_binding_digest"],
        "policy_ref": effect["policy_ref"],
        "notice_version": effect["notice_version"],
        "notice_digest": effect["notice_digest"],
        "purpose": effect["purpose"],
        "scope": effect["scope"],
        "retention_expires_at": effect["retention_expires_at"],
        **{name: effect[name] for name in EFFECT_BINDING_FIELDS},
        "status": status,
        "issued_at": now,
        "expires_at": now + MAX_ASSERTION_SECONDS,
        "jti": effect["jti"],
    }


def sign_capture_receipt(claims):
    """Sign one speaker-evidence capture receipt."""

    return _sign(claims, CAPTURE_RECEIPT_JOSE_TYPE)
