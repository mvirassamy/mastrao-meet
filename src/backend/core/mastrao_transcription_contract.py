"""Strict signed contracts for the canonical Mastrao transcription boundary.

The transcription boundary reuses the recording JOSE material (same Core
effect trust anchor, same Meet receipt key) with distinct payload types so a
recording capability can never be replayed as a transcription capability.
"""

# Strict contract validation keeps all binding predicates visible in one place.
# pylint: disable=too-many-boolean-expressions

import time

from django.conf import settings

from core.mastrao_recording_contract import (
    MAX_ASSERTION_SECONDS,
    RecordingContractRefused,
    _sign,
    _validate_ref,
    _validate_time,
    _verify,
)
from core.mastrao_room_contract import (
    CONTRACT_VERSION,
    DIGEST,
    REQUEST_ID,
    _sha256_canonical,
)

PURPOSE = "meeting_transcription"
SCOPE = "recording_artifact_audio"

TRANSCRIBE_EFFECT_TYPE = "mastrao.core-meeting-transcription-effect"
TRANSCRIBE_EFFECT_JOSE_TYPE = "mastrao-meeting-transcription-effect+jws"
TRANSCRIPT_RECEIPT_TYPE = "mastrao.meeting-transcription-receipt"
TRANSCRIPT_RECEIPT_JOSE_TYPE = "mastrao-meeting-transcription-receipt+jws"

TRANSCRIBE_EFFECT_FIELDS = {
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
    "transcription_ref",
    "artifact_ref",
    "provider_binding_digest",
    "artifact_checksum_digest",
    "artifact_byte_size",
    "purpose",
    "scope",
    "effect_key",
    "arguments_digest",
    "resolve_only",
    "issued_at",
    "expires_at",
    "jti",
}


class TranscriptionContractRefused(RecordingContractRefused):
    """Opaque refusal for transcription effects and receipts."""


def _transcribe_arguments(effect):
    return {
        "version": CONTRACT_VERSION,
        "operation": "transcribe",
        "transcription_ref": effect["transcription_ref"],
        "recording_ref": effect["recording_ref"],
        "artifact_ref": effect["artifact_ref"],
        "meeting_ref": effect["meeting_ref"],
        "room_ref": effect["room_ref"],
        "provider_binding_digest": effect["provider_binding_digest"],
        "artifact_checksum_digest": effect["artifact_checksum_digest"],
    }


def verify_transcribe_effect(compact_jws):
    """Verify the exact Core transcribe effect contract."""

    effect = _verify(compact_jws, TRANSCRIBE_EFFECT_JOSE_TYPE, TRANSCRIBE_EFFECT_FIELDS)
    _validate_time(effect)
    if (
        effect.get("version") != CONTRACT_VERSION
        or effect.get("type") != TRANSCRIBE_EFFECT_TYPE
        or effect.get("issuer") != settings.MASTRAO_RECORDING_EFFECT_ISSUER
        or effect.get("audience") != settings.MASTRAO_RECORDING_EFFECT_AUDIENCE
        or effect.get("operation") != "transcribe_recording_artifact"
        or effect.get("operation_version") != 1
        or effect.get("purpose") != PURPOSE
        or effect.get("scope") != SCOPE
        or not isinstance(effect.get("resolve_only"), bool)
        or not REQUEST_ID.fullmatch(effect.get("jti", ""))
    ):
        raise TranscriptionContractRefused()
    organization = effect.get("organization_external_id")
    if (
        not isinstance(organization, str)
        or not 1 <= len(organization) <= 200
        or any(
            character
            not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._:-"
            for character in organization
        )
    ):
        raise TranscriptionContractRefused()
    for name in (
        "meeting_ref",
        "recording_ref",
        "transcription_ref",
        "artifact_ref",
        "effect_key",
    ):
        _validate_ref(effect, name)
    _validate_ref(effect, "room_ref", max_length=100)
    for name in (
        "provider_binding_digest",
        "arguments_digest",
        "artifact_checksum_digest",
    ):
        if not DIGEST.fullmatch(effect.get(name, "")):
            raise TranscriptionContractRefused()
    byte_size = effect.get("artifact_byte_size")
    if not isinstance(byte_size, int) or isinstance(byte_size, bool) or byte_size < 1:
        raise TranscriptionContractRefused()
    if effect["arguments_digest"] != _sha256_canonical(_transcribe_arguments(effect)):
        raise TranscriptionContractRefused()
    return effect


def build_transcript_receipt_claims(effect, artifact):
    """Build strict persisted transcript receipt claims."""

    now = int(time.time())
    return {
        "version": CONTRACT_VERSION,
        "type": TRANSCRIPT_RECEIPT_TYPE,
        "issuer": settings.MASTRAO_RECORDING_RECEIPT_ISSUER,
        "audience": settings.MASTRAO_RECORDING_RECEIPT_AUDIENCE,
        "operation": "confirm_meeting_transcription_artifact",
        "operation_version": 1,
        "organization_external_id": effect["organization_external_id"],
        "meeting_ref": effect["meeting_ref"],
        "room_ref": effect["room_ref"],
        "recording_ref": effect["recording_ref"],
        "transcription_ref": effect["transcription_ref"],
        "provider_binding_digest": effect["provider_binding_digest"],
        "effect_key": effect["effect_key"],
        "arguments_digest": effect["arguments_digest"],
        "status": "confirmed",
        "transcript_artifact_ref": artifact["transcript_artifact_ref"],
        "object_ref": artifact["object_ref"],
        "content_type": "application/json",
        "byte_size": artifact["byte_size"],
        "checksum_algorithm": "sha256",
        "checksum_digest": artifact["checksum_digest"],
        "segment_count": artifact["segment_count"],
        "engine_ref": artifact["engine_ref"],
        "issued_at": now,
        "expires_at": now + MAX_ASSERTION_SECONDS,
        "jti": effect["jti"],
    }


def sign_transcript_receipt(claims):
    """Sign one exact transcript receipt."""
    return _sign(claims, TRANSCRIPT_RECEIPT_JOSE_TYPE)
