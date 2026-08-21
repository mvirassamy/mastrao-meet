"""Private adapter for exact canonical Mastrao transcription effects."""

import json

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from core import models
from core.mastrao_core_http import post_core_json
from core.mastrao_recording_contract import compact_digest
from core.mastrao_transcription_artifact import (
    extract_verified_audio_file,
    load_result_recovery,
    map_speakers,
    persist_result_recovery,
    persist_transcript,
    recover_persisted_transcript,
)
from core.mastrao_transcription_attempt import (
    cas_sending,
    mark_pre_egress_failure,
    mark_result,
    mark_unknown,
    may_call_provider,
    predeclare_object,
    prepare_attempt,
)
from core.mastrao_transcription_contract import (
    TranscriptionContractRefused,
    TranscriptionPipelineFailed,
    build_submit_receipt_claims,
    build_transcript_artifact_receipt_claims,
    build_transcription_failure_receipt_claims,
    sign_submit_receipt,
    sign_transcript_artifact_receipt,
    sign_transcription_failure_receipt,
    verify_transcription_submit_effect,
)
from core.mastrao_transcription_pipeline import publish_transcription_job
from core.mastrao_transcription_worker import transcribe_extracted

MAX_BODY_BYTES = 32_768
SUBMITTED_OBSERVATION = "submitted"


def _safe_response(payload, status=200):
    return JsonResponse(
        payload,
        status=status,
        headers={
            "Cache-Control": "private, no-store",
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
        },
    )


def _read_effect(request):
    declared = request.headers.get("content-length")
    if (
        request.content_type != "application/json"
        or declared is None
        or not declared.isdecimal()
        or int(declared) > MAX_BODY_BYTES
        or len(request.body) > MAX_BODY_BYTES
    ):
        raise TranscriptionContractRefused()
    try:
        body = json.loads(request.body)
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as error:
        raise TranscriptionContractRefused() from error
    if not isinstance(body, dict) or set(body) != {"transcription_submit_effect"}:
        raise TranscriptionContractRefused()
    return verify_transcription_submit_effect(body["transcription_submit_effect"])


def _recording_binding(effect):
    """Bind the effect to one exact finalized, verified recording artifact."""

    binding = (
        models.MastraoRecordingBinding.objects.select_related("room_binding")
        .filter(
            organization_external_id=effect["organization_external_id"],
            meeting_ref=effect["meeting_ref"],
            room_ref=effect["room_ref"],
            recording_ref=effect["recording_ref"],
            provider_binding_digest=effect["provider_binding_digest"],
            artifact_ref=effect["recording_artifact_ref"],
            state=models.MastraoRecordingBinding.State.FINALIZED,
        )
        .first()
    )
    if (
        binding is None
        or binding.object_ref is None
        or binding.byte_size is None
        or binding.checksum_digest != effect["recording_checksum_digest"]
    ):
        raise TranscriptionContractRefused()
    return binding


def _exact_effect(existing, effect):
    if (
        existing.effect_key != effect["effect_key"]
        or existing.operation != "transcribe"
        or existing.arguments_digest != effect["arguments_digest"]
        or existing.effect_jti != effect["jti"]
    ):
        raise TranscriptionContractRefused(status=409)
    return existing


@transaction.atomic
def _prepare_transcription(effect):
    recording_binding = _recording_binding(effect)
    transcription_binding = (
        models.MastraoTranscriptionBinding.objects.select_for_update(of=("self",))
        .filter(recording_binding=recording_binding)
        .first()
    )
    if transcription_binding:
        if (
            transcription_binding.transcription_ref != effect["transcription_ref"]
            or transcription_binding.artifact_ref != effect["recording_artifact_ref"]
            or transcription_binding.artifact_checksum_digest
            != effect["recording_checksum_digest"]
            or transcription_binding.artifact_byte_size != recording_binding.byte_size
        ):
            raise TranscriptionContractRefused(status=409)
    else:
        if not (
            settings.MASTRAO_MEETING_RECORDING_ENABLED
            and settings.MASTRAO_MEETING_TRANSCRIPTION_ENABLED
        ):
            raise TranscriptionContractRefused()
        transcription_binding = models.MastraoTranscriptionBinding.objects.create(
            recording_binding=recording_binding,
            organization_external_id=effect["organization_external_id"],
            meeting_ref=effect["meeting_ref"],
            room_ref=effect["room_ref"],
            recording_ref=effect["recording_ref"],
            transcription_ref=effect["transcription_ref"],
            artifact_ref=effect["recording_artifact_ref"],
            provider_binding_digest=effect["provider_binding_digest"],
            artifact_checksum_digest=effect["recording_checksum_digest"],
            artifact_byte_size=recording_binding.byte_size,
        )
    existing = (
        models.MastraoTranscriptionEffect.objects.select_for_update()
        .filter(transcription_binding=transcription_binding, operation="transcribe")
        .first()
    )
    if existing:
        return transcription_binding, _exact_effect(existing, effect)
    if effect["resolve_only"]:
        raise TranscriptionContractRefused()
    created = models.MastraoTranscriptionEffect.objects.create(
        transcription_binding=transcription_binding,
        effect_key=effect["effect_key"],
        arguments_digest=effect["arguments_digest"],
        effect_jti=effect["jti"],
        state=models.MastraoTranscriptionEffect.State.APPLYING,
    )
    transcription_binding.state = models.MastraoTranscriptionBinding.State.PROCESSING
    transcription_binding.save(update_fields=["state", "updated_at"])
    return transcription_binding, created


