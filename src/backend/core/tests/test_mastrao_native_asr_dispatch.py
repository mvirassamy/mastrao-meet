"""PostgreSQL coverage for exact-source native ASR dispatch and recovery."""

# pylint: disable=missing-function-docstring,redefined-outer-name

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier, Lock
from unittest.mock import patch
from uuid import uuid4

from django.db import close_old_connections, transaction
from django.utils import timezone

import pytest

from core import models
from core.mastrao_native_asr_worker import (
    _claim,
    _finish,
    _reserve_dispatch,
    dispatch_native_asr,
    process_native_asr_source,
    schedule_native_asr,
)
from core.mastrao_native_source_transfer import _claim as claim_source
from core.mastrao_native_source_transfer import _finish as finish_source
from core.mastrao_recording_reconciler import reconcile_native_recordings
from core.tasks.native_capture import process_native_asr

pytest_plugins = (
    "core.tests.test_mastrao_native_asr",
    "core.tests.test_mastrao_native_source_transfer",
)

pytestmark = pytest.mark.django_db(transaction=True)


def add_asr_source(original):
    epoch = models.MastraoRtcTrackEpoch.objects.create(
        connection=original.epoch.connection, track_sid=f"TR_{uuid4().hex}"
    )
    source = models.MastraoNativeCaptureStart.objects.get(pk=original.pk)
    source.pk = uuid4()
    source.epoch = epoch
    source.capture_ref = uuid4()
    source.effect_key = f"effect_native_{uuid4().hex}"
    source.provider_job_ref = None
    source.source_receipt = {
        "source_ref": str(uuid4()),
        "capture_ref": str(source.capture_ref),
        "epoch_ref": str(epoch.pk),
    }
    source.save(force_insert=True)
    return source


def test_reconciler_publishes_three_exact_sources_in_one_pass(asr_intent, settings):
    settings.CELERY_ENABLED = True
    sources = [asr_intent, add_asr_source(asr_intent), add_asr_source(asr_intent)]
    with (
        patch(
            "core.mastrao_recording_reconciler.reconcile_native_captures",
            return_value=0,
        ),
        patch(
            "core.mastrao_recording_reconciler.reconcile_native_admissions",
            return_value=0,
        ),
        patch(
            "core.mastrao_recording_reconciler.schedule_native_source_transfer",
            return_value=0,
        ),
        patch("core.tasks.native_capture.process_native_asr.apply_async") as publish,
    ):
        assert reconcile_native_recordings(limit=3) == 3
        assert reconcile_native_recordings(limit=3) == 0
    assert {call.kwargs["args"][0] for call in publish.call_args_list} == {
        str(source.pk) for source in sources
    }
    assert all(
        len(call.kwargs["args"]) == 2
        and call.kwargs["queue"] == "mastrao-transcription"
        for call in publish.call_args_list
    )


def test_source_receipt_publishes_only_after_commit(ended_source, settings):
    settings.CELERY_ENABLED = True
    settings.MASTRAO_NATIVE_ASR_ENABLED = True
    receipt = {"source_ref": str(uuid4())}
    with patch("core.tasks.native_capture.process_native_asr.apply_async") as publish:
        with transaction.atomic():
            claimed = claim_source()
            assert finish_source(claimed, receipt, "")
            publish.assert_not_called()
        publish.assert_called_once()
    ended_source.refresh_from_db()
    assert ended_source.source_receipt == receipt
    assert publish.call_args.kwargs["args"][0] == str(ended_source.pk)


def test_rolled_back_source_receipt_publishes_nothing(ended_source, settings):
    settings.CELERY_ENABLED = True
    settings.MASTRAO_NATIVE_ASR_ENABLED = True
    with patch("core.tasks.native_capture.process_native_asr.apply_async") as publish:
        with pytest.raises(RuntimeError, match="rollback"):
            with transaction.atomic():
                assert finish_source(claim_source(), {"source_ref": str(uuid4())}, "")
                raise RuntimeError("rollback")
        publish.assert_not_called()
    ended_source.refresh_from_db()
    assert ended_source.source_receipt is None


def test_concurrent_schedulers_reserve_each_source_once(asr_intent, settings):
    settings.CELERY_ENABLED = True
    sources = [asr_intent, add_asr_source(asr_intent), add_asr_source(asr_intent)]
    barrier = Barrier(2, timeout=5)
    lock = Lock()
    reservations_started = 0

    def reserve_with_overlap():
        nonlocal reservations_started
        with lock:
            reservations_started += 1
            should_wait = reservations_started <= 2
        if should_wait:
            barrier.wait()
        return uuid4()

    def run_scheduler():
        close_old_connections()
        try:
            return schedule_native_asr(limit=3)
        finally:
            close_old_connections()

    with (
        patch("core.mastrao_native_asr_worker.uuid4", side_effect=reserve_with_overlap),
        patch("core.tasks.native_capture.process_native_asr.apply_async") as publish,
    ):
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: run_scheduler(), range(2)))
    assert reservations_started >= 2
    assert sum(results) == 3
    assert {call.kwargs["args"][0] for call in publish.call_args_list} == {
        str(source.pk) for source in sources
    }


