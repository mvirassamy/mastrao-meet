"""Provider-attempt durability, object recovery and queue isolation proofs."""

# pylint: disable=missing-function-docstring

import hashlib
import json
from unittest import mock

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.utils import timezone

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
    cas_sending,
    cleanup_attempt_recovery,
    mark_pre_egress_failure,
    may_call_provider,
    prepare_attempt,
)
from core.mastrao_transcription_contract import (
    TranscriptionContractRefused,
    TranscriptionPipelineFailed,
)
from core.mastrao_transcription_pipeline import complete_transcription
from core.mastrao_transcription_worker import (
    _gateway_fingerprint,
    _validated_transcript,
    transcribe_audio,
)
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


def test_gateway_fingerprint_binds_signed_request_configuration():
    class _Extracted:
        sha256 = "a" * 64
        duration_ms = 4_000
        codec = "flac"

    config_digest = "b" * 64

    class _Attempt:
        provider_ref = "openai"
        requested_model_ref = "gpt-transcribe"
        request_config_digest = config_digest

    class _ChangedAttempt(_Attempt):
        request_config_digest = "c" * 64

    expected = hashlib.sha256(
        "|".join(
            [
                _Extracted.sha256,
                "4000",
                "flac",
                "openai",
                "gpt-transcribe",
                "asr-gateway-v1",
                "1",
                config_digest,
                "fr",
                "",
                "0",
            ]
        ).encode()
    ).hexdigest()

    assert (
        _gateway_fingerprint(
            _Extracted(),
            _Attempt(),
            language="fr",
        )
        == expected
    )
    assert _gateway_fingerprint(
        _Extracted(),
        _ChangedAttempt(),
        language="fr",
    ) != _gateway_fingerprint(
        _Extracted(),
        _Attempt(),
        language="fr",
    )


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
        codec = "flac"
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


def test_v2_attempt_uses_signed_provider_binding_not_runtime_default(settings):
    settings.MASTRAO_TRANSCRIPTION_ASR_MODE = "real"
    settings.MASTRAO_TRANSCRIPTION_PROVIDER = "mistral"
    settings.MASTRAO_TRANSCRIPTION_MODEL = "voxtral-mini-2602"
    settings.MASTRAO_ASR_GATEWAY_AUTH_TOKEN = "workload-token"
    recording = _finalized_recording_binding("signedprovider_0123")
    effect = _effect(
        recording,
        operation_version=2,
        asr_profile_ref="openai-gpt-transcribe-v1",
        asr_profile_digest="1" * 64,
        asr_provider_ref="openai",
        requested_model_ref="gpt-transcribe",
        request_config_digest="2" * 64,
        normalization_version="meeting-transcript-v1",
        processing_region_ref="provider-default",
        data_control_ref="dpa-standard",
    )
    with (
        mock.patch(ENQUEUE),
        mock.patch(
            "core.mastrao_transcription_adapter.sign_submit_receipt",
            return_value="receipt.payload.signature",
        ),
    ):
        _apply_transcription(effect)
    local_effect = models.MastraoTranscriptionEffect.objects.select_related(
        "transcription_binding"
    ).get()

    class _Extracted:
        sha256 = "7" * 64
        duration_ms = 4_000
        codec = "flac"
        byte_size = 128

    attempt = prepare_attempt(local_effect, _Extracted())
    assert attempt.provider_ref == "openai"
    assert attempt.requested_model_ref == "gpt-transcribe"
    assert attempt.request_config_digest == "2" * 64


@pytest.mark.parametrize(
    ("mode", "profile_ref", "provider_ref", "model_ref"),
    [
        ("fake", "openai-gpt-transcribe-v1", "openai", "gpt-transcribe"),
    ],
)
def test_v2_attempt_refuses_execution_mode_profile_mismatch(
    settings, mode, profile_ref, provider_ref, model_ref
):
    settings.MASTRAO_TRANSCRIPTION_ASR_MODE = mode
    recording = _finalized_recording_binding(f"modemismatch_{mode}")
    effect = _effect(
        recording,
        operation_version=2,
        asr_profile_ref=profile_ref,
        asr_profile_digest="1" * 64,
        asr_provider_ref=provider_ref,
        requested_model_ref=model_ref,
        request_config_digest="2" * 64,
        normalization_version="meeting-transcript-v1",
        processing_region_ref="provider-default",
        data_control_ref="dpa-standard",
    )
    with (
        mock.patch(ENQUEUE),
        mock.patch(
            "core.mastrao_transcription_adapter.sign_submit_receipt",
            return_value="receipt.payload.signature",
        ),
    ):
        _apply_transcription(effect)
    local_effect = models.MastraoTranscriptionEffect.objects.select_related(
        "transcription_binding"
    ).get()

    class _Extracted:
        sha256 = "8" * 64
        duration_ms = 4_000
        codec = "flac"
        byte_size = 128

    with pytest.raises(TranscriptionPipelineFailed):
        prepare_attempt(local_effect, _Extracted())


def test_paid_sending_crash_becomes_unknown_and_is_not_resent(settings):
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


