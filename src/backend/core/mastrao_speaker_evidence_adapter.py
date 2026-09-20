"""Private adapter for signed Mastrao speaker evidence capture effects."""

# Generated LiveKit protobuf members and fail-closed contract checks are intentional here.
# pylint: disable=no-member

import hashlib
import hmac
import json
import os
import secrets
import time
import uuid

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import transaction
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from asgiref.sync import async_to_sync
from botocore.exceptions import BotoCoreError, ClientError
from livekit import api

from core import models, utils
from core.mastrao_core_http import post_core_json
from core.mastrao_recording_contract import RecordingContractRefused
from core.mastrao_room_contract import _sha256_canonical
from core.mastrao_speaker_evidence_contract import (
    build_capture_receipt_claims,
    refresh_artifact_receipt_claims,
    sign_artifact_receipt,
    sign_capture_receipt,
    validate_artifact_receipt_claims,
    verify_speaker_evidence_capture_effect,
)
from core.recording.services.metadata_collector import MetadataCollectorService

MAX_BODY_BYTES = 32_768
SPEAKER_EVIDENCE_DISPATCH_KEY = "mastrao_speaker_evidence_dispatch_id"
SPEAKER_EVIDENCE_DISPATCH_PENDING = "pending"
SPEAKER_EVIDENCE_PENDING_LEASE_SECONDS = 60
SPEAKER_EVIDENCE_RECEIPT_SUFFIX = ".receipt.json"
SPEAKER_EVIDENCE_SIDE_CAR_FIELDS = {
    "speaker_evidence_artifact_receipt_claims",
    "speaker_evidence_artifact_receipt_claims_digest",
}
SPEAKER_EVIDENCE_LIVE_CAPTURE_STATES = {
    models.MastraoRecordingBinding.State.STARTING,
    models.MastraoRecordingBinding.State.ACTIVE,
    models.MastraoRecordingBinding.State.STOPPING,
}
SPEAKER_EVIDENCE_REPLAY_ONLY_STATES = {
    models.MastraoRecordingBinding.State.PROCESSING,
    models.MastraoRecordingBinding.State.FINALIZED,
}


def _digest(*parts: str) -> str:
    return hashlib.sha256("\0".join(parts).encode("utf-8")).hexdigest()


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


def _read_capture_effect(request):
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
    if not isinstance(body, dict) or set(body) != {"speaker_evidence_capture_effect"}:
        raise RecordingContractRefused()
    return verify_speaker_evidence_capture_effect(
        body["speaker_evidence_capture_effect"]
    )


def _matches_effect(binding, effect):
    return (
        binding.organization_external_id == effect["organization_external_id"]
        and binding.meeting_ref == effect["meeting_ref"]
        and binding.room_ref == effect["room_ref"]
        and binding.recording_ref == effect["recording_ref"]
        and binding.provider_binding_digest == effect["provider_binding_digest"]
        and binding.policy_ref == effect["policy_ref"]
        and binding.notice_version == effect["notice_version"]
        and binding.notice_digest == effect["notice_digest"]
        and int(binding.retention_expires_at.timestamp())
        == effect["retention_expires_at"]
    )


def _receipt_sidecar_ref(effect):
    return (
        f"mastrao-speaker-evidence/{effect['evidence_ref']}.json"
        f"{SPEAKER_EVIDENCE_RECEIPT_SUFFIX}"
    )


def _artifact_object_ref(effect):
    return f"mastrao-speaker-evidence/{effect['evidence_ref']}.json"


def _canonical_json_bytes(value) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        ).encode("utf-8")
        + b"\n"
    )


