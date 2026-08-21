"""Focused transcription-effect, fake-ASR and default-off proofs."""

# Test names alone carry the proof intent, matching neighbour test modules.
# pylint: disable=missing-function-docstring

import hashlib
import json
import shutil
from unittest import mock

from django.core.cache import cache
from django.core.exceptions import ImproperlyConfigured
from django.utils import timezone

import pytest

from core import models
from core.factories import RoomFactory, UserFactory
from core.mastrao_recording_contract import RecordingContractRefused
from core.mastrao_recording_session import (
    _validate_status,
    media_allowed,
    public_projection,
    record_transcription_decision,
)
from core.mastrao_transcription_adapter import (
    _apply_transcription,
    _notify_core_artifact,
    _prepare_transcription,
    complete_transcription,
    transcribe_mastrao_recording,
)
from core.mastrao_transcription_artifact import map_speakers, persist_transcript
from core.mastrao_transcription_contract import (
    TranscriptionContractRefused,
    TranscriptionPipelineFailed,
    build_submit_receipt_claims,
    build_transcript_artifact_receipt_claims,
    build_transcription_failure_receipt_claims,
)
from core.mastrao_transcription_worker import (
    FAKE_ENGINE_REF,
    _validated_transcript,
    transcribe_audio,
)
from core.models import RoomAccessLevel

pytestmark = pytest.mark.django_db
ENQUEUE = (
    "core.mastrao_transcription_adapter."
    "process_mastrao_transcription.apply_async"
)


@pytest.fixture(autouse=True)
def transcription_settings(settings):
    """Keep focused tests explicit while production defaults remain closed."""

    settings.MASTRAO_MEETING_RECORDING_ENABLED = True
    settings.MASTRAO_MEETING_TRANSCRIPTION_ENABLED = True
    settings.MASTRAO_TRANSCRIPTION_ASR_MODE = "fake"
    cache.clear()


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


def test_transcription_runtime_contains_ffmpeg():
    assert shutil.which("ffmpeg") is not None


def test_transcribed_capture_waits_for_an_explicit_transcription_decision():
    status = {
        "mode": "recorded",
        "recording_state": "active",
        "decision": "accepted",
        "transcription_mode": "transcribed",
        "transcription_decision": "absent",
    }
    assert not media_allowed(status)
    status["transcription_decision"] = "accepted"
    assert media_allowed(status)
    status["transcription_decision"] = "refused"
    assert media_allowed(status)
    status["transcription_decision"] = "withdrawn"
    assert media_allowed(status)


def test_real_mode_without_endpoint_fails_closed(settings):
    settings.MASTRAO_TRANSCRIPTION_ASR_MODE = "real"
    settings.MASTRAO_TRANSCRIPTION_ASR_ENDPOINT = ""
    with pytest.raises(TranscriptionContractRefused):
        transcribe_audio(b"audio")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("segment_id", "invalid segment id"),
        ("text", ""),
        ("text", "x" * 4_001),
        ("confidence", -0.1),
        ("confidence", 1.1),
        ("confidence", float("nan")),
    ],
)
def test_asr_output_rejects_noncanonical_segment_values(field, value):
    transcript = transcribe_audio(b"strict output")
    transcript["segments"][0][field] = value
    with pytest.raises(TranscriptionContractRefused):
        _validated_transcript(transcript)


def test_asr_output_rejects_duplicate_segments_and_invalid_language():
    transcript = transcribe_audio(b"duplicate output")
    transcript["segments"][1]["segment_id"] = transcript["segments"][0]["segment_id"]
    with pytest.raises(TranscriptionContractRefused):
        _validated_transcript(transcript)
    transcript = transcribe_audio(b"invalid language")
    transcript["language"] = "fr-fr"
    with pytest.raises(TranscriptionContractRefused):
        _validated_transcript(transcript)


def test_speaker_mapping_falls_back_to_stable_anonymous_indexes():
    transcript = transcribe_audio(b"mapping fallback audio")
    mapped = map_speakers(json.loads(json.dumps(transcript)))
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


