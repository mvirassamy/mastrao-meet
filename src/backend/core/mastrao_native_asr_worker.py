"""Durable delivery of native sources, with Core-owned attempts and transcripts."""

# Generated LiveKit protobuf members and fail-closed contract checks are intentional here.
# pylint: disable=broad-exception-caught,import-outside-toplevel,missing-function-docstring

import logging
from datetime import timedelta
from uuid import UUID, uuid4

from django.conf import settings
from django.db import transaction
from django.db.models import F, Q
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
def _claim(capture_start_id, dispatch_ref):
    now = timezone.now()
    intent = (
        _eligible(now)
        .filter(
            pk=capture_start_id,
            asr_dispatch_ref=dispatch_ref,
        )
        .select_for_update(skip_locked=True, of=("self",))
        .first()
    )
    if intent is None:
        return None
    intent.asr_claim = uuid4()
    intent.asr_claim_until = now + timedelta(seconds=180)
    intent.asr_attempts += 1
    intent.asr_dispatch_ref = None
    intent.asr_dispatch_until = None
    intent.save(
        update_fields=[
            "asr_claim",
            "asr_claim_until",
            "asr_attempts",
            "asr_dispatch_ref",
            "asr_dispatch_until",
            "updated_at",
        ]
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


def _finish(intent, acknowledged, error=None):
    now = timezone.now()
    values = {
        "asr_acknowledged": acknowledged,
        "asr_claim": None,
        "asr_claim_until": None,
        "asr_next_at": now + timedelta(seconds=30),
        "asr_error": "" if acknowledged else "native_asr_delivery_failed",
        "updated_at": now,
    }
    if isinstance(error, RecordingContractRefused):
        if error.status == 429 or error.outcome in {"retry", "unknown"}:
            # Core bounds provider sends. Recovering a refused/uncertain attempt
            # must not consume the local delivery budget before Core can replace it.
            values["asr_attempts"] = F("asr_attempts") - 1
            deadline = getattr(error, "retry_at", None)
            if deadline is not None:
                values["asr_next_at"] = max(values["asr_next_at"], deadline)
            if error.outcome == "unknown":
                values["asr_error"] = "native_asr_outcome_unknown"
        elif error.outcome == "rejected":
            values["asr_next_at"] = intent.retention_expires_at
            values["asr_error"] = "native_asr_rejected"
    return _owned(intent).update(**values) == 1


def process_native_asr_source(capture_start_id, dispatch_ref):
    """No synchronous API work, no direct ASR secret, no video-finalization gate."""
    if not settings.MASTRAO_NATIVE_ASR_ENABLED:
        return False
    try:
        capture_start_id = UUID(str(capture_start_id))
        dispatch_ref = UUID(str(dispatch_ref))
    except (ValueError, TypeError, AttributeError):
        return False
    intent = _claim(capture_start_id, dispatch_ref)
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
    ) as error:
        _finish(intent, False, error)
        logger.warning("Native ASR delivery failed; durable recovery state retained")
        return False


def _dispatchable(now):
    return _eligible(now).filter(
        Q(asr_dispatch_until__isnull=True) | Q(asr_dispatch_until__lte=now)
    )


@transaction.atomic
def _reserve_dispatch(capture_start_id=None, exclude_ids=()):
    now = timezone.now()
    candidates = (
        _dispatchable(now)
        .exclude(pk__in=exclude_ids)
        .select_for_update(skip_locked=True, of=("self",))
    )
    if capture_start_id is not None:
        candidates = candidates.filter(pk=capture_start_id)
    intent = candidates.order_by("asr_next_at", "pk").first()
    if intent is None:
        return None
    dispatch_ref = intent.asr_dispatch_ref or uuid4()
    intent.asr_dispatch_ref = dispatch_ref
    intent.asr_dispatch_until = now + timedelta(minutes=5)
    intent.save(update_fields=["asr_dispatch_ref", "asr_dispatch_until", "updated_at"])
    return intent.pk, dispatch_ref


def _publish_dispatch(capture_start_id, dispatch_ref):
    from core.tasks.native_capture import (  # noqa: PLC0415  # pylint: disable=cyclic-import
        process_native_asr,
    )

    try:
        process_native_asr.apply_async(
            args=[str(capture_start_id), str(dispatch_ref)],
            queue="mastrao-transcription",
        )
    except Exception:  # noqa: BLE001 - broker errors may include credentials
        models.MastraoNativeCaptureStart.objects.filter(
            pk=capture_start_id, asr_dispatch_ref=dispatch_ref
        ).update(asr_dispatch_until=None)
        logger.warning("Native ASR dispatch unavailable; pending rows retained")


def _dispatch_one(capture_start_id=None, exclude_ids=()):
    reservation = _reserve_dispatch(capture_start_id, exclude_ids)
    if reservation is None:
        return None
    transaction.on_commit(lambda: _publish_dispatch(*reservation))
    return reservation[0]


def dispatch_native_asr(capture_start_id):
    if not settings.MASTRAO_NATIVE_ASR_ENABLED or not settings.CELERY_ENABLED:
        return False
    return _dispatch_one(capture_start_id) is not None


def schedule_native_asr(limit=20):
    if not settings.MASTRAO_NATIVE_ASR_ENABLED or not settings.CELERY_ENABLED:
        return 0
    dispatched_ids = set()
    for _ in range(limit):
        capture_start_id = _dispatch_one(exclude_ids=dispatched_ids)
        if capture_start_id is None:
            break
        dispatched_ids.add(capture_start_id)
    return len(dispatched_ids)
