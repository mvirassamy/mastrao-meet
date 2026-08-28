"""Private adapter for signed Mastrao speaker evidence capture effects."""

import json
import time
import uuid

from django.conf import settings
from django.core.files.storage import default_storage
from django.db import transaction
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from botocore.exceptions import BotoCoreError, ClientError

from core import models
from core.mastrao_core_http import post_core_json
from core.mastrao_recording_contract import RecordingContractRefused
from core.mastrao_speaker_evidence_contract import (
    build_capture_receipt_claims,
    sign_capture_receipt,
    verify_speaker_evidence_capture_effect,
)
from core.recording.services.metadata_collector import MetadataCollectorService

MAX_BODY_BYTES = 32_768
SPEAKER_EVIDENCE_DISPATCH_KEY = "mastrao_speaker_evidence_dispatch_id"
SPEAKER_EVIDENCE_DISPATCH_PENDING = "pending"
SPEAKER_EVIDENCE_PENDING_LEASE_SECONDS = 60
SPEAKER_EVIDENCE_RECEIPT_SUFFIX = ".receipt.json"


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
    return f"mastrao-speaker-evidence/{effect['evidence_ref']}.json{SPEAKER_EVIDENCE_RECEIPT_SUFFIX}"


def _pending_dispatch_marker():
    return {
        "state": SPEAKER_EVIDENCE_DISPATCH_PENDING,
        "claimed_at": int(time.time()),
        "claim_id": uuid.uuid4().hex,
    }


def _is_pending_dispatch(value):
    return value == SPEAKER_EVIDENCE_DISPATCH_PENDING or (
        isinstance(value, dict) and value.get("state") == SPEAKER_EVIDENCE_DISPATCH_PENDING
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
    object_ref = _receipt_sidecar_ref(effect)
    try:
        if not default_storage.exists(object_ref):
            return False
        with default_storage.open(object_ref, "rb") as stream:
            raw = stream.read(MAX_BODY_BYTES + 1)
    except (BotoCoreError, ClientError, OSError, ValueError) as error:
        raise RecordingContractRefused(status=503) from error
    if len(raw) > MAX_BODY_BYTES:
        raise RecordingContractRefused(status=503)
    try:
        body = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as error:
        raise RecordingContractRefused(status=503) from error
    if not isinstance(body, dict) or set(body) != {"speaker_evidence_artifact_receipt"}:
        raise RecordingContractRefused(status=503)
    result = post_core_json(
        endpoint=settings.MASTRAO_CORE_SPEAKER_EVIDENCE_ARTIFACT_ENDPOINT,
        expected_path="/internal/v1/meetings/speaker-evidence/artifacts/finalize",
        body=body,
        timeout=settings.MASTRAO_CORE_RECORDING_TIMEOUT_SECONDS,
        refusal=RecordingContractRefused,
        expected_fields={"state", "outcome"},
        passthrough_statuses=frozenset({404, 409, 503}),
    )
    if result["state"] != "available" or result["outcome"] != "available":
        raise RecordingContractRefused(status=503)
    return True


@transaction.atomic
def _claim_recording_for_capture(effect):
    binding = (
        models.MastraoRecordingBinding.objects.select_for_update(of=("self",))
        .select_related("recording")
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
        not in {
            models.MastraoRecordingBinding.State.STARTING,
            models.MastraoRecordingBinding.State.ACTIVE,
            models.MastraoRecordingBinding.State.STOPPING,
            models.MastraoRecordingBinding.State.PROCESSING,
            models.MastraoRecordingBinding.State.FINALIZED,
        }
    ):
        raise RecordingContractRefused(status=503)
    recording = binding.recording
    dispatch_id = recording.options.get(SPEAKER_EVIDENCE_DISPATCH_KEY)
    if dispatch_id:
        if _is_pending_dispatch(dispatch_id) and _pending_dispatch_expired(dispatch_id):
            marker = _pending_dispatch_marker()
            recording.options[SPEAKER_EVIDENCE_DISPATCH_KEY] = marker
            recording.save(update_fields=["options"])
            return recording, True, marker["claim_id"]
        return recording, False, None
    marker = _pending_dispatch_marker()
    recording.options[SPEAKER_EVIDENCE_DISPATCH_KEY] = marker
    recording.save(update_fields=["options"])
    return recording, True, marker["claim_id"]


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
    recording, should_start, claim_id = _claim_recording_for_capture(effect)
    if not should_start:
        if not _is_pending_dispatch(recording.options.get(SPEAKER_EVIDENCE_DISPATCH_KEY)):
            if not _replay_artifact_receipt(effect):
                raise RecordingContractRefused(status=503)
        return sign_capture_receipt(
            build_capture_receipt_claims(effect, "already_active")
        )
    try:
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
