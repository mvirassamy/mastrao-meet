"""Normal post-meeting ASR contracts, durable limits and recovery fences."""

# pylint: disable=missing-function-docstring

import json
from functools import partial
from types import SimpleNamespace
from unittest import mock

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core import models
from core.mastrao_recording_contract import RecordingContractRefused, _sign
from core.mastrao_room_contract import (
    _base64url_decode,
    _base64url_encode,
    _sha256_canonical,
)
from core.mastrao_transcription_adapter import (
    _apply_transcription,
    _authorize_egress,
    _effect_from_local,
    _prepare_transcription,
    _resume_or_transcribe,
)
from core.mastrao_transcription_attempt import (
    bind_egress_grant,
    may_call_provider,
    prepare_attempt,
)
from core.mastrao_transcription_contract import (
    EGRESS_GRANT_JOSE_TYPE,
    EGRESS_GRANT_TYPE,
    MANAGED_PROFILE_BINDINGS,
    NORMAL_ASR_REQUEST_OPTIONS,
    NORMAL_AUTHORITY_FIELDS,
    NORMAL_MAXIMUM_AUDIO_BYTES,
    NORMAL_MAXIMUM_AUDIO_SECONDS,
    SUBMIT_EFFECT_JOSE_TYPE,
    TranscriptionContractRefused,
    _submit_arguments,
    build_transcript_artifact_receipt_claims,
    build_transcription_egress_request_claims,
    build_transcription_terminal_receipt_claims,
    verify_transcription_egress_grant,
    verify_transcription_submit_effect,
)
from core.mastrao_transcription_pipeline import (
    complete_transcription,
    reconcile_transcription_dispatches,
)
from core.tests.test_mastrao_transcription import (
    ENQUEUE,
    _contract_effect,
    _finalized_recording_binding,
)

pytestmark = pytest.mark.django_db
PROFILE_REF = "mistral-eu-standard-managed-demo-v1"


@pytest.fixture(autouse=True)
def normal_settings(settings, tmp_path):
    """Use ephemeral JOSE keys, local storage and mocked external effects."""

    key = Ed25519PrivateKey.generate()
    public = _base64url_encode(key.public_key().public_bytes_raw())
    settings.MASTRAO_RECORDING_EFFECT_KEY_ID = "normal-asr-test"
    settings.MASTRAO_RECORDING_RECEIPT_KEY_ID = "normal-asr-test"
    settings.MASTRAO_RECORDING_EFFECT_PUBLIC_JWK = json.dumps(
        {"kty": "OKP", "crv": "Ed25519", "x": public}
    )
    settings.MASTRAO_RECORDING_RECEIPT_PRIVATE_JWK = json.dumps(
        {
            "kty": "OKP",
            "crv": "Ed25519",
            "x": public,
            "d": _base64url_encode(key.private_bytes_raw()),
        }
    )
    settings.MASTRAO_MEETING_RECORDING_ENABLED = True
    settings.MASTRAO_TRANSCRIPTION_ASR_MODE = "real"
    settings.STORAGES = {
        "default": {
            "BACKEND": "django.core.files.storage.FileSystemStorage",
            "OPTIONS": {"location": str(tmp_path)},
        },
        "staticfiles": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    }


def _normal_effect(recording, settings, **overrides):
    effect = _contract_effect(recording, settings, operation_version=3)
    del effect["campaign_ref"]
    effect.update(
        operation_version=4,
        asr_profile_ref=PROFILE_REF,
        **MANAGED_PROFILE_BINDINGS[PROFILE_REF],
        authority_version=1,
        authorized_cost_ceiling_micros=198_000,
        maximum_audio_seconds=NORMAL_MAXIMUM_AUDIO_SECONDS,
        maximum_audio_bytes=NORMAL_MAXIMUM_AUDIO_BYTES,
        notice_version="notice_transcription_normal_v1",
        notice_digest="b" * 64,
    )
    effect.update(overrides)
    effect["arguments_digest"] = _sha256_canonical(_submit_arguments(effect))
    return effect