def test_apply_transcription_returns_submitted_receipt_without_running_asr():
    binding = _finalized_recording_binding("apply_0123456789abc")
    effect = _effect(binding)
    with (
        mock.patch(
            ENQUEUE
        ) as enqueue,
        mock.patch(
            "core.mastrao_transcription_adapter.sign_submit_receipt",
            return_value="receipt.payload.signature",
        ),
        mock.patch(
            "core.mastrao_transcription_adapter._produce_transcript"
        ) as produce,
    ):
        assert _apply_transcription(effect) == "receipt.payload.signature"
    produce.assert_not_called()
    enqueue.assert_called_once()
    transcription = models.MastraoTranscriptionBinding.objects.get(
        recording_binding=binding
    )
    assert transcription.state == models.MastraoTranscriptionBinding.State.PROCESSING
    assert transcription.checksum_digest is None
    local_effect = transcription.effects.get()
    assert local_effect.state == models.MastraoTranscriptionEffect.State.APPLYING
    assert local_effect.receipt_claims["status"] == "confirmed"
    assert local_effect.receipt_claims["provider_observation"] == "submitted"


def test_exact_replay_returns_receipt_without_second_enqueue():
    binding = _finalized_recording_binding("replay_0123456789ab")
    effect = _effect(binding)
    with (
        mock.patch(
            ENQUEUE
        ) as enqueue,
        mock.patch(
            "core.mastrao_transcription_adapter.sign_submit_receipt",
            return_value="receipt.payload.signature",
        ),
        mock.patch(
            "core.mastrao_transcription_adapter._produce_transcript"
        ) as produce,
    ):
        assert _apply_transcription(effect) == "receipt.payload.signature"
        assert _apply_transcription(effect) == "receipt.payload.signature"
    produce.assert_not_called()
    enqueue.assert_called_once()


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
        mock.patch("core.mastrao_transcription_adapter._notify_core_artifact"),
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
    transcript = map_speakers(transcribe_audio(b"persisted audio"))
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
            ENQUEUE
        ),
        mock.patch(
            "core.mastrao_transcription_adapter.sign_submit_receipt",
            return_value="receipt.payload.signature",
        ),
        mock.patch(
            "core.mastrao_transcription_adapter._produce_transcript"
        ) as produce,
    ):
        response = transcribe_mastrao_recording(request)
    produce.assert_not_called()
    assert response.status_code == 200
    assert json.loads(response.content) == {
        "transcription_submit_receipt": "receipt.payload.signature"
    }


def test_invalid_asr_mode_fails_explicitly(settings):
    settings.MASTRAO_TRANSCRIPTION_ASR_MODE = "typo"
    with pytest.raises(ImproperlyConfigured):
        transcribe_audio(b"audio")


def test_concurrent_submit_enqueues_one_job():
    binding = _finalized_recording_binding("applying_0123456789")
    effect = _effect(binding)
    with (
        mock.patch(
            ENQUEUE
        ) as enqueue,
        mock.patch(
            "core.mastrao_transcription_adapter.sign_submit_receipt",
            return_value="receipt.payload.signature",
        ),
        mock.patch(
            "core.mastrao_transcription_adapter._produce_transcript"
        ) as produce,
    ):
        first = _apply_transcription(effect)
        second = _apply_transcription(effect)
    assert first == second == "receipt.payload.signature"
    produce.assert_not_called()
    enqueue.assert_called_once()
    local_effect = models.MastraoTranscriptionEffect.objects.get()
    assert local_effect.state == models.MastraoTranscriptionEffect.State.APPLYING


