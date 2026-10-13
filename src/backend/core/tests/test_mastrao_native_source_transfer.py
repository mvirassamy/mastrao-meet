"""Direct private-object manifest transfer; Core response explicitly simulated."""

# Imported pytest fixtures and generated LiveKit protobuf members are resolved dynamically.
# pylint: disable=missing-function-docstring,redefined-outer-name,too-many-arguments
# pylint: disable=too-many-positional-arguments,unused-argument,unused-import

import copy
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event
from unittest.mock import patch
from uuid import uuid4

from django.utils import timezone

import jwt
import pytest
from jwt.algorithms import OKPAlgorithm

from core import models
from core.mastrao_native_source_transfer import (
    SOURCE_JOSE,
    _claim,
    _finish,
    _reserve_sources,
    schedule_native_source_transfer,
    transfer_native_source,
)
from core.mastrao_recording_contract import RecordingContractRefused
from core.mastrao_room_contract import _sha256_canonical
from core.tasks.native_capture import process_native_sources
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
def ended_source(client, signer, effect, provider, settings):
    settings.MASTRAO_NATIVE_SOURCE_TRANSFER_ENABLED = True
    settings.MASTRAO_CORE_NATIVE_SOURCE_ENDPOINT = (
        "http://127.0.0.1:9000/internal/v1/meetings/capture/native/source"
    )
    assert _post(client, signer, effect).status_code == 200
    intent = models.MastraoNativeCaptureStart.objects.get()
    intent.drained_at = timezone.now()
    intent.observed_status = 3
    intent.source_manifest = {
        "version": 1,
        "format": "native_track_egress_ogg_opus_v1",
        "object_ref": f"recordings/{intent.capture_ref}.ogg",
        "started_at": "1000000000",
        "ended_at": "3000000000",
        "duration": "2000000000",
        "size": 4096,
        "coverage": "epoch_only",
    }
    intent.save()
    return intent


def _scheduled_source(settings):
    settings.CELERY_ENABLED = True
    with patch(
        "core.tasks.native_capture.process_native_sources.apply_async"
    ) as publish:
        assert schedule_native_source_transfer(limit=1) == 1
    return publish.call_args.kwargs["args"]


def _another_source(source):
    epoch = models.MastraoRtcTrackEpoch.objects.create(
        connection=source.epoch.connection, track_sid=f"batch-{uuid4()}"
    )
    other = copy.copy(source)
    other.pk = uuid4()
    other.epoch = epoch
    other.capture_ref = uuid4()
    other.effect_key = f"batch-{uuid4()}"
    other.provider_job_ref = f"batch-{uuid4()}"
    other.save()
    return other


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
        assert set(body) == {"request", "manifest"}
        assert body["manifest"] == ended_source.source_manifest
        assert claims["manifest_digest"] == _sha256_canonical(body["manifest"])
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

    source_args = _scheduled_source(settings)
    with patch("core.mastrao_native_source_transfer.post_core_json", side_effect=core):
        assert transfer_native_source(*source_args)
        assert not transfer_native_source(*source_args)
    ended_source.refresh_from_db()
    assert len(calls) == 1
    assert ended_source.source_receipt["state"] == "verified"
    assert ended_source.source_claim is None


def test_lost_receipt_retries_same_object_manifest(ended_source, settings):
    source_args = _scheduled_source(settings)
    with patch(
        "core.mastrao_native_source_transfer.post_core_json",
        side_effect=RecordingContractRefused(status=503),
    ):
        assert not transfer_native_source(*source_args)
    ended_source.refresh_from_db()
    assert ended_source.source_attempts == 1
    assert ended_source.source_receipt is None
    assert ended_source.source_error == "native_source_transfer_failed"
    assert ended_source.source_claim is None
    assert not transfer_native_source(*source_args)  # stale task cannot retry
    assert ended_source.source_manifest["object_ref"] == (
        f"recordings/{ended_source.capture_ref}.ogg"
    )