def _audio(**overrides):
    return SimpleNamespace(
        **{
            "sha256": "a" * 64,
            "duration_ms": 4_000,
            "codec": "flac",
            "byte_size": 128,
            **overrides,
        }
    )


def _grant(request, effect, settings):
    semantic = {
        name: value
        for name, value in request.items()
        if name
        not in {
            "version",
            "type",
            "issuer",
            "audience",
            "operation",
            "execution_mode",
            "issued_at",
            "expires_at",
            "jti",
        }
    }
    semantic.update(
        cabinet_id="cabinet_normal_post_012345",
        notice_digest=effect["notice_digest"],
        consent_epoch=3,
    )
    return {
        **semantic,
        "version": 1,
        "type": EGRESS_GRANT_TYPE,
        "issuer": settings.MASTRAO_RECORDING_EFFECT_ISSUER,
        "audience": settings.MASTRAO_TRANSCRIPTION_EGRESS_GRANT_AUDIENCE,
        "operation": "authorize_meeting_transcription_egress",
        "execution_mode": request["execution_mode"],
        "grant_semantic_digest": _sha256_canonical(semantic),
        "issued_at": request["issued_at"],
        "expires_at": request["expires_at"],
        "jti": "egressgrant_normal_0123456789",
    }


def _prepared(settings, suffix="normalroundtrip0123"):
    recording = _finalized_recording_binding(suffix)
    effect = _normal_effect(recording, settings)
    binding, local_effect = _prepare_transcription(effect)
    attempt = prepare_attempt(local_effect, _audio())
    return recording, effect, binding, local_effect, attempt


def test_signed_normal_submit_persists_and_reconstructs_exact_egress(settings):
    recording, effect, binding, local_effect, attempt = _prepared(settings)
    compact = _sign(effect, SUBMIT_EFFECT_JOSE_TYPE)
    assert verify_transcription_submit_effect(compact) == effect
    replay_binding, replay_effect = _prepare_transcription(
        verify_transcription_submit_effect(compact)
    )
    assert replay_binding.pk == binding.pk
    assert replay_effect.pk == local_effect.pk
    binding.refresh_from_db()
    reconstructed = _effect_from_local(binding, local_effect)
    assert effect["notice_digest"] != recording.notice_digest
    for name in ("notice_version", "notice_digest"):
        assert getattr(binding, name) == effect[name]
        assert reconstructed[name] == effect[name]
    assert _submit_arguments(reconstructed) == _submit_arguments(effect)
    assert reconstructed["arguments_digest"] == _sha256_canonical(
        _submit_arguments(reconstructed)
    )
    assert "campaign_ref" not in reconstructed
    assert binding.campaign_ref is None
    for name in NORMAL_AUTHORITY_FIELDS:
        assert getattr(binding, name) == effect[name]
    claims = build_transcription_egress_request_claims(
        reconstructed, attempt, "send_allowed"
    )
    assert claims["operation_version"] == 2
    assert "campaign_ref" not in claims
    for name in NORMAL_AUTHORITY_FIELDS:
        assert claims[name] == effect[name]
    grant = _grant(claims, effect, settings)
    compact_grant = _sign(grant, EGRESS_GRANT_JOSE_TYPE)
    assert verify_transcription_egress_grant(compact_grant, claims) == grant
    with mock.patch(
        "core.mastrao_transcription_adapter.post_core_json",
        return_value={"transcription_egress_grant": compact_grant},
    ):
        assert _authorize_egress(binding, attempt, "send_allowed") == compact_grant
    assert attempt.authority_version == effect["authority_version"]
    assert (
        attempt.authorized_cost_ceiling_micros
        == effect["authorized_cost_ceiling_micros"]
    )
    assert attempt.currency == effect["currency"]
    assert attempt.tariff_catalog_version == effect["tariff_catalog_version"]
    assert attempt.grant_semantic_digest == grant["grant_semantic_digest"]
    assert attempt.campaign_ref is None