def test_celery_completion_notifies_core_after_submit_receipt():
    binding = _finalized_recording_binding("notify_0123456789ab")
    effect = _effect(binding)
    with (
        mock.patch(
            ENQUEUE
        ),
        mock.patch(
            "core.mastrao_transcription_adapter.sign_submit_receipt",
            return_value="receipt.payload.signature",
        ),
        mock.patch(
            "core.mastrao_transcription_adapter._produce_transcript",
            return_value=_fake_artifact(),
        ),
        mock.patch(
            "core.mastrao_transcription_adapter._notify_core_artifact"
        ) as notify,
    ):
        _apply_transcription(effect)
        notify.assert_not_called()
        complete_transcription(models.MastraoTranscriptionEffect.objects.get().pk)
    notify.assert_called_once()
    assert notify.call_args.args[0]["transcription_ref"] == effect["transcription_ref"]
    assert notify.call_args.args[1] == _fake_artifact()
    transcription = models.MastraoTranscriptionBinding.objects.get()
    assert transcription.state == models.MastraoTranscriptionBinding.State.AVAILABLE
    assert models.MastraoTranscriptionEffect.objects.get().state == (
        models.MastraoTranscriptionEffect.State.APPLIED
    )


def test_artifact_notification_posts_signed_receipt_to_core(settings):
    settings.MASTRAO_CORE_TRANSCRIPTION_ARTIFACT_ENDPOINT = (
        "http://cabinet-core:3911/internal/v1/meetings/transcription/artifacts/finalize"
    )
    binding = _finalized_recording_binding("notifypost_01234567")
    effect = _effect(binding)
    claims = {"artifact_ref": "transcript_0123456789abcdef"}
    with (
        mock.patch(
            "core.mastrao_transcription_adapter."
            "build_transcript_artifact_receipt_claims",
            return_value=claims,
        ),
        mock.patch(
            "core.mastrao_transcription_adapter.sign_transcript_artifact_receipt",
            return_value="artifact.receipt.signature",
        ),
        mock.patch(
            "core.mastrao_transcription_adapter.post_core_json",
            return_value={"artifactRef": "transcript_0123456789abcdef"},
        ) as post,
    ):
        _notify_core_artifact(effect, _fake_artifact())
    post.assert_called_once()
    body = post.call_args.kwargs["body"]
    assert body == {"transcription_artifact_receipt": "artifact.receipt.signature"}


def test_pipeline_failure_marks_local_state_and_notifies_core():
    binding = _finalized_recording_binding("failure_0123456789a")
    effect = _effect(binding)
    with (
        mock.patch(
            ENQUEUE
        ),
        mock.patch(
            "core.mastrao_transcription_adapter.sign_submit_receipt",
            return_value="receipt.payload.signature",
        ),
        mock.patch(
            "core.mastrao_transcription_adapter._produce_transcript",
            side_effect=TranscriptionPipelineFailed("asr_failed"),
        ),
        mock.patch("core.mastrao_transcription_adapter._notify_core_failure") as notify,
    ):
        _apply_transcription(effect)
        with pytest.raises(TranscriptionPipelineFailed):
            complete_transcription(models.MastraoTranscriptionEffect.objects.get().pk)
    assert notify.call_args.args[0]["transcription_ref"] == effect["transcription_ref"]
    assert notify.call_args.args[1] == "asr_failed"
    local_effect = models.MastraoTranscriptionEffect.objects.get()
    assert local_effect.state == models.MastraoTranscriptionEffect.State.FAILED
    transcription = models.MastraoTranscriptionBinding.objects.get()
    assert transcription.state == models.MastraoTranscriptionBinding.State.FAILED


def test_failure_receipt_claims_bind_effect_and_code():
    binding = _finalized_recording_binding("failclaims_01234567")
    effect = _effect(binding)
    claims = build_transcription_failure_receipt_claims(effect, "asr_failed")
    assert claims["type"] == "mastrao.meeting-transcription-failure-receipt"
    assert claims["operation"] == "confirm_meeting_transcription_failed"
    assert claims["failure_code"] == "asr_failed"
    assert claims["transcription_ref"] == effect["transcription_ref"]
    assert claims["provider_job_ref"].startswith("asrjob_")
    with pytest.raises(TranscriptionContractRefused):
        build_transcription_failure_receipt_claims(effect, "unknown_code")