def _produce_transcript(transcription_binding):
    """Extract, transcribe and persist outside any database transaction."""

    recording_binding = transcription_binding.recording_binding
    local_effect = transcription_binding.effects.filter(operation="transcribe").get()
    try:
        extracted = extract_verified_audio_file(
            recording_binding.object_ref,
            recording_binding.byte_size,
            recording_binding.checksum_digest,
        )
    except TranscriptionContractRefused as error:
        raise TranscriptionPipelineFailed("audio_extraction_failed") from error
    try:
        attempt = prepare_attempt(local_effect, extracted)
        recovered = _resume_produced_artifact(transcription_binding, attempt)
        if recovered is not None:
            return recovered
        if not may_call_provider(attempt):
            mark_unknown(attempt)
            raise TranscriptionPipelineFailed("asr_failed", status=409)
        sending = cas_sending(attempt)
        if sending.state == models.MastraoTranscriptionProviderAttempt.State.UNKNOWN:
            raise TranscriptionPipelineFailed("asr_failed", status=409)
        if sending.result_checksum and sending.result_recovery_ref:
            transcript = load_result_recovery(sending.result_recovery_ref)
            if not transcript:
                raise TranscriptionPipelineFailed("asr_failed", status=409)
        else:
            try:
                transcript = transcribe_extracted(extracted, sending)
            except TranscriptionContractRefused as error:
                if error.outcome == "unknown" or error.status == 409:
                    mark_unknown(sending)
                    raise TranscriptionPipelineFailed(
                        "asr_failed", status=409
                    ) from error
                mark_pre_egress_failure(sending)
                raise TranscriptionPipelineFailed("asr_failed") from error
            usage = transcript.pop("_usage", None)
            recovery_ref, _checksum = persist_result_recovery(
                sending.attempt_ref, transcript
            )
            sending.result_recovery_ref = recovery_ref
            sending.save(update_fields=["result_recovery_ref", "updated_at"])
            mark_result(sending, transcript, usage)
        transcript = map_speakers(transcript)
        object_ref = predeclare_object(sending, transcription_binding.transcription_ref)
        if not transcription_binding.object_ref:
            transcription_binding.object_ref = object_ref
            transcription_binding.save(update_fields=["object_ref", "updated_at"])
        return persist_transcript(
            transcription_binding.transcription_ref,
            transcript,
            object_ref=object_ref,
        )
    finally:
        extracted.close()


def _resume_produced_artifact(transcription_binding, attempt):
    object_ref = transcription_binding.object_ref or attempt.transcript_object_ref
    if not object_ref:
        return None
    engine_ref = attempt.resolved_model_ref or attempt.requested_model_ref
    return recover_persisted_transcript(
        object_ref,
        transcription_binding.transcription_ref,
        engine_ref,
    )


def _artifact_from_binding(binding):
    """Rebuild the exact persisted artifact facts for idempotent replays."""

    return {
        "transcript_artifact_ref": binding.transcript_artifact_ref,
        "object_ref": binding.object_ref,
        "byte_size": binding.byte_size,
        "checksum_digest": binding.checksum_digest,
        "segment_count": binding.segment_count,
        "engine_ref": binding.engine_ref,
    }