def _artifact_claims_match_effect(claims, effect):
    return (
        claims.get("organization_external_id") == effect["organization_external_id"]
        and claims.get("meeting_ref") == effect["meeting_ref"]
        and claims.get("room_ref") == effect["room_ref"]
        and claims.get("recording_ref") == effect["recording_ref"]
        and claims.get("evidence_ref") == effect["evidence_ref"]
        and claims.get("provider_binding_digest") == effect["provider_binding_digest"]
        and claims.get("policy_ref") == effect["policy_ref"]
        and claims.get("notice_version") == effect["notice_version"]
        and claims.get("notice_digest") == effect["notice_digest"]
        and claims.get("purpose") == effect["purpose"]
        and claims.get("scope") == effect["scope"]
        and claims.get("retention_expires_at") == effect["retention_expires_at"]
        and claims.get("object_ref") == _artifact_object_ref(effect)
    )


def _sidecar_digest(claims):
    raw_secret = getattr(settings, "MASTRAO_RECORDING_RECEIPT_PRIVATE_JWK", "")
    if not isinstance(raw_secret, str) or not raw_secret:
        raise RecordingContractRefused(status=503)
    secret = raw_secret.encode("utf-8")
    return hmac.digest(
        secret, _sha256_canonical(claims).encode("ascii"), "sha256"
    ).hex()


def _local_roster_snapshot_enabled():
    return (
        os.getenv("METADATA_COLLECTOR_ENABLE_VAD", "true").lower() == "false"
        and os.getenv("METADATA_COLLECTOR_ENABLE_ROSTER_SNAPSHOT", "false").lower()
        == "true"
    )


def _late_roster_snapshot_fallback_enabled(binding_state):
    return os.getenv("METADATA_COLLECTOR_ENABLE_VAD", "true").lower() == "false" and (
        binding_state
        in {
            models.MastraoRecordingBinding.State.STOPPING,
            models.MastraoRecordingBinding.State.PROCESSING,
            models.MastraoRecordingBinding.State.FINALIZED,
        }
    )


def _bounded_label(raw_label: str):
    label = raw_label.strip()
    if not label:
        return None
    return label[:160]


@async_to_sync
async def _list_livekit_participants(room_id: str):
    lkapi = utils.create_livekit_client()
    try:
        response = await lkapi.room.list_participants(
            api.ListParticipantsRequest(room=room_id)
        )
        return list(response.participants)
    finally:
        await lkapi.aclose()


def _server_roster_participants(recording):
    room_id = str(recording.room.id)
    participants = {}
    for index, participant in enumerate(_list_livekit_participants(room_id), start=1):
        participant_kind = getattr(participant, "kind", None)
        participant_kind_name = getattr(participant_kind, "name", "") or str(
            participant_kind
        )
        if "AGENT" in participant_kind_name:
            continue
        participant_key = (
            getattr(participant, "identity", "")
            or getattr(participant, "sid", "")
            or f"participant:{index}"
        )
        participant_ref = _roster_participant_ref(recording, participant_key)
        exported = {
            "participant_ref": participant_ref,
            "participant_kind": "unknown",
            "participant_session_digest": _digest(
                str(recording.id), participant_ref, "session"
            ),
            "display_name_events": [],
        }
        label = _bounded_label(getattr(participant, "name", "") or "")
        if label is not None:
            exported["declared_label_digest"] = _digest(
                str(recording.id), label, "label"
            )
            exported["display_name_events"].append(
                {
                    "effective_at_ms": 0,
                    "label": label,
                    "source": "meet_display_name",
                }
            )
        participants[participant_ref] = exported
    participants.update(_durable_host_roster_participants(recording, participants))
    participants.update(_durable_guest_roster_participants(recording, participants))
    return list(participants.values())


def _roster_participant_ref(recording, participant_key: str) -> str:
    return f"participant_{_digest(str(recording.id), participant_key)[:32]}"