@pytest.mark.parametrize("notice_source", ["transcription", "recording"])
def test_normal_egress_requires_the_dedicated_transcription_notice(
    settings, notice_source
):
    recording, effect, binding, _local_effect, attempt = _prepared(settings)
    assert recording.notice_digest != effect["notice_digest"]
    request = build_transcription_egress_request_claims(effect, attempt, "send_allowed")
    granted_effect = dict(effect)
    if notice_source == "recording":
        granted_effect["notice_digest"] = recording.notice_digest
    grant = _grant(request, granted_effect, settings)
    compact = _sign(grant, EGRESS_GRANT_JOSE_TYPE)
    assert verify_transcription_egress_grant(compact, request) == grant
    with mock.patch(
        "core.mastrao_transcription_adapter.post_core_json",
        return_value={"transcription_egress_grant": compact},
    ):
        if notice_source == "recording":
            with pytest.raises(TranscriptionContractRefused):
                _authorize_egress(binding, attempt, "send_allowed")
            attempt.refresh_from_db()
            assert attempt.grant_semantic_digest is None
            return
        assert _authorize_egress(binding, attempt, "send_allowed") == compact
    assert attempt.grant_semantic_digest == grant["grant_semantic_digest"]


@pytest.mark.parametrize("field", ["notice_version", "notice_digest"])
def test_normal_replay_rejects_notice_substitution_with_same_arguments(settings, field):
    _recording, effect, binding, local_effect, _attempt = _prepared(settings)
    changed = dict(effect)
    changed[field] = "d" * 64 if field == "notice_digest" else "notice_substituted_v1"
    assert _submit_arguments(changed) == _submit_arguments(effect)
    assert (
        verify_transcription_submit_effect(_sign(changed, SUBMIT_EFFECT_JOSE_TYPE))
        == changed
    )
    with pytest.raises(TranscriptionContractRefused) as refused:
        _prepare_transcription(changed)
    assert refused.value.status == 409
    binding.refresh_from_db()
    assert getattr(binding, field) == effect[field]
    assert _effect_from_local(binding, local_effect)[field] == effect[field]


@pytest.mark.parametrize("field", NORMAL_AUTHORITY_FIELDS)
def test_normal_replay_rejects_authority_or_budget_substitution(settings, field):
    _recording, effect, _binding, _local_effect, _attempt = _prepared(settings)
    changed = dict(effect)
    value = effect[field]
    changed[field] = value + 1 if isinstance(value, int) else "substituted"
    with pytest.raises(TranscriptionContractRefused):
        _prepare_transcription(changed)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("campaign_ref", "legacy-campaign"),
        ("maximum_audio_seconds", 120),
        ("maximum_audio_bytes", NORMAL_MAXIMUM_AUDIO_BYTES + 1),
        ("authority_version", True),
        ("currency", "EUR"),
        ("request_config_digest", "f" * 64),
        ("asr_profile_digest", "f" * 64),
        ("tariff_catalog_version", "unknown-tariff"),
        ("requested_model_ref", "unknown-model"),
        ("normalization_version", "unknown-normalizer"),
    ],
)
def test_signed_normal_submit_refuses_contract_substitution(settings, field, value):
    recording = _finalized_recording_binding("normalrefusal01234")
    effect = _normal_effect(recording, settings, **{field: value})
    with pytest.raises(RecordingContractRefused):
        verify_transcription_submit_effect(_sign(effect, SUBMIT_EFFECT_JOSE_TYPE))


def test_normal_arguments_digest_has_top_level_version_and_authority(settings):
    recording = _finalized_recording_binding("normaldigest012345")
    effect = _normal_effect(recording, settings)
    arguments = _submit_arguments(effect)
    assert arguments["operation_version"] == 4
    assert "campaign_ref" not in arguments
    for name in NORMAL_AUTHORITY_FIELDS:
        assert arguments[name] == effect[name]
    for name in ("operation_version", *NORMAL_AUTHORITY_FIELDS):
        altered = dict(arguments)
        del altered[name]
        assert _sha256_canonical(altered) != effect["arguments_digest"]