@pytest.mark.parametrize(
    "condition",
    [
        "not_drained",
        "missing_manifest",
        "failed_status",
        "conflict",
        "expired",
        "exhausted",
        "flag_off",
    ],
)
def test_no_transfer_when_ineligible(ended_source, settings, condition):
    settings.CELERY_ENABLED = True
    if condition == "not_drained":
        ended_source.drained_at = None
    elif condition == "missing_manifest":
        ended_source.source_manifest = None
    elif condition == "failed_status":
        ended_source.observed_status = 4
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
    with patch(
        "core.tasks.native_capture.process_native_sources.apply_async"
    ) as publish:
        assert schedule_native_source_transfer(limit=1) == 0
        publish.assert_not_called()


def test_claim_is_exclusive_and_stale_worker_cannot_commit(ended_source):
    source_id, queued_claim, attempts = _reserve_sources(1)[0]
    claimed = _claim(source_id, queued_claim, attempts)
    assert claimed
    assert _claim(source_id, queued_claim, attempts) is None
    models.MastraoNativeCaptureStart.objects.filter(pk=claimed.pk).update(
        source_claim=uuid4()
    )
    assert not _finish(claimed, {"state": "verified"}, "")
    ended_source.refresh_from_db()
    assert ended_source.source_receipt is None


def test_scheduler_does_not_publish_for_claimed_sources(ended_source, settings):
    settings.CELERY_ENABLED = True
    assert _reserve_sources(1)
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


def test_scheduler_dispatches_a_bounded_distinct_batch(ended_source, settings):
    second = _another_source(ended_source)
    third = _another_source(ended_source)
    settings.CELERY_ENABLED = True
    with patch(
        "core.tasks.native_capture.process_native_sources.apply_async"
    ) as publish:
        assert schedule_native_source_transfer(limit=2) == 2
        first_batch = [call.kwargs["args"] for call in publish.call_args_list]
        assert {args[0] for args in first_batch} <= {
            str(ended_source.pk),
            str(second.pk),
            str(third.pk),
        }
        assert len({args[0] for args in first_batch}) == 2
        assert schedule_native_source_transfer(limit=2) == 1
        assert schedule_native_source_transfer(limit=2) == 0
        assert publish.call_count == 3
    assert all(
        call.kwargs["queue"] == "mastrao-transcription"
        for call in publish.call_args_list
    )
    assert (
        models.MastraoNativeCaptureStart.objects.filter(
            source_claim__isnull=False, source_attempts=0
        ).count()
        == 3
    )


def test_delayed_task_cannot_use_a_renewed_reservation(ended_source, settings):
    old_args = _scheduled_source(settings)
    models.MastraoNativeCaptureStart.objects.filter(pk=ended_source.pk).update(
        source_claim_until=timezone.now() - timedelta(seconds=1)
    )
    with patch(
        "core.tasks.native_capture.process_native_sources.apply_async"
    ) as publish:
        assert schedule_native_source_transfer(limit=1) == 1
    renewed_args = publish.call_args.kwargs["args"]
    assert renewed_args != old_args
    assert _claim(*old_args) is None
    assert _claim(*renewed_args)


def test_queued_legacy_wake_dispatches_one_current_source(ended_source, settings):
    settings.CELERY_ENABLED = True
    with patch(
        "core.tasks.native_capture.process_native_sources.apply_async"
    ) as publish:
        assert process_native_sources() == 1
    assert len(publish.call_args.kwargs["args"]) == 3


def test_duplicate_tasks_cannot_transfer_one_source_concurrently(
    ended_source, settings
):
    source_args = _scheduled_source(settings)
    sending = Event()
    release = Event()
    calls = []

    def send(_intent):
        calls.append(1)
        sending.set()
        assert release.wait(timeout=5)
        return {"state": "verified"}

    with patch("core.mastrao_native_source_transfer._send", side_effect=send):
        with ThreadPoolExecutor(max_workers=2) as workers:
            first = workers.submit(transfer_native_source, *source_args)
            assert sending.wait(timeout=5)
            second = workers.submit(transfer_native_source, *source_args)
            assert not second.result(timeout=5)
            release.set()
            assert first.result(timeout=5)
    assert len(calls) == 1
    ended_source.refresh_from_db()
    assert ended_source.source_attempts == 1
