"""Transcription artifact delivery, cleanup and concurrent terminal outcomes."""

import threading
from unittest import mock

import pytest

from core import models
from core.mastrao_transcription_adapter import (
    _apply_transcription,
    _notify_core_artifact,
    _notify_core_failure,
)
from core.mastrao_transcription_artifact import (
    delete_transcript_object,
    map_speakers,
    persist_transcript,
)
from core.mastrao_transcription_contract import (
    TranscriptionContractRefused,
    TranscriptionPipelineFailed,
)
from core.mastrao_transcription_pipeline import (
    _persist_artifact_pending,
    _persist_failure_pending,
    complete_transcription,
)
from core.mastrao_transcription_worker import transcribe_audio
from core.tests import test_mastrao_transcription as submission_proofs
from core.tests.test_mastrao_transcription import (
    CORE_FAILED_OUTCOME,
    ENQUEUE,
    _effect,
    _fake_artifact,
    _finalized_recording_binding,
)

pytestmark = pytest.mark.django_db
transcription_settings = submission_proofs.transcription_settings


def test_celery_completion_notifies_core_after_submit_receipt():
    """Celery completion notifies core after submit receipt."""

    binding = _finalized_recording_binding("notify_0123456789ab")
    effect = _effect(binding)
    with (
        mock.patch(ENQUEUE),
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
    """Artifact notification posts signed receipt to core."""

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
            return_value={
                "artifactRef": "transcript_0123456789abcdef",
                "outcome": "available",
            },
        ) as post,
    ):
        _notify_core_artifact(effect, _fake_artifact())
    post.assert_called_once()
    body = post.call_args.kwargs["body"]
    assert body == {"transcription_artifact_receipt": "artifact.receipt.signature"}


def test_failure_notification_accepts_available_when_core_already_has_artifact(
    settings,
):
    """Failure notification accepts available when core already has artifact."""

    settings.MASTRAO_CORE_TRANSCRIPTION_FAILURE_ENDPOINT = (
        "http://cabinet-core:3911/internal/v1/meetings/transcription/failures"
    )
    binding = _finalized_recording_binding("failavail_01234567")
    effect = _effect(binding)
    with (
        mock.patch(
            "core.mastrao_transcription_adapter.sign_transcription_failure_receipt",
            return_value="failure.receipt.signature",
        ),
        mock.patch(
            "core.mastrao_transcription_adapter.post_core_json",
            return_value={
                "transcriptionRef": effect["transcription_ref"],
                "state": "available",
                "outcome": "available",
            },
        ),
    ):
        result = _notify_core_failure(effect, "asr_failed")
    assert result["outcome"] == "available"
    assert result["state"] == "available"


