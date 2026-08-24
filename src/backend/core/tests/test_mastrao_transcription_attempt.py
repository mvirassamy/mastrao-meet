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
    _authorize_egress,
    _notify_core_failure,
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
from core.mastrao_transcription_worker import (
    _gateway_fingerprint,
    _gateway_transcribe,
    _validated_transcript,
    transcribe_audio,
)
from core.tests.test_mastrao_transcription import (
    ENQUEUE,
    _effect,
    _fake_artifact,
    _finalized_recording_binding,
    _v3_effect,
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
        audio_sha256 = _Extracted.sha256
        audio_duration_ms = _Extracted.duration_ms
        audio_codec = _Extracted.codec
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


def test_grant_refresh_only_downgrades_to_recover_only():
    binding = _finalized_recording_binding("grantrefresh012345")
    effect = _effect(binding, transcription_ref="transcription_grantrefresh")
    with (
        mock.patch(ENQUEUE),
        mock.patch(
            "core.mastrao_transcription_adapter.sign_submit_receipt",
            return_value="receipt.payload.signature",
        ),
    ):
        _apply_transcription(effect)
    local_effect = models.MastraoTranscriptionEffect.objects.get()

    class Extracted:
        sha256 = "a" * 64
        duration_ms = 4_000
        codec = "flac"
        byte_size = 128

    attempt = prepare_attempt(local_effect, Extracted())
    grant = {
        "grant_semantic_digest": "b" * 64,
        "authority_version": 7,
        "campaign_ref": "managed-canary",
        "authorized_cost_ceiling_micros": 1_000,
        "tariff_catalog_version": "asr-tariff-v2",
        "execution_mode": "send_allowed",
    }
    bound = bind_egress_grant(attempt, grant)
    grant["execution_mode"] = "recover_only"
    recovered = bind_egress_grant(bound, grant)
    assert recovered.execution_mode == "recover_only"
    grant["execution_mode"] = "send_allowed"
    with pytest.raises(TranscriptionPipelineFailed):
        bind_egress_grant(recovered, grant)


def test_core_egress_authorization_refreshes_the_caller_attempt(settings):
    settings.MASTRAO_TRANSCRIPTION_ASR_MODE = "real"
    settings.MASTRAO_ASR_GATEWAY_AUTH_TOKEN = "workload-token"
    recording = _finalized_recording_binding("grantcallerrefresh1")
    effect = _v3_effect(recording, transcription_ref="transcription_grantcaller")
    with (
        mock.patch(ENQUEUE),
        mock.patch(
            "core.mastrao_transcription_adapter.sign_submit_receipt",
            return_value="receipt.payload.signature",
        ),
    ):
        _apply_transcription(effect)
    local_effect = models.MastraoTranscriptionEffect.objects.get()

    class Extracted:
        sha256 = "a" * 64
        duration_ms = 4_000
        codec = "flac"
        byte_size = 128

    attempt = prepare_attempt(local_effect, Extracted())
    grant = {
        "grant_semantic_digest": "b" * 64,
        "authority_version": 7,
        "campaign_ref": "managed-canary-2026-08",
        "authorized_cost_ceiling_micros": 10_000,
        "tariff_catalog_version": "asr-tariff-v2",
        "execution_mode": "send_allowed",
    }
    with (
        mock.patch(
            "core.mastrao_transcription_adapter.sign_transcription_egress_request",
            return_value="request.payload.signature",
        ),
        mock.patch(
            "core.mastrao_transcription_adapter.post_core_json",
            return_value={"transcription_egress_grant": "grant.payload.signature"},
        ),
        mock.patch(
            "core.mastrao_transcription_adapter.verify_transcription_egress_grant",
            return_value=grant,
        ),
    ):
        _authorize_egress(local_effect.transcription_binding, attempt, "send_allowed")
    assert attempt.grant_semantic_digest == "b" * 64
    assert attempt.authority_version == 7
    assert attempt.execution_mode == "send_allowed"


def test_core_pre_send_refusal_is_persisted_without_a_grant(settings):
    settings.MASTRAO_TRANSCRIPTION_ASR_MODE = "real"
    recording = _finalized_recording_binding("egressrefused0123")
    effect = _v3_effect(recording, transcription_ref="transcription_egress_refused")
    with (
        mock.patch(ENQUEUE),
        mock.patch(
            "core.mastrao_transcription_adapter.sign_submit_receipt",
            return_value="receipt.payload.signature",
        ),
    ):
        _apply_transcription(effect)
    local_effect = models.MastraoTranscriptionEffect.objects.get()

    class Extracted:
        sha256 = "a" * 64
        duration_ms = 4_000
        codec = "flac"
        byte_size = 128

    attempt = prepare_attempt(local_effect, Extracted())
    with (
        mock.patch(
            "core.mastrao_transcription_adapter.sign_transcription_egress_request",
            return_value="request.payload.signature",
        ),
        mock.patch(
            "core.mastrao_transcription_adapter.post_core_json",
            side_effect=TranscriptionContractRefused(status=409, outcome="failed"),
        ),
        pytest.raises(TranscriptionContractRefused),
    ):
        _authorize_egress(local_effect.transcription_binding, attempt, "send_allowed")
    attempt.refresh_from_db()
    assert attempt.state == attempt.State.FAILED_PRE_EGRESS
    assert attempt.last_safe_error_code == "egress_refused"
    assert attempt.grant_semantic_digest is None
    with mock.patch("core.mastrao_transcription_adapter.post_core_json") as post:
        assert _notify_core_failure(effect, "asr_failed") == {
            "state": "failed",
            "outcome": "failed",
        }
    post.assert_not_called()


def test_local_rate_limit_retries_with_send_grant(settings, tmp_path):
    settings.MASTRAO_TRANSCRIPTION_ASR_MODE = "real"
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
    recording = _finalized_recording_binding("ratelimitrecover1")
    effect = _v3_effect(recording, transcription_ref="transcription_ratelimit")
    with (
        mock.patch(ENQUEUE),
        mock.patch(
            "core.mastrao_transcription_adapter.sign_submit_receipt",
            return_value="receipt.payload.signature",
        ),
    ):
        _apply_transcription(effect)
    local_effect = models.MastraoTranscriptionEffect.objects.get()

    class Extracted:
        sha256 = "c" * 64
        duration_ms = 4_000
        codec = "flac"
        byte_size = 128

    attempt = prepare_attempt(local_effect, Extracted())
    transcript = {
        "schema_version": 1,
        "engine_ref": "openai:gpt-transcribe",
        "language": "fr",
        "audio_digest": Extracted.sha256,
        "segments": [],
    }
    provenance = {
        "attempt_ref": attempt.attempt_ref,
        "grant_semantic_digest": "b" * 64,
        "authority_version": 7,
        "provider_ref": "openai",
        "requested_model_ref": "gpt-transcribe",
        "processing_region_ref": "openai-eu",
        "data_control_ref": "openai-zdr-approved-v1",
        "usage_audio_seconds": 4,
        "estimated_cost_micros": 300,
        "currency": "USD",
        "tariff_catalog_version": "asr-tariff-v2",
        "provider_egress_opened_at": int(timezone.now().timestamp()),
        "provider_completed_at": int(timezone.now().timestamp()),
    }
    modes = []

    def authorize(_binding, current, execution_mode):
        modes.append(execution_mode)
        grant = {
            "grant_semantic_digest": "b" * 64,
            "authority_version": 7,
            "campaign_ref": "managed-canary-2026-08",
            "authorized_cost_ceiling_micros": 10_000,
            "tariff_catalog_version": "asr-tariff-v2",
            "execution_mode": execution_mode,
        }
        bind_egress_grant(current, grant)
        current.refresh_from_db()
        return f"grant-{execution_mode}"

    limited = TranscriptionContractRefused(
        status=503,
        outcome="retry",
        retry_after_seconds=60,
        provenance=provenance,
    )
    success_provenance = {
        **provenance,
        "provider_egress_opened_at": int(timezone.now().timestamp()),
        "provider_completed_at": int(timezone.now().timestamp()),
    }
    transcript["_usage"] = success_provenance
    with (
        mock.patch(
            "core.mastrao_transcription_adapter._authorize_egress",
            side_effect=authorize,
        ),
        mock.patch(
            "core.mastrao_transcription_adapter._assert_transcription_authority"
        ),
        mock.patch(
            "core.mastrao_transcription_adapter.transcribe_extracted",
            side_effect=[limited, transcript],
        ) as gateway,
    ):
        with pytest.raises(TranscriptionContractRefused) as refused:
            _resume_or_transcribe(
                Extracted(), attempt, local_effect.transcription_binding
            )
        assert refused.value.outcome == "retry"
        attempt.refresh_from_db()
        assert attempt.state == attempt.State.RATE_LIMITED
        assert attempt.provider_egress_opened_at is not None
        resumed = _resume_or_transcribe(
            Extracted(), attempt, local_effect.transcription_binding
        )
    assert modes == ["send_allowed", "send_allowed"]
    assert gateway.call_count == 2
    assert resumed["engine_ref"] == "openai:gpt-transcribe"


def test_gateway_429_ingests_bounded_provenance_and_retry_after(settings, tmp_path):
    settings.MASTRAO_TRANSCRIPTION_ASR_MODE = "real"
    settings.MASTRAO_TRANSCRIPTION_ASR_ENDPOINT = (
        "https://asr.example.test/v1/transcribe"
    )
    settings.MASTRAO_ASR_GATEWAY_AUTH_TOKEN = "workload-token"
    recording = _finalized_recording_binding("ratebodyprovenance")
    effect = _v3_effect(recording, transcription_ref="transcription_ratebody")
    with (
        mock.patch(ENQUEUE),
        mock.patch(
            "core.mastrao_transcription_adapter.sign_submit_receipt",
            return_value="receipt.payload.signature",
        ),
    ):
        _apply_transcription(effect)
    local_effect = models.MastraoTranscriptionEffect.objects.get()
    audio_path = tmp_path / "clip.flac"
    audio_path.write_bytes(b"bounded flac fixture")

    class Extracted:
        path = audio_path
        sha256 = hashlib.sha256(b"bounded flac fixture").hexdigest()
        duration_ms = 4_000
        codec = "flac"
        byte_size = len(b"bounded flac fixture")

    attempt = prepare_attempt(local_effect, Extracted())
    grant = {
        "grant_semantic_digest": "b" * 64,
        "authority_version": 7,
        "campaign_ref": "managed-canary-2026-08",
        "authorized_cost_ceiling_micros": 10_000,
        "tariff_catalog_version": "asr-tariff-v2",
        "execution_mode": "send_allowed",
    }
    attempt = bind_egress_grant(attempt, grant)
    provenance = {
        "attempt_ref": attempt.attempt_ref,
        "grant_semantic_digest": "b" * 64,
        "authority_version": 7,
        "provider_ref": "openai",
        "requested_model_ref": "gpt-transcribe",
        "processing_region_ref": "openai-eu",
        "data_control_ref": "openai-zdr-approved-v1",
        "usage_audio_seconds": 4,
        "estimated_cost_micros": 300,
        "currency": "USD",
        "tariff_catalog_version": "asr-tariff-v2",
        "provider_egress_opened_at": 1_000,
        "provider_completed_at": 1_001,
    }
    body = json.dumps(
        {
            "error": "PROVIDER_RATE_LIMITED",
            "outcome": "rejected",
            "provenance": provenance,
        }
    ).encode()
    response = mock.MagicMock()
    response.status_code = 429
    response.headers = {"Content-Length": str(len(body)), "Retry-After": "60"}
    response.iter_content.return_value = [body]
    session = mock.MagicMock()
    session.__enter__.return_value.post.return_value = response
    with mock.patch(
        "core.mastrao_transcription_worker.requests.Session", return_value=session
    ):
        with pytest.raises(TranscriptionContractRefused) as refused:
            _gateway_transcribe(
                Extracted(), attempt, egress_grant="grant.payload.signature"
            )
    assert refused.value.outcome == "retry"
    assert refused.value.retry_after_seconds == 60
    assert refused.value.provenance == provenance


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


def test_v3_attempt_uses_signed_request_config_digest(settings):
    settings.MASTRAO_TRANSCRIPTION_ASR_MODE = "real"
    settings.MASTRAO_ASR_GATEWAY_AUTH_TOKEN = "workload-token"
    recording = _finalized_recording_binding("signedmanaged_0123")
    effect = _effect(
        recording,
        operation_version=3,
        asr_profile_ref="openai-eu-zdr-gpt-transcribe-canary-v1",
        asr_profile_digest="1" * 64,
        asr_provider_ref="openai",
        requested_model_ref="gpt-transcribe",
        request_config_digest="2" * 64,
        normalization_version="meeting-transcript-v1",
        processing_region_ref="openai-eu",
        data_control_ref="openai-zdr-approved-v1",
        campaign_ref="managed-canary-2026-08",
        authorized_cost_ceiling_micros=10_000,
        currency="USD",
        tariff_catalog_version="asr-tariff-v2",
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


def _paid_attempt_with_recovery(local_effect):
    class Extracted:
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
        events.append("core")

    def ack(*_args, **_kwargs):
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
    ("status", "outcome"),
    [(409, "failed"), (404, "deleted")],
)
def test_terminal_core_acceptance_replays_only_failed_ack(
    settings, tmp_path, status, outcome
):
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
    binding = _finalized_recording_binding(f"terminalack{status}012345")
    effect = _effect(binding, transcription_ref=f"transcription_terminalack{status}")
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
    acknowledgements = []

    def ack(*_args, **_kwargs):
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
        assert default_storage.exists(attempt.result_recovery_ref)
        complete_transcription(local_effect.pk)
    provider.assert_called_once()
    core.assert_called_once()
    terminal_core.assert_called_once()
    assert len(acknowledgements) == 2
    attempt.refresh_from_db()
    assert attempt.terminal_outcome == (
        models.MastraoTranscriptionProviderAttempt.TerminalOutcome.DELETED
    )
    local_effect.refresh_from_db()
    assert local_effect.dispatch_state == (
        models.MastraoTranscriptionEffect.DispatchState.COMPLETED
    )
    assert not default_storage.exists(attempt.result_recovery_ref)


def test_core_retry_precedes_ack_and_does_not_rerun_asr(settings, tmp_path):
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
        events.append("core")
        if events.count("core") == 1:
            raise TranscriptionContractRefused(status=503, outcome="retry")

    def ack(*_args, **_kwargs):
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
