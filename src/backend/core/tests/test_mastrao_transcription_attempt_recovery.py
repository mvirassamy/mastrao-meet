"""Provider-attempt recovery, immutable results and completion ordering proofs."""

import json
from unittest import mock

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage

import pytest

from core import models
from core.mastrao_transcription_adapter import (
    _accepted_recovery_transcript,
    _apply_transcription,
    _produce_transcript,
    _resume_or_transcribe,
)
from core.mastrao_transcription_artifact import (
    persist_result_recovery,
    persist_transcript,
    recovery_object_ref,
)
from core.mastrao_transcription_attempt import (
    bind_egress_grant,
    cas_sending,
    cleanup_attempt_recovery,
    mark_pre_egress_failure,
    mark_result,
    may_call_provider,
    prepare_attempt,
)
from core.mastrao_transcription_contract import (
    TranscriptionContractRefused,
    TranscriptionPipelineFailed,
)
from core.mastrao_transcription_pipeline import complete_transcription
from core.mastrao_transcription_worker import transcribe_audio
from core.tests.test_mastrao_transcription import (
    ENQUEUE,
    _effect,
    _fake_artifact,
    _finalized_recording_binding,
    _v3_effect,
)
from core.tests.test_mastrao_transcription_attempt import transcription_settings

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.usefixtures(transcription_settings.__name__),
]


def test_paid_sending_crash_becomes_unknown_and_is_not_resent(settings):
    """An interrupted paid send becomes unknown and cannot send again."""
    settings.MASTRAO_TRANSCRIPTION_ASR_MODE = "real"
    settings.MASTRAO_TRANSCRIPTION_PROVIDER = "mistral"
    settings.MASTRAO_TRANSCRIPTION_MODEL = "voxtral-mini-2602"
    settings.MASTRAO_ASR_GATEWAY_AUTH_TOKEN = "workload-token"
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
        codec = "flac"
        byte_size = 128

    attempt = prepare_attempt(local_effect, _Extracted())
    cas_sending(attempt)
    crashed = cas_sending(attempt)
    assert crashed.state == models.MastraoTranscriptionProviderAttempt.State.UNKNOWN
    assert may_call_provider(crashed) is False


def test_crash_after_object_save_resumes_without_asr(settings, tmp_path):
    """Resume a saved artifact without calling the provider again."""
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
    """Completion preserves the first accepted provider result checksum."""
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


def test_recovery_copy_is_deleted_after_cleanup(settings, tmp_path):
    """Cleanup deletes the recovery object and persists its completed state."""
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
    binding = _finalized_recording_binding("recoverydel0123456")
    effect = _effect(binding, transcription_ref="transcription_recoverydel01")
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
        sha256 = "d" * 64
        duration_ms = 4_000
        codec = "flac"
        byte_size = 128

    attempt = prepare_attempt(local_effect, _Extracted())
    transcript = transcribe_audio(b"cleanup audio")
    recovery_ref, checksum = persist_result_recovery(attempt.attempt_ref, transcript)
    attempt.result_recovery_ref = recovery_ref
    attempt.result_checksum = checksum
    attempt.save(update_fields=["result_recovery_ref", "result_checksum", "updated_at"])
    assert default_storage.exists(recovery_ref)
    cleanup_attempt_recovery(attempt)
    attempt.refresh_from_db()
    assert not default_storage.exists(recovery_ref)
    assert (
        attempt.cleanup_state
        == models.MastraoTranscriptionProviderAttempt.CleanupState.COMPLETED
    )


def test_proven_pre_egress_failure_stays_retryable_from_sending():
    """A proven failure before egress permits another provider send."""
    binding = _finalized_recording_binding("preeegress01234567")
    effect = _effect(binding, transcription_ref="transcription_preeegress012")
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
        sha256 = "e" * 64
        duration_ms = 4_000
        codec = "flac"
        byte_size = 128

    attempt = prepare_attempt(local_effect, _Extracted())
    sending = cas_sending(attempt)
    failed = mark_pre_egress_failure(sending)
    assert failed.state == (
        models.MastraoTranscriptionProviderAttempt.State.FAILED_PRE_EGRESS
    )
    assert may_call_provider(failed) is True


