"""Strict signed contracts for the canonical Mastrao transcription boundary.

The transcription boundary reuses the recording JOSE material (same Core
effect trust anchor, same Meet receipt key) with distinct payload types so a
recording capability can never be replayed as a transcription capability.
"""

# Strict contract validation keeps all binding predicates visible in one place.
# pylint: disable=too-many-boolean-expressions

import hashlib
import re
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
EGRESS_REQUEST_TYPE = "mastrao.meet-transcription-egress-request"
EGRESS_REQUEST_JOSE_TYPE = "mastrao-meeting-transcription-egress-request+jws"
EGRESS_GRANT_TYPE = "mastrao.core-meeting-transcription-egress-grant"
EGRESS_GRANT_JOSE_TYPE = "mastrao-meeting-transcription-egress-grant+jws"
TERMINAL_RECEIPT_TYPE = "mastrao.meeting-transcription-terminal-receipt"
TERMINAL_RECEIPT_JOSE_TYPE = "mastrao-meeting-transcription-terminal-receipt+jws"

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
SUBMIT_EFFECT_V2_FIELDS = SUBMIT_EFFECT_FIELDS | {
    "asr_profile_ref",
    "asr_profile_digest",
    "asr_provider_ref",
    "requested_model_ref",
    "request_config_digest",
    "normalization_version",
    "processing_region_ref",
    "data_control_ref",
}
SUBMIT_EFFECT_V3_FIELDS = SUBMIT_EFFECT_V2_FIELDS | {
    "campaign_ref",
    "authorized_cost_ceiling_micros",
    "currency",
    "tariff_catalog_version",
}
ASR_REFERENCE = re.compile(r"^[A-Za-z0-9._:-]{1,160}$")
V2_NORMALIZATION_VERSION = "meeting-transcript-v1"
MAX_EGRESS_GRANT_SECONDS = 30


def _provider_free_profile(profile_ref, provider_ref, requested_model_ref):
    request_config_digest = _sha256_canonical(
        {
            "version": 1,
            "provider_ref": provider_ref,
            "requested_model_ref": requested_model_ref,
            "normalization_version": V2_NORMALIZATION_VERSION,
            "language": "fr",
            "diarize": False,
        }
    )
    profile = {
        "asr_profile_ref": profile_ref,
        "asr_provider_ref": provider_ref,
        "requested_model_ref": requested_model_ref,
        "request_config_digest": request_config_digest,
        "normalization_version": V2_NORMALIZATION_VERSION,
        "processing_region_ref": "qualification-local",
        "data_control_ref": "provider-free-no-egress-v1",
    }
    return {
        **profile,
        "asr_profile_digest": _sha256_canonical(
            {
                "version": 1,
                "profile_ref": profile_ref,
                "provider_ref": provider_ref,
                "requested_model_ref": requested_model_ref,
                "request_config_digest": request_config_digest,
                "normalization_version": V2_NORMALIZATION_VERSION,
                "processing_region_ref": "qualification-local",
                "data_control_ref": "provider-free-no-egress-v1",
            }
        ),
    }


V2_PROFILE_MANIFEST = {
    profile["asr_profile_ref"]: profile
    for profile in (
        _provider_free_profile(
            "mistral-voxtral-mini-2602-v1", "mistral", "voxtral-mini-2602"
        ),
        _provider_free_profile("openai-gpt-transcribe-v1", "openai", "gpt-transcribe"),
    )
}
MANAGED_PROFILE_BINDINGS = {
    "mistral-eu-zdr-voxtral-mini-2602-canary-v1": {
        "asr_provider_ref": "mistral",
        "requested_model_ref": "voxtral-mini-2602",
        "processing_region_ref": "mistral-eu",
        "data_control_ref": "mistral-zdr-approved-v1",
        "request_config_digest": "5e835721dbe255ce5624926927b0363f6529f41d8197750e5950942b5accca65",
        "asr_profile_digest": "bad19aa64434075c911fd078a1835aa4cc49bd3a85d5e4bc4526197a35ec9875",
        "tariff_catalog_version": "asr-tariff-v2",
    },
    "openai-eu-zdr-gpt-transcribe-canary-v1": {
        "asr_provider_ref": "openai",
        "requested_model_ref": "gpt-transcribe",
        "processing_region_ref": "openai-eu",
        "data_control_ref": "openai-zdr-approved-v1",
        "request_config_digest": "aa0458c2b16b5b6718a7981e0ddd9d3ea0b9e6710912951aed47a363df98339f",
        "asr_profile_digest": "f796f44fee980262691cbab1c363348a3fc647855df46f0559dd29c088f9239e",
        "tariff_catalog_version": "asr-tariff-v2",
    },
}


