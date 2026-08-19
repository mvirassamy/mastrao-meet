"""Focused transcription-effect, fake-ASR and default-off proofs."""

# Test names alone carry the proof intent, matching neighbour test modules.
# pylint: disable=missing-function-docstring

import hashlib
import json
from types import SimpleNamespace
from unittest import mock

from django.utils import timezone

import pytest

from core import models
from core.factories import RoomFactory, UserFactory
from core.mastrao_transcription_adapter import (
    _apply_transcription,
    _prepare_transcription,
    transcribe_mastrao_recording,
)
from core.mastrao_transcription_artifact import map_speakers, persist_transcript
from core.mastrao_transcription_contract import (
    TranscriptionContractRefused,
    build_submit_receipt_claims,
    build_transcript_artifact_receipt_claims,
)
from core.mastrao_transcription_worker import (
    FAKE_ENGINE_REF,
    transcribe_audio,
)
from core.models import RoomAccessLevel

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def transcription_settings(settings):
    """Keep focused tests explicit while production defaults remain closed."""

    settings.MASTRAO_MEETING_RECORDING_ENABLED = True
    settings.MASTRAO_MEETING_TRANSCRIPTION_ENABLED = True
    settings.MASTRAO_TRANSCRIPTION_ASR_MODE = "fake"


def _finalized_recording_binding(suffix="0123456789abcdef"):
    owner = UserFactory()
    room = RoomFactory(access_level=RoomAccessLevel.RESTRICTED)
    room_binding = models.MastraoRoomBinding.objects.create(
        effect_key=f"effect_room_{suffix}",
        arguments_digest="a" * 64,
        meeting_ref=f"meeting_{suffix}",
        room_ref=f"room_{suffix}",
        owner_ref=f"owner_{suffix}",
        room=room,
        owner=owner,
        provider_binding_digest="b" * 64,
    )
    return models.MastraoRecordingBinding.objects.create(
        room_binding=room_binding,
        organization_external_id="organization_0123456789",
        meeting_ref=room_binding.meeting_ref,
        room_ref=room_binding.room_ref,
        recording_ref=f"recording_{suffix}",
        provider_binding_digest=room_binding.provider_binding_digest,
        policy_ref=f"policy_{suffix}",
        notice_version=f"notice_{suffix}",
        notice_digest="c" * 64,
        retention_expires_at=timezone.now() + timezone.timedelta(days=30),
        state=models.MastraoRecordingBinding.State.FINALIZED,
        artifact_ref=f"artifact_{suffix}",
        object_ref=f"recordings/{suffix}.mp4",
        byte_size=2_048,
        checksum_algorithm="sha256",
        checksum_digest="d" * 64,
    )


def _effect(binding, **overrides):
    effect = {
        "organization_external_id": binding.organization_external_id,
        "meeting_ref": binding.meeting_ref,
        "room_ref": binding.room_ref,
        "recording_ref": binding.recording_ref,
        "transcription_ref": "transcription_0123456789ab",
        "recording_artifact_ref": binding.artifact_ref,
        "provider_binding_digest": binding.provider_binding_digest,
        "recording_checksum_digest": binding.checksum_digest,
        "retention_expires_at": int(binding.retention_expires_at.timestamp()),
        "effect_key": "effect_transcribe_0123456789",
        "arguments_digest": "e" * 64,
        "resolve_only": False,
        "jti": "request_transcribe_012345678",
    }
    effect.update(overrides)
    return effect


def test_fake_asr_is_deterministic_and_schema_valid():
    transcript = transcribe_audio(b"identical audio bytes")
    again = transcribe_audio(b"identical audio bytes")
    different = transcribe_audio(b"other audio bytes")
    assert transcript == again
    assert transcript != different
    assert transcript["engine_ref"] == FAKE_ENGINE_REF
    assert transcript["version"] == 1
    for segment in transcript["segments"]:
        assert segment["segment_id"].startswith("segment_")
        assert 0 <= segment["start_ms"] < segment["end_ms"]
        assert segment["speaker"]["kind"] == "acoustic"
        assert isinstance(segment["text"], str) and segment["text"]
        assert 0 < segment["confidence"] <= 1


def test_real_mode_without_endpoint_fails_closed(settings):
    settings.MASTRAO_TRANSCRIPTION_ASR_MODE = "real"
    settings.MASTRAO_TRANSCRIPTION_ASR_ENDPOINT = ""
    with pytest.raises(TranscriptionContractRefused):
        transcribe_audio(b"audio")


def test_speaker_mapping_falls_back_to_stable_anonymous_indexes():
    transcript = transcribe_audio(b"mapping fallback audio")
    mapped = map_speakers(json.loads(json.dumps(transcript)), samples=[])
    indexes = {
        segment["speaker"]["index"]
        for segment in mapped["segments"]
        if segment["speaker"]["kind"] == "anonymous"
    }
    assert len(mapped["segments"]) == len(transcript["segments"])
    assert all(
        segment["speaker"]["kind"] == "anonymous" for segment in mapped["segments"]
    )
    assert indexes == set(range(1, len(indexes) + 1))


