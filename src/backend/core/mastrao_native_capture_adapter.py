"""Core-signed native start consumer, opt-in and independent of video finalization.

The committed row is a send-once fence, not a lease that permits another start.
A crash before/during/after sending leaves an uncertain intent. Only an exact
provider observation can resolve it; absence never authorizes retransmission.
"""

# Generated LiveKit protobuf members and fail-closed contract checks are intentional here.
# pylint: disable=no-member,too-many-boolean-expressions

import asyncio
import re
from datetime import UTC, datetime
from pathlib import PurePosixPath

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import DatabaseError, IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from asgiref.sync import async_to_sync
from livekit import api

from core import models, utils
from core.mastrao_native_capture_contract import (
    PROFILE,
    require_native_receipt_signer,
    sign_native_receipt,
    verify_native_start,
)
from core.mastrao_recording_adapter import _read_effect, _safe_response
from core.mastrao_recording_contract import RecordingContractRefused


def _spool_prefix(capture_ref):
    root = settings.MASTRAO_NATIVE_CAPTURE_SPOOL_ROOT
    # Operator-owned absolute path only. The runtime must mount a bounded volume
    # at this location; validating a string is NOT a disk-quota/durability proof.
    if (
        not isinstance(root, str)
        or not re.fullmatch(r"/[A-Za-z0-9_/-]{1,240}", root)
        or ".." in PurePosixPath(root).parts
        or len(PurePosixPath(root).parts) < 3
    ):
        raise RecordingContractRefused(status=503)
    return f"{root.rstrip('/')}/{capture_ref}"


def _assert_epoch_authority(epoch, binding, effect):
    connection = epoch.connection
    issued = connection.media_token_binding
    if (
        connection.room_binding_id != binding.pk
        or connection.correlation != "correlated"
        or connection.ended
        or epoch.ended
        or epoch.conflict
        or issued is None
        or str(issued.pk) != effect["media_token_binding_ref"]
        or issued.room_binding_id != binding.pk
        or connection.room_sid != effect["room_sid"]
        or connection.participant_sid != effect["participant_sid"]
        or epoch.track_sid != effect["track_sid"]
        or epoch.first_publication is None
        or epoch.first_publication.track_type != api.TrackType.AUDIO
        or epoch.first_publication.track_source != api.TrackSource.MICROPHONE
        or issued.grant_digest != effect["grant_digest"]
        or issued.session_nonce_digest != effect["session_nonce_digest"]
    ):
        raise RecordingContractRefused()
    # An expired RTC join token does not invalidate an established connection.
    # Core must separately authorize this exact grant/session in the fresh effect.
    if effect["participant_kind"] == "host":
        grant = issued.host_grant
        if grant is None or not grant.identity.user.is_active:
            raise RecordingContractRefused()
        participant_ref = grant.identity.host_ref
    else:
        grant = issued.guest_grant
        if (
            grant is None
            or grant.organization_external_id != effect["organization_external_id"]
            or grant.admission_state != models.MastraoGuestGrant.AdmissionState.ALLOWED
            or grant.decision_allow is not True
            or grant.decision_confirmed_at is None
            or not grant.decision_receipt_digest
        ):
            raise RecordingContractRefused()
        participant_ref = grant.guest_ref
    if (
        participant_ref != effect["participant_ref"]
        or grant.grant_ref != effect["grant_ref"]
        or grant.grant_digest != issued.grant_digest
        or grant.session_nonce_digest != issued.session_nonce_digest
        or grant.room_binding_id != binding.pk
        or grant.meeting_ref != binding.meeting_ref
        or grant.room_ref != binding.room_ref
        or grant.provider_binding_digest != binding.provider_binding_digest
    ):
        raise RecordingContractRefused()


@transaction.atomic(durable=True)
def _prepare(effect):
    binding = (
        models.MastraoRoomBinding.objects.select_for_update()
        .filter(
            meeting_ref=effect["meeting_ref"],
            room_ref=effect["room_ref"],
            provider_binding_digest=effect["provider_binding_digest"],
        )
        .first()
    )
    if binding is None:
        raise RecordingContractRefused()
    previous = models.MastraoNativeCaptureStart.objects.filter(
        Q(effect_key=effect["effect_key"])
        | Q(capture_ref=effect["capture_ref"])
        | Q(epoch_id=effect["epoch_ref"])
    ).first()
    if previous is not None:
        if (
            previous.effect_key != effect["effect_key"]
            or str(previous.capture_ref) != effect["capture_ref"]
            or str(previous.epoch_id) != effect["epoch_ref"]
            or previous.arguments_digest != effect["arguments_digest"]
            or previous.organization_external_id != effect["organization_external_id"]
        ):
            raise RecordingContractRefused(status=409)
        # Reconciliation stays available after closure/flag rollback: no new start.
        return previous, False
    if (
        effect["resolve_only"]
        or not settings.MASTRAO_NATIVE_CAPTURE_START_ENABLED
        or not settings.MASTRAO_MEDIA_TOKEN_BINDING_ENABLED
        or binding.closing_at is not None
        or hasattr(binding, "closure")
    ):
        raise RecordingContractRefused()
    epoch = (
        models.MastraoRtcTrackEpoch.objects.select_related(
            "connection__media_token_binding__host_grant__identity__user",
            "connection__media_token_binding__guest_grant",
            "first_publication",
        )
        .filter(pk=effect["epoch_ref"])
        .first()
    )
    if epoch is None:
        raise RecordingContractRefused()
    _assert_epoch_authority(epoch, binding, effect)
    require_native_receipt_signer()
    return models.MastraoNativeCaptureStart.objects.create(
        epoch=epoch,
        capture_ref=effect["capture_ref"],
        effect_key=effect["effect_key"],
        arguments_digest=effect["arguments_digest"],
        organization_external_id=effect["organization_external_id"],
        output_prefix=_spool_prefix(effect["capture_ref"]),
        retention_expires_at=datetime.fromtimestamp(
            effect["retention_expires_at"], UTC
        ),
    ), True


