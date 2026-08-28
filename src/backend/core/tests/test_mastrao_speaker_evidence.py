"""Focused speaker-evidence capture proofs."""

# Test names carry the proof intent.
# pylint: disable=missing-function-docstring

import hashlib
import json
import time
from io import BytesIO
from unittest import mock

from django.conf import settings
from django.utils import timezone

import pytest

from core import models
from core.factories import RoomFactory, UserFactory
from core.mastrao_recording_contract import RecordingContractRefused
from core.mastrao_speaker_evidence_adapter import _apply_capture, _sidecar_digest
from core.models import RoomAccessLevel

pytestmark = pytest.mark.django_db


def _active_recording_binding():
    owner = UserFactory()
    room = RoomFactory(access_level=RoomAccessLevel.RESTRICTED)
    room_binding = models.MastraoRoomBinding.objects.create(
        effect_key="effect_room_speaker_012345",
        arguments_digest="a" * 64,
        meeting_ref="meeting_speaker_012345",
        room_ref="room_speaker_01234567",
        owner_ref="owner_speaker_01234567",
        room=room,
        owner=owner,
        provider_binding_digest="b" * 64,
    )
    recording = models.Recording.objects.create(
        room=room,
        mode=models.RecordingModeChoices.SCREEN_RECORDING,
    )
    return models.MastraoRecordingBinding.objects.create(
        room_binding=room_binding,
        recording=recording,
        organization_external_id="organization_0123456789",
        meeting_ref=room_binding.meeting_ref,
        room_ref=room_binding.room_ref,
        recording_ref="recording_speaker_012345",
        provider_binding_digest=room_binding.provider_binding_digest,
        policy_ref="policy_speaker_012345",
        notice_version="notice_speaker_012345",
        notice_digest="c" * 64,
        retention_expires_at=timezone.now() + timezone.timedelta(days=30),
        state=models.MastraoRecordingBinding.State.ACTIVE,
        provider_recording_ref="provider_speaker_012345",
    )


def _effect(binding):
    return {
        "organization_external_id": binding.organization_external_id,
        "meeting_ref": binding.meeting_ref,
        "room_ref": binding.room_ref,
        "recording_ref": binding.recording_ref,
        "evidence_ref": "evidence_0123456789abcdef",
        "provider_binding_digest": binding.provider_binding_digest,
        "policy_ref": binding.policy_ref,
        "notice_version": binding.notice_version,
        "notice_digest": binding.notice_digest,
        "purpose": "meeting_speaker_evidence",
        "scope": "recording_roster_vad_timeline",
        "retention_expires_at": int(binding.retention_expires_at.timestamp()),
        "effect_key": "speakerevidence_01234567",
        "arguments_digest": "d" * 64,
        "jti": "speakerevidence_01234567",
    }


def _artifact_claims(binding):
    effect = _effect(binding)
    data = _artifact_object_bytes(binding)
    now = int(time.time())
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
        "object_ref": "mastrao-speaker-evidence/evidence_0123456789abcdef.json",
        "artifact_ref": "speakerartifact_0123456789abcdef",
        "byte_size": len(data),
        "checksum_digest": hashlib.sha256(data).hexdigest(),
        "participant_count": 1,
        "event_count": 2,
        "timeline_started_at_ms": 0,
        "timeline_ended_at_ms": 1000,
        "region_ref": "region_speaker_012345",
        "encryption_ref": "encryption_speaker_012345",
        "lifecycle_policy_ref": "lifecycle_speaker_012345",
        "issued_at": now,
        "expires_at": now + 30,
        "jti": "speakerartifact_0123456789abcdef",
    }


def _artifact_object_bytes(binding):
    effect = _effect(binding)
    return json.dumps(
        {
            "version": 1,
            "recording_ref": effect["recording_ref"],
            "recording_started_at_ms": 0,
            "timeline_started_at_ms": 0,
            "timeline_ended_at_ms": 1000,
            "participants": [{"participant_ref": "participant_0123456789abcdef"}],
            "events": [{"at_ms": 0}, {"at_ms": 1000}],
            "evidence_ref": effect["evidence_ref"],
            "meeting_ref": effect["meeting_ref"],
            "room_ref": effect["room_ref"],
        },
        sort_keys=True,
    ).encode()


