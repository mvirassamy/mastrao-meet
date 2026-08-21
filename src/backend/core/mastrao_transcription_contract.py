"""Strict signed contracts for the canonical Mastrao transcription boundary.

The transcription boundary reuses the recording JOSE material (same Core
effect trust anchor, same Meet receipt key) with distinct payload types so a
recording capability can never be replayed as a transcription capability.
"""

# Strict contract validation keeps all binding predicates visible in one place.
# pylint: disable=too-many-boolean-expressions

import hashlib
import time
from uuid import uuid4

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
SCOPE = "recording_artifact_audio_transcript"

DECISION_TYPE = "mastrao.meet-transcription-decision"
DECISION_JOSE_TYPE = "mastrao-meeting-transcription-decision+jws"
SUBMIT_EFFECT_TYPE = "mastrao.core-meeting-transcription-submit-effect"
SUBMIT_EFFECT_JOSE_TYPE = "mastrao-meeting-transcription-submit-effect+jws"
SUBMIT_RECEIPT_TYPE = "mastrao.meeting-transcription-submit-receipt"
SUBMIT_RECEIPT_JOSE_TYPE = "mastrao-meeting-transcription-submit-receipt+jws"
ARTIFACT_RECEIPT_TYPE = "mastrao.meeting-transcription-artifact-receipt"
ARTIFACT_RECEIPT_JOSE_TYPE = "mastrao-meeting-transcription-artifact-receipt+jws"
FAILURE_RECEIPT_TYPE = "mastrao.meeting-transcription-failure-receipt"
FAILURE_RECEIPT_JOSE_TYPE = "mastrao-meeting-transcription-failure-receipt+jws"

SUBMIT_EFFECT_FIELDS = {
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
    "provider_binding_digest",
    "effect_key",
    "arguments_digest",
    "policy_ref",
    "notice_version",
    "notice_digest",
    "purpose",
    "scope",
    "retention_expires_at",
    "recording_artifact_ref",
    "recording_checksum_digest",
    "resolve_only",
    "issued_at",
    "expires_at",
    "jti",
}


class TranscriptionContractRefused(RecordingContractRefused):
    """Opaque refusal for transcription effects and receipts."""


class TranscriptionPipelineFailed(TranscriptionContractRefused):
    """Terminal pipeline failure carrying the exact Core failure code."""

    def __init__(self, failure_code, status=503):
        super().__init__(status=status)
        self.failure_code = failure_code


def _submit_arguments(effect):
    return {
        "version": CONTRACT_VERSION,
        "operation": "submit",
        "transcription_ref": effect["transcription_ref"],
        "recording_ref": effect["recording_ref"],
        "meeting_ref": effect["meeting_ref"],
        "room_ref": effect["room_ref"],
        "provider_binding_digest": effect["provider_binding_digest"],
    }


def verify_transcription_submit_effect(compact_jws):
    """Verify the exact Core transcription submit effect contract."""

    effect = _verify(compact_jws, SUBMIT_EFFECT_JOSE_TYPE, SUBMIT_EFFECT_FIELDS)
    _validate_time(effect)
    if (
        effect.get("version") != CONTRACT_VERSION
        or effect.get("type") != SUBMIT_EFFECT_TYPE
        or effect.get("issuer") != settings.MASTRAO_RECORDING_EFFECT_ISSUER
        or effect.get("audience") != settings.MASTRAO_RECORDING_EFFECT_AUDIENCE
        or effect.get("operation") != "submit_meeting_transcription"
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
        "recording_artifact_ref",
        "effect_key",
        "policy_ref",
        "notice_version",
    ):
        _validate_ref(effect, name)
    _validate_ref(effect, "room_ref", max_length=100)
    for name in (
        "provider_binding_digest",
        "arguments_digest",
        "recording_checksum_digest",
        "notice_digest",
    ):
        if not DIGEST.fullmatch(effect.get(name, "")):
            raise TranscriptionContractRefused()
    retention = effect.get("retention_expires_at")
    if (
        not isinstance(retention, int)
        or isinstance(retention, bool)
        or retention <= int(time.time())
    ):
        raise TranscriptionContractRefused()
    if effect["arguments_digest"] != _sha256_canonical(_submit_arguments(effect)):
        raise TranscriptionContractRefused()
    return effect