def _durable_host_roster_participants(recording, existing_participants):
    try:
        room_binding = recording.room.mastrao_binding
    except models.MastraoRoomBinding.DoesNotExist:
        return {}
    participants = {}
    hosts = (
        models.MastraoHostGrant.objects.select_related("identity", "identity__user")
        .filter(room_binding=room_binding)
        .order_by("created_at", "pk")
    )
    for host in hosts:
        # Authenticated RTC tokens use user.sub, not the canonical Core host_ref.
        participant_ref = _roster_participant_ref(
            recording, str(host.identity.user.sub)
        )
        if participant_ref in existing_participants:
            continue
        label = _bounded_label(host.display_name or host.identity.user.full_name or "")
        if label is None:
            continue
        participants[participant_ref] = {
            "participant_ref": participant_ref,
            "participant_kind": "host",
            "participant_session_digest": _digest(
                str(recording.id), participant_ref, "session"
            ),
            "declared_label_digest": _digest(str(recording.id), label, "label"),
            "display_name_events": [
                {
                    "effective_at_ms": 0,
                    "label": label,
                    "source": "meet_display_name",
                }
            ],
        }
    return participants


def _durable_guest_roster_participants(recording, existing_participants):
    try:
        room_binding = recording.room.mastrao_binding
    except models.MastraoRoomBinding.DoesNotExist:
        return {}
    participants = {}
    guests = (
        models.MastraoGuestGrant.objects.filter(
            room_binding=room_binding,
            admission_state=models.MastraoGuestGrant.AdmissionState.ALLOWED,
            decision_allow=True,
            decision_confirmed_at__isnull=False,
        )
        .exclude(display_name__isnull=True)
        .exclude(display_name="")
        .order_by("created_at", "pk")
    )
    for guest in guests:
        participant_ref = _roster_participant_ref(recording, guest.guest_ref)
        if participant_ref in existing_participants:
            continue
        label = _bounded_label(guest.display_name or "")
        if label is None:
            continue
        participants[participant_ref] = {
            "participant_ref": participant_ref,
            "participant_kind": "guest",
            "participant_session_digest": _digest(
                str(recording.id), participant_ref, "session"
            ),
            "declared_label_digest": _digest(str(recording.id), label, "label"),
            "display_name_events": [
                {
                    "effective_at_ms": 0,
                    "label": label,
                    "source": "meet_display_name",
                }
            ],
        }
    return participants


def _server_roster_artifact_claims(effect, payload, data):
    now = int(time.time())
    checksum_digest = hashlib.sha256(data).hexdigest()
    artifact_ref = (
        f"speakerartifact_{_digest(payload['evidence_ref'], checksum_digest)[:32]}"
    )
    return {
        "version": 1,
        "type": "mastrao.meeting-speaker-evidence-artifact-receipt",
        "issuer": settings.MASTRAO_RECORDING_RECEIPT_ISSUER,
        "audience": settings.MASTRAO_RECORDING_RECEIPT_AUDIENCE,
        "operation": "confirm_meeting_speaker_evidence_artifact",
        "operation_version": 1,
        "organization_external_id": effect["organization_external_id"],
        "meeting_ref": effect["meeting_ref"],
        "room_ref": effect["room_ref"],
        "recording_ref": effect["recording_ref"],
        "evidence_ref": effect["evidence_ref"],
        "provider_binding_digest": effect["provider_binding_digest"],
        "policy_ref": effect["policy_ref"],
        "notice_version": effect["notice_version"],
        "notice_digest": effect["notice_digest"],
        "purpose": effect["purpose"],
        "scope": effect["scope"],
        "retention_expires_at": effect["retention_expires_at"],
        "artifact_ref": artifact_ref,
        "object_ref": _artifact_object_ref(effect),
        "byte_size": len(data),
        "checksum_digest": checksum_digest,
        "participant_count": len(payload["participants"]),
        "event_count": len(payload["events"]),
        "timeline_started_at_ms": payload["timeline_started_at_ms"],
        "timeline_ended_at_ms": payload["timeline_ended_at_ms"],
        "region_ref": settings.MASTRAO_RECORDING_REGION_REF,
        "encryption_ref": settings.MASTRAO_RECORDING_ENCRYPTION_REF,
        "lifecycle_policy_ref": settings.MASTRAO_RECORDING_LIFECYCLE_POLICY_REF,
        "issued_at": now,
        "expires_at": now + 30,
        "jti": f"speakerartifact_{_digest(payload['evidence_ref'], str(now))[:32]}",
    }