def test_recovery_object_is_discovered_without_db_bind(settings, tmp_path):
    """Discover a saved recovery object before its database binding exists."""
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
    binding = _finalized_recording_binding("discoverkey0123456")
    effect = _effect(binding, transcription_ref="transcription_discoverkey01")
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
        sha256 = "f" * 64
        duration_ms = 4_000
        codec = "flac"
        byte_size = 128

    attempt = prepare_attempt(local_effect, _Extracted())
    transcript = transcribe_audio(b"unbound recovery")
    transcript["audio_digest"] = _Extracted.sha256
    persist_result_recovery(attempt.attempt_ref, transcript)
    sending = cas_sending(attempt)
    resumed = _resume_or_transcribe(
        _Extracted(), sending, models.MastraoTranscriptionBinding.objects.get()
    )
    sending.refresh_from_db()
    assert sending.result_checksum
    assert sending.result_recovery_ref.endswith(f"{attempt.attempt_ref}.json")
    assert resumed["audio_digest"] == _Extracted.sha256


def test_substituted_recovery_is_refused_before_mark_result(settings, tmp_path):
    """Reject recovery for different audio before recording a result checksum."""
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
    binding = _finalized_recording_binding("substrecovery012345")
    effect = _effect(binding, transcription_ref="transcription_substrecovery")
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
        sha256 = "1" * 64
        duration_ms = 4_000
        codec = "flac"
        byte_size = 128

    attempt = prepare_attempt(local_effect, _Extracted())
    persist_result_recovery(attempt.attempt_ref, transcribe_audio(b"other audio"))
    sending = cas_sending(attempt)
    with pytest.raises(TranscriptionPipelineFailed):
        _resume_or_transcribe(
            _Extracted(),
            sending,
            models.MastraoTranscriptionBinding.objects.get(),
        )
    sending.refresh_from_db()
    assert sending.result_checksum is None


def test_paid_recovery_requires_exact_provider_model_engine():
    """Paid recovery accepts only the exact bound provider and model engine."""

    class _Attempt:
        provider_ref = "mistral"
        requested_model_ref = "voxtral-mini-2602"

    class _Extracted:
        sha256 = "a" * 64

    transcript = transcribe_audio(b"engine binding")
    transcript["audio_digest"] = _Extracted.sha256
    transcript["engine_ref"] = "mistral:voxtral-mini-2602"
    assert _accepted_recovery_transcript(transcript, _Extracted(), _Attempt())
    transcript["engine_ref"] = "openai:voxtral-mini-2602"
    assert _accepted_recovery_transcript(transcript, _Extracted(), _Attempt()) is None
    transcript["engine_ref"] = "mistral:gpt-transcribe"
    assert _accepted_recovery_transcript(transcript, _Extracted(), _Attempt()) is None
    transcript["engine_ref"] = "mistral:voxtral-mini-2602:extra"
    assert _accepted_recovery_transcript(transcript, _Extracted(), _Attempt()) is None
    transcript["engine_ref"] = "voxtral-mini-2602"
    assert _accepted_recovery_transcript(transcript, _Extracted(), _Attempt()) is None


def test_fake_recovery_accepts_unprefixed_deterministic_engine():
    """Fake recovery requires its unprefixed deterministic engine name."""

    class _Attempt:
        provider_ref = "fake"
        requested_model_ref = "fake-asr-deterministic-v1"

    class _Extracted:
        sha256 = "b" * 64

    transcript = transcribe_audio(b"fake engine")
    transcript["audio_digest"] = _Extracted.sha256
    transcript["engine_ref"] = "fake-asr-deterministic-v1"
    assert _accepted_recovery_transcript(transcript, _Extracted(), _Attempt())
    transcript["engine_ref"] = "fake:fake-asr-deterministic-v1"
    assert _accepted_recovery_transcript(transcript, _Extracted(), _Attempt()) is None


