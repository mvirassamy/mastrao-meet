"""Bounded provider reconciliation for canonical Mastrao recordings."""

# LiveKit's generated enum members are not visible to pylint.
# pylint: disable=no-member

import logging

from django.utils import timezone

from livekit import api as livekit_api

from core import models
from core.mastrao_recording_adapter import _exact_provider_egress
from core.mastrao_recording_artifact import finalize_mastrao_artifact
from core.mastrao_recording_failure import (
    FAILURE_STATES,
    report_mastrao_recording_failure,
)

COMPLETION_STATES = {
    livekit_api.EgressStatus.EGRESS_COMPLETE,
    livekit_api.EgressStatus.EGRESS_LIMIT_REACHED,
}

logger = logging.getLogger(__name__)


def reconcile_mastrao_recording(binding):
    """Observe and converge one exact provider recording."""

    if binding.recording is None:
        return False
    egress = _exact_provider_egress(binding.recording)
    if egress is None:
        return False
    if egress.status in FAILURE_STATES:
        return report_mastrao_recording_failure(binding.recording, egress.status)
    if egress.status in COMPLETION_STATES:
        binding.state = binding.State.PROCESSING
        binding.save(update_fields=["state", "updated_at"])
        finalize_mastrao_artifact(binding.recording)
        return True
    return False


def reconcile_mastrao_recordings(limit=20):
    """Process a bounded batch for an external scheduler or operator."""

    bindings = (
        models.MastraoRecordingBinding.objects.select_related("recording")
        .filter(
            recording__isnull=False,
            provider_recording_ref__isnull=False,
            state__in=[
                models.MastraoRecordingBinding.State.STARTING,
                models.MastraoRecordingBinding.State.ACTIVE,
                models.MastraoRecordingBinding.State.STOPPING,
                models.MastraoRecordingBinding.State.PROCESSING,
            ],
        )
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
    return reconciled