def test_concurrent_claims_on_same_connection(asr_intent):
    other = add_asr_source(asr_intent)
    dispatches = [_reserve_dispatch(source.pk) for source in (asr_intent, other)]
    claims_selected = Barrier(2, timeout=5)
    original_uuid4 = uuid4

    def hold_claim_lock():
        claims_selected.wait()
        return original_uuid4()

    def claim_on_separate_connection(dispatch):
        close_old_connections()
        try:
            return _claim(*dispatch)
        finally:
            close_old_connections()

    with patch("core.mastrao_native_asr_worker.uuid4", side_effect=hold_claim_lock):
        with ThreadPoolExecutor(max_workers=2) as pool:
            claims = list(pool.map(claim_on_separate_connection, dispatches))

    assert all(claim is not None for claim in claims)
    assert {claim.pk for claim in claims} == {asr_intent.pk, other.pk}


def test_replaced_dispatch_message_is_no_op(asr_intent, peers):
    old = _reserve_dispatch(asr_intent.pk)
    models.MastraoNativeCaptureStart.objects.filter(pk=asr_intent.pk).update(
        asr_dispatch_ref=None, asr_dispatch_until=None
    )
    current = _reserve_dispatch(asr_intent.pk)
    assert current[1] != old[1]
    assert not process_native_asr_source(*old)
    peers[0].assert_not_called()
    assert process_native_asr_source(*current)


def test_late_message_claims_after_expiry_and_republication(
    asr_intent, peers, settings
):
    settings.CELERY_ENABLED = True
    first_dispatch = _reserve_dispatch(asr_intent.pk)
    models.MastraoNativeCaptureStart.objects.filter(pk=asr_intent.pk).update(
        asr_dispatch_until=timezone.now() - timedelta(minutes=6)
    )

    with patch("core.tasks.native_capture.process_native_asr.apply_async") as publish:
        assert schedule_native_asr() == 1
    republished = publish.call_args.kwargs["args"]
    assert republished == [str(first_dispatch[0]), str(first_dispatch[1])]

    assert process_native_asr_source(*first_dispatch)
    assert not process_native_asr_source(*republished)
    asr_intent.refresh_from_db()
    assert asr_intent.asr_attempts == 1
    peers[1].assert_called_once()


def test_legacy_or_malformed_message_is_no_op(asr_intent, peers):
    assert not process_native_asr()
    assert not process_native_asr(str(asr_intent.pk), "invalid-dispatch")
    peers[0].assert_not_called()


def test_broker_failure_releases_reservation_for_reconciliation(asr_intent, settings):
    settings.CELERY_ENABLED = True
    with patch(
        "core.tasks.native_capture.process_native_asr.apply_async",
        side_effect=RuntimeError("broker unavailable"),
    ):
        assert dispatch_native_asr(asr_intent.pk)
    asr_intent.refresh_from_db()
    assert asr_intent.asr_dispatch_ref is not None
    assert asr_intent.asr_dispatch_until is None
    assert asr_intent.asr_attempts == 0
    with patch("core.tasks.native_capture.process_native_asr.apply_async") as publish:
        assert schedule_native_asr() == 1
        publish.assert_called_once()


def test_old_message_claims_once_after_failed_republication(
    asr_intent, peers, settings
):
    settings.CELERY_ENABLED = True
    first_dispatch = _reserve_dispatch(asr_intent.pk)
    models.MastraoNativeCaptureStart.objects.filter(pk=asr_intent.pk).update(
        asr_dispatch_until=timezone.now() - timedelta(minutes=6)
    )

    with patch(
        "core.tasks.native_capture.process_native_asr.apply_async",
        side_effect=RuntimeError("broker unavailable"),
    ):
        assert dispatch_native_asr(asr_intent.pk)

    asr_intent.refresh_from_db()
    assert asr_intent.asr_dispatch_ref == first_dispatch[1]
    assert asr_intent.asr_dispatch_until is None
    assert process_native_asr_source(*first_dispatch)
    assert not process_native_asr_source(*first_dispatch)
    asr_intent.refresh_from_db()
    assert asr_intent.asr_attempts == 1
    peers[1].assert_called_once()


def test_expired_execution_lease_can_be_redispatched(asr_intent, settings):
    settings.CELERY_ENABLED = True
    first_dispatch = _reserve_dispatch(asr_intent.pk)
    first_claim = _claim(*first_dispatch)
    models.MastraoNativeCaptureStart.objects.filter(pk=asr_intent.pk).update(
        asr_claim_until=timezone.now() - timedelta(seconds=1)
    )
    with patch("core.tasks.native_capture.process_native_asr.apply_async") as publish:
        assert schedule_native_asr() == 1
    second_dispatch = publish.call_args.kwargs["args"]
    assert not process_native_asr_source(*first_dispatch)
    second_claim = _claim(*second_dispatch)
    assert second_claim.asr_claim != first_claim.asr_claim
    assert not _finish(first_claim, True)


def test_existing_core_receipt_skips_provider_after_redispatch(asr_intent, peers):
    receipt = peers[2].return_value
    models.MastraoNativeCaptureStart.objects.filter(pk=asr_intent.pk).update(
        asr_receipt=receipt
    )
    assert process_native_asr_source(*_reserve_dispatch(asr_intent.pk))
    peers[0].assert_not_called()
    peers[1].assert_not_called()
    peers[2].assert_not_called()
    peers[3].assert_called_once_with(receipt)
