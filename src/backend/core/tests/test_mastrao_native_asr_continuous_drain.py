"""Native ASR completion refills capacity without waiting for reconciliation."""

# pylint: disable=missing-function-docstring,redefined-outer-name

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from unittest.mock import patch
from uuid import uuid4

from django.db import close_old_connections, transaction
from django.utils import timezone

import pytest

from core import models
from core.mastrao_native_asr_client import NativeAsrDeliveryRefused
from core.mastrao_native_asr_worker import (
    _claim,
    _finish,
    _reserve_dispatch,
    dispatch_native_asr,
    native_asr_dispatch_metrics,
    process_native_asr_source,
)
from core.tests.test_mastrao_native_asr_dispatch import add_asr_source

pytest_plugins = (
    "core.tests.test_mastrao_native_asr",
    "core.tests.test_mastrao_native_source_transfer",
)

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def full_capacity(asr_intent, settings):
    settings.CELERY_ENABLED = True
    settings.MASTRAO_NATIVE_ASR_GLOBAL_LIMIT = 2
    settings.MASTRAO_NATIVE_ASR_TENANT_LIMIT = 2
    settings.MASTRAO_NATIVE_ASR_MEETING_LIMIT = 1
    sources = [
        asr_intent,
        add_asr_source(asr_intent, new_meeting=True),
    ]
    pending = [
        add_asr_source(asr_intent, new_meeting=True),
        add_asr_source(asr_intent, new_meeting=True),
    ]
    reservations = [_reserve_dispatch(source.pk) for source in sources]
    assert native_asr_dispatch_metrics()["active_count"] == 2
    for source in pending:
        assert not dispatch_native_asr(source.pk)
    return reservations, pending


@pytest.mark.parametrize("outcome", ["success", "temporary", "terminal"])
def test_worker_finish_immediately_publishes_one_fresh_source(
    asr_intent, peers, full_capacity, outcome
):
    reservations, pending = full_capacity
    assert _claim(*reservations[1]) is not None
    if outcome == "temporary":
        peers[0].side_effect = NativeAsrDeliveryRefused(status=503, outcome="retry")
    if outcome == "terminal":
        peers[0].side_effect = NativeAsrDeliveryRefused(status=422, outcome="rejected")

    def assert_durable_release(**_kwargs):
        asr_intent.refresh_from_db()
        assert asr_intent.asr_claim is None
        assert asr_intent.asr_acknowledged == (outcome == "success")

    with patch(
        "core.tasks.native_capture.process_native_asr.apply_async",
        side_effect=assert_durable_release,
    ) as publish:
        assert process_native_asr_source(*reservations[0]) == (outcome == "success")
    publish.assert_called_once()
    assert publish.call_args.kwargs["args"][0] in {str(source.pk) for source in pending}
    assert publish.call_args.kwargs["queue"] == "mastrao-native-asr-ready"
    assert native_asr_dispatch_metrics()["active_count"] == 2
    assert native_asr_dispatch_metrics()["ready_count"] == 1


def test_finish_dispatches_only_after_commit(full_capacity):
    reservations, pending = full_capacity
    claim = _claim(*reservations[0])
    with patch("core.tasks.native_capture.process_native_asr.apply_async") as publish:
        with transaction.atomic():
            assert _finish(claim, True)
            publish.assert_not_called()
            for source in pending:
                source.refresh_from_db()
                assert source.asr_dispatch_ref is None
        publish.assert_called_once()


def test_finish_dispatches_at_most_one_source_with_spare_capacity(asr_intent, settings):
    settings.CELERY_ENABLED = True
    for _ in range(2):
        add_asr_source(asr_intent, new_meeting=True, tenant="next-tenant")
    claim = _claim(*_reserve_dispatch(asr_intent.pk))
    with patch("core.tasks.native_capture.process_native_asr.apply_async") as publish:
        assert _finish(claim, True)
    publish.assert_called_once()
    assert native_asr_dispatch_metrics()["active_count"] == 1
    assert native_asr_dispatch_metrics()["ready_count"] == 1


def test_rolled_back_finish_does_not_dispatch(full_capacity):
    reservations, _ = full_capacity
    claim = _claim(*reservations[0])
    with patch("core.tasks.native_capture.process_native_asr.apply_async") as publish:
        with pytest.raises(RuntimeError, match="rollback"):
            with transaction.atomic():
                assert _finish(claim, True)
                raise RuntimeError("rollback")
        publish.assert_not_called()
    claim.refresh_from_db()
    assert claim.asr_claim is not None
    assert not claim.asr_acknowledged


@pytest.mark.parametrize("ownership", ["expired", "replaced"])
def test_non_owned_finish_does_not_dispatch(full_capacity, ownership):
    reservations, _ = full_capacity
    claim = _claim(*reservations[0])
    if ownership == "expired":
        models.MastraoNativeCaptureStart.objects.filter(pk=claim.pk).update(
            asr_claim_until=timezone.now() - timedelta(seconds=1)
        )
    else:
        models.MastraoNativeCaptureStart.objects.filter(pk=claim.pk).update(
            asr_claim=uuid4()
        )
    with patch("core.tasks.native_capture.process_native_asr.apply_async") as publish:
        assert not _finish(claim, True)
        publish.assert_not_called()


def test_two_concurrent_finishes_publish_next_source_once(full_capacity):
    reservations, pending = full_capacity
    claims = [_claim(*reservation) for reservation in reservations]
    models.MastraoNativeCaptureStart.objects.filter(pk=pending[1].pk).update(
        asr_next_at=timezone.now() + timedelta(minutes=1)
    )
    barrier = Barrier(2, timeout=5)

    def finish(claim):
        close_old_connections()
        try:
            barrier.wait()
            return _finish(claim, True)
        finally:
            close_old_connections()

    with patch("core.tasks.native_capture.process_native_asr.apply_async") as publish:
        with ThreadPoolExecutor(max_workers=2) as pool:
            assert list(pool.map(finish, claims)) == [True, True]
    publish.assert_called_once()
    assert publish.call_args.kwargs["args"][0] == str(pending[0].pk)
    assert publish.call_args.kwargs["queue"] == "mastrao-native-asr-ready"