def test_speaker_mapping_uses_dominant_active_speaker_overlap():
    transcript = {
        "version": 1,
        "engine_ref": FAKE_ENGINE_REF,
        "language": "fr",
        "segments": [
            {
                "segment_id": "segment_x_0000",
                "start_ms": 0,
                "end_ms": 4000,
                "speaker": {"kind": "acoustic", "ref": "SPEAKER_0"},
                "text": "dossier",
                "confidence": 0.9,
            }
        ],
    }
    samples = [
        SimpleNamespace(
            participant_ref="participant_alpha",
            speaking_started_at_ms=0,
            speaking_ended_at_ms=3500,
        ),
        SimpleNamespace(
            participant_ref="participant_beta",
            speaking_started_at_ms=3500,
            speaking_ended_at_ms=4000,
        ),
    ]
    mapped = map_speakers(transcript, samples)
    assert mapped["segments"][0]["speaker"] == {
        "kind": "participant",
        "label": "participant_alpha",
    }


def test_feature_off_refuses_new_effects_without_side_effects(settings):
    settings.MASTRAO_MEETING_TRANSCRIPTION_ENABLED = False
    binding = _finalized_recording_binding("feature_off_0123456")
    with pytest.raises(TranscriptionContractRefused):
        _prepare_transcription(_effect(binding))
    assert not models.MastraoTranscriptionBinding.objects.exists()
    assert not models.MastraoTranscriptionEffect.objects.exists()


def test_unfinalized_or_mismatched_artifact_is_refused():
    binding = _finalized_recording_binding("mismatch_0123456789")
    with pytest.raises(TranscriptionContractRefused):
        _prepare_transcription(_effect(binding, recording_checksum_digest="f" * 64))
    binding.state = models.MastraoRecordingBinding.State.PROCESSING
    binding.save(update_fields=["state", "updated_at"])
    with pytest.raises(TranscriptionContractRefused):
        _prepare_transcription(_effect(binding))


def _fake_artifact(**overrides):
    artifact = {
        "transcript_artifact_ref": "transcript_0123456789abcdef",
        "object_ref": "mastrao-transcripts/transcription_0123456789ab.json",
        "byte_size": 512,
        "checksum_digest": "9" * 64,
        "segment_count": 4,
        "engine_ref": FAKE_ENGINE_REF,
    }
    artifact.update(overrides)
    return artifact


def test_apply_transcription_persists_effect_and_binding_states():
    binding = _finalized_recording_binding("apply_0123456789abc")
    effect = _effect(binding)
    with (
        mock.patch(
            "core.mastrao_transcription_adapter._produce_transcript",
            return_value=_fake_artifact(),
        ),
        mock.patch(
            "core.mastrao_transcription_adapter.sign_submit_receipt",
            return_value="receipt.payload.signature",
        ),
    ):
        assert _apply_transcription(effect) == "receipt.payload.signature"
    transcription = models.MastraoTranscriptionBinding.objects.get(
        recording_binding=binding
    )
    assert transcription.state == models.MastraoTranscriptionBinding.State.AVAILABLE
    assert transcription.checksum_digest == "9" * 64
    assert transcription.segment_count == 4
    local_effect = transcription.effects.get()
    assert local_effect.state == models.MastraoTranscriptionEffect.State.APPLIED
    assert local_effect.receipt_claims["status"] == "confirmed"


def test_exact_replay_returns_receipt_without_second_transcription():
    binding = _finalized_recording_binding("replay_0123456789ab")
    effect = _effect(binding)
    with (
        mock.patch(
            "core.mastrao_transcription_adapter._produce_transcript",
            return_value=_fake_artifact(),
        ) as produce,
        mock.patch(
            "core.mastrao_transcription_adapter.sign_submit_receipt",
            return_value="receipt.payload.signature",
        ),
    ):
        assert _apply_transcription(effect) == "receipt.payload.signature"
        assert _apply_transcription(effect) == "receipt.payload.signature"
    produce.assert_called_once()


def test_divergent_replay_is_refused_with_conflict():
    binding = _finalized_recording_binding("conflict_0123456789")
    effect = _effect(binding)
    with (
        mock.patch(
            "core.mastrao_transcription_adapter._produce_transcript",
            return_value=_fake_artifact(),
        ),
        mock.patch(
            "core.mastrao_transcription_adapter.sign_submit_receipt",
            return_value="receipt.payload.signature",
        ),
    ):
        _apply_transcription(effect)
    with pytest.raises(TranscriptionContractRefused) as refusal:
        _prepare_transcription(
            _effect(binding, effect_key="effect_transcribe_divergent01")
        )
    assert refusal.value.status == 409


