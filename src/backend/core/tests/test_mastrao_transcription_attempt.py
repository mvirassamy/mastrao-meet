"""Provider-attempt authorization, gateway requests and queue isolation proofs."""

# pylint: disable=missing-function-docstring

import hashlib
import json
from unittest import mock

from django.utils import timezone

import pytest

from core import models
from core.mastrao_transcription_adapter import (
    _apply_transcription,
    _authorize_egress,
    _notify_core_failure,
    _resume_or_transcribe,
)
from core.mastrao_transcription_attempt import bind_egress_grant, prepare_attempt
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
        """Audio identity used to prepare an attempt before grant refresh."""

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
        """Audio identity bound to the caller receiving the refreshed grant."""

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
        """Audio identity for an attempt refused before provider egress."""

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
        """Audio identity reused across rate-limited and successful sends."""

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


def test_lost_gateway_response_replays_recover_only_without_second_send(
    settings, tmp_path
):
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
    recording = _finalized_recording_binding("lostresponse012345")
    effect = _v3_effect(recording, transcription_ref="transcription_lostresponse")
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
        """Stable audio identity for replaying an unknown gateway result."""

        sha256 = "d" * 64
        duration_ms = 4_000
        codec = "flac"
        byte_size = 128

    attempt = prepare_attempt(local_effect, Extracted())
    now = int(timezone.now().timestamp())
    transcript = {
        "schema_version": 1,
        "engine_ref": "openai:gpt-transcribe",
        "language": "fr",
        "audio_digest": Extracted.sha256,
        "segments": [],
        "_usage": {
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
            "provider_egress_opened_at": now,
            "provider_completed_at": now,
        },
    }
    modes = []

    def authorize(_binding, current, execution_mode):
        modes.append(execution_mode)
        bind_egress_grant(
            current,
            {
                "grant_semantic_digest": "b" * 64,
                "authority_version": 7,
                "campaign_ref": "managed-canary-2026-08",
                "authorized_cost_ceiling_micros": 10_000,
                "tariff_catalog_version": "asr-tariff-v2",
                "execution_mode": execution_mode,
            },
        )
        current.refresh_from_db()
        return f"grant-{execution_mode}"

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
            side_effect=[
                TranscriptionContractRefused(status=503, outcome="unknown"),
                TranscriptionContractRefused(status=503, outcome="unknown"),
                transcript,
            ],
        ) as gateway,
    ):
        with pytest.raises(TranscriptionContractRefused) as refused:
            _resume_or_transcribe(
                Extracted(), attempt, local_effect.transcription_binding
            )
        assert refused.value.outcome == "retry"
        attempt.refresh_from_db()
        assert attempt.state == attempt.State.UNKNOWN
        assert attempt.terminal_outcome is None
        with pytest.raises(TranscriptionContractRefused) as refused:
            _resume_or_transcribe(
                Extracted(), attempt, local_effect.transcription_binding
            )
        assert refused.value.outcome == "retry"
        attempt.refresh_from_db()
        assert attempt.state == attempt.State.UNKNOWN
        assert attempt.terminal_outcome is None
        resumed = _resume_or_transcribe(
            Extracted(), attempt, local_effect.transcription_binding
        )
    assert modes == ["send_allowed", "recover_only", "recover_only"]
    assert gateway.call_count == 3
    assert resumed["engine_ref"] == "openai:gpt-transcribe"


@pytest.mark.parametrize(
    ("body", "content_length"),
    [
        (b"{", 1),
        (b"{}", 2),
        (b"{}", 5_000_001),
    ],
)
def test_unreadable_post_response_is_unknown(settings, tmp_path, body, content_length):
    settings.MASTRAO_TRANSCRIPTION_ASR_MODE = "real"
    settings.MASTRAO_TRANSCRIPTION_ASR_ENDPOINT = (
        "https://asr.example.test/v1/transcribe"
    )
    settings.MASTRAO_ASR_GATEWAY_AUTH_TOKEN = "workload-token"
    audio_path = tmp_path / "clip.flac"
    audio_path.write_bytes(b"flac fixture")

    class Extracted:
        """Local FLAC file and identity sent to the gateway."""

        path = audio_path
        sha256 = hashlib.sha256(b"flac fixture").hexdigest()
        duration_ms = 4_000
        codec = "flac"
        byte_size = len(b"flac fixture")

    class Attempt:
        """Provider request binding used when the gateway response is unreadable."""

        attempt_ref = "attempt_unreadable_012345"
        provider_ref = "openai"
        requested_model_ref = "gpt-transcribe"
        request_config_digest = "d" * 64
        grant_semantic_digest = None

    response = mock.MagicMock()
    response.status_code = 200
    response.headers = {"Content-Length": str(content_length)}
    response.iter_content.return_value = [body]
    session = mock.MagicMock()
    session.__enter__.return_value.post.return_value = response
    with mock.patch(
        "core.mastrao_transcription_worker.requests.Session", return_value=session
    ):
        with pytest.raises(TranscriptionContractRefused) as refused:
            _gateway_transcribe(
                Extracted(), Attempt(), egress_grant="grant.payload.signature"
            )
    assert refused.value.outcome == "unknown"


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
        """Local FLAC identity for a rate-limited gateway request."""

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