def _save_server_roster_artifact(recording, effect):
    participants = _server_roster_participants(recording)
    payload = {
        "version": 1,
        "recording_ref": effect["recording_ref"],
        "recording_started_at_ms": effect["recording_started_at_ms"],
        "timeline_started_at_ms": 0,
        "timeline_ended_at_ms": 0,
        "participants": participants,
        "events": [],
        "evidence_ref": effect["evidence_ref"],
        "meeting_ref": effect["meeting_ref"],
        "room_ref": effect["room_ref"],
    }
    event_times = [
        event["effective_at_ms"]
        for participant in participants
        for event in participant.get("display_name_events", [])
    ]
    payload["timeline_started_at_ms"] = min(event_times, default=0)
    payload["timeline_ended_at_ms"] = max(event_times, default=0)
    data = _canonical_json_bytes(payload)
    claims = _server_roster_artifact_claims(effect, payload, data)
    object_ref = claims["object_ref"]
    sidecar_ref = _receipt_sidecar_ref(effect)
    receipt = sign_artifact_receipt(claims)
    sidecar_body = _canonical_json_bytes(
        {
            "speaker_evidence_artifact_receipt_claims": claims,
            "speaker_evidence_artifact_receipt_claims_digest": _sidecar_digest(claims),
        }
    )
    if not default_storage.exists(object_ref):
        default_storage.save(object_ref, ContentFile(data))
    if not default_storage.exists(sidecar_ref):
        default_storage.save(sidecar_ref, ContentFile(sidecar_body))
    result = post_core_json(
        endpoint=settings.MASTRAO_CORE_SPEAKER_EVIDENCE_ARTIFACT_ENDPOINT,
        expected_path="/internal/v1/meetings/speaker-evidence/artifacts/finalize",
        body={"speaker_evidence_artifact_receipt": receipt},
        timeout=settings.MASTRAO_CORE_RECORDING_TIMEOUT_SECONDS,
        refusal=RecordingContractRefused,
        expected_fields={"state", "outcome"},
        passthrough_statuses=frozenset({404, 409, 503}),
    )
    if result["state"] != "available" or result["outcome"] != "available":
        raise RecordingContractRefused(status=503)
    try:
        default_storage.delete(sidecar_ref)
    except (BotoCoreError, ClientError, OSError, ValueError) as error:
        raise RecordingContractRefused(status=503) from error
    return claims["artifact_ref"]


def _verify_sidecar_body(body):
    if not isinstance(body, dict) or set(body) != SPEAKER_EVIDENCE_SIDE_CAR_FIELDS:
        raise RecordingContractRefused(status=503)
    claims = validate_artifact_receipt_claims(
        body["speaker_evidence_artifact_receipt_claims"],
        allow_expired=True,
    )
    provided = body["speaker_evidence_artifact_receipt_claims_digest"]
    if not isinstance(provided, str) or not secrets.compare_digest(
        provided, _sidecar_digest(claims)
    ):
        raise RecordingContractRefused(status=503)
    return claims


def _verify_artifact_object(claims):
    object_ref = claims["object_ref"]
    try:
        with default_storage.open(object_ref, "rb") as stream:
            raw = stream.read(claims["byte_size"] + 1)
    except (BotoCoreError, ClientError, OSError, ValueError) as error:
        raise RecordingContractRefused(status=503) from error
    if len(raw) != claims["byte_size"] or not hmac.compare_digest(
        hashlib.sha256(raw).hexdigest(), claims["checksum_digest"]
    ):
        raise RecordingContractRefused(status=503)


def _pending_dispatch_marker():
    return {
        "state": SPEAKER_EVIDENCE_DISPATCH_PENDING,
        "claimed_at": int(time.time()),
        "claim_id": uuid.uuid4().hex,
    }


def _is_pending_dispatch(value):
    return value == SPEAKER_EVIDENCE_DISPATCH_PENDING or (
        isinstance(value, dict)
        and value.get("state") == SPEAKER_EVIDENCE_DISPATCH_PENDING
    )