def submit_provider_job_ref(effect):
    """Derive one stable bounded ASR job reference from the effect key."""

    digest = hashlib.sha256(effect["effect_key"].encode("ascii")).hexdigest()
    return f"asrjob_{digest[:40]}"


def build_submit_receipt_claims(effect, observation):
    """Build strict submit receipt claims mirroring the recording pattern."""

    if observation not in {"submitted", "already_submitted"}:
        raise TranscriptionContractRefused()
    now = int(time.time())
    return {
        "version": CONTRACT_VERSION,
        "type": SUBMIT_RECEIPT_TYPE,
        "issuer": settings.MASTRAO_RECORDING_RECEIPT_ISSUER,
        "audience": settings.MASTRAO_RECORDING_RECEIPT_AUDIENCE,
        "operation": "confirm_meeting_transcription_submitted",
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
        "provider_job_ref": submit_provider_job_ref(effect),
        "provider_observation": observation,
        "issued_at": now,
        "expires_at": now + MAX_ASSERTION_SECONDS,
        "jti": effect["jti"],
    }


def build_transcript_artifact_receipt_claims(effect, artifact):
    """Build strict persisted transcript artifact receipt claims."""

    now = int(time.time())
    return {
        "version": CONTRACT_VERSION,
        "type": ARTIFACT_RECEIPT_TYPE,
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
        "artifact_ref": artifact["transcript_artifact_ref"],
        "recording_artifact_ref": effect["recording_artifact_ref"],
        "storage_binding_digest": settings.MASTRAO_RECORDING_STORAGE_BINDING_DIGEST,
        "object_ref": artifact["object_ref"],
        "content_type": "application/json",
        "byte_size": artifact["byte_size"],
        "checksum_algorithm": "sha256",
        "checksum_digest": artifact["checksum_digest"],
        "segment_count": artifact["segment_count"],
        "region_ref": settings.MASTRAO_RECORDING_REGION_REF,
        "encryption_ref": settings.MASTRAO_RECORDING_ENCRYPTION_REF,
        "lifecycle_policy_ref": settings.MASTRAO_RECORDING_LIFECYCLE_POLICY_REF,
        "retention_expires_at": effect["retention_expires_at"],
        "verified_at": now,
        "issued_at": now,
        "expires_at": now + MAX_ASSERTION_SECONDS,
        "jti": f"transcript_artifact_{uuid4().hex}",
    }


def build_transcription_failure_receipt_claims(effect, failure_code):
    """Build strict pipeline-failure receipt claims for one exact effect."""

    if failure_code not in {"audio_extraction_failed", "asr_failed"}:
        raise TranscriptionContractRefused()
    now = int(time.time())
    return {
        "version": CONTRACT_VERSION,
        "type": FAILURE_RECEIPT_TYPE,
        "issuer": settings.MASTRAO_RECORDING_RECEIPT_ISSUER,
        "audience": settings.MASTRAO_RECORDING_RECEIPT_AUDIENCE,
        "operation": "confirm_meeting_transcription_failed",
        "operation_version": 1,
        "organization_external_id": effect["organization_external_id"],
        "meeting_ref": effect["meeting_ref"],
        "room_ref": effect["room_ref"],
        "recording_ref": effect["recording_ref"],
        "transcription_ref": effect["transcription_ref"],
        "provider_binding_digest": effect["provider_binding_digest"],
        "provider_job_ref": submit_provider_job_ref(effect),
        "failure_code": failure_code,
        "issued_at": now,
        "expires_at": now + MAX_ASSERTION_SECONDS,
        "jti": f"transcript_failure_{uuid4().hex}",
    }


def sign_submit_receipt(claims):
    """Sign one exact transcription submit receipt."""
    return _sign(claims, SUBMIT_RECEIPT_JOSE_TYPE)


def sign_transcript_artifact_receipt(claims):
    """Sign one exact transcript artifact receipt."""
    return _sign(claims, ARTIFACT_RECEIPT_JOSE_TYPE)


def sign_transcription_decision_assertion(payload):
    """Sign one participant transcription decision."""
    return _sign(payload, DECISION_JOSE_TYPE)


def sign_transcription_failure_receipt(claims):
    """Sign one exact transcription pipeline-failure receipt."""
    return _sign(claims, FAILURE_RECEIPT_JOSE_TYPE)