def native_request(intent):
    """Pinned AAC/HLS profile; no video, arbitrary URL or cloud upload credentials."""
    return api.TrackCompositeEgressRequest(
        room_name=str(intent.epoch.connection.room_binding.room_id),
        audio_track_id=intent.epoch.track_sid,
        advanced=api.EncodingOptions(
            audio_codec=api.AudioCodec.AAC,
            audio_bitrate=PROFILE["bitrate_kbps"],
            audio_frequency=PROFILE["frequency_hz"],
        ),
        segment_outputs=[
            api.SegmentedFileOutput(
                filename_prefix=f"{intent.output_prefix}/audio",
                playlist_name=f"{intent.output_prefix}/index.m3u8",
                segment_duration=PROFILE["segment_seconds"],
            )
        ],
    )


def _matches(job, request, room_sid):
    return (
        job.room_id == room_sid
        and job.room_name == request.room_name
        and job.HasField("track_composite")
        and job.track_composite == request
        and re.fullmatch(r"EG_[A-Za-z0-9]{1,96}", job.egress_id) is not None
    )


@async_to_sync
async def _provider_observation(intent, first_delivery):
    request = native_request(intent)
    client = None
    try:
        # SDK failover can replay POST bodies on transport errors/5xx. A start
        # has no provider idempotency key: use our read-only resolution instead.
        client = utils.create_livekit_client(
            {**settings.LIVEKIT_CONFIGURATION, "failover": False}
        )
        if first_delivery:
            job = await asyncio.wait_for(
                client.egress.start_track_composite_egress(request),
                timeout=35,
            )
            if not _matches(job, request, intent.epoch.connection.room_sid):
                raise RecordingContractRefused(status=503)
            return job
        result = await asyncio.wait_for(
            client.egress.list_egress(
                api.ListEgressRequest(room_name=request.room_name)
            ),
            timeout=5,
        )
        matches = [
            job
            for job in result.items
            if _matches(
                job,
                request,
                intent.epoch.connection.room_sid,
            )
        ]
        if len(matches) != 1:
            raise RecordingContractRefused(status=409 if matches else 503)
        return matches[0]
    except RecordingContractRefused:
        raise
    except Exception:  # noqa: BLE001 - upstream errors can contain credentials.
        raise RecordingContractRefused(status=503) from None
    finally:
        if client is not None:
            try:
                await asyncio.wait_for(client.aclose(), timeout=5)
            except Exception:  # noqa: BLE001 - retain uncertain intent, sanitize close errors.
                raise RecordingContractRefused(status=503) from None


def apply_native_start(effect):
    """Persist intent before external I/O, then acknowledge only a persisted ID."""
    intent, first_delivery = _prepare(effect)
    if intent.receipt_claims:
        return sign_native_receipt(intent.receipt_claims)
    # Load related rows outside the async callback. Async Django ORM is prohibited.
    intent = models.MastraoNativeCaptureStart.objects.select_related(
        "epoch__connection__room_binding",
    ).get(pk=intent.pk)
    # A stop latched before dispatch forbids a first send. A stop racing an
    # already in-flight send is retained and resolved by the drain reconciler.
    if intent.stop_requested_at is not None:
        first_delivery = False
    job = _provider_observation(intent, first_delivery)
    with transaction.atomic():
        locked = models.MastraoNativeCaptureStart.objects.select_for_update().get(
            pk=intent.pk
        )
        if locked.provider_job_ref and locked.provider_job_ref != job.egress_id:
            raise RecordingContractRefused(status=409)
        if not locked.receipt_claims:
            locked.provider_job_ref = job.egress_id
            locked.receipt_claims = {
                "version": 1,
                "type": "mastrao.native-microphone-start-receipt",
                "issuer": settings.MASTRAO_RECORDING_RECEIPT_ISSUER,
                "audience": settings.MASTRAO_RECORDING_RECEIPT_AUDIENCE,
                "organization_external_id": effect["organization_external_id"],
                "effect_key": locked.effect_key,
                "arguments_digest": locked.arguments_digest,
                "capture_ref": str(locked.capture_ref),
                "epoch_ref": str(locked.epoch_id),
                "provider_job_ref": job.egress_id,
                "observed_status": int(job.status),
                "observed_at": int(timezone.now().timestamp()),
                "media_durability_proven": False,
            }
            # Fail before commit if receipt signing is unavailable; keep intent uncertain.
            sign_native_receipt(locked.receipt_claims)
            locked.save(
                update_fields=["provider_job_ref", "receipt_claims", "updated_at"]
            )
        elif locked.provider_job_ref != job.egress_id:
            raise RecordingContractRefused(status=409)
        return sign_native_receipt(locked.receipt_claims)


@csrf_exempt
@transaction.non_atomic_requests
@require_POST
def start_native_capture(request):
    """Internal effect surface; never accepts an RTC token or participant status."""
    try:
        effect = _read_effect(request, "native_start_effect", verify_native_start)
        return _safe_response({"native_start_receipt": apply_native_start(effect)})
    except RecordingContractRefused as error:
        return _safe_response({"error": "native_capture_refused"}, status=error.status)
    except (IntegrityError, ValidationError):
        return _safe_response({"error": "native_capture_refused"}, status=409)
    except DatabaseError:
        return _safe_response({"error": "native_capture_unavailable"}, status=503)