def _paid_attempt_with_recovery(local_effect):
    class Extracted:
        """Audio identity stored with the paid provider recovery transcript."""

        sha256 = "c" * 64
        duration_ms = 4_000
        codec = "flac"
        byte_size = 128

    attempt = prepare_attempt(local_effect, Extracted())
    transcript = {
        "schema_version": 1,
        "engine_ref": "mistral:voxtral-mini-2602",
        "language": "fr",
        "audio_digest": Extracted.sha256,
        "segments": [],
    }
    recovery_ref, _checksum = persist_result_recovery(attempt.attempt_ref, transcript)
    return mark_result(attempt, transcript, recovery_ref=recovery_ref)


def test_ack_failure_after_core_acceptance_replays_only_ack(settings, tmp_path):
    """Retry a failed acknowledgement after Core accepts the result."""
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
    settings.MASTRAO_TRANSCRIPTION_ASR_MODE = "real"
    settings.MASTRAO_TRANSCRIPTION_PROVIDER = "mistral"
    settings.MASTRAO_TRANSCRIPTION_MODEL = "voxtral-mini-2602"
    settings.MASTRAO_ASR_GATEWAY_AUTH_TOKEN = "workload-token"
    settings.MASTRAO_TRANSCRIPTION_ASR_ENDPOINT = (
        "https://asr.example.test/v1/transcribe"
    )
    binding = _finalized_recording_binding("ackreplay012345678")
    effect = _effect(binding, transcription_ref="transcription_ackreplay0123")
    with (
        mock.patch(ENQUEUE),
        mock.patch(
            "core.mastrao_transcription_adapter.sign_submit_receipt",
            return_value="receipt.payload.signature",
        ),
    ):
        _apply_transcription(effect)
    local_effect = models.MastraoTranscriptionEffect.objects.get()
    attempt = _paid_attempt_with_recovery(local_effect)
    events = []
    acks = []

    def notify(*_args, **_kwargs):
        """Record the Core notification before gateway acknowledgement."""
        events.append("core")

    def ack(*_args, **_kwargs):
        """Record the gateway acknowledgement and its simulated retry."""
        events.append("ack")
        acks.append(1)
        if len(acks) == 1:
            raise TranscriptionContractRefused(status=503, outcome="retry")

    with (
        mock.patch(
            "core.mastrao_transcription_adapter._produce_transcript",
            return_value=_fake_artifact(),
        ) as provider,
        mock.patch(
            "core.mastrao_transcription_adapter._notify_core_artifact",
            side_effect=notify,
        ) as core,
        mock.patch(
            "core.mastrao_transcription_worker.ack_gateway_attempt",
            side_effect=ack,
        ),
    ):
        with pytest.raises(TranscriptionContractRefused) as refused:
            complete_transcription(local_effect.pk)
        assert refused.value.outcome == "retry"
        local_effect.refresh_from_db()
        assert local_effect.dispatch_state == (
            models.MastraoTranscriptionEffect.DispatchState.CLEANUP_PENDING
        )
        assert default_storage.exists(attempt.result_recovery_ref)
        complete_transcription(local_effect.pk)
    provider.assert_called_once()
    core.assert_called_once()
    assert len(acks) == 2
    assert events == ["core", "ack", "ack"]
    local_effect.refresh_from_db()
    assert local_effect.dispatch_state == (
        models.MastraoTranscriptionEffect.DispatchState.COMPLETED
    )
    assert not default_storage.exists(attempt.result_recovery_ref)