def _pending_dispatch_expired(value):
    if value == SPEAKER_EVIDENCE_DISPATCH_PENDING:
        return True
    if not isinstance(value, dict):
        return False
    claimed_at = value.get("claimed_at")
    return (
        not isinstance(claimed_at, int)
        or isinstance(claimed_at, bool)
        or claimed_at + SPEAKER_EVIDENCE_PENDING_LEASE_SECONDS <= int(time.time())
    )


def _replay_artifact_receipt(effect):
    sidecar_ref = _receipt_sidecar_ref(effect)
    try:
        if not default_storage.exists(sidecar_ref):
            return False
        with default_storage.open(sidecar_ref, "rb") as stream:
            raw = stream.read(MAX_BODY_BYTES + 1)
    except (BotoCoreError, ClientError, OSError, ValueError) as error:
        raise RecordingContractRefused(status=503) from error
    if len(raw) > MAX_BODY_BYTES:
        raise RecordingContractRefused(status=503)
    try:
        body = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as error:
        raise RecordingContractRefused(status=503) from error
    claims = _verify_sidecar_body(body)
    if not _artifact_claims_match_effect(claims, effect):
        raise RecordingContractRefused(status=503)
    _verify_artifact_object(claims)
    result = post_core_json(
        endpoint=settings.MASTRAO_CORE_SPEAKER_EVIDENCE_ARTIFACT_ENDPOINT,
        expected_path="/internal/v1/meetings/speaker-evidence/artifacts/finalize",
        body={
            "speaker_evidence_artifact_receipt": sign_artifact_receipt(
                refresh_artifact_receipt_claims(claims)
            )
        },
        timeout=settings.MASTRAO_CORE_RECORDING_TIMEOUT_SECONDS,
        refusal=RecordingContractRefused,
        expected_fields={"state", "outcome"},
        passthrough_statuses=frozenset({404, 409, 503}),
    )
    if result["state"] != "available" or result["outcome"] != "available":
        raise RecordingContractRefused(status=503)
    try:
        default_storage.delete(sidecar_ref)
    except (BotoCoreError, ClientError, OSError, ValueError) as error:
        raise RecordingContractRefused(status=503) from error
    return True


@transaction.atomic
def _claim_recording_for_capture(effect):
    binding = (
        models.MastraoRecordingBinding.objects.select_for_update(of=("self",))
        .select_related("recording__room")
        .filter(
            meeting_ref=effect["meeting_ref"],
            room_ref=effect["room_ref"],
            recording_ref=effect["recording_ref"],
        )
        .first()
    )
    if (
        binding is None
        or binding.recording is None
        or not _matches_effect(binding, effect)
        or binding.state
        not in SPEAKER_EVIDENCE_LIVE_CAPTURE_STATES
        | SPEAKER_EVIDENCE_REPLAY_ONLY_STATES
    ):
        raise RecordingContractRefused(status=503)
    recording = binding.recording
    dispatch_id = recording.options.get(SPEAKER_EVIDENCE_DISPATCH_KEY)
    if binding.state in SPEAKER_EVIDENCE_REPLAY_ONLY_STATES:
        if dispatch_id and not _is_pending_dispatch(dispatch_id):
            return recording, False, None, binding.state
        raise RecordingContractRefused(status=503)
    if dispatch_id:
        if _is_pending_dispatch(dispatch_id) and _pending_dispatch_expired(dispatch_id):
            marker = _pending_dispatch_marker()
            recording.options[SPEAKER_EVIDENCE_DISPATCH_KEY] = marker
            recording.save(update_fields=["options"])
            return recording, True, marker["claim_id"], binding.state
        return recording, False, None, binding.state
    marker = _pending_dispatch_marker()
    recording.options[SPEAKER_EVIDENCE_DISPATCH_KEY] = marker
    recording.save(update_fields=["options"])
    return recording, True, marker["claim_id"], binding.state


