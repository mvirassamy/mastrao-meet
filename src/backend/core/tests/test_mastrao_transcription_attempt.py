"""Provider-attempt durability, object recovery and queue isolation proofs."""

# pylint: disable=missing-function-docstring

import json
from unittest import mock

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage

import pytest

from core import models
from core.mastrao_transcription_adapter import _apply_transcription
from core.mastrao_transcription_artifact import persist_transcript
from core.mastrao_transcription_attempt import (
    cas_sending,
    may_call_provider,
    prepare_attempt,
)
from core.mastrao_transcription_pipeline import complete_transcription
from core.mastrao_transcription_worker import _validated_transcript, transcribe_audio
from core.tests.test_mastrao_transcription import (
    ENQUEUE,
    _effect,
    _fake_artifact,
    _finalized_recording_binding,
)

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def transcription_settings(settings):
    settings.MASTRAO_MEETING_RECORDING_ENABLED = True
    settings.MASTRAO_MEETING_TRANSCRIPTION_ENABLED = True
    settings.MASTRAO_TRANSCRIPTION_ASR_MODE = "fake"


def test_missing_confidence_is_accepted_and_not_fabricated():
    transcript = transcribe_audio(b"optional confidence")
    del transcript["segments"][0]["confidence"]
    validated = _validated_transcript(transcript)
    assert "confidence" not in validated["segments"][0]


def test_enqueue_uses_dedicated_mastrao_transcription_queue():
    binding = _finalized_recording_binding("queueiso_012345678")
    effect = _effect(binding, transcription_ref="transcription_queueiso012345")
    with (
        mock.patch(ENQUEUE) as enqueue,
        mock.patch(
            "core.mastrao_transcription_adapter.sign_submit_receipt",
            return_value="receipt.payload.signature",
        ),
    ):
        _apply_transcription(effect)
    assert enqueue.call_args.kwargs["queue"] == "mastrao-transcription"


def test_concurrent_prepare_creates_one_attempt():
    binding = _finalized_recording_binding("oneattempt01234567")
    effect = _effect(binding, transcription_ref="transcription_oneattempt012")
    with (
        mock.patch(ENQUEUE),
        mock.patch(
            "core.mastrao_transcription_adapter.sign_submit_receipt",
            return_value="receipt.payload.signature",
        ),
    ):
        _apply_transcription(effect)
    local_effect = models.MastraoTranscriptionEffect.objects.get()

    class _Extracted:
        sha256 = "a" * 64
        duration_ms = 4_000
        codec = "wav_pcm_s16le"
        byte_size = 128

    first = prepare_attempt(local_effect, _Extracted())
    second = prepare_attempt(local_effect, _Extracted())
    assert first.pk == second.pk
    assert (
        models.MastraoTranscriptionProviderAttempt.objects.filter(
            effect=local_effect
        ).count()
        == 1
    )


def test_paid_sending_crash_becomes_unknown_and_is_not_resent(settings):
    settings.MASTRAO_TRANSCRIPTION_ASR_MODE = "real"
    settings.MASTRAO_TRANSCRIPTION_PROVIDER = "mistral"
    settings.MASTRAO_TRANSCRIPTION_MODEL = "voxtral-mini-2602"
    binding = _finalized_recording_binding("unknownsend0123456")
    effect = _effect(binding, transcription_ref="transcription_unknownsend01")
    with (
        mock.patch(ENQUEUE),
        mock.patch(
            "core.mastrao_transcription_adapter.sign_submit_receipt",
            return_value="receipt.payload.signature",
        ),
    ):
        _apply_transcription(effect)
    local_effect = models.MastraoTranscriptionEffect.objects.get()

    class _Extracted:
        sha256 = "b" * 64
        duration_ms = 4_000
        codec = "wav_pcm_s16le"
        byte_size = 128

    attempt = prepare_attempt(local_effect, _Extracted())
    cas_sending(attempt)
    crashed = cas_sending(attempt)
    assert crashed.state == models.MastraoTranscriptionProviderAttempt.State.UNKNOWN
    assert may_call_provider(crashed) is False


def test_crash_after_object_save_resumes_without_asr(settings, tmp_path):
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
    binding = _finalized_recording_binding("objcrash_012345678")
    effect = _effect(binding, transcription_ref="transcription_objcrash01234")
    with (
        mock.patch(ENQUEUE),
        mock.patch(
            "core.mastrao_transcription_adapter.sign_submit_receipt",
            return_value="receipt.payload.signature",
        ),
    ):
        _apply_transcription(effect)
    transcript = transcribe_audio(b"recovery audio")
    artifact = persist_transcript(effect["transcription_ref"], transcript)
    transcription = models.MastraoTranscriptionBinding.objects.get()
    transcription.object_ref = artifact["object_ref"]
    transcription.engine_ref = artifact["engine_ref"]
    transcription.save(update_fields=["object_ref", "engine_ref", "updated_at"])
    local_effect = models.MastraoTranscriptionEffect.objects.get()
    with (
        mock.patch("core.mastrao_transcription_adapter._produce_transcript") as produce,
        mock.patch("core.mastrao_transcription_adapter._notify_core_artifact"),
    ):
        complete_transcription(local_effect.pk)
    produce.assert_not_called()
    transcription.refresh_from_db()
    assert transcription.checksum_digest == artifact["checksum_digest"]
    assert default_storage.exists(artifact["object_ref"])


def test_second_provider_result_cannot_overwrite_first_checksum(settings, tmp_path):
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
    binding = _finalized_recording_binding("firstwrite01234567")
    effect = _effect(binding, transcription_ref="transcription_firstwrite012")
    with (
        mock.patch(ENQUEUE),
        mock.patch(
            "core.mastrao_transcription_adapter.sign_submit_receipt",
            return_value="receipt.payload.signature",
        ),
        mock.patch("core.mastrao_transcription_adapter._notify_core_artifact"),
        mock.patch(
            "core.mastrao_transcription_adapter._produce_transcript",
            return_value=_fake_artifact(),
        ),
    ):
        _apply_transcription(effect)
        complete_transcription(models.MastraoTranscriptionEffect.objects.get().pk)
    first = models.MastraoTranscriptionBinding.objects.get()
    default_storage.save(
        first.object_ref,
        ContentFile(json.dumps({"version": 1, "overwrite": True}).encode()),
    )
    with (
        mock.patch("core.mastrao_transcription_adapter._produce_transcript") as produce,
        mock.patch("core.mastrao_transcription_adapter._notify_core_artifact"),
    ):
        complete_transcription(models.MastraoTranscriptionEffect.objects.get().pk)
    produce.assert_not_called()
    first.refresh_from_db()
    assert first.checksum_digest == "9" * 64