@pytest.mark.parametrize(
    "terminal_case",
    [
        (409, "conflict", "conflict", False),
        (404, "deleted", "deleted", False),
        (409, "conflict", "conflict", True),
        (404, "deleted", "deleted", True),
    ],
)
def test_terminal_core_acceptance_replays_only_failed_ack(
    settings, tmp_path, terminal_case
):
    """Accepted terminal outcomes retry only the failed acknowledgement."""
    status, outcome, expected_terminal, terminal_refusal = terminal_case
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
    settings.MASTRAO_TRANSCRIPTION_ASR_MODE = "real"
    settings.MASTRAO_TRANSCRIPTION_PROVIDER = "openai"
    settings.MASTRAO_TRANSCRIPTION_MODEL = "gpt-transcribe"
    settings.MASTRAO_ASR_GATEWAY_AUTH_TOKEN = "workload-token"
    settings.MASTRAO_TRANSCRIPTION_ASR_ENDPOINT = (
        "https://asr.example.test/v1/transcribe"
    )
    binding = _finalized_recording_binding(f"terminalack{status}012345")
    effect = _v3_effect(binding, transcription_ref=f"transcription_terminalack{status}")
    with (
        mock.patch(ENQUEUE),
        mock.patch(
            "core.mastrao_transcription_adapter.sign_submit_receipt",
            return_value="receipt.payload.signature",
        ),
    ):
        _apply_transcription(effect)
    local_effect = models.MastraoTranscriptionEffect.objects.get()
    attempt = _paid_attempt_with_recovery(local_effect)
    attempt = bind_egress_grant(
        attempt,
        {
            "grant_semantic_digest": "d" * 64,
            "authority_version": 7,
            "campaign_ref": "managed-canary-2026-08",
            "authorized_cost_ceiling_micros": 10_000,
            "tariff_catalog_version": "asr-tariff-v2",
            "execution_mode": "send_allowed",
        },
    )
    recovery_ref = attempt.result_recovery_ref
    acknowledgements = []

    def ack(*_args, **_kwargs):
        """Record the gateway acknowledgement and its simulated retry."""
        acknowledgements.append(1)
        if len(acknowledgements) == 1:
            raise TranscriptionContractRefused(status=503, outcome="retry")

    with (
        mock.patch(
            "core.mastrao_transcription_adapter._produce_transcript",
            return_value=_fake_artifact(),
        ) as provider,
        mock.patch(
            "core.mastrao_transcription_adapter._notify_core_artifact",
            side_effect=TranscriptionContractRefused(status=status, outcome=outcome),
        ) as core,
        mock.patch(
            "core.mastrao_transcription_adapter._notify_core_failure",
            return_value={"state": "failed", "outcome": "failed"},
            side_effect=(
                TranscriptionContractRefused(status=status, outcome=outcome)
                if terminal_refusal
                else None
            ),
        ) as terminal_core,
        mock.patch(
            "core.mastrao_transcription_worker.ack_gateway_attempt",
            side_effect=ack,
        ),
    ):
        with pytest.raises(TranscriptionContractRefused) as refused:
            complete_transcription(local_effect.pk)
        assert refused.value.outcome == "retry"
        local_effect.refresh_from_db()
        assert local_effect.state == models.MastraoTranscriptionEffect.State.FAILED
        assert local_effect.dispatch_state == (
            models.MastraoTranscriptionEffect.DispatchState.CLEANUP_PENDING
        )
        assert default_storage.exists(recovery_ref)
        complete_transcription(local_effect.pk)
    provider.assert_called_once()
    core.assert_called_once()
    terminal_core.assert_called_once()
    assert len(acknowledgements) == 2
    attempt.refresh_from_db()
    assert attempt.terminal_outcome == expected_terminal
    local_effect.refresh_from_db()
    assert local_effect.dispatch_state == (
        models.MastraoTranscriptionEffect.DispatchState.COMPLETED
    )
    assert not default_storage.exists(recovery_ref)