def test_pipeline_failure_marks_local_state_and_notifies_core():
    """Pipeline failure marks local state and notifies core."""

    binding = _finalized_recording_binding("failure_0123456789a")
    effect = _effect(binding)
    with (
        mock.patch(ENQUEUE),
        mock.patch(
            "core.mastrao_transcription_adapter.sign_submit_receipt",
            return_value="receipt.payload.signature",
        ),
        mock.patch(
            "core.mastrao_transcription_adapter._produce_transcript",
            side_effect=TranscriptionPipelineFailed("asr_failed"),
        ),
        mock.patch(
            "core.mastrao_transcription_adapter._notify_core_failure",
            return_value=CORE_FAILED_OUTCOME,
        ) as notify,
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


def test_artifact_callback_never_runs_before_submit_receipt():
    """Artifact callback never runs before submit receipt."""

    binding = _finalized_recording_binding("order_0123456789abc")
    effect = _effect(binding)
    events = []
    with (
        mock.patch(ENQUEUE),
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
    """Deleted recording refusal removes the written object."""

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
        mock.patch(ENQUEUE),
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
        complete_transcription(models.MastraoTranscriptionEffect.objects.get().pk)
    assert not object_path.exists()
    assert models.MastraoTranscriptionBinding.objects.get().state == (
        models.MastraoTranscriptionBinding.State.FAILED
    )
    assert models.MastraoTranscriptionEffect.objects.get().state == (
        models.MastraoTranscriptionEffect.State.FAILED
    )
    assert models.MastraoTranscriptionEffect.objects.get().dispatch_state == (
        models.MastraoTranscriptionEffect.DispatchState.COMPLETED
    )


def test_already_failed_artifact_callback_cleans_and_completes(settings, tmp_path):
    """Already failed artifact callback cleans and completes."""

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
    binding = _finalized_recording_binding("failedwin_01234567")
    effect = _effect(binding, transcription_ref="transcription_failedwin_012")
    transcript = map_speakers(transcribe_audio(b"already failed audio"))
    artifact = persist_transcript(effect["transcription_ref"], transcript)
    object_path = (
        tmp_path / "mastrao-transcripts" / f"{effect['transcription_ref']}.json"
    )
    with (
        mock.patch(ENQUEUE),
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
            side_effect=TranscriptionContractRefused(status=409, outcome="failed"),
        ),
    ):
        _apply_transcription(effect)
        complete_transcription(models.MastraoTranscriptionEffect.objects.get().pk)
    assert not object_path.exists()
    local_effect = models.MastraoTranscriptionEffect.objects.get()
    assert local_effect.dispatch_state == (
        models.MastraoTranscriptionEffect.DispatchState.COMPLETED
    )
    assert local_effect.state == models.MastraoTranscriptionEffect.State.FAILED


def test_divergent_conflict_deletes_the_object_and_fails(settings, tmp_path):
    """Divergent conflict deletes the object and fails."""

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
    binding = _finalized_recording_binding("conflictwin_012345")
    effect = _effect(binding, transcription_ref="transcription_conflictwin01")
    transcript = map_speakers(transcribe_audio(b"divergent conflict audio"))
    artifact = persist_transcript(effect["transcription_ref"], transcript)
    object_path = (
        tmp_path / "mastrao-transcripts" / f"{effect['transcription_ref']}.json"
    )
    with (
        mock.patch(ENQUEUE),
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
            side_effect=TranscriptionContractRefused(status=409, outcome="conflict"),
        ),
    ):
        _apply_transcription(effect)
        complete_transcription(models.MastraoTranscriptionEffect.objects.get().pk)
    assert not object_path.exists()
    local_effect = models.MastraoTranscriptionEffect.objects.get()
    assert local_effect.dispatch_state == (
        models.MastraoTranscriptionEffect.DispatchState.COMPLETED
    )
    assert local_effect.state == models.MastraoTranscriptionEffect.State.FAILED
    assert (
        models.MastraoTranscriptionBinding.objects.get().state
        == models.MastraoTranscriptionBinding.State.FAILED
    )


def test_late_failure_callback_converges_to_available_after_artifact():
    """Late failure callback converges to available after artifact."""

    binding = _finalized_recording_binding("latefail_012345678")
    effect = _effect(binding)
    with (
        mock.patch(ENQUEUE),
        mock.patch(
            "core.mastrao_transcription_adapter.sign_submit_receipt",
            return_value="receipt.payload.signature",
        ),
        mock.patch(
            "core.mastrao_transcription_adapter._produce_transcript",
            return_value=_fake_artifact(),
        ),
        mock.patch("core.mastrao_transcription_adapter._notify_core_artifact"),
        mock.patch(
            "core.mastrao_transcription_adapter._notify_core_failure",
            return_value={
                "transcriptionRef": effect["transcription_ref"],
                "state": "available",
                "outcome": "available",
            },
        ),
    ):
        _apply_transcription(effect)
        complete_transcription(models.MastraoTranscriptionEffect.objects.get().pk)
        local_effect = models.MastraoTranscriptionEffect.objects.get()
        local_effect.dispatch_state = (
            models.MastraoTranscriptionEffect.DispatchState.FAILURE_NOTIFICATION_PENDING
        )
        local_effect.failure_code = "asr_failed"
        local_effect.save(
            update_fields=["dispatch_state", "failure_code", "updated_at"]
        )
        complete_transcription(local_effect.pk)
    local_effect.refresh_from_db()
    assert local_effect.state == models.MastraoTranscriptionEffect.State.APPLIED
    assert local_effect.dispatch_state == (
        models.MastraoTranscriptionEffect.DispatchState.COMPLETED
    )
    assert models.MastraoTranscriptionBinding.objects.get().state == (
        models.MastraoTranscriptionBinding.State.AVAILABLE
    )