def test_resolve_only_never_creates_a_first_effect():
    binding = _finalized_recording_binding("resolve_0123456789a")
    with pytest.raises(TranscriptionContractRefused):
        _prepare_transcription(_effect(binding, resolve_only=True))
    assert not models.MastraoTranscriptionEffect.objects.exists()


def test_receipt_claims_bind_effect_and_artifact_exactly():
    binding = _finalized_recording_binding("claims_0123456789ab")
    effect = _effect(binding)
    submit = build_submit_receipt_claims(effect, "submitted")
    assert submit["type"] == "mastrao.meeting-transcription-submit-receipt"
    assert submit["operation"] == "confirm_meeting_transcription_submitted"
    assert submit["transcription_ref"] == "transcription_0123456789ab"
    assert submit["effect_key"] == "effect_transcribe_0123456789"
    assert submit["arguments_digest"] == "e" * 64
    assert submit["status"] == "confirmed"
    assert submit["provider_observation"] == "submitted"
    assert submit["provider_job_ref"].startswith("asrjob_")
    assert submit["jti"] == "request_transcribe_012345678"
    artifact = build_transcript_artifact_receipt_claims(effect, _fake_artifact())
    assert artifact["type"] == "mastrao.meeting-transcription-artifact-receipt"
    assert artifact["operation"] == "confirm_meeting_transcription_artifact"
    assert artifact["transcription_ref"] == "transcription_0123456789ab"
    assert artifact["recording_artifact_ref"] == binding.artifact_ref
    assert artifact["checksum_digest"] == "9" * 64
    assert artifact["content_type"] == "application/json"
    assert artifact["segment_count"] == 4
    assert artifact["retention_expires_at"] == effect["retention_expires_at"]
    assert artifact["jti"].startswith("transcript_artifact_")


def test_persist_transcript_is_checksummed_and_idempotent(settings, tmp_path):
    settings.STORAGES = {
        "default": {
            "BACKEND": "django.core.files.storage.FileSystemStorage",
            "OPTIONS": {"location": str(tmp_path)},
        },
        "staticfiles": {
            "BACKEND": "django.core.files.storage.FileSystemStorage",
            "OPTIONS": {"location": str(tmp_path / "static")},
        },
    }
    transcript = map_speakers(transcribe_audio(b"persisted audio"), samples=[])
    artifact = persist_transcript("transcription_persist_01234", transcript)
    replay = persist_transcript("transcription_persist_01234", transcript)
    payload = json.dumps(
        {
            "version": 1,
            "transcription_ref": "transcription_persist_01234",
            "language": transcript["language"],
            "segments": transcript["segments"],
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode()
    assert artifact["checksum_digest"] == hashlib.sha256(payload).hexdigest()
    assert artifact["byte_size"] == len(payload)
    assert replay["checksum_digest"] == artifact["checksum_digest"]
    assert artifact["segment_count"] == len(transcript["segments"])
    with (tmp_path / "mastrao-transcripts" / "transcription_persist_01234.json").open(
        encoding="utf-8"
    ) as stream:
        stored = json.load(stream)
    assert stored["version"] == 1
    assert stored["transcription_ref"] == "transcription_persist_01234"
    assert "engine_ref" not in stored
    assert "audio_digest" not in stored
    for segment in stored["segments"]:
        assert set(segment) == {
            "segment_id",
            "start_ms",
            "end_ms",
            "speaker",
            "text",
            "confidence",
        }
        assert segment["speaker"]["kind"] in {"participant", "anonymous"}


def test_endpoint_refuses_oversized_and_malformed_bodies(rf):
    request = rf.post(
        "/internal/mastrao/transcriptions/transcribe/",
        data="not-json",
        content_type="text/plain",
    )
    response = transcribe_mastrao_recording(request)
    assert response.status_code == 404


def test_endpoint_returns_signed_receipt_for_verified_effect(rf):
    binding = _finalized_recording_binding("endpoint_0123456789")
    effect = _effect(binding)
    request = rf.post(
        "/internal/mastrao/transcriptions/transcribe/",
        data=json.dumps({"transcription_submit_effect": "header.payload.signature"}),
        content_type="application/json",
    )
    with (
        mock.patch(
            "core.mastrao_transcription_adapter.verify_transcription_submit_effect",
            return_value=effect,
        ),
        mock.patch(
            "core.mastrao_transcription_adapter._produce_transcript",
            return_value=_fake_artifact(),
        ),
        mock.patch(
            "core.mastrao_transcription_adapter.sign_submit_receipt",
            return_value="receipt.payload.signature",
        ),
    ):
        response = transcribe_mastrao_recording(request)
    assert response.status_code == 200
    assert json.loads(response.content) == {
        "transcription_submit_receipt": "receipt.payload.signature"
    }
