"""Private adapter for exact canonical Mastrao recording effects."""

import json
from datetime import UTC, datetime

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from core import models
from core.mastrao_recording_contract import (
    RecordingContractRefused,
    build_start_receipt_claims,
    build_stop_receipt_claims,
    compact_digest,
    sign_start_receipt,
    sign_stop_receipt,
    verify_recording_start_effect,
    verify_recording_stop_effect,
)
from core.recording.worker.exceptions import RecordingStartError, RecordingStopError
from core.recording.worker.factories import get_worker_service
from core.recording.worker.mediator import WorkerServiceMediator

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


def _read_effect(request, field, verifier):
    declared = request.headers.get("content-length")
    if (
        request.content_type != "application/json"
        or declared is None
        or not declared.isdecimal()
        or int(declared) > MAX_BODY_BYTES
        or len(request.body) > MAX_BODY_BYTES
    ):
        raise RecordingContractRefused()
    try:
        body = json.loads(request.body)
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as error:
        raise RecordingContractRefused() from error
    if not isinstance(body, dict) or set(body) != {field}:
        raise RecordingContractRefused()
    return verifier(body[field])


def _binding(effect, *, lock=False):
    queryset = models.MastraoRoomBinding.objects.select_related("room", "owner")
    if lock:
        queryset = queryset.select_for_update()
    binding = queryset.filter(
        meeting_ref=effect["meeting_ref"], room_ref=effect["room_ref"]
    ).first()
    if (
        binding is None
        or binding.provider_binding_digest != effect["provider_binding_digest"]
        or binding.closing_at is not None
        or hasattr(binding, "closure")
    ):
        raise RecordingContractRefused()
    return binding


def _exact_effect(existing, effect, operation):
    if (
        existing.effect_key != effect["effect_key"]
        or existing.operation != operation
        or existing.arguments_digest != effect["arguments_digest"]
        or existing.effect_jti != effect["jti"]
    ):
        raise RecordingContractRefused(status=409)
    return existing


@transaction.atomic
def _prepare_start(effect):
    room_binding = _binding(effect, lock=True)
    recording_binding = (
        models.MastraoRecordingBinding.objects.select_for_update()
        .select_related("recording")
        .filter(room_binding=room_binding)
        .first()
    )
    if recording_binding:
        if (
            recording_binding.organization_external_id
            != effect["organization_external_id"]
            or recording_binding.recording_ref != effect["recording_ref"]
            or recording_binding.provider_binding_digest
            != effect["provider_binding_digest"]
            or recording_binding.policy_ref != effect["policy_ref"]
            or recording_binding.notice_version != effect["notice_version"]
            or recording_binding.notice_digest != effect["notice_digest"]
            or recording_binding.purpose != effect["purpose"]
            or recording_binding.scope != effect["scope"]
            or int(recording_binding.retention_expires_at.timestamp())
            != effect["retention_expires_at"]
        ):
            raise RecordingContractRefused(status=409)
    else:
        recording_binding = models.MastraoRecordingBinding.objects.create(
            room_binding=room_binding,
            organization_external_id=effect["organization_external_id"],
            meeting_ref=effect["meeting_ref"],
            room_ref=effect["room_ref"],
            recording_ref=effect["recording_ref"],
            provider_binding_digest=effect["provider_binding_digest"],
            policy_ref=effect["policy_ref"],
            notice_version=effect["notice_version"],
            notice_digest=effect["notice_digest"],
            purpose=effect["purpose"],
            scope=effect["scope"],
            retention_expires_at=datetime.fromtimestamp(
                effect["retention_expires_at"], tz=UTC
            ),
        )
    existing = (
        models.MastraoRecordingEffect.objects.select_for_update()
        .filter(recording_binding=recording_binding, operation="start")
        .first()
    )
    if existing:
        return recording_binding, _exact_effect(existing, effect, "start")
    created = models.MastraoRecordingEffect.objects.create(
        recording_binding=recording_binding,
        effect_key=effect["effect_key"],
        operation="start",
        arguments_digest=effect["arguments_digest"],
        effect_jti=effect["jti"],
    )
    return recording_binding, created


def _apply_start(effect):
    recording_binding, local_effect = _prepare_start(effect)
    if local_effect.state == models.MastraoRecordingEffect.State.APPLIED:
        return sign_start_receipt(local_effect.receipt_claims)

    recording = recording_binding.recording
    observation = "already_active"
    if recording is None:
        with transaction.atomic():
            locked = models.MastraoRecordingBinding.objects.select_for_update().get(
                pk=recording_binding.pk
            )
            recording = models.Recording.objects.create(
                room=locked.room_binding.room,
                mode=models.RecordingModeChoices.SCREEN_RECORDING,
                options={
                    "mastrao_recording_ref": locked.recording_ref,
                    "mastrao_retention_expires_at": int(
                        locked.retention_expires_at.timestamp()
                    ),
                },
            )
            models.RecordingAccess.objects.create(
                user=locked.room_binding.owner,
                role=models.RoleChoices.OWNER,
                recording=recording,
            )
            locked.recording = recording
            locked.state = models.MastraoRecordingBinding.State.STARTING
            locked.save(update_fields=["recording", "state", "updated_at"])
        observation = "started"

    if recording.status == models.RecordingStatusChoices.INITIATED:
        try:
            WorkerServiceMediator(
                get_worker_service(mode=models.RecordingModeChoices.SCREEN_RECORDING)
            ).start(recording)
        except RecordingStartError as error:
            models.MastraoRecordingBinding.objects.filter(
                pk=recording_binding.pk
            ).update(state=models.MastraoRecordingBinding.State.FAILED)
            raise RecordingContractRefused(status=503) from error
    elif recording.status != models.RecordingStatusChoices.ACTIVE:
        raise RecordingContractRefused(status=409)

    claims = build_start_receipt_claims(effect, recording.worker_id, observation)
    with transaction.atomic():
        locked_effect = models.MastraoRecordingEffect.objects.select_for_update().get(
            pk=local_effect.pk
        )
        if locked_effect.state == models.MastraoRecordingEffect.State.APPLIED:
            return sign_start_receipt(locked_effect.receipt_claims)
        locked_effect.state = models.MastraoRecordingEffect.State.APPLIED
        locked_effect.provider_observation = observation
        locked_effect.receipt_claims = claims
        locked_effect.receipt_digest = compact_digest(sign_start_receipt(claims))
        locked_effect.applied_at = timezone.now()
        locked_effect.save()
        models.MastraoRecordingBinding.objects.filter(pk=recording_binding.pk).update(
            provider_recording_ref=recording.worker_id,
            state=models.MastraoRecordingBinding.State.ACTIVE,
        )
    return sign_start_receipt(claims)


