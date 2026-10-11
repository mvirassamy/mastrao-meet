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
from core.factories import RoomFactory
from core.mastrao_native_asr_client import NativeAsrDeliveryRefused
from core.mastrao_native_asr_worker import (
    _claim,
    _finish,
    _lock_dispatch_capacity,
    _reserve_dispatch,
    dispatch_native_asr,
    native_asr_dispatch_metrics,
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


def add_asr_source(original, *, new_meeting=False, tenant=None):
    connection = original.epoch.connection
    if new_meeting:
        binding = models.MastraoRoomBinding.objects.get(pk=connection.room_binding_id)
        binding.pk = uuid4()
        binding.room = RoomFactory(name=f"room_{uuid4().hex}")
        binding.effect_key = f"binding_{uuid4().hex}"
        binding.room_ref = f"room_{uuid4().hex}"
        binding.meeting_ref = f"meeting_{uuid4().hex}"
        binding.save(force_insert=True)
        connection = models.MastraoRtcConnection.objects.get(pk=connection.pk)
        connection.pk = uuid4()
        connection.room_binding = binding
        connection.room_sid = f"RM_{uuid4().hex}"
        connection.save(force_insert=True)
    epoch = models.MastraoRtcTrackEpoch.objects.create(
        connection=connection, track_sid=f"TR_{uuid4().hex}"
    )
    source = models.MastraoNativeCaptureStart.objects.get(pk=original.pk)
    source.pk = uuid4()
    source.epoch = epoch
    source.capture_ref = uuid4()
    source.effect_key = f"effect_native_{uuid4().hex}"
    if tenant is not None:
        source.organization_external_id = tenant
    source.provider_job_ref = None
    source.asr_dispatch_ref = None
    source.asr_dispatch_until = None
    source.asr_dispatch_queue = ""
    source.source_receipt = {
        "source_ref": str(uuid4()),
        "capture_ref": str(source.capture_ref),
        "epoch_ref": str(epoch.pk),
    }
    source.save(force_insert=True)
    return source


def test_reconciler_publishes_three_exact_sources_in_one_pass(asr_intent, settings):
    settings.CELERY_ENABLED = True
    sources = [
        asr_intent,
        add_asr_source(asr_intent, new_meeting=True, tenant="second-tenant"),
        add_asr_source(asr_intent, new_meeting=True, tenant="second-tenant"),
    ]
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
        and call.kwargs["queue"] == "mastrao-native-asr-ready"
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
    sources = [
        asr_intent,
        add_asr_source(asr_intent, new_meeting=True),
        add_asr_source(asr_intent, new_meeting=True, tenant="second-tenant"),
    ]
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
        return _lock_dispatch_capacity()

    def run_scheduler():
        close_old_connections()
        try:
            return schedule_native_asr(limit=3)
        finally:
            close_old_connections()

    with (
        patch(
            "core.mastrao_native_asr_worker._lock_dispatch_capacity",
            side_effect=reserve_with_overlap,
        ),
        patch("core.tasks.native_capture.process_native_asr.apply_async") as publish,
    ):
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: run_scheduler(), range(2)))
    assert reservations_started >= 2
    assert sum(results) == 3
    assert {call.kwargs["args"][0] for call in publish.call_args_list} == {
        str(source.pk) for source in sources
    }


def test_concurrent_claims_on_same_connection(asr_intent, settings):
    # The two sources share the joined connection; allow both under this limit.
    settings.MASTRAO_NATIVE_ASR_MEETING_LIMIT = 2
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

    with (
        patch("core.mastrao_native_asr_worker.uuid4", side_effect=hold_claim_lock),
        patch("core.mastrao_native_asr_worker._lock_dispatch_capacity"),
    ):
        with ThreadPoolExecutor(max_workers=2) as pool:
            claims = list(pool.map(claim_on_separate_connection, dispatches))

    assert all(claim is not None for claim in claims)
    assert {claim.pk for claim in claims} == {asr_intent.pk, other.pk}


def test_concurrent_claims_respect_single_meeting_slot(asr_intent, settings):
    settings.MASTRAO_NATIVE_ASR_MEETING_LIMIT = 2
    other = add_asr_source(asr_intent)
    dispatches = [_reserve_dispatch(source.pk) for source in (asr_intent, other)]
    models.MastraoNativeCaptureStart.objects.filter(
        pk__in=[asr_intent.pk, other.pk]
    ).update(asr_dispatch_until=timezone.now() - timedelta(seconds=1))
    settings.MASTRAO_NATIVE_ASR_MEETING_LIMIT = 1
    start = Barrier(2, timeout=5)

    def claim(dispatch):
        close_old_connections()
        try:
            start.wait()
            return _claim(*dispatch)
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(claim, dispatches))
    assert sum(claim is not None for claim in claims) == 1


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
    assert asr_intent.asr_dispatch_queue == "mastrao-native-asr-ready"
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