@pytest.mark.django_db(transaction=True)
def test_concurrent_failure_does_not_notify_after_artifact_persisted():
    """Concurrent failure does not notify after artifact persisted."""

    binding = _finalized_recording_binding("racefail_012345678")
    effect = _effect(binding)
    produce_entered = threading.Event()
    artifact_written = threading.Event()
    notify_failure = mock.Mock(return_value=CORE_FAILED_OUTCOME)

    def produce(*_args, **_kwargs):
        produce_entered.set()
        assert artifact_written.wait(timeout=5)
        raise TranscriptionPipelineFailed("asr_failed")

    def run():
        try:
            complete_transcription(models.MastraoTranscriptionEffect.objects.get().pk)
        except TranscriptionPipelineFailed:
            pass

    with (
        mock.patch(ENQUEUE),
        mock.patch(
            "core.mastrao_transcription_adapter.sign_submit_receipt",
            return_value="receipt.payload.signature",
        ),
        mock.patch(
            "core.mastrao_transcription_adapter._produce_transcript",
            side_effect=produce,
        ),
        mock.patch("core.mastrao_transcription_adapter._notify_core_artifact"),
        mock.patch(
            "core.mastrao_transcription_adapter._notify_core_failure",
            new=notify_failure,
        ),
    ):
        _apply_transcription(effect)
        thread = threading.Thread(target=run)
        thread.start()
        assert produce_entered.wait(timeout=5)
        local_effect = models.MastraoTranscriptionEffect.objects.get()
        transcription = models.MastraoTranscriptionBinding.objects.get()
        _persist_artifact_pending(local_effect.pk, transcription.pk, _fake_artifact())
        artifact_written.set()
        thread.join(timeout=5)
    assert thread.is_alive() is False
    notify_failure.assert_not_called()
    local_effect.refresh_from_db()
    assert local_effect.dispatch_state == (
        models.MastraoTranscriptionEffect.DispatchState.COMPLETED
    )
    assert local_effect.state == models.MastraoTranscriptionEffect.State.APPLIED
    assert models.MastraoTranscriptionBinding.objects.get().state == (
        models.MastraoTranscriptionBinding.State.AVAILABLE
    )


def test_first_artifact_pending_is_not_replaced_by_a_concurrent_producer():
    """First artifact pending is not replaced by a concurrent producer."""

    first = _fake_artifact(transcript_artifact_ref="transcript_aaaaaaaaaaaaaaaa")
    second = _fake_artifact(
        transcript_artifact_ref="transcript_bbbbbbbbbbbbbbbb",
        checksum_digest="b" * 64,
        byte_size=1024,
    )
    binding = _finalized_recording_binding("raceart_012345678")
    effect = _effect(binding)
    with (
        mock.patch(ENQUEUE),
        mock.patch(
            "core.mastrao_transcription_adapter.sign_submit_receipt",
            return_value="receipt.payload.signature",
        ),
    ):
        _apply_transcription(effect)
        local_effect = models.MastraoTranscriptionEffect.objects.get()
        transcription = models.MastraoTranscriptionBinding.objects.get()
        assert (
            _persist_artifact_pending(local_effect.pk, transcription.pk, first)
            == "notify_artifact"
        )
        assert (
            _persist_artifact_pending(local_effect.pk, transcription.pk, second)
            == "notify_artifact"
        )
    transcription.refresh_from_db()
    assert transcription.transcript_artifact_ref == first["transcript_artifact_ref"]
    assert transcription.checksum_digest == first["checksum_digest"]
    assert transcription.byte_size == first["byte_size"]


