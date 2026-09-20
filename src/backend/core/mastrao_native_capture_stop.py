"""Core-signed stop demand; no provider calls and no new capture authority."""

# Generated LiveKit protobuf members and fail-closed contract checks are intentional here.
# pylint: disable=missing-function-docstring,too-many-boolean-expressions,unidiomatic-typecheck

import re
from uuid import UUID

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import DatabaseError, transaction
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from core import models
from core.mastrao_native_capture_contract import require_native_receipt_signer
from core.mastrao_recording_adapter import _read_effect, _safe_response
from core.mastrao_recording_contract import (
    RecordingContractRefused,
    _sign,
    _validate_ref,
    _validate_time,
    _verify,
)

STOP_JOSE = "mastrao-native-microphone-stop-effect+jws"
STOP_RECEIPT_JOSE = "mastrao-native-microphone-stop-receipt+jws"
STOP_FIELDS = {
    "version",
    "type",
    "issuer",
    "audience",
    "organization_external_id",
    "meeting_ref",
    "room_ref",
    "provider_binding_digest",
    "capture_ref",
    "epoch_ref",
    "effect_key",
    "arguments_digest",
    "provider_job_ref",
    "issued_at",
    "expires_at",
    "jti",
}


def verify_native_stop(compact):
    effect = _verify(compact, STOP_JOSE, STOP_FIELDS)
    _validate_time(effect)
    if (
        type(effect["version"]) is not int
        or effect["version"] != 1
        or effect["type"] != "mastrao.core-native-microphone-stop-effect"
        or not settings.MASTRAO_RECORDING_EFFECT_ISSUER
        or not settings.MASTRAO_RECORDING_EFFECT_AUDIENCE
        or effect["issuer"] != settings.MASTRAO_RECORDING_EFFECT_ISSUER
        or effect["audience"] != settings.MASTRAO_RECORDING_EFFECT_AUDIENCE
    ):
        raise RecordingContractRefused()
    for name in ("meeting_ref", "room_ref", "effect_key", "jti"):
        _validate_ref(effect, name)
    for name in ("provider_binding_digest", "arguments_digest"):
        if not isinstance(effect[name], str) or not re.fullmatch(
            r"[a-f0-9]{64}", effect[name]
        ):
            raise RecordingContractRefused()
    if not isinstance(effect["organization_external_id"], str) or not re.fullmatch(
        r"[A-Za-z0-9._:-]{1,160}", effect["organization_external_id"]
    ):
        raise RecordingContractRefused()
    for name in ("capture_ref", "epoch_ref"):
        if str(UUID(effect[name])) != effect[name]:
            raise RecordingContractRefused()
    job = effect["provider_job_ref"]
    if job is not None and (
        not isinstance(job, str) or not re.fullmatch(r"EG_[A-Za-z0-9]{1,96}", job)
    ):
        raise RecordingContractRefused()
    return effect


@transaction.atomic(durable=True)
def apply_native_stop(effect):
    intent = (
        models.MastraoNativeCaptureStart.objects.select_for_update()
        .filter(
            capture_ref=effect["capture_ref"],
            epoch_id=effect["epoch_ref"],
            organization_external_id=effect["organization_external_id"],
            effect_key=effect["effect_key"],
            arguments_digest=effect["arguments_digest"],
        )
        .first()
    )
    if intent is None:
        raise RecordingContractRefused()
    room = intent.epoch.connection.room_binding
    if (
        room.meeting_ref != effect["meeting_ref"]
        or room.room_ref != effect["room_ref"]
        or room.provider_binding_digest != effect["provider_binding_digest"]
        or (
            effect["provider_job_ref"] is not None
            and intent.provider_job_ref != effect["provider_job_ref"]
        )
    ):
        raise RecordingContractRefused(status=409)
    require_native_receipt_signer()
    if intent.stop_requested_at is None:
        intent.stop_requested_at = timezone.now()
        intent.stop_reason = "core_requested"
        intent.next_check_at = timezone.now()
        intent.save(
            update_fields=[
                "stop_requested_at",
                "stop_reason",
                "next_check_at",
                "updated_at",
            ]
        )
    return _sign(
        {
            "version": 1,
            "type": "mastrao.native-microphone-stop-receipt",
            "issuer": settings.MASTRAO_RECORDING_RECEIPT_ISSUER,
            "audience": settings.MASTRAO_RECORDING_RECEIPT_AUDIENCE,
            "organization_external_id": intent.organization_external_id,
            "effect_key": intent.effect_key,
            "arguments_digest": intent.arguments_digest,
            "capture_ref": str(intent.capture_ref),
            "epoch_ref": str(intent.epoch_id),
            "provider_job_ref": intent.provider_job_ref,
            "observed_status": intent.observed_status,
            "state": "drained" if intent.drained_at else "requested",
            "reported_at": int(timezone.now().timestamp()),
            "media_durability_proven": False,
        },
        STOP_RECEIPT_JOSE,
    )


@csrf_exempt
@transaction.non_atomic_requests
@require_POST
def stop_native_capture(request):
    try:
        effect = _read_effect(request, "native_stop_effect", verify_native_stop)
        return _safe_response({"native_stop_receipt": apply_native_stop(effect)})
    except RecordingContractRefused as error:
        return _safe_response(
            {"error": "native_capture_stop_refused"}, status=error.status
        )
    except (ValueError, TypeError, AttributeError, ValidationError):
        return _safe_response({"error": "native_capture_stop_refused"}, status=404)
    except DatabaseError:
        return _safe_response({"error": "native_capture_stop_unavailable"}, status=503)
