"""Private adapter for exact canonical Mastrao recording effects."""

# Generated LiveKit symbols and strict binding predicates are intentionally explicit.
# pylint: disable=no-member,too-many-boolean-expressions,too-many-branches,missing-function-docstring

import json
import logging
from datetime import UTC, datetime, timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from asgiref.sync import async_to_sync
from livekit import api as livekit_api

from core import models, utils
from core.mastrao_recording_contract import (
    RecordingContractRefused,
    build_start_receipt_claims,
    build_stop_receipt_claims,
    compact_digest,
    sign_start_receipt,
    sign_stop_receipt,
    verify_recording_start_effect,
    verify_recording_stop_effect,
)
from core.mastrao_recording_failure import report_mastrao_recording_failure
from core.mastrao_video_roster import authorize_video_start
from core.recording.worker.exceptions import (
    RecordingStopError,
    WorkerConnectionError,
    WorkerRequestError,
    WorkerResponseError,
)
from core.recording.worker.factories import get_worker_service
from core.recording.worker.mediator import WorkerServiceMediator
from core.services.room_management import (
    RoomManagement,
    RoomManagementException,
    RoomNotFoundException,
)

logger = logging.getLogger(__name__)

MAX_BODY_BYTES = 32_768
PROVIDER_REGISTRATION_GRACE_SECONDS = 30
# A process lost during StartEgress cannot persist its completion time. Allow
# the provider request this much time before starting the registration grace.
PROVIDER_START_RECOVERY_SECONDS = 60
VIDEO_START_REFUSED = "video_start_refused"

ACTIVE_EGRESS_STATES = {
    livekit_api.EgressStatus.EGRESS_STARTING,
    livekit_api.EgressStatus.EGRESS_ACTIVE,
}
STOPPABLE_EGRESS_STATES = {
    livekit_api.EgressStatus.EGRESS_STARTING,
    livekit_api.EgressStatus.EGRESS_ACTIVE,
}
TERMINAL_EGRESS_STATES = {
    livekit_api.EgressStatus.EGRESS_ENDING,
    livekit_api.EgressStatus.EGRESS_COMPLETE,
    livekit_api.EgressStatus.EGRESS_ABORTED,
    livekit_api.EgressStatus.EGRESS_FAILED,
    livekit_api.EgressStatus.EGRESS_LIMIT_REACHED,
}


def _safe_response(payload, status=200):
    return JsonResponse(
        payload,
        status=status,
        headers={
            "Cache-Control": "private, no-store",
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
        },
    )


def _read_effect(request, field, verifier):
    declared = request.headers.get("content-length")
    if (
        request.content_type != "application/json"
        or declared is None
        or not declared.isdecimal()
        or int(declared) > MAX_BODY_BYTES
        or len(request.body) > MAX_BODY_BYTES
    ):
        raise RecordingContractRefused()
    try:
        body = json.loads(request.body)
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as error:
        raise RecordingContractRefused() from error
    if not isinstance(body, dict) or set(body) != {field}:
        raise RecordingContractRefused()
    return verifier(body[field])


def _binding(effect, *, lock=False):
    queryset = models.MastraoRoomBinding.objects.select_related("room", "owner")
    if lock:
        queryset = queryset.select_for_update()
    binding = queryset.filter(
        meeting_ref=effect["meeting_ref"], room_ref=effect["room_ref"]
    ).first()
    if (
        binding is None
        or binding.provider_binding_digest != effect["provider_binding_digest"]
        or binding.closing_at is not None
        or hasattr(binding, "closure")
    ):
        raise RecordingContractRefused()
    return binding


def _exact_effect(existing, effect, operation):
    if (
        existing.effect_key != effect["effect_key"]
        or existing.operation != operation
        or existing.arguments_digest != effect["arguments_digest"]
        or existing.effect_jti != effect["jti"]
    ):
        raise RecordingContractRefused(status=409)
    return existing