@transaction.atomic
def _prepare_stop(effect):
    room_binding = (
        models.MastraoRoomBinding.objects.select_for_update()
        .filter(meeting_ref=effect["meeting_ref"], room_ref=effect["room_ref"])
        .first()
    )
    if (
        room_binding is None
        or room_binding.provider_binding_digest != effect["provider_binding_digest"]
    ):
        raise RecordingContractRefused()
    recording_binding = (
        models.MastraoRecordingBinding.objects.select_for_update()
        .select_related("recording")
        .filter(room_binding=room_binding, recording_ref=effect["recording_ref"])
        .first()
    )
    if (
        recording_binding is None
        or recording_binding.provider_recording_ref != effect["provider_recording_ref"]
    ):
        raise RecordingContractRefused()
    existing = (
        models.MastraoRecordingEffect.objects.select_for_update()
        .filter(recording_binding=recording_binding, operation="stop")
        .first()
    )
    if existing:
        return recording_binding, _exact_effect(existing, effect, "stop")
    created = models.MastraoRecordingEffect.objects.create(
        recording_binding=recording_binding,
        effect_key=effect["effect_key"],
        operation="stop",
        arguments_digest=effect["arguments_digest"],
        effect_jti=effect["jti"],
    )
    recording_binding.state = models.MastraoRecordingBinding.State.STOPPING
    recording_binding.save(update_fields=["state", "updated_at"])
    return recording_binding, created


def _apply_stop(effect):
    recording_binding, local_effect = _prepare_stop(effect)
    if local_effect.state == models.MastraoRecordingEffect.State.APPLIED:
        return sign_stop_receipt(local_effect.receipt_claims)
    recording = recording_binding.recording
    if recording is None:
        raise RecordingContractRefused(status=409)
    if hasattr(recording_binding.room_binding, "closure"):
        observation = "room_ended"
    elif recording.status == models.RecordingStatusChoices.ACTIVE:
        try:
            WorkerServiceMediator(get_worker_service(mode=recording.mode)).stop(
                recording
            )
        except RecordingStopError as error:
            raise RecordingContractRefused(status=503) from error
        observation = "stopped"
    elif recording.status in {
        models.RecordingStatusChoices.STOPPED,
        models.RecordingStatusChoices.SAVED,
        models.RecordingStatusChoices.NOTIFICATION_SUCCEEDED,
        models.RecordingStatusChoices.EXTERNAL_PROCESS_SUCCESSFUL,
        models.RecordingStatusChoices.EXTERNAL_PROCESS_FAILED,
    }:
        observation = "already_stopped"
    else:
        raise RecordingContractRefused(status=409)

    claims = build_stop_receipt_claims(effect, observation)
    compact = sign_stop_receipt(claims)
    with transaction.atomic():
        locked_effect = models.MastraoRecordingEffect.objects.select_for_update().get(
            pk=local_effect.pk
        )
        if locked_effect.state == models.MastraoRecordingEffect.State.APPLIED:
            return sign_stop_receipt(locked_effect.receipt_claims)
        locked_effect.state = models.MastraoRecordingEffect.State.APPLIED
        locked_effect.provider_observation = observation
        locked_effect.receipt_claims = claims
        locked_effect.receipt_digest = compact_digest(compact)
        locked_effect.applied_at = timezone.now()
        locked_effect.save()
        models.MastraoRecordingBinding.objects.filter(pk=recording_binding.pk).update(
            state=models.MastraoRecordingBinding.State.PROCESSING
        )
    return compact


def _handle(request, field, verifier, applier, response_field):
    if not settings.MASTRAO_MEETING_RECORDING_ENABLED:
        return _safe_response({"message": "Not found"}, 404)
    try:
        effect = _read_effect(request, field, verifier)
        return _safe_response({response_field: applier(effect)})
    except RecordingContractRefused as error:
        return _safe_response(
            {"message": "Not found" if error.status == 404 else "Unavailable"},
            error.status,
        )
    except (IntegrityError, ValidationError):
        return _safe_response({"message": "Unavailable"}, 409)


@csrf_exempt
@require_POST
def start_mastrao_recording(request):
    return _handle(
        request,
        "recording_start_effect",
        verify_recording_start_effect,
        _apply_start,
        "recording_start_receipt",
    )


@csrf_exempt
@require_POST
def stop_mastrao_recording(request):
    return _handle(
        request,
        "recording_stop_effect",
        verify_recording_stop_effect,
        _apply_stop,
        "recording_stop_receipt",
    )
