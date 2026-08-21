"""Private adapter for exact canonical Mastrao transcription effects."""

import json

from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from core import models
from core.mastrao_core_http import post_core_json
from core.mastrao_recording_contract import compact_digest
from core.mastrao_transcription_artifact import (
    FFMPEG_TIMEOUT_SECONDS,
    delete_transcript_object,
    extract_verified_audio,
    map_speakers,
    persist_transcript,
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
from core.mastrao_transcription_worker import transcribe_audio
from core.tasks.transcription import process_mastrao_transcription

MAX_BODY_BYTES = 32_768
RUNNING_OBSERVATION = "running"
SUBMITTED_OBSERVATION = "submitted"


def _pipeline_timeout_seconds():
    """Upper bound of one extraction + ASR run, used as a crash lease."""

    return FFMPEG_TIMEOUT_SECONDS + settings.MASTRAO_TRANSCRIPTION_ASR_TIMEOUT_SECONDS


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
    try:
        audio_bytes = extract_verified_audio(
            recording_binding.object_ref,
            recording_binding.byte_size,
            recording_binding.checksum_digest,
        )
    except TranscriptionContractRefused as error:
        raise TranscriptionPipelineFailed("audio_extraction_failed") from error
    try:
        transcript = transcribe_audio(audio_bytes)
    except TranscriptionContractRefused as error:
        raise TranscriptionPipelineFailed("asr_failed") from error
    transcript = map_speakers(transcript)
    return persist_transcript(transcription_binding.transcription_ref, transcript)


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


def enqueue_transcription_job(effect_pk, effect_jti):
    """Enqueue at most one Celery job for the exact effect JTI."""

    enqueue_key = f"mastrao-transcribe-enqueue:{effect_jti}"
    if not cache.add(enqueue_key, "1", timeout=_pipeline_timeout_seconds()):
        return
    process_mastrao_transcription.apply_async(
        args=[str(effect_pk)],
        task_id=f"mastrao-transcribe-{effect_jti}",
        ignore_result=True,
    )


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
        expected_fields={"artifactRef"},
    )
    if result["artifactRef"] != claims["artifact_ref"]:
        raise TranscriptionContractRefused(status=503)


def _notify_core_failure(effect, failure_code):
    """Report one terminal pipeline failure to Core, never raising twice."""

    claims = build_transcription_failure_receipt_claims(effect, failure_code)
    try:
        post_core_json(
            endpoint=settings.MASTRAO_CORE_TRANSCRIPTION_FAILURE_ENDPOINT,
            expected_path="/internal/v1/meetings/transcription/failures",
            body={
                "transcription_failure_receipt": sign_transcription_failure_receipt(
                    claims
                )
            },
            timeout=settings.MASTRAO_CORE_RECORDING_TIMEOUT_SECONDS,
            refusal=TranscriptionContractRefused,
        )
    except TranscriptionContractRefused:
        pass


def _mark_pipeline_failed(local_effect_pk, binding_pk):
    with transaction.atomic():
        effect_row = models.MastraoTranscriptionEffect.objects.select_for_update().get(
            pk=local_effect_pk
        )
        if effect_row.state == models.MastraoTranscriptionEffect.State.APPLIED:
            return
        effect_row.state = models.MastraoTranscriptionEffect.State.FAILED
        effect_row.save(update_fields=["state", "updated_at"])
        binding_row = (
            models.MastraoTranscriptionBinding.objects.select_for_update().get(
                pk=binding_pk
            )
        )
        binding_row.state = models.MastraoTranscriptionBinding.State.FAILED
        binding_row.save(update_fields=["state", "updated_at"])


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
    """Reserve the effect, return the submitted receipt, enqueue Celery."""

    transcription_binding, local_effect = _prepare_transcription(effect)
    if local_effect.state == models.MastraoTranscriptionEffect.State.FAILED:
        raise TranscriptionContractRefused(status=409)
    claims = _persist_submit_receipt(local_effect, effect)
    if local_effect.state != models.MastraoTranscriptionEffect.State.APPLIED:
        enqueue_transcription_job(local_effect.pk, local_effect.effect_jti)
    return sign_submit_receipt(claims)