@transaction.atomic
def _clear_pending_dispatch(recording, claim_id):
    locked = models.Recording.objects.select_for_update().get(pk=recording.pk)
    current = locked.options.get(SPEAKER_EVIDENCE_DISPATCH_KEY)
    if (
        _is_pending_dispatch(current)
        and isinstance(current, dict)
        and current.get("claim_id") == claim_id
    ):
        locked.options.pop(SPEAKER_EVIDENCE_DISPATCH_KEY, None)
        locked.save(update_fields=["options"])


@transaction.atomic
def _clear_terminal_dispatch(recording):
    locked = models.Recording.objects.select_for_update().get(pk=recording.pk)
    current = locked.options.get(SPEAKER_EVIDENCE_DISPATCH_KEY)
    if current and not _is_pending_dispatch(current):
        locked.options.pop(SPEAKER_EVIDENCE_DISPATCH_KEY, None)
        locked.save(update_fields=["options"])


@transaction.atomic
def _store_terminal_dispatch(recording, value):
    locked = models.Recording.objects.select_for_update().get(pk=recording.pk)
    locked.options[SPEAKER_EVIDENCE_DISPATCH_KEY] = value
    locked.save(update_fields=["options"])


def _capture_metadata(recording, effect):
    return json.dumps(
        {
            "version": 1,
            "recording_id": str(recording.id),
            "meeting_ref": effect["meeting_ref"],
            "room_ref": effect["room_ref"],
            "recording_ref": effect["recording_ref"],
            "evidence_ref": effect["evidence_ref"],
            "organization_external_id": effect["organization_external_id"],
            "object_ref": f"mastrao-speaker-evidence/{effect['evidence_ref']}.json",
            "provider_binding_digest": effect["provider_binding_digest"],
            "policy_ref": effect["policy_ref"],
            "notice_version": effect["notice_version"],
            "notice_digest": effect["notice_digest"],
            "retention_expires_at": effect["retention_expires_at"],
            "recording_started_at_ms": effect["recording_started_at_ms"],
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _apply_capture(effect):
    recording, should_start, claim_id, binding_state = _claim_recording_for_capture(
        effect
    )
    if not should_start:
        if not _is_pending_dispatch(
            recording.options.get(SPEAKER_EVIDENCE_DISPATCH_KEY)
        ):
            if _replay_artifact_receipt(effect):
                return sign_capture_receipt(
                    build_capture_receipt_claims(effect, "already_active")
                )
            if binding_state in {
                models.MastraoRecordingBinding.State.PROCESSING,
                models.MastraoRecordingBinding.State.FINALIZED,
            }:
                _clear_terminal_dispatch(recording)
            if not _late_roster_snapshot_fallback_enabled(binding_state):
                raise RecordingContractRefused(status=503)
            artifact_ref = _save_server_roster_artifact(recording, effect)
            _store_terminal_dispatch(recording, artifact_ref)
            return sign_capture_receipt(
                build_capture_receipt_claims(effect, "accepted")
            )
        return sign_capture_receipt(
            build_capture_receipt_claims(effect, "already_active")
        )
    try:
        if _local_roster_snapshot_enabled():
            artifact_ref = _save_server_roster_artifact(recording, effect)
            _store_terminal_dispatch(recording, artifact_ref)
            return sign_capture_receipt(
                build_capture_receipt_claims(effect, "accepted")
            )
        MetadataCollectorService().start(
            recording,
            metadata=_capture_metadata(recording, effect),
            dispatch_option_key=SPEAKER_EVIDENCE_DISPATCH_KEY,
            expected_pending_claim_id=claim_id,
        )
    except Exception as error:
        _clear_pending_dispatch(recording, claim_id)
        raise RecordingContractRefused(status=503) from error
    return sign_capture_receipt(build_capture_receipt_claims(effect, "accepted"))


@csrf_exempt
@require_POST
def capture_mastrao_speaker_evidence(request):
    """Verify a Core speaker evidence effect and start the metadata collector."""

    try:
        receipt = _apply_capture(_read_capture_effect(request))
    except RecordingContractRefused as error:
        return _safe_response(
            {"error": "speaker_evidence_capture_refused"},
            status=error.status,
        )
    return _safe_response({"speaker_evidence_capture_receipt": receipt})