class TranscriptionContractRefused(RecordingContractRefused):
    """Opaque refusal for transcription effects and receipts."""

    def __init__(  # pylint: disable=too-many-arguments
        self,
        *,
        status=404,
        outcome=None,
        retry_after_seconds=None,
        provenance=None,
        error_code=None,
    ):
        super().__init__(status=status, outcome=outcome)
        self.retry_after_seconds = retry_after_seconds
        self.provenance = provenance
        self.error_code = error_code


class TranscriptionPipelineFailed(TranscriptionContractRefused):
    """Terminal pipeline failure carrying the exact Core failure code."""

    def __init__(self, failure_code, status=503):
        super().__init__(status=status)
        self.failure_code = failure_code


def _submit_arguments(effect):
    arguments = {
        "version": CONTRACT_VERSION,
        "operation": "submit",
        "transcription_ref": effect["transcription_ref"],
        "recording_ref": effect["recording_ref"],
        "meeting_ref": effect["meeting_ref"],
        "room_ref": effect["room_ref"],
        "provider_binding_digest": effect["provider_binding_digest"],
    }
    if effect["operation_version"] in {2, 3}:
        arguments.update(
            {
                name: effect[name]
                for name in (
                    "asr_profile_ref",
                    "asr_profile_digest",
                    "asr_provider_ref",
                    "requested_model_ref",
                    "request_config_digest",
                    "normalization_version",
                    "processing_region_ref",
                    "data_control_ref",
                )
            }
        )
    if effect["operation_version"] == 3:
        arguments.update(
            {
                name: effect[name]
                for name in (
                    "campaign_ref",
                    "authorized_cost_ceiling_micros",
                    "currency",
                    "tariff_catalog_version",
                )
            }
        )
    return arguments


def _verified_submit_payload(compact_jws):
    """Verify either exact submit schema without trusting an unverified version."""

    try:
        return (
            _verify(compact_jws, SUBMIT_EFFECT_JOSE_TYPE, SUBMIT_EFFECT_V2_FIELDS),
            2,
        )
    except RecordingContractRefused:
        pass
    try:
        return (
            _verify(compact_jws, SUBMIT_EFFECT_JOSE_TYPE, SUBMIT_EFFECT_V3_FIELDS),
            3,
        )
    except RecordingContractRefused:
        return (
            _verify(compact_jws, SUBMIT_EFFECT_JOSE_TYPE, SUBMIT_EFFECT_FIELDS),
            1,
        )


def _validate_asr_reference(effect, name):
    if not ASR_REFERENCE.fullmatch(effect.get(name, "")):
        raise TranscriptionContractRefused()


def _validate_v2_profile(effect):
    for name in (
        "asr_profile_ref",
        "requested_model_ref",
        "normalization_version",
        "processing_region_ref",
        "data_control_ref",
    ):
        _validate_asr_reference(effect, name)
    expected_profile = V2_PROFILE_MANIFEST.get(effect.get("asr_profile_ref"))
    if expected_profile is None or any(
        effect.get(name) != expected for name, expected in expected_profile.items()
    ):
        raise TranscriptionContractRefused()


def _validate_v3_reservation(effect):
    expected = MANAGED_PROFILE_BINDINGS.get(effect.get("asr_profile_ref"))
    if expected is None or any(
        effect.get(name) != value for name, value in expected.items()
    ):
        raise TranscriptionContractRefused()
    for name in ("campaign_ref", "tariff_catalog_version"):
        _validate_asr_reference(effect, name)
    ceiling = effect.get("authorized_cost_ceiling_micros")
    if (
        not isinstance(ceiling, int)
        or isinstance(ceiling, bool)
        or ceiling < 0
        or effect.get("currency") != "USD"
        or effect.get("normalization_version") != V2_NORMALIZATION_VERSION
    ):
        raise TranscriptionContractRefused()
    for name in ("asr_profile_digest", "request_config_digest"):
        if not DIGEST.fullmatch(effect.get(name, "")):
            raise TranscriptionContractRefused()