def test_real_prepare_refuses_implicit_mistral_defaults(settings):
    settings.MASTRAO_TRANSCRIPTION_ASR_MODE = "real"
    settings.MASTRAO_TRANSCRIPTION_PROVIDER = ""
    settings.MASTRAO_TRANSCRIPTION_MODEL = ""
    settings.MASTRAO_ASR_GATEWAY_AUTH_TOKEN = ""
    binding = _finalized_recording_binding("noprovider01234567")
    effect = _effect(binding, transcription_ref="transcription_noprovider012")
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
        sha256 = "c" * 64
        duration_ms = 4_000
        codec = "flac"
        byte_size = 128

    with pytest.raises(TranscriptionPipelineFailed):
        prepare_attempt(local_effect, _Extracted())


def test_recovery_copy_is_deleted_after_cleanup(settings, tmp_path):
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


def test_recovery_retries_ack_without_a_second_provider_call(settings, tmp_path):
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
    transcript = transcribe_audio(b"ack replay")
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

    class _Extracted:
        sha256 = "c" * 64
        duration_ms = 4_000
        codec = "flac"
        byte_size = 128

    attempt = prepare_attempt(local_effect, _Extracted())
    transcript["audio_digest"] = _Extracted.sha256
    transcript["engine_ref"] = "mistral:voxtral-mini-2602"
    persist_result_recovery(attempt.attempt_ref, transcript)
    sending = cas_sending(attempt)
    acks = []

    def ack(*_args, **_kwargs):
        acks.append(1)
        if len(acks) == 1:
            raise TranscriptionContractRefused(status=503, outcome="retry")

    with (
        mock.patch(
            "core.mastrao_transcription_adapter.transcribe_extracted"
        ) as provider,
        mock.patch(
            "core.mastrao_transcription_adapter.ack_gateway_attempt",
            side_effect=ack,
        ),
    ):
        with pytest.raises(TranscriptionContractRefused) as refused:
            _resume_or_transcribe(
                _Extracted(),
                sending,
                models.MastraoTranscriptionBinding.objects.get(),
            )
        assert refused.value.outcome == "retry"
        resumed = _resume_or_transcribe(
            _Extracted(),
            sending,
            models.MastraoTranscriptionBinding.objects.get(),
        )
    provider.assert_not_called()
    assert len(acks) == 2
    assert resumed["engine_ref"] == "mistral:voxtral-mini-2602"


def test_revocation_discards_an_inflight_second_run_recovery(settings, tmp_path):
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
            side_effect=[alternate, alternate, authority_revoked],
        ),
        mock.patch(
            "core.mastrao_transcription_adapter.transcribe_extracted",
            return_value=transcript,
        ) as provider,
        mock.patch("core.mastrao_transcription_adapter.ack_gateway_attempt"),
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


def test_pre_egress_failure_defers_without_notifying_core():
    binding = _finalized_recording_binding("defercore012345678")
    effect = _effect(binding, transcription_ref="transcription_defercore0123")
    with (
        mock.patch(ENQUEUE),
        mock.patch(
            "core.mastrao_transcription_adapter.sign_submit_receipt",
            return_value="receipt.payload.signature",
        ),
    ):
        _apply_transcription(effect)
    local_effect = models.MastraoTranscriptionEffect.objects.get()
    with (
        mock.patch(
            "core.mastrao_transcription_adapter._produce_transcript",
            side_effect=TranscriptionContractRefused(
                status=503, outcome="failed_pre_egress"
            ),
        ),
        mock.patch("core.mastrao_transcription_adapter._notify_core_failure") as notify,
    ):
        complete_transcription(local_effect.pk)
    notify.assert_not_called()
    local_effect.refresh_from_db()
    assert local_effect.dispatch_state == (
        models.MastraoTranscriptionEffect.DispatchState.DISPATCH_PENDING
    )
    assert local_effect.next_attempt_at > timezone.now()


def test_retry_after_holds_deadline_without_burning_retries():
    binding = _finalized_recording_binding("retryafter01234567")
    effect = _effect(binding, transcription_ref="transcription_retryafter012")
    with (
        mock.patch(ENQUEUE),
        mock.patch(
            "core.mastrao_transcription_adapter.sign_submit_receipt",
            return_value="receipt.payload.signature",
        ),
        mock.patch(
            "core.mastrao_transcription_adapter._produce_transcript",
            side_effect=TranscriptionContractRefused(
                status=503, outcome="retry", retry_after_seconds=60
            ),
        ) as produce,
        mock.patch("core.mastrao_transcription_adapter._notify_core_failure") as notify,
    ):
        _apply_transcription(effect)
        local_effect = models.MastraoTranscriptionEffect.objects.get()
        complete_transcription(local_effect.pk)
        local_effect.refresh_from_db()
        due = local_effect.next_attempt_at
        count = local_effect.attempt_count
        complete_transcription(local_effect.pk)
        complete_transcription(local_effect.pk)
    local_effect.refresh_from_db()
    assert produce.call_count == 1
    notify.assert_not_called()
    assert local_effect.attempt_count == count
    assert local_effect.next_attempt_at == due
    assert due >= timezone.now() + timezone.timedelta(seconds=50)
    assert local_effect.dispatch_state == (
        models.MastraoTranscriptionEffect.DispatchState.DISPATCH_PENDING
    )