@async_to_sync
async def _list_room_egresses(room_name):
    client = utils.create_livekit_client(settings.LIVEKIT_CONFIGURATION)
    try:
        return await client.egress.list_egress(
            livekit_api.ListEgressRequest(room_name=room_name)
        )
    except (livekit_api.TwirpError, OSError) as error:
        raise RecordingContractRefused(status=503) from error
    finally:
        await client.aclose()


def _exact_provider_egress(recording):
    """Find only the Egress whose immutable output key names this recording."""

    expected_key = recording.key
    matches = []
    for egress in _list_room_egresses(str(recording.room_id)).items:
        outputs = getattr(getattr(egress, "room_composite", None), "file_outputs", ())
        if egress.room_name == str(recording.room_id) and any(
            output.filepath == expected_key for output in outputs
        ):
            matches.append(egress)
    if len(matches) > 1:
        raise RecordingContractRefused(status=409)
    return matches[0] if matches else None


def _registration_grace_started_at(start_effect):
    finished_at = start_effect.receipt_claims.get("start_attempt_finished_at")
    if isinstance(finished_at, (int, float)) and not isinstance(finished_at, bool):
        return datetime.fromtimestamp(finished_at, tz=UTC)
    if start_effect.applied_at is not None:
        return start_effect.applied_at
    attempted_at = start_effect.receipt_claims.get("start_attempted_at")
    if isinstance(attempted_at, (int, float)) and not isinstance(attempted_at, bool):
        return datetime.fromtimestamp(attempted_at, tz=UTC) + timedelta(
            seconds=PROVIDER_START_RECOVERY_SECONDS
        )
    return start_effect.created_at + timedelta(seconds=PROVIDER_START_RECOVERY_SECONDS)


def _provider_registration_started_at(recording_binding):
    start_effect = (
        models.MastraoRecordingEffect.objects.filter(
            recording_binding=recording_binding,
            operation=models.MastraoRecordingEffect.Operation.START,
        )
        .order_by("created_at")
        .first()
    )
    if start_effect is None:
        return recording_binding.created_at
    return _registration_grace_started_at(start_effect)


def _provider_registration_timed_out(recording_binding):
    started_at = _provider_registration_started_at(recording_binding)
    return timezone.now() - started_at >= timedelta(
        seconds=PROVIDER_REGISTRATION_GRACE_SECONDS
    )


def fail_stale_starting_provider_egress(recording_binding, recording):
    if not _provider_registration_timed_out(recording_binding):
        return False
    return report_mastrao_recording_failure(recording, None)


def _record_start_attempt_finished(local_effect):
    with transaction.atomic():
        locked = models.MastraoRecordingEffect.objects.select_for_update().get(
            pk=local_effect.pk
        )
        if (
            locked.state != models.MastraoRecordingEffect.State.APPLYING
            or locked.receipt_claims.get("claim_id")
            != local_effect.receipt_claims.get("claim_id")
        ):
            return
        locked.receipt_claims = {
            **locked.receipt_claims,
            "start_attempt_finished_at": timezone.now().timestamp(),
        }
        locked.save(update_fields=["receipt_claims", "updated_at"])


def _confirm_discovered_start(recording, provider_egress):
    if provider_egress.status in {
        livekit_api.EgressStatus.EGRESS_FAILED,
        livekit_api.EgressStatus.EGRESS_ABORTED,
    }:
        report_mastrao_recording_failure(recording, None)
        raise RecordingContractRefused(status=503)
    if provider_egress.status not in ACTIVE_EGRESS_STATES:
        raise RecordingContractRefused(status=503)
    return provider_egress.egress_id