@pytest.mark.parametrize(
    "field", ["authority_version", "maximum_audio_seconds", "maximum_audio_bytes"]
)
def test_normal_grant_refuses_limits_substituted_after_submit(settings, field):
    _recording, effect, _binding, _local_effect, attempt = _prepared(settings)
    request = build_transcription_egress_request_claims(effect, attempt, "send_allowed")
    grant = _grant(request, effect, settings)
    grant[field] += 1
    with pytest.raises(TranscriptionContractRefused):
        verify_transcription_egress_grant(_sign(grant, EGRESS_GRANT_JOSE_TYPE), request)


def test_normal_grant_digest_requires_version_discriminator(settings):
    _recording, effect, _binding, _local_effect, attempt = _prepared(settings)
    request = build_transcription_egress_request_claims(effect, attempt, "recover_only")
    grant = _grant(request, effect, settings)
    semantic = {
        name: value
        for name, value in grant.items()
        if name
        not in {
            "version",
            "operation_version",
            "type",
            "issuer",
            "audience",
            "operation",
            "execution_mode",
            "grant_semantic_digest",
            "issued_at",
            "expires_at",
            "jti",
        }
    }
    grant["grant_semantic_digest"] = _sha256_canonical(semantic)
    with pytest.raises(TranscriptionContractRefused):
        verify_transcription_egress_grant(_sign(grant, EGRESS_GRANT_JOSE_TYPE), request)


@pytest.mark.parametrize("field", ["duration_ms", "byte_size"])
def test_normal_audio_limit_refuses_attempt_before_egress(settings, field):
    recording = _finalized_recording_binding("normaloversize0123")
    effect = _normal_effect(recording, settings)
    _binding, local_effect = _prepare_transcription(effect)
    limit = NORMAL_MAXIMUM_AUDIO_BYTES
    if field == "duration_ms":
        limit = NORMAL_MAXIMUM_AUDIO_SECONDS * 1_000
    with pytest.raises(TranscriptionContractRefused):
        prepare_attempt(local_effect, _audio(**{field: limit + 1}))
    assert not local_effect.provider_attempts.exists()


def test_normal_signed_grant_has_immutable_authority_and_recovery_mode(settings):
    _recording, effect, _binding, _local_effect, attempt = _prepared(settings)
    request = build_transcription_egress_request_claims(effect, attempt, "send_allowed")
    grant = _grant(request, effect, settings)
    attempt = bind_egress_grant(attempt, grant)
    grant["execution_mode"] = "recover_only"
    attempt = bind_egress_grant(attempt, grant)
    assert may_call_provider(attempt) is False
    grant["execution_mode"] = "send_allowed"
    with pytest.raises(TranscriptionContractRefused):
        bind_egress_grant(attempt, grant)


@pytest.mark.parametrize("version", [1, 2, 3])
def test_new_legacy_submit_is_refused_without_dispatch(settings, version):
    recording = _finalized_recording_binding(f"newlegacy{version}0123456")
    effect = _contract_effect(recording, settings, operation_version=version)
    with mock.patch(ENQUEUE) as publish, pytest.raises(TranscriptionContractRefused):
        _apply_transcription(effect)
    publish.assert_not_called()
    assert not models.MastraoTranscriptionBinding.objects.exists()


def _persist_legacy(settings, version, suffix):
    recording = _finalized_recording_binding(suffix)
    effect = _contract_effect(recording, settings, operation_version=version)
    identity = {
        name: effect[name]
        for name in (
            "organization_external_id",
            "meeting_ref",
            "room_ref",
            "recording_ref",
            "transcription_ref",
            "provider_binding_digest",
        )
    }
    profile = {
        name: effect[name]
        for name in (
            "asr_profile_ref",
            "asr_profile_digest",
            "asr_provider_ref",
            "requested_model_ref",
            "request_config_digest",
            "normalization_version",
            "processing_region_ref",
            "data_control_ref",
            "campaign_ref",
            "authorized_cost_ceiling_micros",
            "currency",
            "tariff_catalog_version",
        )
        if name in effect
    }
    identity["transcription_ref"] = f"transcription_{suffix}"
    binding = models.MastraoTranscriptionBinding.objects.create(
        recording_binding=recording,
        **identity,
        **profile,
        artifact_ref=recording.artifact_ref,
        artifact_checksum_digest=recording.checksum_digest,
        artifact_byte_size=recording.byte_size,
        contract_operation_version=version,
    )
    return models.MastraoTranscriptionEffect.objects.create(
        transcription_binding=binding,
        effect_key=f"effect_{suffix}",
        effect_jti=f"request_{suffix}",
        arguments_digest=effect["arguments_digest"],
    )


