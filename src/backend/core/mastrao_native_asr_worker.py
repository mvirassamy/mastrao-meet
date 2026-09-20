"""Durable delivery of native sources, with Core-owned attempts and transcripts."""

# Generated LiveKit protobuf members and fail-closed contract checks are intentional here.
# pylint: disable=broad-exception-caught,import-outside-toplevel,missing-function-docstring

import logging
from datetime import timedelta
from uuid import uuid4

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

import requests

from core import models
from core.mastrao_native_asr_client import (
    ack_native_asr,
    prepare_native_asr,
    transcribe_native_asr,
    upload_native_asr_result,
)
from core.mastrao_recording_contract import RecordingContractRefused

logger = logging.getLogger(__name__)


def _eligible(now):
    return models.MastraoNativeCaptureStart.objects.filter(
        source_receipt__isnull=False,
        asr_acknowledged=False,
        asr_next_at__lte=now,
        asr_attempts__lt=8,
        retention_expires_at__gt=now,
        epoch__conflict=False,
        epoch__connection__correlation="correlated",
    ).filter(Q(asr_claim_until__isnull=True) | Q(asr_claim_until__lte=now))


@transaction.atomic
def _claim():
    now = timezone.now()
    intent = (
        _eligible(now)
        .select_for_update(skip_locked=True)
        .order_by("asr_next_at", "pk")
        .first()
    )
    if intent is None:
        return None
    intent.asr_claim = uuid4()
    intent.asr_claim_until = now + timedelta(seconds=180)
    intent.asr_attempts += 1
    intent.save(
        update_fields=["asr_claim", "asr_claim_until", "asr_attempts", "updated_at"]
    )
    return intent


def _owned(intent):
    return models.MastraoNativeCaptureStart.objects.filter(
        pk=intent.pk,
        asr_claim=intent.asr_claim,
        asr_claim_until__gt=timezone.now(),
        asr_acknowledged=False,
        retention_expires_at__gt=timezone.now(),
        epoch__conflict=False,
        epoch__connection__correlation="correlated",
    )


def _fresh(intent):
    if not _owned(intent).exists():
        raise RecordingContractRefused(status=503)


def _finish(intent, acknowledged):
    return (
        _owned(intent).update(
            asr_acknowledged=acknowledged,
            asr_claim=None,
            asr_claim_until=None,
            asr_next_at=timezone.now() + timedelta(seconds=30),
            asr_error="" if acknowledged else "native_asr_delivery_failed",
            updated_at=timezone.now(),
        )
        == 1
    )


def process_next_native_asr():
    """No synchronous API work, no direct ASR secret, no video-finalization gate."""
    if not settings.MASTRAO_NATIVE_ASR_ENABLED:
        return False
    intent = _claim()
    if intent is None:
        return False
    try:
        receipt = intent.asr_receipt
        if receipt is None:
            prepared, audio = prepare_native_asr(intent)
            _fresh(intent)
            result = transcribe_native_asr(prepared, audio)
            _fresh(intent)
            receipt = upload_native_asr_result(intent, prepared, result)
            # The gateway cache cannot be released before durable receipt commit.
            if (
                _owned(intent)
                .filter(asr_receipt__isnull=True)
                .update(asr_receipt=receipt, updated_at=timezone.now())
                != 1
            ):
                raise RecordingContractRefused(status=503)
        _fresh(intent)
        ack_native_asr(receipt)
        return _finish(intent, True)
    except (
        RecordingContractRefused,
        requests.RequestException,
        ValueError,
        TypeError,
        KeyError,
        OSError,
    ):
        _finish(intent, False)
        logger.warning("Native ASR delivery failed; bounded durable retry retained")
        return False


def schedule_native_asr():
    if not settings.MASTRAO_NATIVE_ASR_ENABLED or not settings.CELERY_ENABLED:
        return 0
    if not _eligible(timezone.now()).exists():
        return 0
    from core.tasks.native_capture import (  # noqa: PLC0415  # pylint: disable=cyclic-import
        process_native_asr,
    )

    try:
        process_native_asr.apply_async(args=[], queue="mastrao-transcription")
        return 1
    except Exception:  # noqa: BLE001 - broker errors may include credentials
        logger.warning("Native ASR dispatch unavailable; pending rows retained")
        return 0