def test_losing_artifact_is_deleted_after_core_confirms_failure(settings, tmp_path):
    """Losing artifact is deleted after core confirms failure."""

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
    binding = _finalized_recording_binding("loserart_01234567")
    effect = _effect(binding, transcription_ref="transcription_loserart_012")
    notify_artifact = mock.Mock()
    with (
        mock.patch(ENQUEUE),
        mock.patch(
            "core.mastrao_transcription_adapter.sign_submit_receipt",
            return_value="receipt.payload.signature",
        ),
        mock.patch(
            "core.mastrao_transcription_adapter._notify_core_failure",
            return_value=CORE_FAILED_OUTCOME,
        ) as notify_failure,
        mock.patch(
            "core.mastrao_transcription_adapter._notify_core_artifact",
            new=notify_artifact,
        ),
    ):
        _apply_transcription(effect)
        local_effect = models.MastraoTranscriptionEffect.objects.get()
        transcription = models.MastraoTranscriptionBinding.objects.get()
        _persist_failure_pending(local_effect.pk, "asr_failed")
        transcript = map_speakers(transcribe_audio(b"losing artifact audio"))
        artifact = persist_transcript(effect["transcription_ref"], transcript)
        object_path = (
            tmp_path / "mastrao-transcripts" / f"{effect['transcription_ref']}.json"
        )
        assert object_path.exists()
        assert (
            _persist_artifact_pending(local_effect.pk, transcription.pk, artifact)
            == "notify_failure"
        )
        complete_transcription(local_effect.pk)
    notify_artifact.assert_not_called()
    notify_failure.assert_called_once()
    assert not object_path.exists()
    local_effect.refresh_from_db()
    assert local_effect.state == models.MastraoTranscriptionEffect.State.FAILED
    assert local_effect.dispatch_state == (
        models.MastraoTranscriptionEffect.DispatchState.COMPLETED
    )
    assert models.MastraoTranscriptionBinding.objects.get().checksum_digest is None


def test_losing_artifact_delete_retries_after_storage_503(settings, tmp_path):
    """Losing artifact delete retries after storage 503."""

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
    binding = _finalized_recording_binding("del503_0123456789")
    effect = _effect(binding, transcription_ref="transcription_del503_01234")
    delete_attempts = []

    def flaky_delete(object_ref):
        delete_attempts.append(1)
        if len(delete_attempts) == 1:
            raise TranscriptionContractRefused(status=503)
        delete_transcript_object(object_ref)

    with (
        mock.patch(ENQUEUE),
        mock.patch(
            "core.mastrao_transcription_adapter.sign_submit_receipt",
            return_value="receipt.payload.signature",
        ),
        mock.patch(
            "core.mastrao_transcription_adapter._notify_core_failure",
            return_value=CORE_FAILED_OUTCOME,
        ),
        mock.patch(
            "core.mastrao_transcription_pipeline.delete_transcript_object",
            side_effect=flaky_delete,
        ),
    ):
        _apply_transcription(effect)
        local_effect = models.MastraoTranscriptionEffect.objects.get()
        transcription = models.MastraoTranscriptionBinding.objects.get()
        _persist_failure_pending(local_effect.pk, "asr_failed")
        transcript = map_speakers(transcribe_audio(b"retry delete audio"))
        artifact = persist_transcript(effect["transcription_ref"], transcript)
        object_path = (
            tmp_path / "mastrao-transcripts" / f"{effect['transcription_ref']}.json"
        )
        _persist_artifact_pending(local_effect.pk, transcription.pk, artifact)
        with pytest.raises(TranscriptionContractRefused) as refusal:
            complete_transcription(local_effect.pk)
        assert refusal.value.status == 503
        assert object_path.exists()
        local_effect.refresh_from_db()
        assert local_effect.dispatch_state == (
            models.MastraoTranscriptionEffect.DispatchState.FAILURE_NOTIFICATION_PENDING
        )
        complete_transcription(local_effect.pk)
    assert len(delete_attempts) == 2
    assert not object_path.exists()
    local_effect.refresh_from_db()
    assert local_effect.state == models.MastraoTranscriptionEffect.State.FAILED
    assert local_effect.dispatch_state == (
        models.MastraoTranscriptionEffect.DispatchState.COMPLETED
    )


def test_503_does_not_delete_the_written_object(settings, tmp_path):
    """503 does not delete the written object."""

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
    binding = _finalized_recording_binding("keep503_0123456789")
    effect = _effect(binding, transcription_ref="transcription_persist_01234")
    transcript = map_speakers(transcribe_audio(b"retryable callback audio"))
    artifact = persist_transcript(effect["transcription_ref"], transcript)
    object_path = (
        tmp_path / "mastrao-transcripts" / f"{effect['transcription_ref']}.json"
    )
    with (
        mock.patch(ENQUEUE),
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
            side_effect=TranscriptionContractRefused(status=503),
        ),
    ):
        _apply_transcription(effect)
        with pytest.raises(TranscriptionContractRefused):
            complete_transcription(models.MastraoTranscriptionEffect.objects.get().pk)
    assert object_path.exists()
    assert models.MastraoTranscriptionBinding.objects.get().state == (
        models.MastraoTranscriptionBinding.State.PROCESSING
    )
