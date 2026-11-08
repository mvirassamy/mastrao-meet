"""Durable post-meeting ASR for one verified mixed room-audio source."""

import logging
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID, uuid4

from django.conf import settings
from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone

import requests
from celery import current_app

from core import models
from core.mastrao_recording_contract import RecordingContractRefused
from core.mastrao_room_audio_asr_client import (
    ack_room_audio_asr,
    prepare_room_audio_asr,
    transcribe_room_audio,
    upload_room_audio_asr_result,
)
from core.room_audio_queue import ROOM_AUDIO_PROCESSING_QUEUE

logger = logging.getLogger(__name__)
CLAIM_SECONDS = 300
MAX_ATTEMPTS = 12
PREPARE_UNAVAILABLE_RETRY_SECONDS = 30


@dataclass(frozen=True)
class RetryPolicy:
    """Bound one retry without conflating authorization waits and attempts."""

    retry_seconds: int | None
    error_code: str
    consume_attempt: bool


def _eligible(now):
    return models.MastraoRoomAudioSourceOutbox.objects.filter(
        state=models.MastraoRoomAudioSourceOutbox.State.DELIVERED,
        source_id__isnull=False,
        asr_acknowledged=False,
        asr_next_at__lte=now,
        asr_attempts__lt=MAX_ATTEMPTS,
    ).filter(Q(asr_claim_until__isnull=True) | Q(asr_claim_until__lte=now))


@transaction.atomic
def _claim(source_id):
    now = timezone.now()
    row = (
        _eligible(now).filter(pk=source_id).select_for_update(skip_locked=True).first()
    )
    if row is None:
        return None
    row.asr_claim = uuid4()
    row.asr_claim_until = now + timedelta(seconds=CLAIM_SECONDS)
    row.asr_attempts += 1
    row.save(
        update_fields=[
            "asr_claim",
            "asr_claim_until",
            "asr_attempts",
            "updated_at",
        ]
    )
    return row


def _owned(row):
    return models.MastraoRoomAudioSourceOutbox.objects.filter(
        pk=row.pk,
        asr_claim=row.asr_claim,
        asr_claim_until__gt=timezone.now(),
        asr_acknowledged=False,
    )


def _fresh(row):
    if not _owned(row).exists():
        raise RecordingContractRefused(status=503)


def _exponential_retry_seconds(attempts):
    return min(30 * (2 ** min(max(attempts - 1, 0), 6)), 1800)


def _finish(row, acknowledged, receipt=None, retry_policy=None):
    policy = retry_policy or RetryPolicy(
        retry_seconds=None,
        error_code="room_audio_asr_delivery_failed",
        consume_attempt=True,
    )
    delay = (
        _exponential_retry_seconds(row.asr_attempts)
        if policy.retry_seconds is None
        else policy.retry_seconds
    )
    values = {
        "asr_acknowledged": acknowledged,
        "asr_claim": None,
        "asr_claim_until": None,
        "asr_next_at": timezone.now() + timedelta(seconds=delay),
        "asr_receipt": receipt,
        "asr_error": "" if acknowledged else policy.error_code,
        "updated_at": timezone.now(),
    }
    if not policy.consume_attempt:
        values["asr_attempts"] = F("asr_attempts") - 1
    return _owned(row).update(**values) == 1


def _failure_policy(error, stage):
    core_reason_codes = getattr(error, "core_reason_codes", ())
    if (
        stage == "prepare"
        and isinstance(error, RecordingContractRefused)
        and getattr(error, "core_error_code", None) == "resource_unavailable"
        and "room_audio_asr_finance_not_sending" in core_reason_codes
    ):
        return RetryPolicy(
            retry_seconds=PREPARE_UNAVAILABLE_RETRY_SECONDS,
            error_code="room_audio_asr_finance_not_sending",
            consume_attempt=False,
        )
    return RetryPolicy(
        retry_seconds=None,
        error_code="room_audio_asr_delivery_failed",
        consume_attempt=True,
    )


def process_room_audio_asr_source(source_id):
    """Run the exact room source through Core-authorized Mistral ASR."""
    try:
        source_id = UUID(str(source_id))
    except (ValueError, TypeError, AttributeError):
        return False
    source = _claim(source_id)
    if source is None:
        return False
    receipt = source.asr_receipt
    stage = "prepare"
    try:
        if receipt is None:
            prepared, audio = prepare_room_audio_asr(source)
            _fresh(source)
            stage = "transcribe"
            result = transcribe_room_audio(prepared, audio)
            _fresh(source)
            stage = "upload_result"
            receipt = upload_room_audio_asr_result(source, prepared, result)
            if (
                _owned(source)
                .filter(asr_receipt__isnull=True)
                .update(asr_receipt=receipt, updated_at=timezone.now())
                != 1
            ):
                raise RecordingContractRefused(status=503)
        _fresh(source)
        stage = "ack"
        ack_room_audio_asr(receipt)
        return _finish(source, True, receipt)
    except (
        RecordingContractRefused,
        requests.RequestException,
        ValueError,
        TypeError,
        KeyError,
        OSError,
    ) as error:
        _finish(
            source,
            False,
            receipt,
            retry_policy=_failure_policy(error, stage),
        )
        logger.warning(
            "Room audio ASR attempt failed stage=%s error_type=%s "
            "core_http_status=%s core_error_code=%s core_reason_codes=%s; "
            "source retained for retry",
            stage,
            type(error).__name__,
            getattr(error, "core_http_status", None),
            getattr(error, "core_error_code", None),
            getattr(error, "core_reason_codes", ()),
        )
        return False


def schedule_room_audio_asr(source_id=None, limit=20):
    """Enqueue eligible room sources without taking provider work inline."""
    if not settings.CELERY_ENABLED:
        return 0
    now = timezone.now()
    eligible = _eligible(now)
    if source_id is not None:
        eligible = eligible.filter(pk=source_id)
    source_ids = list(
        eligible.order_by("asr_next_at", "pk").values_list("pk", flat=True)[:limit]
    )
    dispatched = 0
    for eligible_source_id in source_ids:
        try:
            current_app.send_task(
                "core.process_room_audio_asr",
                args=[str(eligible_source_id)],
                queue=ROOM_AUDIO_PROCESSING_QUEUE,
            )
            dispatched += 1
        except Exception:  # noqa: BLE001  # pylint: disable=broad-exception-caught
            logger.warning("Room audio ASR queue unavailable; source remains eligible")
    return dispatched