def _start_provider(recording, recording_binding, local_effect, effect):
    authorization = authorize_video_start(effect, recording_binding, recording)
    if not authorization["authorized"]:
        _persist_video_refusal(local_effect, recording_binding, effect, authorization)
        raise RecordingContractRefused(status=409)
    try:
        return get_worker_service(
            mode=models.RecordingModeChoices.SCREEN_RECORDING
        ).start(str(recording.room_id), recording.pk)
    except (WorkerRequestError, WorkerConnectionError, WorkerResponseError) as error:
        provider_egress = _exact_provider_egress(recording)
        if provider_egress is None:
            raise RecordingContractRefused(status=503) from error
        return _confirm_discovered_start(recording, provider_egress)
    finally:
        _record_start_attempt_finished(local_effect)


def publish_recording_start(binding_id, provider_ref, observation):
    """Commit the provider registration and its signed receipt together."""
    with transaction.atomic():
        binding = models.MastraoRecordingBinding.objects.select_for_update().get(
            pk=binding_id
        )
        if binding.state in {
            models.MastraoRecordingBinding.State.CANCELLED,
            models.MastraoRecordingBinding.State.FAILED,
            models.MastraoRecordingBinding.State.FINALIZED,
        }:
            raise RecordingContractRefused(status=409)
        start_effect = models.MastraoRecordingEffect.objects.select_for_update().get(
            recording_binding=binding,
            operation=models.MastraoRecordingEffect.Operation.START,
        )
        if start_effect.state == models.MastraoRecordingEffect.State.APPLIED:
            return sign_start_receipt(start_effect.receipt_claims)
        if start_effect.state != models.MastraoRecordingEffect.State.APPLYING:
            raise RecordingContractRefused(status=409)
        recording = models.Recording.objects.select_for_update().get(
            pk=binding.recording_id
        )
        if recording.worker_id and recording.worker_id != provider_ref:
            raise RecordingContractRefused(status=409)
        claims = build_start_receipt_claims(
            {
                "organization_external_id": binding.organization_external_id,
                "meeting_ref": binding.meeting_ref,
                "room_ref": binding.room_ref,
                "recording_ref": binding.recording_ref,
                "provider_binding_digest": binding.provider_binding_digest,
                "effect_key": start_effect.effect_key,
                "arguments_digest": start_effect.arguments_digest,
                "jti": start_effect.effect_jti,
            },
            provider_ref,
            observation,
        )
        receipt = sign_start_receipt(claims)
        recording.worker_id = provider_ref
        recording.status = models.RecordingStatusChoices.ACTIVE
        recording.save(update_fields=["worker_id", "status", "updated_at"])
        binding.provider_recording_ref = provider_ref
        binding.state = models.MastraoRecordingBinding.State.ACTIVE
        binding.save(update_fields=["provider_recording_ref", "state", "updated_at"])
        start_effect.state = models.MastraoRecordingEffect.State.APPLIED
        start_effect.provider_observation = observation
        start_effect.receipt_claims = claims
        start_effect.receipt_digest = compact_digest(receipt)
        start_effect.applied_at = timezone.now()
        start_effect.save()
        return receipt


