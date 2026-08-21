"""Durable provider-attempt lifecycle for one Mastrao transcription effect."""

from __future__ import annotations

import hashlib
import json
from uuid import uuid4

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from core import models
from core.mastrao_transcription_artifact import (
    canonical_transcript_object_ref,
    delete_result_recovery,
    recovery_object_ref,
)
from core.mastrao_transcription_contract import TranscriptionPipelineFailed

ADAPTER_VERSION = "asr-gateway-v1"
NORMALIZATION_SCHEMA_VERSION = "1"
PAID_PROVIDERS = {"mistral", "openai"}
AttemptState = models.MastraoTranscriptionProviderAttempt.State


def _config_digest(provider, model, codec, duration_ms):
    payload = "|".join(
        [
            provider,
            model,
            ADAPTER_VERSION,
            NORMALIZATION_SCHEMA_VERSION,
            codec,
            str(duration_ms),
        ]
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def _provider_profile():
    mode = settings.MASTRAO_TRANSCRIPTION_ASR_MODE
    if mode == "fake":
        return "fake", "fake-asr-deterministic-v1"
    provider = (getattr(settings, "MASTRAO_TRANSCRIPTION_PROVIDER", "") or "").strip()
    model = (getattr(settings, "MASTRAO_TRANSCRIPTION_MODEL", "") or "").strip()
    token = (getattr(settings, "MASTRAO_ASR_GATEWAY_AUTH_TOKEN", "") or "").strip()
    if provider not in PAID_PROVIDERS or not model or not token:
        raise TranscriptionPipelineFailed("asr_failed")
    return provider, model


def prepare_attempt(local_effect, extracted):
    """Create or reuse generation 1 with an immutable fingerprint."""

    provider, model = _provider_profile()
    digest = _config_digest(provider, model, extracted.codec, extracted.duration_ms)
    attempt_ref = f"attempt_{uuid4().hex}"
    with transaction.atomic():
        existing = (
            models.MastraoTranscriptionProviderAttempt.objects.select_for_update()
            .filter(effect=local_effect, generation=1)
            .first()
        )
        if existing:
            if (
                existing.audio_sha256 != extracted.sha256
                or existing.provider_ref != provider
                or existing.requested_model_ref != model
                or existing.request_config_digest != digest
            ):
                raise TranscriptionPipelineFailed("asr_failed")
            return existing
        return models.MastraoTranscriptionProviderAttempt.objects.create(
            effect=local_effect,
            generation=1,
            attempt_ref=attempt_ref,
            provider_ref=provider,
            requested_model_ref=model,
            adapter_version=ADAPTER_VERSION,
            request_config_digest=digest,
            audio_sha256=extracted.sha256,
            audio_duration_ms=extracted.duration_ms,
            audio_codec=extracted.codec,
            input_bytes=extracted.byte_size,
        )


def cas_sending(attempt):
    """Move prepared -> sending. Paid sending crash becomes unknown."""

    with transaction.atomic():
        locked = (
            models.MastraoTranscriptionProviderAttempt.objects.select_for_update().get(
                pk=attempt.pk
            )
        )
        if locked.state in {
            AttemptState.RESULT_RECEIVED,
            AttemptState.ARTIFACT_WRITE_PENDING,
            AttemptState.SUCCEEDED,
        }:
            return locked
        if locked.state == AttemptState.UNKNOWN:
            return locked
        if locked.state == AttemptState.SENDING:
            if locked.provider_ref in PAID_PROVIDERS:
                locked.state = AttemptState.UNKNOWN
                locked.last_safe_error_code = "PROVIDER_OUTCOME_UNKNOWN"
                locked.save(
                    update_fields=["state", "last_safe_error_code", "updated_at"]
                )
            return locked
        if locked.state == AttemptState.FAILED_PRE_EGRESS:
            locked.state = AttemptState.SENDING
            locked.started_at = timezone.now()
            locked.save(update_fields=["state", "started_at", "updated_at"])
            return locked
        if locked.state != AttemptState.PREPARED:
            return locked
        locked.state = AttemptState.SENDING
        locked.started_at = timezone.now()
        locked.save(update_fields=["state", "started_at", "updated_at"])
        return locked


def mark_result(attempt, transcript, usage=None, recovery_ref=None):
    """Persist the first normalized result checksum and refuse a conflicting payload."""

    payload = json.dumps(transcript, sort_keys=True, separators=(",", ":")).encode()
    checksum = hashlib.sha256(payload).hexdigest()
    with transaction.atomic():
        locked = (
            models.MastraoTranscriptionProviderAttempt.objects.select_for_update().get(
                pk=attempt.pk
            )
        )
        if locked.result_checksum:
            if locked.result_checksum != checksum:
                raise TranscriptionPipelineFailed("asr_failed")
            if recovery_ref and not locked.result_recovery_ref:
                locked.result_recovery_ref = recovery_ref
                locked.save(update_fields=["result_recovery_ref", "updated_at"])
            return locked
        locked.state = AttemptState.RESULT_RECEIVED
        locked.result_checksum = checksum
        locked.resolved_model_ref = transcript.get("engine_ref")
        if recovery_ref:
            locked.result_recovery_ref = recovery_ref
        if usage:
            locked.provider_request_ref_digest = usage.get(
                "provider_request_ref_digest"
            )
            locked.usage_audio_seconds = usage.get("usage_audio_seconds")
            locked.estimated_cost_micros = usage.get("estimated_cost_micros")
            locked.currency = usage.get("currency")
        locked.save(
            update_fields=[
                "state",
                "result_checksum",
                "resolved_model_ref",
                "result_recovery_ref",
                "provider_request_ref_digest",
                "usage_audio_seconds",
                "estimated_cost_micros",
                "currency",
                "updated_at",
            ]
        )
        return locked


def mark_pre_egress_failure(attempt, error_code="PROVIDER_PRE_EGRESS_FAILED"):
    """Record a proven pre-egress failure, or unknown if bytes may have been sent."""

    with transaction.atomic():
        locked = (
            models.MastraoTranscriptionProviderAttempt.objects.select_for_update().get(
                pk=attempt.pk
            )
        )
        if locked.state == AttemptState.UNKNOWN:
            return locked
        locked.state = AttemptState.FAILED_PRE_EGRESS
        locked.last_safe_error_code = error_code
        locked.save(update_fields=["state", "last_safe_error_code", "updated_at"])
        return locked


def mark_unknown(attempt):
    """Mark an ambiguous paid send so Celery must not open a second provider call."""

    with transaction.atomic():
        locked = (
            models.MastraoTranscriptionProviderAttempt.objects.select_for_update().get(
                pk=attempt.pk
            )
        )
        if locked.result_checksum:
            return locked
        locked.state = AttemptState.UNKNOWN
        locked.last_safe_error_code = "PROVIDER_OUTCOME_UNKNOWN"
        locked.save(update_fields=["state", "last_safe_error_code", "updated_at"])
        return locked


def predeclare_object(attempt, transcription_ref):
    """Persist the canonical transcript object key before writing transcript bytes."""

    object_ref = canonical_transcript_object_ref(transcription_ref)
    with transaction.atomic():
        locked = (
            models.MastraoTranscriptionProviderAttempt.objects.select_for_update().get(
                pk=attempt.pk
            )
        )
        if locked.transcript_object_ref and locked.transcript_object_ref != object_ref:
            raise TranscriptionPipelineFailed("asr_failed")
        locked.transcript_object_ref = object_ref
        if locked.state == AttemptState.RESULT_RECEIVED:
            locked.state = AttemptState.ARTIFACT_WRITE_PENDING
        locked.save(update_fields=["transcript_object_ref", "state", "updated_at"])
        return object_ref


def mark_succeeded(attempt):
    """Mark the attempt succeeded after Core accepts the artifact."""

    with transaction.atomic():
        locked = (
            models.MastraoTranscriptionProviderAttempt.objects.select_for_update().get(
                pk=attempt.pk
            )
        )
        locked.state = AttemptState.SUCCEEDED
        locked.completed_at = timezone.now()
        locked.save(update_fields=["state", "completed_at", "updated_at"])
        return locked


def may_replay_gateway(attempt):
    """Return whether Meet may POST the same attempt to recover a durable result."""

    if attempt.state == AttemptState.UNKNOWN:
        return True
    if attempt.state == AttemptState.SENDING and attempt.provider_ref in PAID_PROVIDERS:
        return True
    return False


def cleanup_attempt_recovery(attempt):
    """Delete the recovery copy after Core commits success or failure."""

    completed = models.MastraoTranscriptionProviderAttempt.CleanupState.COMPLETED
    pending = models.MastraoTranscriptionProviderAttempt.CleanupState.PENDING
    with transaction.atomic():
        locked = (
            models.MastraoTranscriptionProviderAttempt.objects.select_for_update().get(
                pk=attempt.pk
            )
        )
        if locked.cleanup_state == completed:
            return locked
        object_ref = locked.result_recovery_ref or recovery_object_ref(
            locked.attempt_ref
        )
        locked.cleanup_state = pending
        locked.save(update_fields=["cleanup_state", "updated_at"])
    delete_result_recovery(object_ref)
    with transaction.atomic():
        locked = (
            models.MastraoTranscriptionProviderAttempt.objects.select_for_update().get(
                pk=attempt.pk
            )
        )
        locked.cleanup_state = completed
        locked.result_recovery_ref = None
        locked.save(
            update_fields=["cleanup_state", "result_recovery_ref", "updated_at"]
        )
        return locked


def may_call_provider(attempt):
    """Return whether this attempt may still open a provider request."""

    if attempt.state == AttemptState.UNKNOWN:
        return False
    if attempt.state in {
        AttemptState.RESULT_RECEIVED,
        AttemptState.ARTIFACT_WRITE_PENDING,
        AttemptState.SUCCEEDED,
    }:
        return False
    if attempt.state == AttemptState.SENDING and attempt.provider_ref in PAID_PROVIDERS:
        return False
    return True
