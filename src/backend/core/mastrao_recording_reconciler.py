"""Bounded provider reconciliation for canonical Mastrao recordings."""

# LiveKit's generated enum members are not visible to pylint.
# pylint: disable=no-member

import logging

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from livekit import api as livekit_api

from core import models
from core.mastrao_native_admission import reconcile_native_admissions
from core.mastrao_native_asr_worker import schedule_native_asr
from core.mastrao_native_capture_drain import reconcile_native_captures
from core.mastrao_native_source_transfer import schedule_native_source_transfer
from core.mastrao_recording_adapter import (
    _exact_provider_egress,
    fail_stale_starting_provider_egress,
    publish_recording_start,
)
from core.mastrao_recording_artifact import finalize_mastrao_artifact
from core.mastrao_recording_failure import (
    FAILURE_STATES,
    report_mastrao_recording_failure,
)
from core.mastrao_transcription_pipeline import reconcile_transcription_dispatches

COMPLETION_STATES = {
    livekit_api.EgressStatus.EGRESS_COMPLETE,
    livekit_api.EgressStatus.EGRESS_LIMIT_REACHED,
}

logger = logging.getLogger(__name__)


def _reconcile_unregistered_egress(binding):
    if binding.provider_recording_ref is not None:
        return False
    return fail_stale_starting_provider_egress(binding, binding.recording)


def reconcile_mastrao_recording(binding):
    """Observe and converge one exact provider recording."""

    if binding.recording is None:
        return False
    egress = _exact_provider_egress(binding.recording)
    if egress is None:
        return _reconcile_unregistered_egress(binding)
    if egress.status in FAILURE_STATES:
        provider_status = egress.status if binding.provider_recording_ref else None
        return report_mastrao_recording_failure(binding.recording, provider_status)
    if egress.status == livekit_api.EgressStatus.EGRESS_STARTING:
        return False
    adopted = False
    if binding.provider_recording_ref is None and egress.status in {
        livekit_api.EgressStatus.EGRESS_ACTIVE,
        *COMPLETION_STATES,
    }:
        publish_recording_start(binding.pk, egress.egress_id, "already_active")
        binding.provider_recording_ref = egress.egress_id
        binding.state = models.MastraoRecordingBinding.State.ACTIVE
        adopted = True
    if egress.status in COMPLETION_STATES:
        with transaction.atomic():
            updated = models.MastraoRecordingBinding.objects.filter(
                pk=binding.pk,
                state__in=[
                    models.MastraoRecordingBinding.State.ACTIVE,
                    models.MastraoRecordingBinding.State.STOPPING,
                    models.MastraoRecordingBinding.State.PROCESSING,
                ],
            ).update(
                state=models.MastraoRecordingBinding.State.PROCESSING,
                updated_at=timezone.now(),
            )
            if updated:
                models.Recording.objects.filter(
                    pk=binding.recording_id,
                    status__in=[
                        models.RecordingStatusChoices.INITIATED,
                        models.RecordingStatusChoices.ACTIVE,
                    ],
                ).update(
                    status=models.RecordingStatusChoices.STOPPED,
                    updated_at=timezone.now(),
                )
        if updated:
            finalize_mastrao_artifact(binding.recording)
        return bool(updated)
    return adopted


def reconcile_native_recordings(limit=20):
    """Advance native audio without invoking or waiting for video finalization."""
    return (
        reconcile_native_captures(limit=limit)
        + reconcile_native_admissions(limit=limit)
        + schedule_native_source_transfer()
        + schedule_native_asr()
    )


def reconcile_mastrao_recordings(limit=20):
    """Process a bounded batch for an external scheduler or operator."""

    applying_starts = models.MastraoRecordingEffect.objects.filter(
        operation=models.MastraoRecordingEffect.Operation.START,
        state=models.MastraoRecordingEffect.State.APPLYING,
    ).values("recording_binding_id")
    bindings = (
        models.MastraoRecordingBinding.objects.select_related("recording")
        .filter(
            recording__isnull=False,
            state__in=[
                models.MastraoRecordingBinding.State.STARTING,
                models.MastraoRecordingBinding.State.ACTIVE,
                models.MastraoRecordingBinding.State.STOPPING,
                models.MastraoRecordingBinding.State.PROCESSING,
            ],
        )
        .filter(Q(provider_recording_ref__isnull=False) | Q(pk__in=applying_starts))
        .order_by("updated_at")[:limit]
    )
    reconciled = 0
    for binding in bindings:
        try:
            if reconcile_mastrao_recording(binding):
                reconciled += 1
            else:
                models.MastraoRecordingBinding.objects.filter(pk=binding.pk).update(
                    updated_at=timezone.now()
                )
        # A bounded scheduler must keep progressing when one provider object or
        # Core receipt is temporarily invalid. The exception remains observable
        # without logging room, meeting or recording references.
        except Exception:  # pylint: disable=broad-exception-caught
            logger.exception("Mastrao recording reconciliation item failed")
            # Move a poison item behind the rest of the bounded queue. This
            # preserves retryability while preventing the oldest failing rows
            # from occupying every subsequent batch.
            models.MastraoRecordingBinding.objects.filter(pk=binding.pk).update(
                updated_at=timezone.now()
            )
    return (
        reconciled
        + reconcile_transcription_dispatches(limit=limit)
        + reconcile_native_recordings(limit=limit)
    )