@pytest.mark.parametrize("version", [1, 2, 3])
@pytest.mark.parametrize(
    "state", ["dispatch_pending", "queued", "running", "artifact_notification_pending"]
)
def test_scheduler_excludes_legacy_even_when_due_or_crashed(settings, version, state):
    legacy = _persist_legacy(settings, version, f"legacy{version}{state}")
    legacy.dispatch_state = state
    legacy.next_attempt_at = timezone.now() - timezone.timedelta(hours=1)
    legacy.save()
    models.MastraoTranscriptionEffect.objects.filter(pk=legacy.pk).update(
        updated_at=timezone.now() - timezone.timedelta(hours=1)
    )
    _recording, _effect, _binding, normal, _attempt = _prepared(settings)
    with mock.patch(
        "core.mastrao_transcription_pipeline.publish_transcription_job",
        return_value=True,
    ) as publish:
        assert reconcile_transcription_dispatches(limit=1) == 1
    publish.assert_called_once_with(normal.pk)
    legacy.refresh_from_db()
    assert legacy.attempt_count == 0


@pytest.mark.parametrize("state", ["sending", "unknown"])
def test_scheduler_fences_uncertain_normal_attempt_before_recovery(settings, state):
    _recording, _effect, binding, local_effect, attempt = _prepared(settings)
    attempt.state = state
    attempt.execution_mode = attempt.ExecutionMode.SEND_ALLOWED
    attempt.save()
    local_effect.dispatch_state = local_effect.DispatchState.RUNNING
    local_effect.save()
    models.MastraoTranscriptionEffect.objects.filter(pk=local_effect.pk).update(
        updated_at=timezone.now() - timezone.timedelta(hours=1)
    )
    with mock.patch(
        "core.mastrao_transcription_pipeline.publish_transcription_job",
        return_value=True,
    ):
        assert reconcile_transcription_dispatches() == 1
    attempt.refresh_from_db()
    assert attempt.state == attempt.State.UNKNOWN
    assert attempt.execution_mode == attempt.ExecutionMode.RECOVER_ONLY
    assert may_call_provider(attempt) is False
    with (
        mock.patch(
            "core.mastrao_transcription_adapter._authorize_egress",
            return_value="recover-grant",
        ) as authorize,
        mock.patch(
            "core.mastrao_transcription_adapter._replay_gateway_result", return_value={}
        ) as replay,
        mock.patch(
            "core.mastrao_transcription_adapter.transcribe_extracted"
        ) as provider,
    ):
        _resume_or_transcribe(_audio(), attempt, binding)
    authorize.assert_called_once_with(binding, attempt, execution_mode="recover_only")
    replay.assert_called_once()
    provider.assert_not_called()


def test_scheduler_skips_normal_intent_before_due_time(settings):
    _recording, _effect, _binding, local_effect, _attempt = _prepared(settings)
    local_effect.next_attempt_at = timezone.now() + timezone.timedelta(hours=1)
    local_effect.save()
    with mock.patch(
        "core.mastrao_transcription_pipeline.publish_transcription_job"
    ) as publish:
        assert reconcile_transcription_dispatches() == 0
    publish.assert_not_called()


