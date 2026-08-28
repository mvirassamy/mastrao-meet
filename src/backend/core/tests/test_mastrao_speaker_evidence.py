"""Focused speaker-evidence capture proofs."""

# Test names carry the proof intent.
# pylint: disable=missing-function-docstring

from unittest import mock

from django.utils import timezone

import pytest

from core import models
from core.factories import RoomFactory, UserFactory
from core.mastrao_speaker_evidence_adapter import _apply_capture
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


def test_speaker_evidence_capture_replays_existing_dispatch_without_second_start():
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
        assert _apply_capture(_effect(binding)) == "receipt.payload.signature"

    start.assert_not_called()


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