def _sidecar_bytes(claims):
    return json.dumps(
        {
            "speaker_evidence_artifact_receipt_claims": claims,
            "speaker_evidence_artifact_receipt_claims_digest": _sidecar_digest(claims),
        },
        sort_keys=True,
    ).encode()


def _open_bytes(data):
    return BytesIO(data)


def test_speaker_evidence_capture_starts_collector_with_opaque_metadata():
    binding = _active_recording_binding()
    with (
        mock.patch(
            "core.mastrao_speaker_evidence_adapter.MetadataCollectorService.start"
        ) as start,
        mock.patch(
            "core.mastrao_speaker_evidence_adapter.sign_capture_receipt",
            return_value="receipt.payload.signature",
        ),
    ):
        assert _apply_capture(_effect(binding)) == "receipt.payload.signature"

    start.assert_called_once()
    _, kwargs = start.call_args
    assert kwargs["dispatch_option_key"] == "mastrao_speaker_evidence_dispatch_id"
    assert "Matthias" not in kwargs["metadata"]
    assert (
        "mastrao-speaker-evidence/evidence_0123456789abcdef.json" in kwargs["metadata"]
    )


def test_speaker_evidence_capture_retries_existing_dispatch_without_receipt():
    binding = _active_recording_binding()
    binding.recording.options["mastrao_speaker_evidence_dispatch_id"] = "dispatch-1"
    binding.recording.save(update_fields=["options"])
    with (
        mock.patch(
            "core.mastrao_speaker_evidence_adapter.MetadataCollectorService.start"
        ) as start,
        mock.patch(
            "core.mastrao_speaker_evidence_adapter.sign_capture_receipt",
            return_value="receipt.payload.signature",
        ),
    ):
        with pytest.raises(RecordingContractRefused):
            _apply_capture(_effect(binding))

    start.assert_not_called()


def test_speaker_evidence_capture_replays_existing_sidecar_without_second_start():
    binding = _active_recording_binding()
    sidecar_claims = _artifact_claims(binding)
    artifact_data = _artifact_object_bytes(binding)
    binding.recording.options["mastrao_speaker_evidence_dispatch_id"] = "dispatch-1"
    binding.recording.save(update_fields=["options"])
    with (
        mock.patch(
            "core.mastrao_speaker_evidence_adapter.MetadataCollectorService.start"
        ) as start,
        mock.patch(
            "core.mastrao_speaker_evidence_adapter.default_storage.exists",
            return_value=True,
        ),
        mock.patch(
            "core.mastrao_speaker_evidence_adapter.default_storage.open",
            side_effect=[
                _open_bytes(_sidecar_bytes(sidecar_claims)),
                _open_bytes(artifact_data),
            ],
        ),
        mock.patch(
            "core.mastrao_speaker_evidence_adapter.default_storage.delete",
        ) as delete,
        mock.patch(
            "core.mastrao_speaker_evidence_adapter.validate_artifact_receipt_claims",
            return_value=sidecar_claims,
        ) as validate,
        mock.patch(
            "core.mastrao_speaker_evidence_adapter.refresh_artifact_receipt_claims",
            return_value={"jti": "fresh"},
        ) as refresh,
        mock.patch(
            "core.mastrao_speaker_evidence_adapter.sign_artifact_receipt",
            return_value="fresh.receipt.signature",
        ) as sign_artifact,
        mock.patch(
            "core.mastrao_speaker_evidence_adapter.post_core_json",
            return_value={"state": "available", "outcome": "available"},
        ) as post,
        mock.patch(
            "core.mastrao_speaker_evidence_adapter.sign_capture_receipt",
            return_value="receipt.payload.signature",
        ),
    ):
        assert _apply_capture(_effect(binding)) == "receipt.payload.signature"

    start.assert_not_called()
    validate.assert_called_once_with(sidecar_claims, allow_expired=True)
    refresh.assert_called_once_with(sidecar_claims)
    sign_artifact.assert_called_once_with({"jti": "fresh"})
    delete.assert_called_once_with(
        "mastrao-speaker-evidence/evidence_0123456789abcdef.json.receipt.json"
    )
    post.assert_called_once()
    _, kwargs = post.call_args
    assert kwargs["body"] == {
        "speaker_evidence_artifact_receipt": "fresh.receipt.signature"
    }