def test_normal_receipts_retain_grant_provenance_without_campaign(settings):
    _recording, effect, _binding, _local_effect, attempt = _prepared(settings)
    request = build_transcription_egress_request_claims(effect, attempt, "send_allowed")
    attempt = bind_egress_grant(attempt, _grant(request, effect, settings))
    artifact = {
        "transcript_artifact_ref": "transcriptartifact_normal012345",
        "object_ref": "mastrao-transcripts/normal.json",
        "byte_size": 128,
        "checksum_digest": "a" * 64,
        "segment_count": 0,
    }
    receipts = [
        build_transcript_artifact_receipt_claims(effect, artifact, attempt),
        build_transcription_terminal_receipt_claims(effect, attempt, "unknown"),
    ]
    for receipt in receipts:
        assert receipt["operation_version"] == 2
        assert receipt["grant_semantic_digest"] == attempt.grant_semantic_digest
        assert receipt["authority_version"] == effect["authority_version"]
        assert receipt["currency"] == effect["currency"]
        assert receipt["tariff_catalog_version"] == effect["tariff_catalog_version"]
        assert "campaign_ref" not in receipt


def test_legacy_artifact_notification_can_drain_without_producing(settings):
    local_effect = _persist_legacy(settings, 1, "legacycallback0123")
    binding = local_effect.transcription_binding
    binding.checksum_digest = "b" * 64
    binding.object_ref = "mastrao-transcripts/legacy.json"
    binding.transcript_artifact_ref = "transcriptartifact_legacy012345"
    binding.byte_size = 128
    binding.segment_count = 0
    binding.engine_ref = "legacy-engine"
    binding.save()
    local_effect.dispatch_state = (
        local_effect.DispatchState.ARTIFACT_NOTIFICATION_PENDING
    )
    local_effect.save()
    with (
        mock.patch(
            "core.mastrao_transcription_adapter._notify_core_artifact"
        ) as notify,
        mock.patch("core.mastrao_transcription_adapter._produce_transcript") as produce,
    ):
        complete_transcription(local_effect.pk)
    notify.assert_called_once()
    produce.assert_not_called()
    local_effect.refresh_from_db()
    assert local_effect.dispatch_state == local_effect.DispatchState.COMPLETED


@pytest.mark.parametrize(
    "field",
    [
        "authority_version",
        "maximum_audio_seconds",
        "maximum_audio_bytes",
        "notice_version",
        "notice_digest",
    ],
)
def test_normal_persistence_requires_complete_authority(settings, field):
    _recording, _effect, binding, _local_effect, _attempt = _prepared(settings)
    setattr(binding, field, None)
    with pytest.raises(ValidationError):
        binding.full_clean()


@pytest.mark.parametrize(
    ("field", "value"),
    [("notice_version", None), ("notice_digest", None), ("notice_digest", "x" * 64)],
)
def test_database_requires_a_complete_normal_transcription_notice(
    settings, field, value
):
    _recording, effect, binding, _local_effect, _attempt = _prepared(settings)
    with pytest.raises(IntegrityError), transaction.atomic():
        models.MastraoTranscriptionBinding.objects.filter(pk=binding.pk).update(
            **{field: value}
        )
    binding.refresh_from_db()
    assert getattr(binding, field) == effect[field]


@pytest.mark.parametrize("version", [1, 2, 3])
def test_queued_legacy_job_cannot_reexecute_or_rewrite_its_outcome(settings, version):
    local_effect = _persist_legacy(settings, version, f"legacyqueued{version}01234")
    local_effect.dispatch_state = local_effect.DispatchState.QUEUED
    local_effect.save()
    with (
        mock.patch("core.mastrao_transcription_adapter._produce_transcript") as produce,
        mock.patch("core.mastrao_transcription_adapter._notify_core_failure") as notify,
    ):
        complete_transcription(local_effect.pk)
    produce.assert_not_called()
    notify.assert_not_called()
    local_effect.refresh_from_db()
    assert local_effect.dispatch_state == local_effect.DispatchState.QUEUED
    assert local_effect.state == local_effect.State.PENDING


@pytest.mark.parametrize("field", ["duration_ms", "byte_size", "codec"])
def test_normal_attempt_reconstruction_rejects_audio_metadata_drift(settings, field):
    _recording, _effect, _binding, local_effect, _attempt = _prepared(settings)
    audio = _audio()
    value = getattr(audio, field)
    setattr(audio, field, value + 1 if isinstance(value, int) else "wav")
    with pytest.raises(TranscriptionContractRefused):
        prepare_attempt(local_effect, audio)