def verify_transcription_submit_effect(compact_jws):
    """Verify the exact Core transcription submit effect contract."""

    effect, schema_version = _verified_submit_payload(compact_jws)
    _validate_time(effect)
    if (
        effect.get("version") != CONTRACT_VERSION
        or effect.get("type") != SUBMIT_EFFECT_TYPE
        or effect.get("issuer") != settings.MASTRAO_RECORDING_EFFECT_ISSUER
        or effect.get("audience") != settings.MASTRAO_RECORDING_EFFECT_AUDIENCE
        or effect.get("operation") != "submit_meeting_transcription"
        or effect.get("operation_version") != schema_version
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
    if effect["operation_version"] == 2:
        _validate_v2_profile(effect)
    if effect["operation_version"] == 3:
        for name in ("asr_profile_digest", "request_config_digest"):
            if not DIGEST.fullmatch(effect.get(name, "")):
                raise TranscriptionContractRefused()
        for name in (
            "asr_profile_ref",
            "requested_model_ref",
            "normalization_version",
            "processing_region_ref",
            "data_control_ref",
        ):
            _validate_asr_reference(effect, name)
        _validate_v3_reservation(effect)
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


def build_transcription_egress_request_claims(effect, attempt, execution_mode):
    """Build the exact, short-lived disclosure request for one prepared audio."""

    if effect.get("operation_version") != 3 or execution_mode not in {
        "send_allowed",
        "recover_only",
    }:
        raise TranscriptionContractRefused()
    now = int(time.time())
    claims = {
        "version": CONTRACT_VERSION,
        "type": EGRESS_REQUEST_TYPE,
        "issuer": settings.MASTRAO_RECORDING_RECEIPT_ISSUER,
        "audience": settings.MASTRAO_RECORDING_RECEIPT_AUDIENCE,
        "operation": "request_meeting_transcription_egress",
        "operation_version": 1,
        "organization_external_id": effect["organization_external_id"],
        "meeting_ref": effect["meeting_ref"],
        "room_ref": effect["room_ref"],
        "recording_ref": effect["recording_ref"],
        "transcription_ref": effect["transcription_ref"],
        "provider_binding_digest": effect["provider_binding_digest"],
        "effect_key": effect["effect_key"],
        "arguments_digest": effect["arguments_digest"],
        "attempt_ref": attempt.attempt_ref,
        "audio_sha256": attempt.audio_sha256,
        "audio_bytes": attempt.input_bytes,
        "audio_duration_ms": attempt.audio_duration_ms,
        "asr_profile_ref": effect["asr_profile_ref"],
        "asr_profile_digest": effect["asr_profile_digest"],
        "asr_provider_ref": effect["asr_provider_ref"],
        "requested_model_ref": effect["requested_model_ref"],
        "request_config_digest": effect["request_config_digest"],
        "normalization_version": effect["normalization_version"],
        "processing_region_ref": effect["processing_region_ref"],
        "data_control_ref": effect["data_control_ref"],
        "campaign_ref": effect["campaign_ref"],
        "authorized_cost_ceiling_micros": effect["authorized_cost_ceiling_micros"],
        "currency": effect["currency"],
        "tariff_catalog_version": effect["tariff_catalog_version"],
        "execution_mode": execution_mode,
        "issued_at": now,
        "expires_at": now + MAX_EGRESS_GRANT_SECONDS,
        "jti": f"transcript_egress_{uuid4().hex}",
    }
    return claims


def sign_transcription_egress_request(claims):
    """Sign one exact managed-provider egress request."""
    return _sign(claims, EGRESS_REQUEST_JOSE_TYPE)


EGRESS_GRANT_FIELDS = {
    *(
        SUBMIT_EFFECT_V3_FIELDS
        - {
            "policy_ref",
            "notice_version",
            "purpose",
            "scope",
            "retention_expires_at",
            "recording_artifact_ref",
            "recording_checksum_digest",
            "resolve_only",
        }
    ),
    "cabinet_id",
    "attempt_ref",
    "audio_sha256",
    "audio_bytes",
    "audio_duration_ms",
    "execution_mode",
    "notice_digest",
    "consent_epoch",
    "authority_version",
    "grant_semantic_digest",
}


def verify_transcription_egress_grant(compact_jws, request_claims):
    """Verify Core's exact grant and its semantic binding before disclosure."""

    grant = _verify(compact_jws, EGRESS_GRANT_JOSE_TYPE, EGRESS_GRANT_FIELDS)
    _validate_time(grant, maximum=MAX_EGRESS_GRANT_SECONDS)
    if (
        grant.get("version") != CONTRACT_VERSION
        or grant.get("type") != EGRESS_GRANT_TYPE
        or grant.get("issuer") != settings.MASTRAO_RECORDING_EFFECT_ISSUER
        or grant.get("audience") != settings.MASTRAO_TRANSCRIPTION_EGRESS_GRANT_AUDIENCE
        or grant.get("operation") != "authorize_meeting_transcription_egress"
        or grant.get("operation_version") != 1
        or not DIGEST.fullmatch(grant.get("notice_digest", ""))
        or not DIGEST.fullmatch(grant.get("grant_semantic_digest", ""))
        or not REQUEST_ID.fullmatch(grant.get("jti", ""))
        or not isinstance(grant.get("cabinet_id"), str)
        or not 1 <= len(grant["cabinet_id"]) <= 160
        or not isinstance(grant.get("consent_epoch"), int)
        or isinstance(grant.get("consent_epoch"), bool)
        or grant["consent_epoch"] <= 0
        or not isinstance(grant.get("authority_version"), int)
        or isinstance(grant.get("authority_version"), bool)
        or grant["authority_version"] <= 0
    ):
        raise TranscriptionContractRefused()
    common = set(request_claims) - {
        "type",
        "issuer",
        "audience",
        "operation",
        "jti",
        "issued_at",
        "expires_at",
    }
    if any(grant.get(name) != request_claims[name] for name in common):
        raise TranscriptionContractRefused()
    semantic = {
        name: value
        for name, value in grant.items()
        if name
        not in {
            "audience",
            "execution_mode",
            "expires_at",
            "grant_semantic_digest",
            "issued_at",
            "issuer",
            "jti",
            "operation",
            "operation_version",
            "type",
            "version",
        }
    }
    if grant.get("grant_semantic_digest") != _sha256_canonical(semantic):
        raise TranscriptionContractRefused()
    return grant


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


def _execution_provenance(effect, attempt):
    if not attempt.grant_semantic_digest or not attempt.authority_version:
        raise TranscriptionContractRefused()
    provenance = {
        "attempt_ref": attempt.attempt_ref,
        "grant_semantic_digest": attempt.grant_semantic_digest,
        "authority_version": attempt.authority_version,
        "provider_ref": attempt.provider_ref,
        "requested_model_ref": attempt.requested_model_ref,
        "processing_region_ref": effect["processing_region_ref"],
        "data_control_ref": effect["data_control_ref"],
        # The database column predates signed v2 receipts and is a FloatField.
        # Emit the contract's integer billing seconds so Python does not sign
        # ``44.0`` while the cross-language canonicalizer reconstructs ``44``.
        "usage_audio_seconds": int(attempt.usage_audio_seconds or 0),
        "estimated_cost_micros": attempt.estimated_cost_micros or 0,
        "currency": attempt.currency or effect["currency"],
        "tariff_catalog_version": attempt.tariff_catalog_version
        or effect["tariff_catalog_version"],
    }
    optional = {
        "observed_model_ref": attempt.provider_observed_model_ref,
        "provider_request_ref_digest": attempt.provider_request_ref_digest,
        "provider_egress_opened_at": attempt.provider_egress_opened_at,
        "provider_completed_at": attempt.provider_completed_at,
    }
    provenance.update(
        {
            name: int(value.timestamp()) if hasattr(value, "timestamp") else value
            for name, value in optional.items()
            if value is not None
        }
    )
    return provenance


def build_transcript_artifact_receipt_claims(effect, artifact, attempt=None):
    """Build strict persisted transcript artifact receipt claims."""

    now = int(time.time())
    claims = {
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
    if effect.get("operation_version") == 3:
        if attempt is None:
            raise TranscriptionContractRefused()
        claims.update(
            {
                "operation_version": 2,
                "effect_key": effect["effect_key"],
                "asr_profile_ref": effect["asr_profile_ref"],
                "asr_profile_digest": effect["asr_profile_digest"],
                "audio_sha256": attempt.audio_sha256,
                "request_config_digest": effect["request_config_digest"],
                "normalization_version": effect["normalization_version"],
                **_execution_provenance(effect, attempt),
            }
        )
    return claims


def build_transcription_terminal_receipt_claims(effect, attempt, outcome):
    """Build truthful v2 terminal evidence for a managed attempt."""

    if outcome not in {
        "failed_pre_egress",
        "rejected",
        "unknown",
        "deleted",
        "conflict",
    }:
        raise TranscriptionContractRefused()
    now = int(time.time())
    return {
        "version": CONTRACT_VERSION,
        "type": TERMINAL_RECEIPT_TYPE,
        "issuer": settings.MASTRAO_RECORDING_RECEIPT_ISSUER,
        "audience": settings.MASTRAO_RECORDING_RECEIPT_AUDIENCE,
        "operation": "confirm_meeting_transcription_terminal",
        "operation_version": 2,
        "organization_external_id": effect["organization_external_id"],
        "meeting_ref": effect["meeting_ref"],
        "room_ref": effect["room_ref"],
        "recording_ref": effect["recording_ref"],
        "transcription_ref": effect["transcription_ref"],
        "provider_binding_digest": effect["provider_binding_digest"],
        "effect_key": effect["effect_key"],
        "asr_profile_ref": effect["asr_profile_ref"],
        "asr_profile_digest": effect["asr_profile_digest"],
        "audio_sha256": attempt.audio_sha256,
        "request_config_digest": effect["request_config_digest"],
        "normalization_version": effect["normalization_version"],
        "outcome": outcome,
        "provider_egress_opened": attempt.provider_egress_opened_at is not None,
        **_execution_provenance(effect, attempt),
        "issued_at": now,
        "expires_at": now + MAX_ASSERTION_SECONDS,
        "jti": f"transcript_terminal_{uuid4().hex}",
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


def sign_transcription_terminal_receipt(claims):
    """Sign truthful terminal evidence for one managed attempt."""
    return _sign(claims, TERMINAL_RECEIPT_JOSE_TYPE)
