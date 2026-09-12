"""Real Django/PostgreSQL delivery state; Core/gateway peers explicitly simulated."""

import base64
import hashlib
import json
from datetime import timedelta
from unittest.mock import Mock, patch
from uuid import uuid4

from django.utils import timezone

import pytest

from core import models
from core.mastrao_core_http import read_bounded_core_json
from core.mastrao_native_asr_client import prepare_native_asr, transcribe_native_asr
from core.mastrao_native_asr_worker import (
    _claim,
    _finish,
    process_next_native_asr,
    schedule_native_asr,
)
from core.mastrao_recording_contract import RecordingContractRefused
from core.tests.test_mastrao_native_capture import (
    _post,
    binding,
    effect,
    host,
    isolated_binding_settings,
    provider,
    receipt,
    signer,
)

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def asr_intent(client, signer, effect, provider, settings):
    settings.MASTRAO_NATIVE_ASR_ENABLED = True
    assert _post(client, signer, effect).status_code == 200
    intent = models.MastraoNativeCaptureStart.objects.get()
    intent.source_receipt = {
        "source_ref": str(uuid4()),
        "capture_ref": str(intent.capture_ref),
        "epoch_ref": str(intent.epoch_id),
    }
    intent.save()
    return intent


@pytest.fixture
def peers(asr_intent):
    core_receipt = {
        "core": {
            "state": "verified",
            "result_ref": str(uuid4()),
            "source_ref": asr_intent.source_receipt["source_ref"],
            "attempt_ref": "native_attempt_fixture",
        },
        "fingerprint": "a" * 64,
    }
    with (
        patch(
            "core.mastrao_native_asr_worker.prepare_native_asr",
            return_value=({}, b"audio"),
        ) as prepare,
        patch(
            "core.mastrao_native_asr_worker.transcribe_native_asr", return_value={}
        ) as transcribe,
        patch(
            "core.mastrao_native_asr_worker.upload_native_asr_result",
            return_value=core_receipt,
        ) as upload,
        patch("core.mastrao_native_asr_worker.ack_native_asr") as ack,
    ):
        yield prepare, transcribe, upload, ack


def test_worker_persists_core_receipt_before_ack_without_video_gate(asr_intent, peers):
    prepare, transcribe, upload, ack = peers

    def assert_durable(value):
        asr_intent.refresh_from_db()
        assert asr_intent.asr_receipt == value
        assert value == upload.return_value
        assert not asr_intent.asr_acknowledged

    ack.side_effect = assert_durable
    assert process_next_native_asr()
    assert (
        prepare.call_count
        == transcribe.call_count
        == upload.call_count
        == ack.call_count
        == 1
    )
    asr_intent.refresh_from_db()
    assert asr_intent.asr_acknowledged
    assert asr_intent.asr_claim is None
    assert not process_next_native_asr()
    assert transcribe.call_count == 1


def test_ack_failure_retries_only_ack_and_never_transcribes_again(asr_intent, peers):
    prepare, transcribe, upload, ack = peers
    ack.side_effect = RecordingContractRefused(status=503)
    assert not process_next_native_asr()
    asr_intent.refresh_from_db()
    assert asr_intent.asr_receipt == upload.return_value
    assert not asr_intent.asr_acknowledged
    models.MastraoNativeCaptureStart.objects.filter(pk=asr_intent.pk).update(
        asr_next_at=timezone.now() - timedelta(seconds=1)
    )
    ack.side_effect = None
    assert process_next_native_asr()
    assert prepare.call_count == transcribe.call_count == upload.call_count == 1
    assert ack.call_count == 2


@pytest.mark.parametrize("stage", [0, 1, 2])
def test_upstream_failure_keeps_gateway_cache_and_durable_retry(
    asr_intent, peers, stage
):
    peers[stage].side_effect = RecordingContractRefused(status=503)
    assert not process_next_native_asr()
    peers[3].assert_not_called()
    asr_intent.refresh_from_db()
    assert asr_intent.asr_receipt is None
    assert asr_intent.asr_error == "native_asr_delivery_failed"