@pytest.mark.parametrize("version", [1, 2, 3])
def test_normal_submit_cannot_replace_a_persisted_legacy_job(settings, version):
    legacy = _persist_legacy(settings, version, f"noreplace{version}0123456")
    recording = legacy.transcription_binding.recording_binding
    effect = _normal_effect(recording, settings)
    with mock.patch(ENQUEUE) as publish, pytest.raises(TranscriptionContractRefused):
        _apply_transcription(effect)
    publish.assert_not_called()
    assert recording.transcription_bindings.count() == 1
    legacy.refresh_from_db()
    assert legacy.state == legacy.State.PENDING
    assert legacy.dispatch_state == legacy.DispatchState.DISPATCH_PENDING


def test_normal_lost_response_only_refreshes_recovery_grants(settings):
    _recording, effect, binding, _local_effect, attempt = _prepared(settings)
    modes = []

    def authorize_core(**kwargs):
        compact = kwargs["body"]["transcription_egress_request"]
        request = json.loads(_base64url_decode(compact.split(".")[1]))
        modes.append(request["execution_mode"])
        grant = _grant(request, effect, settings)
        return {"transcription_egress_grant": _sign(grant, EGRESS_GRANT_JOSE_TYPE)}

    def gateway(_audio, current, egress_grant):
        grant = json.loads(_base64url_decode(egress_grant.split(".")[1]))
        if len(modes) < 3:
            raise TranscriptionContractRefused(status=503, outcome="unknown")
        assert grant["execution_mode"] == "recover_only"
        return {
            "version": 1,
            "engine_ref": "mistral:voxtral-mini-2602",
            "language": "fr",
            "audio_digest": current.audio_sha256,
            "segments": [],
        }

    with (
        mock.patch(
            "core.mastrao_transcription_adapter.post_core_json",
            side_effect=authorize_core,
        ),
        mock.patch(
            "core.mastrao_transcription_adapter.transcribe_extracted",
            side_effect=gateway,
        ) as transcribe,
    ):
        for _ in range(2):
            with pytest.raises(TranscriptionContractRefused) as refused:
                _resume_or_transcribe(_audio(), attempt, binding)
            assert refused.value.outcome == "retry"
            attempt.refresh_from_db()
            assert attempt.state == attempt.State.UNKNOWN
        transcript = _resume_or_transcribe(_audio(), attempt, binding)
    assert transcript["engine_ref"] == "mistral:voxtral-mini-2602"
    assert modes == ["send_allowed", "recover_only", "recover_only"]
    assert transcribe.call_count == 3
    attempt.refresh_from_db()
    assert attempt.execution_mode == attempt.ExecutionMode.RECOVER_ONLY
    assert attempt.result_checksum
    assert attempt.effect.provider_attempts.count() == 1