def test_reservation_enforces_meeting_and_tenant_limits(asr_intent, settings):
    settings.MASTRAO_NATIVE_ASR_MEETING_LIMIT = 1
    settings.MASTRAO_NATIVE_ASR_TENANT_LIMIT = 2
    same_meeting = add_asr_source(asr_intent)
    same_tenant = [
        add_asr_source(asr_intent, new_meeting=True),
        add_asr_source(asr_intent, new_meeting=True),
    ]
    assert _reserve_dispatch(asr_intent.pk)
    assert _reserve_dispatch(same_meeting.pk) is None
    assert _reserve_dispatch(same_tenant[0].pk)
    assert _reserve_dispatch(same_tenant[1].pk) is None


def test_concurrent_schedulers_respect_meeting_capacity(asr_intent, settings):
    settings.CELERY_ENABLED = True
    settings.MASTRAO_NATIVE_ASR_MEETING_LIMIT = 1
    add_asr_source(asr_intent)
    add_asr_source(asr_intent)

    def reserve():
        close_old_connections()
        try:
            return schedule_native_asr(limit=3)
        finally:
            close_old_connections()

    with patch("core.tasks.native_capture.process_native_asr.apply_async") as publish:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: reserve(), range(2)))
    assert sum(results) == 1
    publish.assert_called_once()


def test_retry_capacity_reserves_one_ready_slot_and_routes_lanes(asr_intent, settings):
    settings.CELERY_ENABLED = True
    retries = [
        add_asr_source(asr_intent, new_meeting=True, tenant=f"tenant-{index}")
        for index in range(settings.MASTRAO_NATIVE_ASR_GLOBAL_LIMIT)
    ]
    models.MastraoNativeCaptureStart.objects.filter(
        pk__in=[source.pk for source in retries]
    ).update(asr_error="native_asr_delivery_failed")
    with patch("core.tasks.native_capture.process_native_asr.apply_async") as publish:
        for source in retries[:-1]:
            assert dispatch_native_asr(source.pk)
        assert not dispatch_native_asr(retries[-1].pk)
        assert dispatch_native_asr(asr_intent.pk)
    assert [call.kwargs["queue"] for call in publish.call_args_list] == [
        "mastrao-native-asr-retry"
    ] * (settings.MASTRAO_NATIVE_ASR_GLOBAL_LIMIT - 1) + ["mastrao-native-asr-ready"]


def test_retry_capacity_preserves_fresh_slot_in_same_tenant(asr_intent, settings):
    settings.MASTRAO_NATIVE_ASR_TENANT_LIMIT = 3
    retries = [add_asr_source(asr_intent, new_meeting=True) for _ in range(3)]
    models.MastraoNativeCaptureStart.objects.filter(
        pk__in=[source.pk for source in retries]
    ).update(asr_error="native_asr_delivery_failed")

    assert _reserve_dispatch(retries[0].pk)
    assert _reserve_dispatch(retries[1].pk)
    assert _reserve_dispatch(retries[2].pk) is None
    assert _reserve_dispatch(asr_intent.pk)


def test_late_retry_cannot_claim_tenant_fresh_slot(asr_intent, settings):
    settings.MASTRAO_NATIVE_ASR_TENANT_LIMIT = 3
    models.MastraoNativeCaptureStart.objects.filter(pk=asr_intent.pk).update(
        asr_error="native_asr_delivery_failed"
    )
    late = _reserve_dispatch(asr_intent.pk)
    models.MastraoNativeCaptureStart.objects.filter(pk=asr_intent.pk).update(
        asr_dispatch_until=timezone.now() - timedelta(seconds=1)
    )
    retries = [add_asr_source(asr_intent, new_meeting=True) for _ in range(2)]
    for source in retries:
        assert _reserve_dispatch(source.pk)
    fresh = add_asr_source(asr_intent, new_meeting=True)
    models.MastraoNativeCaptureStart.objects.filter(pk=fresh.pk).update(asr_error="")

    assert _claim(*late) is None
    assert _reserve_dispatch(fresh.pk)


