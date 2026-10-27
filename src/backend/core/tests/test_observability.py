"""Prometheus metrics stay aggregate and free of customer identifiers."""

from contextlib import contextmanager
from datetime import timedelta
from types import SimpleNamespace

from django.test import RequestFactory
from django.urls import resolve
from django.utils import timezone

import pytest

from core import models, observability
from core.factories import RoomFactory, UserFactory


class MetricsCursor:
    """Small cursor fake returning the operational aggregate queries."""

    def __init__(self, rows):
        """Store successive rows returned by the aggregate queries."""

        self._rows = iter(rows)
        self.queries = []
        self.parameters = []

    def execute(self, query, params):
        """Record one aggregate query and its bound parameters."""

        self.queries.append(query)
        self.parameters.append(params)

    def fetchall(self):
        """Return rows for the next aggregate query."""

        return next(self._rows)


def test_pipeline_metrics_only_accept_bounded_labels(monkeypatch):
    """Keep query labels finite and historical reads bounded."""

    now = timezone.now()
    cursor = MetricsCursor(
        [
            [("ready", 2, 15, 1), ("meeting_123", 99, 99, 99)],
            [("pending", "queued", 3), ("customer_state", "queued", 99)],
            [("rate_limited", 4), ("tenant_123", 99)],
            [(12.5, 42.5)],
            [(10, 30, 3.0, 3.0, 3, 4)],
        ]
    )

    @contextmanager
    def cursor_context():
        yield cursor

    monkeypatch.setattr(
        observability,
        "connection",
        SimpleNamespace(cursor=cursor_context),
    )

    metrics = observability.pipeline_metrics(now=now)

    assert metrics.asr_queues["ready"] == (2, 15, 1)
    assert metrics.asr_queues["retry"] == (0, 0, 0)
    assert metrics.transcription_effects[("pending", "queued")] == 3
    assert metrics.transcription_attempts["rate_limited"] == 4
    assert metrics.closure_to_transcription_seconds == {"0.5": 12.5, "0.95": 42.5}
    assert metrics.native_capture_topology == observability.NativeCaptureTopology(
        room_count=10,
        track_job_count=30,
        tracks_per_room={"0.5": 3.0, "0.95": 3.0, "max": 3.0},
        active_track_job_count=4,
    )
    assert "meeting_ref" not in "".join(cursor.queries)
    assert "participant" not in "".join(cursor.queries)
    history_start = now - timedelta(days=7)
    assert cursor.parameters == [
        (),
        (history_start,),
        (history_start,),
        (history_start, history_start),
        (history_start,),
    ]
    assert "WHERE created_at >= %s" in cursor.queries[1]
    assert "WHERE created_at >= %s" in cursor.queries[2]
    assert "state = 'available'" in cursor.queries[3]
    assert "updated_at >= %s" in cursor.queries[3]
    assert "closure.applied_at >= %s" in cursor.queries[3]
    assert "GROUP BY connection.room_binding_id" in cursor.queries[4]
    assert "capture.created_at >= %s" in cursor.queries[4]


def test_metrics_endpoint_exposes_only_aggregate_labels(monkeypatch):
    """Expose only finite operational labels on the public endpoint."""

    metrics = observability.PipelineMetrics(
        asr_queues={"ready": (0, 0, 0), "retry": (0, 0, 0)},
        transcription_effects={
            (state, dispatch_state): 0
            for state in observability.EFFECT_STATES
            for dispatch_state in observability.DISPATCH_STATES
        },
        transcription_attempts=dict.fromkeys(observability.ATTEMPT_STATES, 0),
        closure_to_transcription_seconds=dict.fromkeys(observability.QUANTILES, 0.0),
        native_capture_topology=observability.NativeCaptureTopology.empty(),
    )
    monkeypatch.setattr(observability, "pipeline_metrics", lambda: metrics)

    response = observability.metrics_response(RequestFactory().get("/metrics"))
    body = response.content.decode()

    assert response.status_code == 200
    assert 'lane="ready"' in body
    assert 'state="rate_limited"' in body
    assert 'quantile="0.95"' in body
    assert "mastrao_native_capture_rooms_observed 0.0" in body
    assert "mastrao_native_capture_track_jobs_observed 0.0" in body
    assert 'mastrao_native_capture_tracks_per_room{quantile="max"} 0.0' in body
    assert "mastrao_native_capture_active_track_jobs 0.0" in body
    assert "meeting_ref" not in body
    assert "room_ref" not in body
    assert "participant_ref" not in body
    assert 'transcript="' not in body