def _acquire_completion_lease(effect_pk):
    """Return (binding, effect, skip) under a crash-expiring exclusive lease."""

    with transaction.atomic():
        local_effect = (
            models.MastraoTranscriptionEffect.objects.select_for_update().get(
                pk=effect_pk
            )
        )
        transcription_binding = (
            models.MastraoTranscriptionBinding.objects.select_related(
                "recording_binding"
            )
            .select_for_update(of=("self",))
            .get(pk=local_effect.transcription_binding_id)
        )
        if local_effect.state == models.MastraoTranscriptionEffect.State.APPLIED:
            return transcription_binding, local_effect, "applied"
        if local_effect.state == models.MastraoTranscriptionEffect.State.FAILED:
            return transcription_binding, local_effect, "failed"
        stale_lease = timezone.now() - local_effect.updated_at >= timezone.timedelta(
            seconds=_pipeline_timeout_seconds()
        )
        if (
            local_effect.provider_observation == RUNNING_OBSERVATION
            and not stale_lease
        ):
            return transcription_binding, local_effect, "running"
        local_effect.provider_observation = RUNNING_OBSERVATION
        local_effect.save(update_fields=["provider_observation", "updated_at"])
        return transcription_binding, local_effect, "produce"


def _commit_available_artifact(local_effect_pk, binding_pk, artifact):
    with transaction.atomic():
        locked_effect = (
            models.MastraoTranscriptionEffect.objects.select_for_update().get(
                pk=local_effect_pk
            )
        )
        if locked_effect.state == models.MastraoTranscriptionEffect.State.APPLIED:
            return
        locked_binding = (
            models.MastraoTranscriptionBinding.objects.select_for_update().get(
                pk=binding_pk
            )
        )
        if locked_binding.checksum_digest is not None and (
            locked_binding.checksum_digest != artifact["checksum_digest"]
            or locked_binding.byte_size != artifact["byte_size"]
        ):
            raise TranscriptionContractRefused(status=409)
        locked_effect.state = models.MastraoTranscriptionEffect.State.APPLIED
        locked_effect.provider_observation = SUBMITTED_OBSERVATION
        locked_effect.applied_at = timezone.now()
        locked_effect.save(
            update_fields=["state", "provider_observation", "applied_at", "updated_at"]
        )
        locked_binding.transcript_artifact_ref = artifact["transcript_artifact_ref"]
        locked_binding.object_ref = artifact["object_ref"]
        locked_binding.content_type = "application/json"
        locked_binding.byte_size = artifact["byte_size"]
        locked_binding.checksum_algorithm = "sha256"
        locked_binding.checksum_digest = artifact["checksum_digest"]
        locked_binding.segment_count = artifact["segment_count"]
        locked_binding.engine_ref = artifact["engine_ref"]
        locked_binding.transcript_verified_at = timezone.now()
        locked_binding.state = models.MastraoTranscriptionBinding.State.AVAILABLE
        locked_binding.save()


def complete_transcription(effect_pk):
    """Finish one reserved effect: extract, ASR, persist, notify Core."""

    transcription_binding, local_effect, action = _acquire_completion_lease(effect_pk)
    effect = _effect_from_local(transcription_binding, local_effect)
    if action == "applied":
        _notify_core_artifact(effect, _artifact_from_binding(transcription_binding))
        return
    if action in {"failed", "running"}:
        return
    if transcription_binding.checksum_digest:
        artifact = _artifact_from_binding(transcription_binding)
    else:
        try:
            artifact = _produce_transcript(transcription_binding)
        except TranscriptionPipelineFailed as failure:
            _mark_pipeline_failed(local_effect.pk, transcription_binding.pk)
            _notify_core_failure(effect, failure.failure_code)
            raise
    try:
        _notify_core_artifact(effect, artifact)
    except TranscriptionContractRefused as error:
        if error.status in {404, 409} and artifact.get("object_ref"):
            delete_transcript_object(artifact["object_ref"])
            _mark_pipeline_failed(local_effect.pk, transcription_binding.pk)
        raise
    _commit_available_artifact(local_effect.pk, transcription_binding.pk, artifact)


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