def test_terminal_outcome_remains_immutable_when_artifact_refusal_changes(
    settings, tmp_path
):
    """Preserve the first terminal outcome across later artifact refusals."""
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
    settings.MASTRAO_TRANSCRIPTION_ASR_MODE = "real"
    binding = _finalized_recording_binding("terminalimmutable01")
    effect = _v3_effect(binding, transcription_ref="transcription_terminalimmutable")
    with (
        mock.patch(ENQUEUE),
        mock.patch(
            "core.mastrao_transcription_adapter.sign_submit_receipt",
            return_value="receipt.payload.signature",
        ),
    ):
        _apply_transcription(effect)
    local_effect = models.MastraoTranscriptionEffect.objects.get()
    attempt = bind_egress_grant(
        _paid_attempt_with_recovery(local_effect),
        {
            "grant_semantic_digest": "d" * 64,
            "authority_version": 7,
            "campaign_ref": "managed-canary-2026-08",
            "authorized_cost_ceiling_micros": 10_000,
            "tariff_catalog_version": "asr-tariff-v2",
            "execution_mode": "send_allowed",
        },
    )
    recovery_ref = attempt.result_recovery_ref
    with (
        mock.patch(
            "core.mastrao_transcription_adapter._produce_transcript",
            return_value=_fake_artifact(),
        ) as provider,
        mock.patch(
            "core.mastrao_transcription_adapter._notify_core_artifact",
            side_effect=[
                TranscriptionContractRefused(status=409, outcome="conflict"),
                TranscriptionContractRefused(status=404, outcome="deleted"),
            ],
        ) as artifact_core,
        mock.patch(
            "core.mastrao_transcription_adapter._notify_core_failure",
            side_effect=[
                TranscriptionContractRefused(status=503, outcome="retry"),
                {"state": "failed", "outcome": "failed"},
            ],
        ) as terminal_core,
        mock.patch("core.mastrao_transcription_worker.ack_gateway_attempt"),
    ):
        with pytest.raises(TranscriptionContractRefused) as refused:
            complete_transcription(local_effect.pk)
        assert refused.value.outcome == "retry"
        attempt.refresh_from_db()
        assert attempt.terminal_outcome == "conflict"
        complete_transcription(local_effect.pk)
    provider.assert_called_once()
    assert artifact_core.call_count == 2
    assert terminal_core.call_count == 2
    attempt.refresh_from_db()
    assert attempt.terminal_outcome == "conflict"
    local_effect.refresh_from_db()
    assert local_effect.dispatch_state == (
        models.MastraoTranscriptionEffect.DispatchState.COMPLETED
    )
    assert not default_storage.exists(recovery_ref)


def test_core_retry_precedes_ack_and_does_not_rerun_asr(settings, tmp_path):
    """Retry Core acceptance before acknowledgement without repeating ASR."""
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
    settings.MASTRAO_TRANSCRIPTION_ASR_MODE = "real"
    settings.MASTRAO_TRANSCRIPTION_PROVIDER = "mistral"
    settings.MASTRAO_TRANSCRIPTION_MODEL = "voxtral-mini-2602"
    settings.MASTRAO_ASR_GATEWAY_AUTH_TOKEN = "workload-token"
    settings.MASTRAO_TRANSCRIPTION_ASR_ENDPOINT = (
        "https://asr.example.test/v1/transcribe"
    )
    binding = _finalized_recording_binding("corebeforeack01234")
    effect = _effect(binding, transcription_ref="transcription_corebeforeack")
    with (
        mock.patch(ENQUEUE),
        mock.patch(
            "core.mastrao_transcription_adapter.sign_submit_receipt",
            return_value="receipt.payload.signature",
        ),
    ):
        _apply_transcription(effect)
    local_effect = models.MastraoTranscriptionEffect.objects.get()
    attempt = _paid_attempt_with_recovery(local_effect)
    events = []

    def notify(*_args, **_kwargs):
        """Record the Core notification before gateway acknowledgement."""
        events.append("core")
        if events.count("core") == 1:
            raise TranscriptionContractRefused(status=503, outcome="retry")

    def ack(*_args, **_kwargs):
        """Record the gateway acknowledgement and its simulated retry."""
        events.append("ack")

    with (
        mock.patch(
            "core.mastrao_transcription_adapter._produce_transcript",
            return_value=_fake_artifact(),
        ) as provider,
        mock.patch(
            "core.mastrao_transcription_adapter._notify_core_artifact",
            side_effect=notify,
        ) as core,
        mock.patch(
            "core.mastrao_transcription_worker.ack_gateway_attempt",
            side_effect=ack,
        ) as gateway_ack,
    ):
        with pytest.raises(TranscriptionContractRefused):
            complete_transcription(local_effect.pk)
        gateway_ack.assert_not_called()
        assert default_storage.exists(attempt.result_recovery_ref)
        complete_transcription(local_effect.pk)
    provider.assert_called_once()
    assert core.call_count == 2
    gateway_ack.assert_called_once()
    assert events == ["core", "core", "ack"]