def _transcribed_status(**overrides):
    status = {
        "version": 1,
        "organization_external_id": "organization_0123456789",
        "meeting_ref": "meeting_0123456789abcdef",
        "room_ref": "room_0123456789abcdef",
        "mode": "recorded",
        "recording_ref": "recording_0123456789abcdef",
        "policy_ref": "policy_0123456789abcdef",
        "notice_version": "notice_0123456789abcdef",
        "notice_digest": "a" * 64,
        "purpose": "meeting_recording",
        "scope": "room_composite_audio_video_screen",
        "retention_expires_at": 2_000_000_000,
        "recording_state": "collecting",
        "decision": "absent",
        "transcription_mode": "transcribed",
        "transcription_notice_version": "notice_transcription_01234",
        "transcription_notice_digest": "b" * 64,
        "transcription_decision": "absent",
    }
    status.update(overrides)
    return status


def test_transcription_decision_posts_dedicated_purpose_assertion(settings):
    settings.MASTRAO_CORE_TRANSCRIPTION_DECISION_ENDPOINT = (
        "http://cabinet-core:3911/internal/v1/meetings/transcription/decisions"
    )
    participant = {
        "kind": "guest",
        "ref": "guest_0123456789abcdef",
        "session_digest": "d" * 64,
        "compact": "header.payload.signature",
        "claims": {},
    }
    status = _transcribed_status()
    room = mock.Mock()
    room.mastrao_binding.provider_binding_digest = "c" * 64
    result = {
        "version": 1,
        "meeting_ref": status["meeting_ref"],
        "recording_ref": status["recording_ref"],
        "purpose": "meeting_transcription",
        "decision": "accepted",
    }
    with (
        mock.patch(
            "core.mastrao_recording_session._participant", return_value=participant
        ),
        mock.patch(
            "core.mastrao_recording_session.recording_session_status",
            return_value=status,
        ),
        mock.patch(
            "core.mastrao_recording_session.sign_transcription_decision_assertion",
            return_value="decision.assertion.signature",
        ) as sign,
        mock.patch(
            "core.mastrao_recording_session.post_core_json", return_value=result
        ) as post,
    ):
        assert (
            record_transcription_decision(
                mock.Mock(), room, "accepted", "consentrequest_0123456789"
            )
            == result
        )
    payload = sign.call_args.args[0]
    assert payload["purpose"] == "meeting_transcription"
    assert payload["scope"] == "recording_artifact_audio_transcript"
    assert payload["notice_version"] == "notice_transcription_01234"
    assert payload["notice_digest"] == "b" * 64
    assert post.call_args.kwargs["body"] == {
        "participant_grant": "header.payload.signature",
        "decision_assertion": "decision.assertion.signature",
    }


def test_transcription_decision_refused_when_policy_not_transcribed():
    status = _transcribed_status(transcription_mode="disabled")
    del status["transcription_notice_version"]
    del status["transcription_notice_digest"]
    del status["transcription_decision"]
    with (
        mock.patch("core.mastrao_recording_session._participant"),
        mock.patch(
            "core.mastrao_recording_session.recording_session_status",
            return_value=status,
        ),
        mock.patch("core.mastrao_recording_session.post_core_json") as post,
    ):
        with pytest.raises(RecordingContractRefused):
            record_transcription_decision(
                mock.Mock(), mock.Mock(), "accepted", "consentrequest_0123456789"
            )
    post.assert_not_called()


def test_public_projection_exposes_transcription_notice_and_decision():
    projection = public_projection(
        {**_transcribed_status(), "participant_kind": "guest"}
    )
    assert projection["transcription_mode"] == "transcribed"
    assert projection["transcription_notice_version"] == ("notice_transcription_01234")
    assert projection["transcription_decision"] == "absent"


