"""Actual PostgreSQL drain, with synthetic Egress state (not media proof)."""

# Imported pytest fixtures and generated LiveKit protobuf members are resolved dynamically.
# pylint: disable=missing-function-docstring,no-member,no-name-in-module,protected-access
# pylint: disable=redefined-outer-name,too-many-arguments,too-many-positional-arguments
# pylint: disable=unused-argument,unused-import

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from io import StringIO
from unittest.mock import AsyncMock, patch

from django.core.management import call_command
from django.db import DatabaseError, close_old_connections, connection
from django.test import Client
from django.utils import timezone

import psycopg
import pytest
import requests
from livekit import api

from core import mastrao_native_capture_adapter as adapter
from core import models
from core.mastrao_native_capture_drain import (
    TERMINAL,
    _claim_next,
    _finish,
    reconcile_native_captures,
)
from core.tests.test_mastrao_native_capture import (
    URL,
    _post,
    _twirp_fixture,
    binding,
    effect,
    host,
    isolated_binding_settings,
    provider,
    receipt,
    signer,
)

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize("lose_stop", [False, True])
def test_real_http_start_stop_and_independent_terminal_observation(
    live_server, signer, effect, settings, lose_stop
):
    with _twirp_fixture(effect, False, lose_stop=lose_stop) as (
        configuration,
        calls,
        errors,
    ):
        settings.LIVEKIT_CONFIGURATION = configuration
        settings.LIVEKIT_VERIFY_SSL = True
        response = requests.post(
            live_server.url + URL,
            json={"native_start_effect": signer(effect)},
            timeout=8,
        )
        assert response.status_code == 200, errors
        _closed()
        assert reconcile_native_captures() == 0, errors
        assert models.MastraoNativeCaptureStart.objects.get().drained_at is None
        _due()
        assert reconcile_native_captures() == 1, errors
        with psycopg.connect(**connection.get_connection_params()) as independent:
            row = independent.execute(
                "SELECT provider_job_ref, observed_status, drained_at "
                "FROM meet_mastrao_native_capture_start"
            ).fetchone()
        assert (
            row[0] == "EG_nativehttp"
            and row[1] == api.EgressStatus.EGRESS_COMPLETE
            and row[2]
        )
        assert calls == [
            "/twirp/livekit.Egress/StartTrackCompositeEgress",
            "/twirp/livekit.Egress/ListEgress",
            "/twirp/livekit.Egress/StopEgress",
            "/twirp/livekit.Egress/ListEgress",
        ]
        assert not errors


def _due():
    models.MastraoNativeCaptureStart.objects.update(next_check_at=timezone.now())


def _closed():
    models.MastraoRoomBinding.objects.update(closing_at=timezone.now())


def _stop_provider(provider, *, lost=False):
    params = connection.get_connection_params()

    async def stop(request):
        with psycopg.connect(**params) as independent:
            row = independent.execute(
                "SELECT stop_requested_at, drained_at, drain_claim FROM "
                "meet_mastrao_native_capture_start WHERE provider_job_ref=%s",
                [request.egress_id],
            ).fetchone()
        assert row and row[0] and row[1] is None and row[2]
        job = provider.jobs[0]
        assert request.egress_id == job.egress_id
        job.status = api.EgressStatus.EGRESS_ENDING
        if lost:
            raise TimeoutError("synthetic stop acknowledgement lost")
        return api.EgressInfo(
            egress_id="EG_wrongAck", status=api.EgressStatus.EGRESS_COMPLETE
        )

    provider.egress.stop_egress = AsyncMock(side_effect=stop)


@pytest.mark.parametrize("lost", [False, True])
def test_stop_ack_or_lost_reply_never_means_drained(
    client, signer, effect, provider, lost
):
    assert _post(client, signer, effect).status_code == 200
    _stop_provider(provider, lost=lost)
    _closed()
    assert reconcile_native_captures() == 0
    intent = models.MastraoNativeCaptureStart.objects.get()
    assert intent.stop_reason == "room_closed" and intent.drained_at is None
    original_receipt = intent.receipt_claims
    _due()
    assert reconcile_native_captures() == 0  # ENDING does not repeat Stop.
    assert provider.egress.stop_egress.await_count == 1
    provider.jobs[0].status = api.EgressStatus.EGRESS_COMPLETE
    _due()
    assert reconcile_native_captures() == 1
    intent.refresh_from_db()
    assert intent.drained_at is not None
    assert intent.observed_status == api.EgressStatus.EGRESS_COMPLETE
    assert intent.receipt_claims == original_receipt
    assert intent.receipt_claims["media_durability_proven"] is False
    assert reconcile_native_captures() == 0
    assert provider.egress.start_track_composite_egress.await_count == 1


