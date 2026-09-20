"""Actual Django/PostgreSQL + AAC/HLS/FLAC; Core response explicitly simulated."""

# Imported pytest fixtures and generated LiveKit protobuf members are resolved dynamically.
# pylint: disable=missing-function-docstring,redefined-outer-name,too-many-arguments
# pylint: disable=too-many-positional-arguments,unused-argument,unused-import

import base64
import hashlib
import subprocess
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from django.utils import timezone

import jwt
import pytest
from jwt.algorithms import OKPAlgorithm

from core import models
from core.mastrao_native_audio import native_audio_manifest_digest
from core.mastrao_native_source_transfer import (
    SOURCE_JOSE,
    _claim,
    _finish,
    schedule_native_source_transfer,
    transfer_next_native_source,
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
def ended_source(client, signer, effect, provider, settings, tmp_path):  # noqa: PLR0913,PLR0917
    settings.MASTRAO_NATIVE_CAPTURE_SPOOL_ROOT = str(tmp_path.resolve() / "spool")
    settings.MASTRAO_NATIVE_SOURCE_TRANSFER_ENABLED = True
    settings.MASTRAO_CORE_NATIVE_SOURCE_ENDPOINT = (
        "http://127.0.0.1:9000/internal/v1/meetings/capture/native/source"
    )
    assert _post(client, signer, effect).status_code == 200
    intent = models.MastraoNativeCaptureStart.objects.get()
    intent.drained_at = timezone.now()
    intent.observed_status = 3
    intent.save()
    output = Path(intent.output_prefix)
    output.mkdir(parents=True)
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=2",
            "-threads",
            "1",
            "-c:a",
            "aac",
            "-ar",
            "48000",
            "-f",
            "hls",
            "-hls_time",
            "1",
            "-hls_playlist_type",
            "event",
            "-hls_segment_filename",
            str(output / "audio_%05d.ts"),
            str(output / "index.m3u8"),
        ],
        check=True,
        timeout=15,
        capture_output=True,
    )
    return intent


def test_real_audio_transfer_keeps_exact_binding_and_canonical_receipt(
    ended_source, settings
):
    calls = []

    def core(**kwargs):
        body = kwargs["body"]
        assert kwargs["headers"] == {"Authorization": f"Bearer {body['request']}"}
        claims = jwt.decode(
            body["request"],
            OKPAlgorithm.from_jwk(settings.MASTRAO_RECORDING_EFFECT_PUBLIC_JWK),
            algorithms=["EdDSA"],
            options={"verify_aud": False},
        )
        assert claims["issuer"] == "meet-fixture"
        assert claims["audience"] == "core-fixture"
        assert jwt.get_unverified_header(body["request"])["typ"] == SOURCE_JOSE
        assert claims["epoch_ref"] == str(ended_source.epoch_id)
        assert claims["capture_ref"] == str(ended_source.capture_ref)
        assert claims["provider_job_ref"] == ended_source.provider_job_ref
        assert claims["manifest_digest"] == native_audio_manifest_digest(
            body["manifest"]
        )
        audio = base64.b64decode(body["audio_base64"], validate=True)
        assert audio.startswith(b"fLaC")
        assert hashlib.sha256(audio).hexdigest() == body["manifest"]["audio"]["sha256"]
        pending = models.MastraoNativeCaptureStart.objects.get()
        assert pending.source_claim and pending.source_receipt is None
        calls.append(body)
        return {
            "version": 1,
            "source_ref": str(uuid4()),
            "capture_ref": claims["capture_ref"],
            "epoch_ref": claims["epoch_ref"],
            "manifest_digest": claims["manifest_digest"],
            "state": "verified",
            "coverage": "epoch_only",
            "transcription_state": "not_requested",
        }

    with patch("core.mastrao_native_source_transfer.post_core_json", side_effect=core):
        assert transfer_next_native_source()
        assert not transfer_next_native_source()
    ended_source.refresh_from_db()
    assert len(calls) == 1
    assert ended_source.source_receipt["state"] == "verified"
    assert ended_source.source_claim is None
    assert (Path(ended_source.output_prefix) / "index.m3u8").exists()


def test_lost_receipt_retries_same_epoch_without_deleting_spool(ended_source):
    with patch(
        "core.mastrao_native_source_transfer.post_core_json",
        side_effect=RecordingContractRefused(status=503),
    ):
        assert not transfer_next_native_source()
    ended_source.refresh_from_db()
    assert ended_source.source_attempts == 1
    assert ended_source.source_receipt is None
    assert ended_source.source_error == "native_source_transfer_failed"
    assert ended_source.source_claim is None
    assert not transfer_next_native_source()  # backoff, not an immediate retry
    assert (Path(ended_source.output_prefix) / "audio_00000.ts").exists()


@pytest.mark.parametrize(
    "condition", ["not_drained", "conflict", "expired", "exhausted", "flag_off"]
)
def test_no_transfer_when_ineligible(ended_source, settings, condition):
    if condition == "not_drained":
        ended_source.drained_at = None
    elif condition == "conflict":
        ended_source.epoch.conflict = True
        ended_source.epoch.save()
    elif condition == "expired":
        ended_source.retention_expires_at = timezone.now() - timedelta(seconds=1)
    elif condition == "exhausted":
        ended_source.source_attempts = 8
    else:
        settings.MASTRAO_NATIVE_SOURCE_TRANSFER_ENABLED = False
    ended_source.save()
    with patch("core.mastrao_native_source_transfer.post_core_json") as send:
        assert not transfer_next_native_source()
        send.assert_not_called()


def test_claim_is_exclusive_and_stale_worker_cannot_commit(ended_source):
    claimed = _claim()
    assert claimed
    assert _claim() is None
    models.MastraoNativeCaptureStart.objects.filter(pk=claimed.pk).update(
        source_claim=uuid4()
    )
    assert not _finish(claimed, {"state": "verified"}, "")
    ended_source.refresh_from_db()
    assert ended_source.source_receipt is None


def test_scheduler_does_not_publish_for_claimed_sources(ended_source, settings):
    settings.CELERY_ENABLED = True
    assert _claim()
    with patch(
        "core.tasks.native_capture.process_native_sources.apply_async"
    ) as publish:
        assert schedule_native_source_transfer() == 0
        publish.assert_not_called()


def test_scheduler_broker_failure_preserves_retryable_intent(ended_source, settings):
    settings.CELERY_ENABLED = True
    with patch(
        "core.tasks.native_capture.process_native_sources.apply_async",
        side_effect=RuntimeError("fixture broker unavailable"),
    ):
        assert schedule_native_source_transfer() == 0
    ended_source.refresh_from_db()
    assert ended_source.source_claim is None
    assert ended_source.source_receipt is None
    assert ended_source.source_attempts == 0