@transaction.atomic
def _prepare_start(effect):
    room_binding = _binding(effect, lock=True)
    recording_binding = (
        models.MastraoRecordingBinding.objects.select_for_update(of=("self",))
        .select_related("recording")
        .filter(room_binding=room_binding)
        .first()
    )
    if recording_binding:
        if (
            recording_binding.organization_external_id
            != effect["organization_external_id"]
            or recording_binding.recording_ref != effect["recording_ref"]
            or recording_binding.provider_binding_digest
            != effect["provider_binding_digest"]
            or recording_binding.policy_ref != effect["policy_ref"]
            or recording_binding.notice_version != effect["notice_version"]
            or recording_binding.notice_digest != effect["notice_digest"]
            or recording_binding.purpose != effect["purpose"]
            or recording_binding.scope != effect["scope"]
            or int(recording_binding.retention_expires_at.timestamp())
            != effect["retention_expires_at"]
        ):
            raise RecordingContractRefused(status=409)
    else:
        recording_binding = models.MastraoRecordingBinding.objects.create(
            room_binding=room_binding,
            organization_external_id=effect["organization_external_id"],
            meeting_ref=effect["meeting_ref"],
            room_ref=effect["room_ref"],
            recording_ref=effect["recording_ref"],
            provider_binding_digest=effect["provider_binding_digest"],
            policy_ref=effect["policy_ref"],
            notice_version=effect["notice_version"],
            notice_digest=effect["notice_digest"],
            purpose=effect["purpose"],
            scope=effect["scope"],
            retention_expires_at=datetime.fromtimestamp(
                effect["retention_expires_at"], tz=UTC
            ),
        )
    existing = (
        models.MastraoRecordingEffect.objects.select_for_update()
        .filter(recording_binding=recording_binding, operation="start")
        .first()
    )
    if existing:
        _exact_effect(existing, effect, "start")
        if (
            existing.state == models.MastraoRecordingEffect.State.PENDING
            and existing.provider_observation == VIDEO_START_REFUSED
            and existing.receipt_claims.get("claim_id") != effect["claim_id"]
            and not effect["resolve_only"]
            and recording_binding.recording is not None
            and recording_binding.recording.status
            == models.RecordingStatusChoices.INITIATED
            and settings.MASTRAO_MEETING_RECORDING_ENABLED
            and settings.MASTRAO_MEETING_RECORDING_START_ENABLED
        ):
            existing.state = models.MastraoRecordingEffect.State.APPLYING
            existing.provider_observation = None
            existing.receipt_claims = {
                "claim_id": effect["claim_id"],
                "start_attempted_at": int(timezone.now().timestamp()),
            }
            existing.save(
                update_fields=[
                    "state",
                    "provider_observation",
                    "receipt_claims",
                    "updated_at",
                ]
            )
            recording_binding.state = models.MastraoRecordingBinding.State.STARTING
            recording_binding.save(update_fields=["state", "updated_at"])
            return recording_binding, existing, True
        return recording_binding, existing, False
    if not (
        settings.MASTRAO_MEETING_RECORDING_ENABLED
        and settings.MASTRAO_MEETING_RECORDING_START_ENABLED
    ):
        raise RecordingContractRefused()
    created = models.MastraoRecordingEffect.objects.create(
        recording_binding=recording_binding,
        effect_key=effect["effect_key"],
        operation="start",
        arguments_digest=effect["arguments_digest"],
        effect_jti=effect["jti"],
        state=models.MastraoRecordingEffect.State.APPLYING,
        receipt_claims={
            "claim_id": effect["claim_id"],
            "start_attempted_at": int(timezone.now().timestamp()),
        },
    )
    recording = models.Recording.objects.create(
        room=room_binding.room,
        mode=models.RecordingModeChoices.SCREEN_RECORDING,
        options={
            "mastrao_recording_ref": recording_binding.recording_ref,
            "mastrao_retention_expires_at": int(
                recording_binding.retention_expires_at.timestamp()
            ),
        },
    )
    models.RecordingAccess.objects.create(
        user=room_binding.owner,
        role=models.RoleChoices.OWNER,
        recording=recording,
    )
    recording_binding.recording = recording
    recording_binding.state = models.MastraoRecordingBinding.State.STARTING
    recording_binding.save(update_fields=["recording", "state", "updated_at"])
    return recording_binding, created, True


@transaction.atomic
def _persist_video_refusal(local_effect, recording_binding, effect, authorization):
    """Journal a definite no-dispatch decision, retaining the refused claim."""
    # Lock binding before effect, matching preparation's lock order. A refusal
    # has no provider registration to time out while waiting for another click.
    models.MastraoRecordingBinding.objects.filter(pk=recording_binding.pk).update(
        state=models.MastraoRecordingBinding.State.PREPARED,
    )
    locked = models.MastraoRecordingEffect.objects.select_for_update().get(
        pk=local_effect.pk
    )
    if locked.state != models.MastraoRecordingEffect.State.APPLYING:
        raise RecordingContractRefused(status=409)
    locked.state = models.MastraoRecordingEffect.State.PENDING
    locked.provider_observation = VIDEO_START_REFUSED
    # Only APPLIED rows contain a signed provider receipt. A pending row keeps
    # Core's refusal so this claim cannot be mistaken for a new manual click.
    locked.receipt_claims = {
        "claim_id": effect["claim_id"],
        "start_authorization": authorization,
    }
    locked.save(
        update_fields=["state", "provider_observation", "receipt_claims", "updated_at"]
    )