def test_stale_lease_cannot_finish_or_ack_another_workers_result(asr_intent, peers):
    first = _claim()
    assert first is not None
    assert _claim() is None
    models.MastraoNativeCaptureStart.objects.filter(pk=asr_intent.pk).update(
        asr_claim_until=timezone.now() - timedelta(seconds=1)
    )
    second = _claim()
    assert second.asr_claim != first.asr_claim
    assert not _finish(first, True)
    assert _finish(second, False)
    peers[3].assert_not_called()


def test_lease_loss_before_core_receipt_commit_preserves_gateway_copy(
    asr_intent, peers
):
    def lose_claim(*_args):
        models.MastraoNativeCaptureStart.objects.filter(pk=asr_intent.pk).update(
            asr_claim=uuid4()
        )
        return peers[2].return_value

    peers[2].side_effect = lose_claim
    assert not process_next_native_asr()
    peers[3].assert_not_called()
    asr_intent.refresh_from_db()
    assert asr_intent.asr_receipt is None


def test_disabled_or_unverified_source_is_not_scheduled(asr_intent, peers, settings):
    settings.MASTRAO_NATIVE_ASR_ENABLED = False
    assert not process_next_native_asr()
    settings.MASTRAO_NATIVE_ASR_ENABLED = True
    models.MastraoNativeCaptureStart.objects.filter(pk=asr_intent.pk).update(
        source_receipt=None
    )
    assert not process_next_native_asr()
    settings.CELERY_ENABLED = False
    assert schedule_native_asr() == 0
    peers[0].assert_not_called()


def test_prepare_accepts_scoped_large_response_but_refuses_changed_audio(
    asr_intent, settings
):
    settings.MASTRAO_CORE_NATIVE_ASR_PREPARE_ENDPOINT = (
        "http://127.0.0.1:9000/internal/v1/meetings/capture/native/asr/prepare"
    )
    audio = b"fLaC" + b"x" * 30000
    prepared = {
        "version": 1,
        "source_ref": asr_intent.source_receipt["source_ref"],
        "capture_ref": str(asr_intent.capture_ref),
        "epoch_ref": str(asr_intent.epoch_id),
        "metadata": {
            "provider": "mistral",
            "audio_codec": "flac",
            "audio_sha256": hashlib.sha256(audio).hexdigest(),
        },
        "native_source_egress_grant": "signed-fixture-placeholder",
        "audio_base64": base64.b64encode(audio).decode(),
    }
    with patch(
        "core.mastrao_native_asr_client.post_core_json", return_value=prepared
    ) as core:
        assert prepare_native_asr(asr_intent)[1] == audio
        assert core.call_args.kwargs["maximum_response_bytes"] == 24 * 1024**2
        prepared["metadata"]["audio_sha256"] = "f" * 64
        with pytest.raises(RecordingContractRefused):
            prepare_native_asr(asr_intent)


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://api.eu.mistral.ai/v1/native/transcribe",
        "http://asr-gateway/v1/transcribe",
        "http://user:password@asr-gateway/v1/native/transcribe",
        "http://asr-gateway/v1/native/transcribe?x=1",
    ],
)
def test_client_never_targets_direct_provider_or_legacy_gateway(endpoint, settings):
    settings.MASTRAO_NATIVE_ASR_GATEWAY_ENDPOINT = endpoint
    settings.MASTRAO_NATIVE_ASR_GATEWAY_AUTH_TOKEN = "native-gateway-fixture-only-token"
    with (
        patch("core.mastrao_native_asr_client.requests.Session") as session,
        pytest.raises(RecordingContractRefused),
    ):
        transcribe_native_asr({}, b"audio")
    session.assert_not_called()


def test_large_core_bound_is_opt_in_and_closes_oversized_streams():
    raw = json.dumps({"audio_base64": "x" * 30000}).encode()

    def response():
        item = Mock(status_code=200, headers={})
        item.iter_content.return_value = [raw]
        return item

    small = response()
    with pytest.raises(RecordingContractRefused):
        read_bounded_core_json(small, RecordingContractRefused)
    small.close.assert_called_once()
    assert (
        len(
            read_bounded_core_json(
                response(), RecordingContractRefused, maximum_bytes=40000
            )["audio_base64"]
        )
        == 30000
    )
