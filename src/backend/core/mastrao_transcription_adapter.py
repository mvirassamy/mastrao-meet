"""Private adapter for exact canonical Mastrao transcription effects."""

import json

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from core import models
from core.mastrao_recording_contract import compact_digest
from core.mastrao_transcription_artifact import (
    extract_verified_audio,
    map_speakers,
    persist_transcript,
)
from core.mastrao_transcription_contract import (
    TranscriptionContractRefused,
    build_submit_receipt_claims,
    sign_submit_receipt,
    verify_transcription_submit_effect,
)
from core.mastrao_transcription_worker import transcribe_audio

MAX_BODY_BYTES = 32_768


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
    audio_bytes = extract_verified_audio(
        recording_binding.object_ref,
        recording_binding.byte_size,
        recording_binding.checksum_digest,
    )
    transcript = transcribe_audio(audio_bytes)
    samples = list(
        models.MastraoActiveSpeakerSample.objects.filter(
            recording_binding=recording_binding
        )
    )
    transcript = map_speakers(transcript, samples)
    return persist_transcript(transcription_binding.transcription_ref, transcript)


def _apply_transcription(effect):
    transcription_binding, local_effect = _prepare_transcription(effect)
    if local_effect.state == models.MastraoTranscriptionEffect.State.APPLIED:
        return sign_submit_receipt(local_effect.receipt_claims)

    artifact = _produce_transcript(transcription_binding)
    claims = build_submit_receipt_claims(effect, "submitted")
    with transaction.atomic():
        locked_effect = (
            models.MastraoTranscriptionEffect.objects.select_for_update().get(
                pk=local_effect.pk
            )
        )
        if locked_effect.state == models.MastraoTranscriptionEffect.State.APPLIED:
            return sign_submit_receipt(locked_effect.receipt_claims)
        locked_binding = (
            models.MastraoTranscriptionBinding.objects.select_for_update().get(
                pk=transcription_binding.pk
            )
        )
        if locked_binding.checksum_digest is not None and (
            locked_binding.checksum_digest != artifact["checksum_digest"]
            or locked_binding.byte_size != artifact["byte_size"]
        ):
            raise TranscriptionContractRefused(status=409)
        locked_effect.state = models.MastraoTranscriptionEffect.State.APPLIED
        locked_effect.provider_observation = "submitted"
        locked_effect.receipt_claims = claims
        locked_effect.receipt_digest = compact_digest(sign_submit_receipt(claims))
        locked_effect.applied_at = timezone.now()
        locked_effect.save()
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