def test_validate_status_defaults_missing_transcription_mode_to_disabled(settings):
    """A staggered deploy where Core predates the transcription projection
    must not break recording consent: the missing field defaults to
    disabled instead of failing the exact-fields check."""
    settings.MASTRAO_RECORDING_NOTICE_VERSION = "notice_0123456789abcdef"
    settings.MASTRAO_RECORDING_NOTICE_DIGEST = "a" * 64
    status = _transcribed_status(transcription_mode="disabled")
    del status["transcription_mode"]
    del status["transcription_notice_version"]
    del status["transcription_notice_digest"]
    del status["transcription_decision"]
    participant = {
        "claims": {
            "organization_external_id": status["organization_external_id"],
            "meeting_ref": status["meeting_ref"],
            "room_ref": status["room_ref"],
        }
    }
    room = mock.Mock()
    room.mastrao_binding.room_ref = status["room_ref"]
    _validate_status(status, participant, room)
    assert status["transcription_mode"] == "disabled"


def test_long_running_asr_does_not_block_the_submit_receipt():
    """Core's HTTP claim window must not wait for ffmpeg or ASR."""

    binding = _finalized_recording_binding("slowasr_0123456789")
    effect = _effect(binding)
    with (
        mock.patch(
            ENQUEUE
        ),
        mock.patch(
            "core.mastrao_transcription_adapter.sign_submit_receipt",
            return_value="receipt.payload.signature",
        ),
        mock.patch(
            "core.mastrao_transcription_adapter._produce_transcript",
            side_effect=AssertionError("asr_ran_inside_http_submit"),
        ),
    ):
        assert _apply_transcription(effect) == "receipt.payload.signature"
    assert models.MastraoTranscriptionBinding.objects.get().state == (
        models.MastraoTranscriptionBinding.State.PROCESSING
    )


def test_artifact_callback_never_runs_before_submit_receipt():
    binding = _finalized_recording_binding("order_0123456789abc")
    effect = _effect(binding)
    events = []
    with (
        mock.patch(
            ENQUEUE
        ),
        mock.patch(
            "core.mastrao_transcription_adapter.sign_submit_receipt",
            return_value="receipt.payload.signature",
        ),
        mock.patch(
            "core.mastrao_transcription_adapter._produce_transcript",
            return_value=_fake_artifact(),
        ),
        mock.patch(
            "core.mastrao_transcription_adapter._notify_core_artifact",
            side_effect=lambda *_args, **_kwargs: events.append("artifact"),
        ),
    ):
        _apply_transcription(effect)
        events.append("submitted")
        complete_transcription(models.MastraoTranscriptionEffect.objects.get().pk)
    assert events == ["submitted", "artifact"]


def test_deleted_recording_refusal_removes_the_written_object(settings, tmp_path):
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
    binding = _finalized_recording_binding("orphan_0123456789a")
    effect = _effect(binding, transcription_ref="transcription_persist_01234")
    transcript = map_speakers(transcribe_audio(b"deleted recording audio"))
    artifact = persist_transcript(effect["transcription_ref"], transcript)
    object_path = (
        tmp_path / "mastrao-transcripts" / f"{effect['transcription_ref']}.json"
    )
    assert object_path.exists()
    with (
        mock.patch(
            ENQUEUE
        ),
        mock.patch(
            "core.mastrao_transcription_adapter.sign_submit_receipt",
            return_value="receipt.payload.signature",
        ),
        mock.patch(
            "core.mastrao_transcription_adapter._produce_transcript",
            return_value=artifact,
        ),
        mock.patch(
            "core.mastrao_transcription_adapter._notify_core_artifact",
            side_effect=TranscriptionContractRefused(status=404),
        ),
    ):
        _apply_transcription(effect)
        with pytest.raises(TranscriptionContractRefused):
            complete_transcription(models.MastraoTranscriptionEffect.objects.get().pk)
    assert not object_path.exists()
    assert models.MastraoTranscriptionBinding.objects.get().state == (
        models.MastraoTranscriptionBinding.State.FAILED
    )
    assert models.MastraoTranscriptionEffect.objects.get().state == (
        models.MastraoTranscriptionEffect.State.FAILED
    )
