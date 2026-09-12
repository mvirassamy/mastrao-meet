"""End-of-epoch transfer through Core, independent of the composite video."""

import base64
import logging
from datetime import timedelta
from uuid import UUID, uuid4

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from core import models
from core.mastrao_core_http import post_core_json
from core.mastrao_native_audio import (
    materialize_native_audio,
    native_audio_manifest_digest,
)
from core.mastrao_native_capture_adapter import _spool_prefix
from core.mastrao_native_notice import native_envelope
from core.mastrao_native_spool import SpoolRefused
from core.mastrao_recording_contract import RecordingContractRefused, _sign

SOURCE_PATH = "/internal/v1/meetings/capture/native/source"
SOURCE_JOSE = "mastrao-native-audio-source+jws"
logger = logging.getLogger(__name__)


def _eligible_sources(now):
    return models.MastraoNativeCaptureStart.objects.filter(
        drained_at__isnull=False,
        provider_job_ref__isnull=False,
        source_receipt__isnull=True,
        source_next_at__lte=now,
        source_attempts__lt=8,
        retention_expires_at__gt=now,
        observed_status__in=[3, 4, 5, 6],
        epoch__conflict=False,
        epoch__connection__correlation="correlated",
    ).filter(Q(source_claim_until__isnull=True) | Q(source_claim_until__lte=now))


@transaction.atomic
def _claim():
    now = timezone.now()
    intent = (
        _eligible_sources(now)
        .select_for_update(skip_locked=True)
        .order_by("source_next_at", "pk")
        .first()
    )
    if intent is None:
        return None
    intent.source_claim = uuid4()
    intent.source_claim_until = now + timedelta(seconds=120)
    intent.source_attempts += 1
    intent.save(
        update_fields=[
            "source_claim",
            "source_claim_until",
            "source_attempts",
            "updated_at",
        ]
    )
    return intent


def _send(intent, audio):
    manifest_digest = native_audio_manifest_digest(audio.manifest)
    assertion = {
        **native_envelope("mastrao.meet-native-audio-source"),
        "organization_external_id": intent.organization_external_id,
        "capture_ref": str(intent.capture_ref),
        "epoch_ref": str(intent.epoch_id),
        "provider_job_ref": intent.provider_job_ref,
        "observed_status": intent.observed_status,
        "manifest_digest": manifest_digest,
    }
    signed_request = _sign(assertion, SOURCE_JOSE)
    result = post_core_json(
        endpoint=settings.MASTRAO_CORE_NATIVE_SOURCE_ENDPOINT,
        expected_path=SOURCE_PATH,
        body={
            "request": signed_request,
            "manifest": audio.manifest,
            "audio_base64": base64.b64encode(audio.path.read_bytes()).decode("ascii"),
        },
        timeout=20,
        headers={"Authorization": f"Bearer {signed_request}"},
        refusal=RecordingContractRefused,
        passthrough_statuses={409},
    )
    if (
        set(result)
        != {
            "version",
            "source_ref",
            "capture_ref",
            "epoch_ref",
            "manifest_digest",
            "state",
            "coverage",
            "transcription_state",
        }
        or type(result["version"]) is not int
        or result["version"] != 1
        or result["capture_ref"] != str(intent.capture_ref)
        or result["epoch_ref"] != str(intent.epoch_id)
        or result["manifest_digest"] != manifest_digest
        or result["state"] != "verified"
        or result["coverage"] != "epoch_only"
        or result["transcription_state"] != "not_requested"
    ):
        raise RecordingContractRefused(status=503)
    try:
        if str(UUID(result["source_ref"])) != result["source_ref"]:
            raise ValueError
    except (ValueError, TypeError, AttributeError):
        raise RecordingContractRefused(status=503) from None
    return result


@transaction.atomic
def _finish(intent, receipt, error):
    now = timezone.now()
    return (
        models.MastraoNativeCaptureStart.objects.filter(
            pk=intent.pk,
            source_claim=intent.source_claim,
            source_claim_until__gt=now,
            source_receipt__isnull=True,
        ).update(
            source_receipt=receipt,
            source_claim=None,
            source_claim_until=None,
            source_next_at=now + timedelta(seconds=30),
            source_error=error,
            updated_at=now,
        )
        == 1
    )


def transfer_next_native_source():
    """One bounded transfer per worker call; no Start, direct S3 or provider call."""
    if not settings.MASTRAO_NATIVE_SOURCE_TRANSFER_ENABLED:
        return False
    intent = _claim()
    if intent is None:
        return False
    audio = None
    try:
        # Path is derived by the operator's pool root and server UUID only.
        expected = _spool_prefix(str(intent.capture_ref))
        if intent.output_prefix != expected:
            raise SpoolRefused("native_output_binding_mismatch")
        audio = materialize_native_audio(expected)
        if not models.MastraoNativeCaptureStart.objects.filter(
            pk=intent.pk,
            source_claim=intent.source_claim,
            source_claim_until__gt=timezone.now(),
            retention_expires_at__gt=timezone.now(),
            epoch__conflict=False,
            epoch__connection__correlation="correlated",
        ).exists():
            raise SpoolRefused("native_source_authority_changed")
        return _finish(intent, _send(intent, audio), "")
    except (SpoolRefused, OSError, ValueError, RecordingContractRefused):
        _finish(intent, None, "native_source_transfer_failed")
        logger.warning("Native audio source transfer failed; durable retry retained")
        return False
    finally:
        if audio is not None:
            audio.close()


def schedule_native_source_transfer():
    """No synchronous fallback inside a scheduler/API when Celery is disabled."""
    if (
        not settings.MASTRAO_NATIVE_SOURCE_TRANSFER_ENABLED
        or not settings.CELERY_ENABLED
    ):
        return 0
    if not _eligible_sources(timezone.now()).exists():
        return 0
    from core.tasks.native_capture import process_native_sources  # noqa: PLC0415

    try:
        process_native_sources.apply_async(args=[], queue="mastrao-transcription")
        return 1
    except Exception:  # noqa: BLE001 - broker exception may contain secrets.
        logger.warning("Native source dispatch unavailable; pending rows retained")
        return 0