@pytest.mark.parametrize(
    "reason",
    ["room", "epoch", "connection", "quarantine", "conflict", "expiry", "unknown"],
)
def test_local_stop_triggers_survive_admission_rollback(  # noqa: PLR0913,PLR0917
    client, signer, effect, provider, settings, reason
):
    assert _post(client, signer, effect).status_code == 200
    _stop_provider(provider)
    settings.MASTRAO_NATIVE_CAPTURE_START_ENABLED = False
    settings.MASTRAO_MEDIA_TOKEN_BINDING_ENABLED = False
    if reason == "room":
        _closed()
    elif reason == "epoch":
        models.MastraoRtcTrackEpoch.objects.update(ended=True)
    elif reason == "connection":
        models.MastraoRtcConnection.objects.update(ended=True)
    elif reason == "quarantine":
        models.MastraoRtcConnection.objects.update(correlation="token_reused")
    elif reason == "conflict":
        models.MastraoRtcTrackEpoch.objects.update(conflict=True)
    else:
        models.MastraoNativeCaptureStart.objects.update(
            retention_expires_at=None if reason == "unknown" else timezone.now(),
        )
    assert reconcile_native_captures() == 0
    provider.egress.stop_egress.assert_awaited_once()
    # Latch is permanent even if the local projection is subsequently changed.
    models.MastraoRoomBinding.objects.update(closing_at=None)
    models.MastraoRtcTrackEpoch.objects.update(ended=False, conflict=False)
    models.MastraoRtcConnection.objects.update(ended=False, correlation="correlated")
    provider.jobs[0].status = api.EgressStatus.EGRESS_ACTIVE
    _due()
    reconcile_native_captures()
    assert provider.egress.stop_egress.await_count == 2


@pytest.mark.parametrize("state", sorted(TERMINAL))
def test_natural_terminal_is_not_audio_success(client, signer, effect, provider, state):
    assert _post(client, signer, effect).status_code == 200
    _stop_provider(provider)
    provider.jobs[0].status = state
    assert reconcile_native_captures() == 1
    intent = models.MastraoNativeCaptureStart.objects.get()
    assert intent.drained_at and intent.observed_status == state
    assert not intent.stop_requested_at
    provider.egress.stop_egress.assert_not_called()


@pytest.mark.parametrize(
    "fault",
    [
        "absent",
        "multiple",
        "foreign_id",
        "foreign_room",
        "foreign_request",
        "unknown_status",
    ],
)
def test_ambiguous_observations_never_stop_or_drain(
    client, signer, effect, provider, fault
):
    assert _post(client, signer, effect).status_code == 200
    _stop_provider(provider)
    _closed()
    if fault == "absent":
        provider.jobs.clear()
    elif fault == "multiple":
        second = api.EgressInfo()
        second.CopyFrom(provider.jobs[0])
        second.egress_id = "EG_duplicated"
        provider.jobs.append(second)
    elif fault == "foreign_id":
        provider.jobs[0].egress_id = "EG_foreign"
    elif fault == "foreign_room":
        provider.jobs[0].room_id = "RM_foreign"
    elif fault == "foreign_request":
        provider.jobs[0].track_composite.audio_track_id = "TR_foreign"
    else:
        provider.jobs[0].status = 999
    assert reconcile_native_captures() == 0
    intent = models.MastraoNativeCaptureStart.objects.get()
    assert intent.stop_requested_at and intent.drained_at is None
    assert intent.drain_claim is None
    provider.egress.stop_egress.assert_not_called()
    assert provider.egress.start_track_composite_egress.await_count == 1


def test_inflight_start_is_drained_after_lost_response(
    client, signer, effect, provider
):
    entered, release = threading.Event(), threading.Event()
    start = provider.egress.start_track_composite_egress.side_effect

    async def held(request):
        entered.set()
        assert release.wait(8)
        await start(request)
        raise TimeoutError()

    provider.egress.start_track_composite_egress.side_effect = held

    def deliver():
        close_old_connections()
        try:
            return _post(Client(), signer, effect)
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(deliver)
        try:
            assert entered.wait(8)
            _closed()
            assert reconcile_native_captures() == 0  # No job YET, not drained.
        finally:
            release.set()
        assert pending.result(timeout=8).status_code == 503
    provider.egress.stop_egress = AsyncMock(return_value=provider.jobs[0])
    _due()
    assert reconcile_native_captures() == 0
    provider.egress.stop_egress.assert_awaited_once()
    provider.jobs[0].status = api.EgressStatus.EGRESS_COMPLETE
    _due()
    assert reconcile_native_captures() == 1
    assert provider.egress.start_track_composite_egress.await_count == 1