def test_normal_completion_persists_signed_gateway_provenance(settings, tmp_path):
    settings.MASTRAO_TRANSCRIPTION_ASR_ENDPOINT = (
        "https://asr.example.test/v1/transcribe"
    )
    settings.MASTRAO_ASR_GATEWAY_AUTH_TOKEN = "offline-test-token"
    recording = _finalized_recording_binding("normalcompletion01")
    effect = _normal_effect(recording, settings)
    with mock.patch(ENQUEUE):
        _apply_transcription(effect)
    local_effect = models.MastraoTranscriptionEffect.objects.get()
    extracted = _audio()
    extracted.path = tmp_path / "audio.flac"
    extracted.path.write_bytes(b"offline audio fixture")
    extracted.close = mock.Mock()
    callbacks = []

    def core_callback(**kwargs):
        envelope = kwargs["body"]
        compact = next(iter(envelope.values()))
        claims = json.loads(_base64url_decode(compact.split(".")[1]))
        if "transcription_egress_request" in envelope:
            assert claims["operation_version"] == 2
            assert "campaign_ref" not in claims
            grant = _grant(claims, effect, settings)
            return {"transcription_egress_grant": _sign(grant, EGRESS_GRANT_JOSE_TYPE)}
        callbacks.append(claims)
        assert claims["operation_version"] == 2
        return {"artifactRef": claims["artifact_ref"], "outcome": "available"}

    with (
        mock.patch(
            "core.mastrao_transcription_adapter.extract_verified_audio_file",
            return_value=extracted,
        ),
        mock.patch(
            "core.mastrao_transcription_adapter.post_core_json",
            side_effect=core_callback,
        ),
        mock.patch("core.mastrao_transcription_worker.requests.Session") as session,
        mock.patch("core.mastrao_transcription_worker.ack_gateway_attempt") as ack,
    ):
        session.return_value.__enter__.return_value.post.side_effect = partial(
            _normal_gateway_response, local_effect, extracted, effect
        )
        complete_transcription(local_effect.pk)
    extracted.close.assert_called_once()
    session.return_value.__enter__.return_value.post.assert_called_once()
    ack.assert_called_once()
    local_effect.refresh_from_db()
    assert local_effect.state == local_effect.State.APPLIED
    assert local_effect.dispatch_state == local_effect.DispatchState.COMPLETED
    binding = local_effect.transcription_binding
    binding.refresh_from_db()
    assert binding.state == binding.State.AVAILABLE
    assert (tmp_path / binding.object_ref).exists()
    attempt = local_effect.provider_attempts.get()
    assert attempt.state == attempt.State.SUCCEEDED
    assert callbacks[0]["grant_semantic_digest"] == attempt.grant_semantic_digest
    assert callbacks[0]["authority_version"] == effect["authority_version"]
    assert callbacks[0]["estimated_cost_micros"] == 300
    assert callbacks[0]["usage_audio_seconds"] == 4
    assert "campaign_ref" not in callbacks[0]


def _normal_gateway_response(local_effect, extracted, effect, _endpoint, **kwargs):
    grant = json.loads(
        _base64url_decode(
            kwargs["headers"]["X-Mastrao-Transcription-Egress-Grant"].split(".")[1]
        )
    )
    assert grant["execution_mode"] == "send_allowed"
    metadata = json.loads(kwargs["files"]["metadata"][1])
    assert metadata["language"] == NORMAL_ASR_REQUEST_OPTIONS["language"]
    assert metadata["diarize"] is NORMAL_ASR_REQUEST_OPTIONS["diarize"]
    assert "context_bias" not in metadata
    assert metadata["request_config_digest"] == effect["request_config_digest"]
    attempt = local_effect.provider_attempts.get()
    now = int(timezone.now().timestamp())
    payload = {
        "outcome": "succeeded",
        "transcript": {
            "version": 1,
            "engine_ref": "mistral:voxtral-mini-2602",
            "language": "fr",
            "audio_digest": extracted.sha256,
            "segments": [],
        },
        "provenance": {
            "attempt_ref": attempt.attempt_ref,
            "grant_semantic_digest": attempt.grant_semantic_digest,
            "authority_version": attempt.authority_version,
            "provider_ref": "mistral",
            "requested_model_ref": "voxtral-mini-2602",
            "processing_region_ref": effect["processing_region_ref"],
            "data_control_ref": effect["data_control_ref"],
            "currency": effect["currency"],
            "tariff_catalog_version": effect["tariff_catalog_version"],
            "usage_audio_seconds": 4,
            "estimated_cost_micros": 300,
            "provider_egress_opened_at": now,
            "provider_completed_at": now,
        },
    }
    response = mock.Mock(status_code=200, headers={})
    response.iter_content.return_value = [json.dumps(payload).encode()]
    return response


def test_normal_request_options_match_approved_profile_digest():
    profile = MANAGED_PROFILE_BINDINGS[PROFILE_REF]
    digest = _sha256_canonical(
        {
            "version": 1,
            "provider_ref": profile["asr_provider_ref"],
            "requested_model_ref": profile["requested_model_ref"],
            "normalization_version": "meeting-transcript-v1",
            **NORMAL_ASR_REQUEST_OPTIONS,
        }
    )
    assert digest == profile["request_config_digest"]