def test_revocation_discards_an_inflight_second_run_recovery(settings, tmp_path):
    """Revocation deletes the alternate run recovery and preserves the first run."""
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
    recording = _finalized_recording_binding("tworunrevoke012345")
    first_effect = _effect(
        recording, transcription_ref="transcription_revoke_primary01"
    )
    second_effect = _effect(
        recording,
        transcription_ref="transcription_revoke_alternate",
        effect_key="effect_transcribe_alternate01",
        jti="request_transcribe_alternate01",
    )
    with (
        mock.patch(ENQUEUE),
        mock.patch(
            "core.mastrao_transcription_adapter.sign_submit_receipt",
            return_value="receipt.payload.signature",
        ),
    ):
        _apply_transcription(first_effect)
        _apply_transcription(second_effect)

    first = models.MastraoTranscriptionBinding.objects.get(
        transcription_ref=first_effect["transcription_ref"]
    )
    alternate = models.MastraoTranscriptionBinding.objects.get(
        transcription_ref=second_effect["transcription_ref"]
    )

    class _Extracted:
        sha256 = "9" * 64
        duration_ms = 4_000
        codec = "flac"
        byte_size = 128

        @staticmethod
        def close():
            """Close the extracted-audio stand-in without allocating resources."""
            return None

    transcript = transcribe_audio(b"late alternate transcript")
    transcript["audio_digest"] = _Extracted.sha256
    authority_revoked = TranscriptionContractRefused(status=404, outcome="deleted")
    with (
        mock.patch(
            "core.mastrao_transcription_adapter.extract_verified_audio_file",
            return_value=_Extracted(),
        ),
        mock.patch(
            "core.mastrao_transcription_adapter._assert_transcription_authority",
            side_effect=[alternate, alternate, alternate, authority_revoked],
        ),
        mock.patch(
            "core.mastrao_transcription_adapter.transcribe_extracted",
            return_value=transcript,
        ) as provider,
    ):
        with pytest.raises(TranscriptionContractRefused) as refused:
            _produce_transcript(alternate)

    assert refused.value.outcome == "deleted"
    provider.assert_called_once()
    attempt = models.MastraoTranscriptionProviderAttempt.objects.get(
        effect__transcription_binding=alternate
    )
    assert attempt.cleanup_state == (
        models.MastraoTranscriptionProviderAttempt.CleanupState.COMPLETED
    )
    assert attempt.result_recovery_ref is None
    assert not default_storage.exists(recovery_object_ref(attempt.attempt_ref))
    alternate.refresh_from_db()
    assert alternate.object_ref is None
    first.refresh_from_db()
    assert first.state == models.MastraoTranscriptionBinding.State.PROCESSING
