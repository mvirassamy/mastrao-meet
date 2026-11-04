"""Deliver durable RTC candidates; Core alone resolves consent and authorizes Start.

No browser polling, bearer outbox or provider I/O. A lost response retries the
same epoch. Core owns idempotence. The recording reconciler recovers broker loss.
"""

# Generated LiveKit protobuf members and fail-closed contract checks are intentional here.
# pylint: disable=broad-exception-caught,import-outside-toplevel,no-member
# pylint: disable=too-many-boolean-expressions,unidiomatic-typecheck

import logging
import unicodedata
from datetime import timedelta
from uuid import uuid4

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from livekit import api

from core import models
from core.mastrao_core_http import post_core_json
from core.mastrao_recording_contract import RecordingContractRefused, _sign
from core.mastrao_room_audio_authorization import room_audio_envelope
from core.mastrao_room_contract import OPAQUE_REFERENCE
from core.mastrao_track_authority import assert_track_authority

logger = logging.getLogger(__name__)
OBSERVED_PATH = "/internal/v1/meetings/room-audio/track/observed"
OBSERVED_JOSE = "mastrao-room-audio-track-observed+jws"


def admission_enabled():
    """Room Audio candidates require the complete trusted meeting integration."""
    return settings.MASTRAO_MEETING_INTEGRATION_CONFIGURED


def _session_label(epoch):
    """Display text from the bound server session, not a claimed identity."""
    issued = epoch.connection.media_token_binding
    grant = (issued.host_grant or issued.guest_grant) if issued else None
    name = grant.display_name if grant else None
    if isinstance(name, str):
        name = name.strip()
    if (
        not isinstance(name, str)
        or not 1 <= len(name.encode("utf-16-le", errors="surrogatepass")) // 2 <= 160
        or any(unicodedata.category(char) in {"Cc", "Cf", "Cs"} for char in name)
    ):
        name = None
    return {"source": "meet_session_display_name", "display_name": name}


def _pending_audio_epochs(connection_id=None):
    pending = models.MastraoRtcTrackEpoch.objects.filter(
        room_audio_admitted_at__isnull=True,
        room_audio_admission_blocked=False,
        ended=False,
        conflict=False,
        connection__ended=False,
        connection__correlation="correlated",
        first_publication__track_type=api.TrackType.AUDIO,
        first_publication__track_source=api.TrackSource.MICROPHONE,
    )
    if connection_id is not None:
        pending = pending.filter(connection_id=connection_id)
    return pending


def _due_audio_epochs(connection_id=None):
    now = timezone.now()
    return (
        _pending_audio_epochs(connection_id)
        .filter(
            room_audio_admission_next_at__lte=now,
        )
        .filter(
            Q(room_audio_admission_claim_until__isnull=True)
            | Q(room_audio_admission_claim_until__lte=now)
        )
    )


@transaction.atomic
def _claim_next(connection_id=None):
    now = timezone.now()
    epoch = (
        _due_audio_epochs(connection_id)
        .select_for_update(skip_locked=True, of=("self",))
        .order_by("room_audio_admission_next_at", "pk")
        .first()
    )
    if epoch is None:
        return None
    if epoch.room_audio_participant_label is None:
        epoch.room_audio_participant_label = _session_label(epoch)
    epoch.room_audio_admission_claim = uuid4()
    epoch.room_audio_admission_claim_until = now + timedelta(seconds=45)
    epoch.save(
        update_fields=[
            "room_audio_admission_claim",
            "room_audio_admission_claim_until",
            "room_audio_participant_label",
            "updated_at",
        ]
    )
    return epoch


def _observed(epoch):
    connection = epoch.connection
    binding = connection.room_binding
    issued = connection.media_token_binding
    recording = models.MastraoRecordingBinding.objects.filter(
        room_binding=binding,
        meeting_ref=binding.meeting_ref,
        room_ref=binding.room_ref,
        provider_binding_digest=binding.provider_binding_digest,
    ).first()
    if (
        not admission_enabled()
        or binding.closing_at is not None
        or hasattr(binding, "closure")
        or recording is None
        or issued is None
    ):
        raise RecordingContractRefused()
    grant = issued.host_grant or issued.guest_grant
    # The grant expiry bounds admission of the RTC join token. Once the
    # connection and its track epoch are durably correlated, expiry alone does
    # not revoke that established session; Core authorizes the fresh capture
    # effect against the exact bound grant/session evidence below.
    if grant is None:
        raise RecordingContractRefused()
    observed = {
        **room_audio_envelope("mastrao.meet-room-audio-track-observed"),
        "organization_external_id": recording.organization_external_id,
        "meeting_ref": binding.meeting_ref,
        "room_ref": binding.room_ref,
        "provider_binding_digest": binding.provider_binding_digest,
        "epoch_ref": str(epoch.pk),
        "media_token_binding_ref": str(issued.pk),
        "room_sid": connection.room_sid,
        "participant_sid": connection.participant_sid,
        "track_sid": epoch.track_sid,
        "grant_ref": grant.grant_ref,
        "grant_digest": issued.grant_digest,
        "session_nonce_digest": issued.session_nonce_digest,
        "participant_kind": "host" if issued.host_grant_id else "guest",
        "participant_ref": grant.identity.host_ref
        if issued.host_grant_id
        else grant.guest_ref,
        **(
            {"participant_label": epoch.room_audio_participant_label}
            if epoch.room_audio_participant_label is not None
            else {}
        ),
    }
    assert_track_authority(epoch, binding, observed)
    return observed