def _apply_start(effect):
    recording_binding, local_effect, first_delivery = _prepare_start(effect)
    if local_effect.state == models.MastraoRecordingEffect.State.APPLIED:
        return sign_start_receipt(local_effect.receipt_claims)
    if (
        local_effect.state == models.MastraoRecordingEffect.State.PENDING
        or recording_binding.state == models.MastraoRecordingBinding.State.FAILED
    ):
        raise RecordingContractRefused(status=409)

    recording = models.Recording.objects.select_related("room").get(
        pk=recording_binding.recording_id
    )
    observation = "already_active"
    provider_egress = None if first_delivery else _exact_provider_egress(recording)
    if provider_egress is not None:
        provider_ref = _confirm_discovered_start(recording, provider_egress)
    elif effect["resolve_only"]:
        if timezone.now() - _registration_grace_started_at(local_effect) >= timedelta(
            seconds=PROVIDER_REGISTRATION_GRACE_SECONDS
        ):
            # A previously accepted start with no matching provider Egress is
            # terminal after the provider registration grace period.
            report_mastrao_recording_failure(recording, None)
        raise RecordingContractRefused(status=503)
    elif not first_delivery:
        raise RecordingContractRefused(status=503)
    elif recording.status == models.RecordingStatusChoices.INITIATED:
        provider_ref = _start_provider(
            recording, recording_binding, local_effect, effect
        )
        observation = "started"
    elif recording.status != models.RecordingStatusChoices.ACTIVE:
        raise RecordingContractRefused(status=409)
    else:
        provider_ref = recording.worker_id

    receipt = publish_recording_start(recording_binding.pk, provider_ref, observation)
    if observation == "started":
        mode = recording.options.get("original_mode") or recording.mode
        try:
            RoomManagement().update_metadata(
                str(recording.room_id),
                {"recording_mode": mode, "recording_status": "starting"},
            )
        except RoomNotFoundException:
            logger.info("LiveKit room %s no longer exists", recording.room_id)
        except RoomManagementException:
            logger.exception("Failed to update recording room metadata")
    return receipt


@transaction.atomic
def _prepare_stop(effect):
    room_binding = (
        models.MastraoRoomBinding.objects.select_for_update()
        .filter(meeting_ref=effect["meeting_ref"], room_ref=effect["room_ref"])
        .first()
    )
    if (
        room_binding is None
        or room_binding.provider_binding_digest != effect["provider_binding_digest"]
    ):
        raise RecordingContractRefused()
    recording_binding = (
        models.MastraoRecordingBinding.objects.select_for_update(of=("self",))
        .select_related("recording")
        .filter(room_binding=room_binding, recording_ref=effect["recording_ref"])
        .first()
    )
    if (
        recording_binding is None
        or recording_binding.provider_recording_ref != effect["provider_recording_ref"]
    ):
        raise RecordingContractRefused()
    existing = (
        models.MastraoRecordingEffect.objects.select_for_update()
        .filter(recording_binding=recording_binding, operation="stop")
        .first()
    )
    if existing:
        return recording_binding, _exact_effect(existing, effect, "stop"), False
    created = models.MastraoRecordingEffect.objects.create(
        recording_binding=recording_binding,
        effect_key=effect["effect_key"],
        operation="stop",
        arguments_digest=effect["arguments_digest"],
        effect_jti=effect["jti"],
        state=models.MastraoRecordingEffect.State.APPLYING,
    )
    recording_binding.state = models.MastraoRecordingBinding.State.STOPPING
    recording_binding.save(update_fields=["state", "updated_at"])
    return recording_binding, created, True