def _effect_from_local(transcription_binding, local_effect):
    recording_binding = transcription_binding.recording_binding
    return {
        "organization_external_id": transcription_binding.organization_external_id,
        "meeting_ref": transcription_binding.meeting_ref,
        "room_ref": transcription_binding.room_ref,
        "recording_ref": transcription_binding.recording_ref,
        "transcription_ref": transcription_binding.transcription_ref,
        "recording_artifact_ref": transcription_binding.artifact_ref,
        "provider_binding_digest": transcription_binding.provider_binding_digest,
        "recording_checksum_digest": transcription_binding.artifact_checksum_digest,
        "retention_expires_at": int(recording_binding.retention_expires_at.timestamp()),
        "effect_key": local_effect.effect_key,
        "arguments_digest": local_effect.arguments_digest,
        "resolve_only": False,
        "jti": local_effect.effect_jti,
    }


def _notify_core_artifact(effect, artifact):
    """Report one exact persisted transcript artifact to Core."""

    claims = build_transcript_artifact_receipt_claims(effect, artifact)
    result = post_core_json(
        endpoint=settings.MASTRAO_CORE_TRANSCRIPTION_ARTIFACT_ENDPOINT,
        expected_path="/internal/v1/meetings/transcription/artifacts/finalize",
        body={
            "transcription_artifact_receipt": sign_transcript_artifact_receipt(claims)
        },
        timeout=settings.MASTRAO_CORE_RECORDING_TIMEOUT_SECONDS,
        refusal=TranscriptionContractRefused,
        expected_fields={"artifactRef", "outcome"},
        passthrough_statuses=frozenset({404, 409, 503}),
    )
    if (
        result["artifactRef"] != claims["artifact_ref"]
        or result["outcome"] != "available"
    ):
        raise TranscriptionContractRefused(status=503, outcome="retry")


def _notify_core_failure(effect, failure_code):
    """Report one pipeline failure to Core. A 503 must be retried."""

    claims = build_transcription_failure_receipt_claims(effect, failure_code)
    result = post_core_json(
        endpoint=settings.MASTRAO_CORE_TRANSCRIPTION_FAILURE_ENDPOINT,
        expected_path="/internal/v1/meetings/transcription/failures",
        body={
            "transcription_failure_receipt": sign_transcription_failure_receipt(claims)
        },
        timeout=settings.MASTRAO_CORE_RECORDING_TIMEOUT_SECONDS,
        refusal=TranscriptionContractRefused,
        expected_fields={"transcriptionRef", "state", "outcome"},
        passthrough_statuses=frozenset({404, 409, 503}),
    )
    if result["outcome"] == "available" and result["state"] == "available":
        return result
    if result["outcome"] != "failed" or result["state"] != "failed":
        raise TranscriptionContractRefused(status=503, outcome="retry")
    return result


def _persist_submit_receipt(local_effect, effect):
    if local_effect.receipt_claims:
        return local_effect.receipt_claims
    claims = build_submit_receipt_claims(effect, SUBMITTED_OBSERVATION)
    local_effect.receipt_claims = claims
    local_effect.receipt_digest = compact_digest(sign_submit_receipt(claims))
    local_effect.provider_observation = SUBMITTED_OBSERVATION
    local_effect.save(
        update_fields=[
            "receipt_claims",
            "receipt_digest",
            "provider_observation",
            "updated_at",
        ]
    )
    return claims


def _apply_transcription(effect):
    """Reserve the effect, return the submitted receipt, publish Celery."""

    _transcription_binding, local_effect = _prepare_transcription(effect)
    if local_effect.state == models.MastraoTranscriptionEffect.State.FAILED:
        raise TranscriptionContractRefused(status=409)
    claims = _persist_submit_receipt(local_effect, effect)
    if (
        local_effect.dispatch_state
        != models.MastraoTranscriptionEffect.DispatchState.COMPLETED
    ):
        publish_transcription_job(local_effect.pk)
    return sign_submit_receipt(claims)


@csrf_exempt
@require_POST
def transcribe_mastrao_recording(request):
    """Claim one exact signed submit effect and return a signed submit receipt."""

    try:
        effect = _read_effect(request)
        return _safe_response(
            {"transcription_submit_receipt": _apply_transcription(effect)}
        )
    except TranscriptionContractRefused as error:
        return _safe_response(
            {"message": "Not found" if error.status == 404 else "Unavailable"},
            error.status,
        )
    except (IntegrityError, ValidationError):
        return _safe_response({"message": "Unavailable"}, 409)