def test_metrics_url_uses_the_aggregate_metrics_response():
    """Route Prometheus scrapes to the aggregate metrics response."""

    assert resolve("/metrics").func is observability.metrics_response


def _create_latency_sample(suffix, *, closure_at, available_at, state):
    """Persist one closure and transcription binding for a latency query."""

    room_binding = models.MastraoRoomBinding.objects.create(
        room=RoomFactory(),
        owner=UserFactory(),
        effect_key=f"effect_room_{suffix}",
        arguments_digest="a" * 64,
        meeting_ref=f"meeting_{suffix}",
        room_ref=f"room_{suffix}",
        owner_ref=f"owner_{suffix}",
        provider_binding_digest="b" * 64,
    )
    recording_binding = models.MastraoRecordingBinding.objects.create(
        room_binding=room_binding,
        organization_external_id="organization_test",
        meeting_ref=room_binding.meeting_ref,
        room_ref=room_binding.room_ref,
        recording_ref=f"recording_{suffix}",
        provider_binding_digest=room_binding.provider_binding_digest,
        policy_ref=f"policy_{suffix}",
        notice_version=f"notice_{suffix}",
        notice_digest="c" * 64,
        retention_expires_at=closure_at + timedelta(days=30),
    )
    models.MastraoRoomClosure.objects.create(
        room_binding=room_binding,
        organization_external_id=recording_binding.organization_external_id,
        meeting_ref=room_binding.meeting_ref,
        room_ref=room_binding.room_ref,
        provider_binding_digest=room_binding.provider_binding_digest,
        close_ref=f"close_{suffix}",
        effect_key=f"effect_close_{suffix}",
        arguments_digest="d" * 64,
        state=models.MastraoRoomClosure.State.APPLIED,
        requested_at=closure_at,
        applied_at=closure_at,
        provider_observation=models.MastraoRoomClosure.ProviderObservation.DELETED,
        receipt_claims={"state": "ended"},
        receipt_digest="e" * 64,
    )
    transcription = models.MastraoTranscriptionBinding.objects.create(
        recording_binding=recording_binding,
        organization_external_id=recording_binding.organization_external_id,
        meeting_ref=room_binding.meeting_ref,
        room_ref=room_binding.room_ref,
        recording_ref=recording_binding.recording_ref,
        transcription_ref=f"transcription_{suffix}",
        artifact_ref=f"artifact_{suffix}",
        provider_binding_digest=room_binding.provider_binding_digest,
        artifact_checksum_digest="f" * 64,
        artifact_byte_size=2_048,
        state=state,
    )
    models.MastraoTranscriptionBinding.objects.filter(pk=transcription.pk).update(
        updated_at=available_at
    )


@pytest.mark.django_db(transaction=True)
def test_pipeline_metrics_uses_available_transcriptions_within_history_window():
    """Measure only available bindings that belong to the seven-day window."""

    now = timezone.now()
    _create_latency_sample(
        "available",
        closure_at=now - timedelta(minutes=10),
        available_at=now - timedelta(minutes=5),
        state=models.MastraoTranscriptionBinding.State.AVAILABLE,
    )
    _create_latency_sample(
        "failed",
        closure_at=now - timedelta(minutes=10),
        available_at=now - timedelta(minutes=5),
        state=models.MastraoTranscriptionBinding.State.FAILED,
    )
    _create_latency_sample(
        "expired",
        closure_at=now - timedelta(days=8),
        available_at=now - timedelta(days=8) + timedelta(minutes=5),
        state=models.MastraoTranscriptionBinding.State.AVAILABLE,
    )

    metrics = observability.pipeline_metrics(now=now)

    assert metrics.closure_to_transcription_seconds == {"0.5": 300.0, "0.95": 300.0}
