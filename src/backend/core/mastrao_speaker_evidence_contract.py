"""Strict signed contract for Mastrao speaker evidence capture."""

import re
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
MAX_ARTIFACT_BYTES = 5_000_000
MAX_PARTICIPANTS = 500
MAX_EVENTS = 200_000
OBJECT_REF = re.compile(r"^mastrao-speaker-evidence/evidence_[a-f0-9]{32}[.]json$")
STORAGE_REFERENCE = re.compile(r"^[A-Za-z0-9_.:-]{1,160}$")
CAPTURE_EFFECT_TYPE = "mastrao.core-meeting-speaker-evidence-capture-effect"
CAPTURE_EFFECT_JOSE_TYPE = "mastrao-meeting-speaker-evidence-capture-effect+jws"
CAPTURE_RECEIPT_TYPE = "mastrao.meeting-speaker-evidence-capture-receipt"
CAPTURE_RECEIPT_JOSE_TYPE = "mastrao-meeting-speaker-evidence-capture-receipt+jws"
ARTIFACT_RECEIPT_TYPE = "mastrao.meeting-speaker-evidence-artifact-receipt"
ARTIFACT_RECEIPT_JOSE_TYPE = "mastrao-meeting-speaker-evidence-artifact-receipt+jws"

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
ARTIFACT_RECEIPT_FIELDS = {
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
    "artifact_ref",
    "object_ref",
    "byte_size",
    "checksum_digest",
    "participant_count",
    "event_count",
    "timeline_started_at_ms",
    "timeline_ended_at_ms",
    "region_ref",
    "encryption_ref",
    "lifecycle_policy_ref",
    "issued_at",
    "expires_at",
    "jti",
}


def _validate_receipt_common(claims):
    if (
        claims.get("version") != CONTRACT_VERSION
        or claims.get("type") != ARTIFACT_RECEIPT_TYPE
        or claims.get("issuer") != settings.MASTRAO_RECORDING_RECEIPT_ISSUER
        or claims.get("audience") != settings.MASTRAO_RECORDING_RECEIPT_AUDIENCE
        or claims.get("operation") != "confirm_meeting_speaker_evidence_artifact"
        or claims.get("operation_version") != 1
        or claims.get("purpose") != PURPOSE
        or claims.get("scope") != SCOPE
    ):
        raise RecordingContractRefused()
    organization = claims.get("organization_external_id")
    if (
        not isinstance(organization, str)
        or not 1 <= len(organization) <= 200
        or any(
            character
            not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._:-"
            for character in organization
        )
    ):
        raise RecordingContractRefused()


def _validate_positive_int(claims, name, *, maximum=None, allow_zero=False):
    value = claims.get(name)
    minimum = 0 if allow_zero else 1
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value < minimum
        or (maximum is not None and value > maximum)
    ):
        raise RecordingContractRefused()


def _validate_storage_reference(claims, name):
    value = claims.get(name)
    if not isinstance(value, str) or not STORAGE_REFERENCE.fullmatch(value):
        raise RecordingContractRefused()


def _validate_artifact_receipt_time(claims, *, allow_expired):
    issued_at = claims.get("issued_at")
    expires_at = claims.get("expires_at")
    now = int(time.time())
    if (
        not isinstance(issued_at, int)
        or isinstance(issued_at, bool)
        or not isinstance(expires_at, int)
        or isinstance(expires_at, bool)
        or issued_at > now
        or not 1 <= expires_at - issued_at <= MAX_ASSERTION_SECONDS
        or (not allow_expired and expires_at <= now)
    ):
        raise RecordingContractRefused()


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


def refresh_artifact_receipt_claims(claims):
    """Refresh replay-safe timing fields for one exact artifact receipt."""

    validate_artifact_receipt_claims(claims, allow_expired=True)
    now = int(time.time())
    refreshed = dict(claims)
    refreshed.update(
        issued_at=now,
        expires_at=now + MAX_ASSERTION_SECONDS,
        jti=f"speakerartifact_{_sha256_canonical([claims, now])[:32]}",
    )
    validate_artifact_receipt_claims(refreshed)
    return refreshed


def validate_artifact_receipt_claims(claims, *, allow_expired=False):
    """Validate one exact speaker-evidence artifact receipt claim set."""

    if not isinstance(claims, dict) or set(claims) != ARTIFACT_RECEIPT_FIELDS:
        raise RecordingContractRefused()
    _validate_receipt_common(claims)
    for name in (
        "meeting_ref",
        "recording_ref",
        "evidence_ref",
        "policy_ref",
        "notice_version",
        "artifact_ref",
    ):
        _validate_ref(claims, name)
    for name in ("region_ref", "encryption_ref", "lifecycle_policy_ref"):
        _validate_storage_reference(claims, name)
    _validate_ref(claims, "room_ref", max_length=100)
    if not OBJECT_REF.fullmatch(claims.get("object_ref", "")):
        raise RecordingContractRefused()
    for name in ("provider_binding_digest", "notice_digest", "checksum_digest"):
        if not DIGEST.fullmatch(claims.get(name, "")):
            raise RecordingContractRefused()
    _validate_positive_int(claims, "retention_expires_at")
    _validate_positive_int(claims, "byte_size", maximum=MAX_ARTIFACT_BYTES)
    _validate_positive_int(
        claims, "participant_count", maximum=MAX_PARTICIPANTS, allow_zero=True
    )
    _validate_positive_int(claims, "event_count", maximum=MAX_EVENTS, allow_zero=True)
    _validate_positive_int(
        claims, "timeline_started_at_ms", maximum=None, allow_zero=True
    )
    _validate_positive_int(
        claims, "timeline_ended_at_ms", maximum=None, allow_zero=True
    )
    if claims["timeline_ended_at_ms"] < claims["timeline_started_at_ms"]:
        raise RecordingContractRefused()
    if not isinstance(claims.get("jti"), str) or not claims["jti"].startswith(
        "speakerartifact_"
    ):
        raise RecordingContractRefused()
    _validate_artifact_receipt_time(claims, allow_expired=allow_expired)
    return claims


def sign_artifact_receipt(claims):
    """Sign one speaker-evidence artifact receipt."""

    return _sign(claims, ARTIFACT_RECEIPT_JOSE_TYPE)
