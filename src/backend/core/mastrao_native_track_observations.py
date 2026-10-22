"""Deliver verified microphone publications independently of capture admission.

The RTC inbox is the outbox, including facts from ended or conflicting epochs.
Only delivery fields change. A fresh signature wraps the same immutable fact on
retry; Core deduplicates by observation_ref and validates the exact binding.
"""

# LiveKit protobuf enums are generated; receipt version must not accept bool.
# pylint: disable=no-member,unidiomatic-typecheck

import logging
import re
from datetime import timedelta
from uuid import uuid4

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from livekit import api

from core import models
from core.mastrao_core_http import post_core_json
from core.mastrao_native_authorization import native_envelope
from core.mastrao_recording_contract import RecordingContractRefused, _sign

logger = logging.getLogger(__name__)
OBSERVATION_PATH = "/internal/v1/meetings/capture/native/track-observations"
OBSERVATION_JOSE = "mastrao-native-track-observation+jws"
OBSERVATION_TYPE = "mastrao.meet-native-track-observation"
CLAIM_SECONDS = 45
RETRY_SECONDS = 30
MAX_BATCH = 20


def track_observation_export_enabled():
    """Metadata opt-in does not grant media authority or depend on preentry."""
    return bool(
        settings.MASTRAO_MEETING_INTEGRATION_CONFIGURED
        and settings.MASTRAO_CORE_NATIVE_TRACK_OBSERVATION_ENDPOINT
    )


@transaction.atomic
def _claim_next(observation_id=None):
    now = timezone.now()
    pending = models.MastraoRtcObservation.objects.filter(
        event_type="track_published",
        track_type=api.TrackType.AUDIO,
        track_source=api.TrackSource.MICROPHONE,
        native_observation_acknowledged_at__isnull=True,
        native_observation_next_at__lte=now,
    ).filter(
        Q(native_observation_claim_until__isnull=True)
        | Q(native_observation_claim_until__lte=now)
    )
    if observation_id is not None:
        pending = pending.filter(pk=observation_id)
    observation = (
        pending.select_for_update(skip_locked=True, of=("self",))
        .order_by("native_observation_next_at", "pk")
        .first()
    )
    if observation is None:
        return None
    observation.native_observation_claim = uuid4()
    observation.native_observation_claim_until = now + timedelta(seconds=CLAIM_SECONDS)
    models.MastraoRtcObservation.objects.filter(pk=observation.pk).update(
        native_observation_claim=observation.native_observation_claim,
        native_observation_claim_until=observation.native_observation_claim_until,
    )
    return observation


def _assertion(observation):
    binding = observation.room_binding
    recording = models.MastraoRecordingBinding.objects.filter(
        room_binding_id=binding.pk,
        meeting_ref=binding.meeting_ref,
        room_ref=binding.room_ref,
        provider_binding_digest=binding.provider_binding_digest,
    ).first()
    if recording is None or not recording.organization_external_id:
        # Never infer the organization from grants, identities or token markers.
        raise RecordingContractRefused(status=503)
    if (
        not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", observation.event_id)
        or not 0 < observation.event_time_seconds <= 9_007_199_254_740_991
        or not re.fullmatch(r"[a-f0-9]{64}", observation.payload_digest)
        or any(
            not re.fullmatch(rf"{prefix}_[A-Za-z0-9_-]{{1,120}}", sid)
            for prefix, sid in (
                ("RM", observation.room_sid),
                ("PA", observation.participant_sid),
                ("TR", observation.track_sid),
            )
        )
    ):
        raise RecordingContractRefused(status=503)
    return {
        **native_envelope(OBSERVATION_TYPE),
        "organization_external_id": recording.organization_external_id,
        "meeting_ref": recording.meeting_ref,
        "room_ref": recording.room_ref,
        "provider_binding_digest": recording.provider_binding_digest,
        "observation_ref": str(observation.pk),
        "payload_digest": observation.payload_digest,
        "event_id": observation.event_id,
        "observed_at": observation.event_time_seconds,
        "room_sid": observation.room_sid,
        "participant_sid": observation.participant_sid,
        "track_sid": observation.track_sid,
    }


def _send(observation):
    result = post_core_json(
        endpoint=settings.MASTRAO_CORE_NATIVE_TRACK_OBSERVATION_ENDPOINT,
        expected_path=OBSERVATION_PATH,
        body={"observation": _sign(_assertion(observation), OBSERVATION_JOSE)},
        timeout=min(settings.MASTRAO_CORE_RECORDING_TIMEOUT_SECONDS, 10),
        refusal=RecordingContractRefused,
        expected_fields={"version", "observation_ref", "state"},
    )
    if (
        type(result["version"]) is not int
        or result["version"] != 1
        or result["observation_ref"] != str(observation.pk)
        or result["state"] != "recorded"
    ):
        raise RecordingContractRefused(status=503)


def _finish(observation, acknowledged):
    """Both success and retry are fenced against expired/replaced claimants."""
    now = timezone.now()
    return models.MastraoRtcObservation.objects.filter(
        pk=observation.pk,
        native_observation_acknowledged_at__isnull=True,
        native_observation_claim=observation.native_observation_claim,
        native_observation_claim_until__gt=now,
    ).update(
        native_observation_acknowledged_at=now if acknowledged else None,
        native_observation_claim=None,
        native_observation_claim_until=None,
        native_observation_next_at=now + timedelta(seconds=RETRY_SECONDS),
    )


def reconcile_native_track_observations(limit=20, observation_id=None):
    """Recover pending publications without requiring another RTC event."""
    if not track_observation_export_enabled():
        return 0
    acknowledged_count = 0
    for _ in range(max(0, min(limit, MAX_BATCH))):
        observation = _claim_next(observation_id)
        if observation is None:
            break
        acknowledged = False
        try:
            _send(observation)
            acknowledged = True
        except RecordingContractRefused:
            # Missing binding, transport loss and refused/malformed receipts all
            # stay pending. Log neither upstream messages nor participant facts.
            logger.warning("Native track observation pending; reconciler will retry")
        committed = _finish(observation, acknowledged)
        acknowledged_count += int(bool(committed and acknowledged))
    return acknowledged_count


def wake_native_track_observation(observation_id):
    """Queue after commit; never run Celery's synchronous fallback in intake."""
    if (
        not track_observation_export_enabled()
        or not settings.CELERY_ENABLED
        or settings.CELERY_TASK_ALWAYS_EAGER
    ):
        return
    from core.tasks.native_capture import (  # noqa: PLC0415  # pylint: disable=import-outside-toplevel,cyclic-import
        process_native_track_observations,
    )

    try:
        process_native_track_observations.delay(str(observation_id))
    except Exception:  # noqa: BLE001  # pylint: disable=broad-exception-caught
        logger.warning(
            "Native track observation wakeup unavailable; reconciler will retry"
        )