def test_scheduler_prefers_ready_and_reports_only_aggregate_backlog(
    asr_intent, settings
):
    settings.CELERY_ENABLED = True
    retry = add_asr_source(asr_intent, new_meeting=True, tenant="retry-tenant")
    now = timezone.now()
    models.MastraoNativeCaptureStart.objects.filter(pk=retry.pk).update(
        asr_error="native_asr_delivery_failed",
        asr_next_at=now - timedelta(minutes=3),
    )
    models.MastraoNativeCaptureStart.objects.filter(pk=asr_intent.pk).update(
        asr_next_at=now - timedelta(minutes=1)
    )
    metrics = native_asr_dispatch_metrics(now)
    assert metrics["ready_count"] == metrics["retry_count"] == 1
    assert metrics["ready_age_seconds"] >= 60
    assert metrics["retry_age_seconds"] >= 180
    assert set(metrics) == {
        "ready_count",
        "retry_count",
        "ready_age_seconds",
        "retry_age_seconds",
        "active_count",
    }
    with patch("core.tasks.native_capture.process_native_asr.apply_async") as publish:
        assert schedule_native_asr(limit=1) == 1
    assert publish.call_args.kwargs["args"][0] == str(asr_intent.pk)


def test_sustained_ready_backlog_still_dispatches_retry(asr_intent, settings):
    settings.CELERY_ENABLED = True
    add_asr_source(asr_intent, new_meeting=True)
    retry = add_asr_source(asr_intent, new_meeting=True, tenant="retry-tenant")
    models.MastraoNativeCaptureStart.objects.filter(pk=retry.pk).update(
        asr_error="native_asr_delivery_failed"
    )
    with patch("core.tasks.native_capture.process_native_asr.apply_async") as publish:
        assert schedule_native_asr(limit=3) == 3
    assert [call.kwargs["queue"] for call in publish.call_args_list] == [
        "mastrao-native-asr-ready",
        "mastrao-native-asr-retry",
        "mastrao-native-asr-ready",
    ]
    retry.refresh_from_db()
    assert retry.asr_dispatch_queue == "mastrao-native-asr-retry"


def test_provider_retry_after_precedes_local_backoff(asr_intent):
    claimed = _claim(*_reserve_dispatch(asr_intent.pk))
    deadline = timezone.now() + timedelta(seconds=5)
    error = NativeAsrDeliveryRefused(status=429, outcome="retry", retry_at=deadline)
    assert _finish(claimed, False, error)
    asr_intent.refresh_from_db()
    assert asr_intent.asr_next_at == deadline
    assert asr_intent.asr_attempts == 0
    assert asr_intent.asr_retry_streak == 1


def test_local_retry_backoff_grows_after_repeated_failures(asr_intent):
    first = _claim(*_reserve_dispatch(asr_intent.pk))
    assert _finish(first, False)
    asr_intent.refresh_from_db()
    assert timedelta(seconds=29) <= asr_intent.asr_next_at - timezone.now()

    models.MastraoNativeCaptureStart.objects.filter(pk=asr_intent.pk).update(
        asr_next_at=timezone.now() - timedelta(seconds=1)
    )
    second = _claim(*_reserve_dispatch(asr_intent.pk))
    assert _finish(second, False)
    asr_intent.refresh_from_db()
    assert timedelta(seconds=59) <= asr_intent.asr_next_at - timezone.now()


def test_local_backoff_grows_without_consuming_core_retry_budget(asr_intent):
    error = NativeAsrDeliveryRefused(status=429, outcome="retry")
    for minimum in (29, 59):
        models.MastraoNativeCaptureStart.objects.filter(pk=asr_intent.pk).update(
            asr_next_at=timezone.now() - timedelta(seconds=1)
        )
        claim = _claim(*_reserve_dispatch(asr_intent.pk))
        assert _finish(claim, False, error)
        asr_intent.refresh_from_db()
        assert asr_intent.asr_attempts == 0
        assert asr_intent.asr_next_at - timezone.now() >= timedelta(seconds=minimum)


def test_late_message_cannot_exceed_global_capacity(asr_intent, settings):
    late = _reserve_dispatch(asr_intent.pk)
    models.MastraoNativeCaptureStart.objects.filter(pk=asr_intent.pk).update(
        asr_dispatch_until=timezone.now() - timedelta(minutes=1)
    )
    others = [
        add_asr_source(asr_intent, new_meeting=True, tenant=f"tenant-{index}")
        for index in range(settings.MASTRAO_NATIVE_ASR_GLOBAL_LIMIT)
    ]
    for source in others:
        assert _reserve_dispatch(source.pk)
    assert _claim(*late) is None
    models.MastraoNativeCaptureStart.objects.filter(pk=others[0].pk).update(
        asr_dispatch_ref=None, asr_dispatch_until=None, asr_dispatch_queue=""
    )
    assert _reserve_dispatch(asr_intent.pk) == late
    assert _claim(*late)