def test_start_latched_before_send_is_never_sent(
    client, signer, effect, provider, monkeypatch
):
    prepare = adapter._prepare

    def close_after_prepare(payload):
        result = prepare(payload)
        _closed()
        assert reconcile_native_captures() == 0
        return result

    monkeypatch.setattr(adapter, "_prepare", close_after_prepare)
    assert _post(client, signer, effect).status_code == 503
    assert models.MastraoNativeCaptureStart.objects.get().stop_requested_at
    provider.egress.start_track_composite_egress.assert_not_called()


def test_claim_fences_stale_results_and_recovers_after_expiry(
    client, signer, effect, provider
):
    assert _post(client, signer, effect).status_code == 200
    first = _claim_next()
    assert first is not None and _claim_next() is None
    models.MastraoNativeCaptureStart.objects.update(
        drain_claim_until=timezone.now() - timedelta(seconds=1)
    )
    second = _claim_next()
    assert second.drain_claim != first.drain_claim
    provider.jobs[0].status = api.EgressStatus.EGRESS_COMPLETE
    assert _finish(first, provider.jobs[0]) is False
    assert models.MastraoNativeCaptureStart.objects.get().drained_at is None
    assert _finish(second, provider.jobs[0]) is True


@pytest.mark.parametrize("native_only", [False, True])
def test_reconciler_consumes_native_without_legacy_video(
    client, signer, effect, provider, native_only
):
    assert _post(client, signer, effect).status_code == 200
    assert not models.MastraoRecordingBinding.objects.exists()
    _stop_provider(provider)
    _closed()
    output = StringIO()
    options = {"native_only": True} if native_only else {}
    with (
        patch(
            "core.mastrao_recording_reconciler.reconcile_mastrao_recording",
            side_effect=AssertionError("video must not run"),
        ),
        patch(
            "core.mastrao_recording_reconciler.reconcile_transcription_dispatches",
            return_value=0,
        ) as legacy,
    ):
        call_command("reconcile_mastrao_recordings", limit=1, stdout=output, **options)
        if native_only:
            legacy.assert_not_called()
    assert "Reconciled 0 Mastrao recording(s)." in output.getvalue()
    provider.egress.stop_egress.assert_awaited_once()
    provider.jobs[0].status = api.EgressStatus.EGRESS_COMPLETE
    _due()
    call_command("reconcile_mastrao_recordings", limit=1, stdout=output, **options)
    assert "Reconciled 1 Mastrao recording(s)." in output.getvalue()


def test_concurrent_workers_only_one_live_stop_claim(client, signer, effect, provider):
    assert _post(client, signer, effect).status_code == 200
    _closed()
    entered, release = threading.Event(), threading.Event()

    async def stop(request):
        assert request.egress_id == provider.jobs[0].egress_id
        entered.set()
        assert release.wait(8)
        return provider.jobs[0]

    provider.egress.stop_egress = AsyncMock(side_effect=stop)

    def reconcile():
        close_old_connections()
        try:
            return reconcile_native_captures()
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(reconcile)
        try:
            assert entered.wait(8)
            assert reconcile_native_captures() == 0
        finally:
            release.set()
        assert first.result(timeout=8) == 0
    assert provider.egress.stop_egress.await_count == 1


def test_failed_terminal_commit_recovers_without_start(
    client, signer, effect, provider, monkeypatch
):
    assert _post(client, signer, effect).status_code == 200
    provider.jobs[0].status = api.EgressStatus.EGRESS_COMPLETE
    original = models.MastraoNativeCaptureStart.save

    def failing_save(self, *args, **kwargs):
        original(self, *args, **kwargs)
        if self.drained_at:
            raise DatabaseError("synthetic transaction failure after terminal write")

    monkeypatch.setattr(models.MastraoNativeCaptureStart, "save", failing_save)
    with pytest.raises(DatabaseError):
        reconcile_native_captures()
    intent = models.MastraoNativeCaptureStart.objects.get()
    assert intent.drained_at is None and intent.drain_claim is not None
    monkeypatch.setattr(models.MastraoNativeCaptureStart, "save", original)
    models.MastraoNativeCaptureStart.objects.update(
        drain_claim_until=timezone.now() - timedelta(seconds=1)
    )
    assert reconcile_native_captures() == 1
    assert provider.egress.start_track_composite_egress.await_count == 1