def _send(epoch):
    result = post_core_json(
        endpoint=settings.MASTRAO_CORE_ROOM_AUDIO_OBSERVED_ENDPOINT,
        expected_path=OBSERVED_PATH,
        body={"candidate": _sign(_observed(epoch), OBSERVED_JOSE)},
        timeout=min(settings.MASTRAO_CORE_RECORDING_TIMEOUT_SECONDS, 10),
        refusal=RecordingContractRefused,
        passthrough_statuses={409},
        expected_fields={
            "version",
            "epoch_ref",
            "audio_run_refs",
            "state",
        },
    )
    run_refs = result["audio_run_refs"]
    if (
        type(result["version"]) is not int
        or result["version"] != 1
        or result["epoch_ref"] != str(epoch.pk)
        or result["state"] != "authorized"
        or not isinstance(run_refs, list)
        or not 1 <= len(run_refs) <= 2
        or len(set(run_refs)) != len(run_refs)
        or any(
            not isinstance(run_ref, str) or not OPAQUE_REFERENCE.fullmatch(run_ref)
            for run_ref in run_refs
        )
    ):
        raise RecordingContractRefused(status=503)
    return run_refs


def _finish(epoch, run_refs, blocked):
    """An expired claimant cannot overwrite another worker's delivery result."""
    now = timezone.now()
    return models.MastraoRtcTrackEpoch.objects.filter(
        pk=epoch.pk,
        room_audio_admission_claim=epoch.room_audio_admission_claim,
        room_audio_admission_claim_until__gt=now,
    ).update(
        room_audio_run_refs=run_refs,
        room_audio_admitted_at=now if run_refs else None,
        room_audio_admission_blocked=blocked,
        room_audio_admission_claim=None,
        room_audio_admission_claim_until=None,
        room_audio_admission_next_at=now + timedelta(seconds=30),
        updated_at=now,
    )


def reconcile_room_audio_admissions(limit=20, connection_id=None):
    """Bounded independent deliveries, safe to retry from the existing scheduler."""
    if not admission_enabled():
        return 0
    admitted = 0
    for _ in range(max(0, min(limit, 20))):
        epoch = _claim_next(connection_id)
        if epoch is None:
            break
        run_refs, blocked = [], False
        try:
            run_refs = _send(epoch)
        except RecordingContractRefused as error:
            logger.warning(
                "Room Audio admission refused status=%s core_http_status=%s "
                "core_error_code=%s cause=%s",
                error.status,
                getattr(error, "core_http_status", None),
                getattr(error, "core_error_code", None),
                type(error.__cause__).__name__ if error.__cause__ is not None else None,
            )
            # Authority denials need a fresh epoch/authority, not a retry storm.
            blocked = error.status in (400, 403, 404, 409)
        committed = _finish(epoch, run_refs, blocked)
        admitted += int(bool(committed and run_refs and not blocked))
    return admitted


def room_audio_admission_retry_pending(connection_id):
    """Return whether one active microphone epoch still needs Core admission."""
    return _pending_audio_epochs(connection_id).exists()


def room_audio_admission_due(connection_id):
    """Return whether this task can claim an admission attempt now."""
    return _due_audio_epochs(connection_id).exists()


def wake_room_audio_admissions(connection_id):
    """Never execute the synchronous task fallback in the webhook request."""
    if not admission_enabled() or not settings.CELERY_ENABLED:
        return
    from core.tasks.room_audio import (  # noqa: PLC0415  # pylint: disable=cyclic-import
        process_room_audio_admissions,
    )

    try:
        process_room_audio_admissions.delay(str(connection_id))
    except Exception:  # noqa: BLE001  # Broker loss must not undo committed RTC intake.
        # No payload/credential logging; the epoch remains durable and pending.
        logger.warning("Room Audio admission wakeup unavailable; reconciler will retry")