def _apply_stop(effect):
    recording_binding, local_effect, _first_delivery = _prepare_stop(effect)
    if local_effect.state == models.MastraoRecordingEffect.State.APPLIED:
        return sign_stop_receipt(local_effect.receipt_claims)
    recording = recording_binding.recording
    if recording is None:
        raise RecordingContractRefused(status=409)
    provider_egress = _exact_provider_egress(recording)
    observation = None
    if hasattr(recording_binding.room_binding, "closure"):
        observation = "room_ended"
    elif (
        provider_egress is not None and provider_egress.status in TERMINAL_EGRESS_STATES
    ):
        observation = "already_stopped"
    elif (
        provider_egress is not None
        and provider_egress.status in STOPPABLE_EGRESS_STATES
    ):
        recording.status = models.RecordingStatusChoices.ACTIVE
        recording.save(update_fields=["status", "updated_at"])
    if recording.status == models.RecordingStatusChoices.ACTIVE and observation not in {
        "room_ended",
        "already_stopped",
    }:
        try:
            WorkerServiceMediator(get_worker_service(mode=recording.mode)).stop(
                recording
            )
        except RecordingStopError as error:
            provider_egress = _exact_provider_egress(recording)
            if (
                provider_egress is None
                or provider_egress.status not in TERMINAL_EGRESS_STATES
            ):
                local_effect.state = models.MastraoRecordingEffect.State.PENDING
                local_effect.save(update_fields=["state", "updated_at"])
                raise RecordingContractRefused(status=503) from error
            observation = "already_stopped"
        else:
            observation = "stopped"
    elif observation not in {"room_ended", "already_stopped"} and recording.status in {
        models.RecordingStatusChoices.STOPPED,
        models.RecordingStatusChoices.SAVED,
        models.RecordingStatusChoices.NOTIFICATION_SUCCEEDED,
        models.RecordingStatusChoices.EXTERNAL_PROCESS_SUCCESSFUL,
        models.RecordingStatusChoices.EXTERNAL_PROCESS_FAILED,
    }:
        observation = "already_stopped"
    elif observation not in {"room_ended", "already_stopped"}:
        raise RecordingContractRefused(status=409)

    claims = build_stop_receipt_claims(effect, observation)
    compact = sign_stop_receipt(claims)
    with transaction.atomic():
        locked_effect = models.MastraoRecordingEffect.objects.select_for_update().get(
            pk=local_effect.pk
        )
        if locked_effect.state == models.MastraoRecordingEffect.State.APPLIED:
            return sign_stop_receipt(locked_effect.receipt_claims)
        locked_effect.state = models.MastraoRecordingEffect.State.APPLIED
        locked_effect.provider_observation = observation
        locked_effect.receipt_claims = claims
        locked_effect.receipt_digest = compact_digest(compact)
        locked_effect.applied_at = timezone.now()
        locked_effect.save()
        models.MastraoRecordingBinding.objects.filter(pk=recording_binding.pk).update(
            state=models.MastraoRecordingBinding.State.PROCESSING
        )
    return compact


def _handle(request, field, verifier, applier, response_field):
    try:
        effect = _read_effect(request, field, verifier)
        return _safe_response({response_field: applier(effect)})
    except RecordingContractRefused as error:
        return _safe_response(
            {"message": "Not found" if error.status == 404 else "Unavailable"},
            error.status,
        )
    except (IntegrityError, ValidationError):
        return _safe_response({"message": "Unavailable"}, 409)


@csrf_exempt
@require_POST
def start_mastrao_recording(request):
    return _handle(
        request,
        "recording_start_effect",
        verify_recording_start_effect,
        _apply_start,
        "recording_start_receipt",
    )


@csrf_exempt
@require_POST
def stop_mastrao_recording(request):
    return _handle(
        request,
        "recording_stop_effect",
        verify_recording_stop_effect,
        _apply_stop,
        "recording_stop_receipt",
    )
