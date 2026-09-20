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
from uuid import UUID, uuid4

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from livekit import api

from core import models
from core.mastrao_core_http import post_core_json
from core.mastrao_native_capture_adapter import _assert_epoch_authority
from core.mastrao_native_notice import native_envelope
from core.mastrao_recording_contract import RecordingContractRefused, _sign

logger = logging.getLogger(__name__)
OBSERVED_PATH = "/internal/v1/meetings/capture/native/observed"
OBSERVED_JOSE = "mastrao-native-observed-microphone+jws"


def admission_enabled():
    """New candidates require both explicit preentry and trusted RTC provenance."""
    return (
        settings.MASTRAO_NATIVE_PREENTRY_ENABLED
        and settings.MASTRAO_MEDIA_TOKEN_BINDING_ENABLED
    )


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


@transaction.atomic
def _claim_next(connection_id=None):
    now = timezone.now()
    pending = models.MastraoRtcTrackEpoch.objects.filter(
        native_admission_capture_ref__isnull=True,
        native_admission_blocked=False,
        native_admission_next_at__lte=now,
        ended=False,
        conflict=False,
        connection__ended=False,
        connection__correlation="correlated",
        first_publication__track_type=api.TrackType.AUDIO,
        first_publication__track_source=api.TrackSource.MICROPHONE,
    ).filter(
        Q(native_admission_claim_until__isnull=True)
        | Q(native_admission_claim_until__lte=now)
    )
    if connection_id is not None:
        pending = pending.filter(connection_id=connection_id)
    epoch = (
        pending.select_for_update(skip_locked=True, of=("self",))
        .order_by("native_admission_next_at", "pk")
        .first()
    )
    if epoch is None:
        return None
    if epoch.native_participant_label is None:
        epoch.native_participant_label = _session_label(epoch)
    epoch.native_admission_claim = uuid4()
    epoch.native_admission_claim_until = now + timedelta(seconds=45)
    epoch.save(
        update_fields=[
            "native_admission_claim",
            "native_admission_claim_until",
            "native_participant_label",
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
    if grant is None or grant.expires_at <= timezone.now():
        raise RecordingContractRefused()
    observed = {
        **native_envelope("mastrao.meet-native-observed-microphone"),
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
            {"participant_label": epoch.native_participant_label}
            if epoch.native_participant_label is not None
            else {}
        ),
    }
    _assert_epoch_authority(epoch, binding, observed)
    return observed


def _send(epoch):
    result = post_core_json(
        endpoint=settings.MASTRAO_CORE_NATIVE_OBSERVED_ENDPOINT,
        expected_path=OBSERVED_PATH,
        body={"candidate": _sign(_observed(epoch), OBSERVED_JOSE)},
        timeout=min(settings.MASTRAO_CORE_RECORDING_TIMEOUT_SECONDS, 10),
        refusal=RecordingContractRefused,
        passthrough_statuses={409},
        expected_fields={
            "version",
            "capture_ref",
            "epoch_ref",
            "state",
            "media_durability_proven",
        },
    )
    try:
        capture = UUID(result["capture_ref"])
    except (ValueError, TypeError, AttributeError) as error:
        raise RecordingContractRefused(status=503) from error
    if (
        type(result["version"]) is not int
        or result["version"] != 1
        or result["epoch_ref"] != str(epoch.pk)
        or str(capture) != result["capture_ref"]
        or result["state"] not in ("pending", "attempted", "confirmed", "blocked")
        or result["media_durability_proven"] is not False
    ):
        raise RecordingContractRefused(status=503)
    return capture, result["state"] == "blocked"


def _finish(epoch, capture_ref, blocked):
    """An expired claimant cannot overwrite another worker's delivery result."""
    now = timezone.now()
    return models.MastraoRtcTrackEpoch.objects.filter(
        pk=epoch.pk,
        native_admission_claim=epoch.native_admission_claim,
        native_admission_claim_until__gt=now,
    ).update(
        native_admission_capture_ref=capture_ref,
        native_admission_blocked=blocked,
        native_admission_claim=None,
        native_admission_claim_until=None,
        native_admission_next_at=now + timedelta(seconds=30),
        updated_at=now,
    )


def reconcile_native_admissions(limit=20, connection_id=None):
    """Bounded independent deliveries, safe to retry from the existing scheduler."""
    if not admission_enabled():
        return 0
    admitted = 0
    for _ in range(max(0, min(limit, 20))):
        epoch = _claim_next(connection_id)
        if epoch is None:
            break
        capture_ref, blocked = None, False
        try:
            capture_ref, blocked = _send(epoch)
        except RecordingContractRefused as error:
            # Authority denials need a fresh epoch/authority, not a retry storm.
            blocked = error.status in (400, 403, 404, 409)
        committed = _finish(epoch, capture_ref, blocked)
        admitted += int(bool(committed and capture_ref and not blocked))
    return admitted


def wake_native_admissions(connection_id):
    """Never execute the synchronous task fallback in the webhook request."""
    if not admission_enabled() or not settings.CELERY_ENABLED:
        return
    from core.tasks.native_capture import (  # noqa: PLC0415  # pylint: disable=cyclic-import
        process_native_admissions,
    )

    try:
        process_native_admissions.delay(str(connection_id))
    except Exception:  # noqa: BLE001  # Broker loss must not undo committed RTC intake.
        # No payload/credential logging; the epoch remains durable and pending.
        logger.warning("Native admission wakeup unavailable; reconciler will retry")