def test_speaker_evidence_capture_refuses_tampered_sidecar_claims():
    binding = _active_recording_binding()
    sidecar_claims = {
        **_artifact_claims(binding),
        "recording_ref": "recording_other_012345",
    }
    artifact_data = _artifact_object_bytes(binding)
    binding.recording.options["mastrao_speaker_evidence_dispatch_id"] = "dispatch-1"
    binding.recording.save(update_fields=["options"])
    with (
        mock.patch(
            "core.mastrao_speaker_evidence_adapter.default_storage.exists",
            return_value=True,
        ),
        mock.patch(
            "core.mastrao_speaker_evidence_adapter.default_storage.open",
            side_effect=[
                _open_bytes(_sidecar_bytes(sidecar_claims)),
                _open_bytes(artifact_data),
            ],
        ),
        mock.patch(
            "core.mastrao_speaker_evidence_adapter.sign_artifact_receipt"
        ) as sign_artifact,
    ):
        with pytest.raises(RecordingContractRefused):
            _apply_capture(_effect(binding))

    sign_artifact.assert_not_called()


def test_speaker_evidence_capture_refuses_unsigned_sidecar_claims():
    binding = _active_recording_binding()
    sidecar_claims = _artifact_claims(binding)
    binding.recording.options["mastrao_speaker_evidence_dispatch_id"] = "dispatch-1"
    binding.recording.save(update_fields=["options"])
    with (
        mock.patch(
            "core.mastrao_speaker_evidence_adapter.default_storage.exists",
            return_value=True,
        ),
        mock.patch(
            "core.mastrao_speaker_evidence_adapter.default_storage.open",
            return_value=_open_bytes(
                json.dumps(
                    {"speaker_evidence_artifact_receipt_claims": sidecar_claims},
                    sort_keys=True,
                ).encode()
            ),
        ),
        mock.patch(
            "core.mastrao_speaker_evidence_adapter.sign_artifact_receipt"
        ) as sign_artifact,
    ):
        with pytest.raises(RecordingContractRefused):
            _apply_capture(_effect(binding))

    sign_artifact.assert_not_called()


def test_speaker_evidence_capture_clears_terminal_dispatch_without_sidecar():
    binding = _active_recording_binding()
    binding.state = models.MastraoRecordingBinding.State.FINALIZED
    binding.save(update_fields=["state"])
    binding.recording.options["mastrao_speaker_evidence_dispatch_id"] = "dispatch-1"
    binding.recording.save(update_fields=["options"])
    with mock.patch(
        "core.mastrao_speaker_evidence_adapter.default_storage.exists",
        return_value=False,
    ):
        with pytest.raises(RecordingContractRefused):
            _apply_capture(_effect(binding))

    binding.recording.refresh_from_db()
    assert "mastrao_speaker_evidence_dispatch_id" not in binding.recording.options


def test_speaker_evidence_capture_recovers_stale_pending_dispatch():
    binding = _active_recording_binding()
    binding.recording.options["mastrao_speaker_evidence_dispatch_id"] = "pending"
    binding.recording.save(update_fields=["options"])
    with (
        mock.patch(
            "core.mastrao_speaker_evidence_adapter.MetadataCollectorService.start"
        ) as start,
        mock.patch(
            "core.mastrao_speaker_evidence_adapter.sign_capture_receipt",
            return_value="receipt.payload.signature",
        ),
    ):
        assert _apply_capture(_effect(binding)) == "receipt.payload.signature"

    start.assert_called_once()
