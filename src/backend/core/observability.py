"""Privacy-safe Prometheus metrics for the Mastrao post-meeting pipeline."""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import timedelta

from django.db import connection
from django.http import HttpResponse
from django.utils import timezone

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Gauge,
    generate_latest,
)

ASR_LANES = ("ready", "retry")
EFFECT_STATES = ("applying", "pending", "applied", "failed")
DISPATCH_STATES = (
    "dispatch_pending",
    "queued",
    "running",
    "artifact_notification_pending",
    "failure_notification_pending",
    "cleanup_pending",
    "completed",
)
ATTEMPT_STATES = (
    "prepared",
    "sending",
    "result_received",
    "artifact_write_pending",
    "succeeded",
    "failed_pre_egress",
    "rate_limited",
    "unknown",
    "cancelled",
)
QUANTILES = ("0.5", "0.95")
TOPOLOGY_QUANTILES = (*QUANTILES, "max")
HISTORY_WINDOW = timedelta(days=7)


@dataclass(frozen=True)
class NativeCaptureTopology:
    """Content-free shape of the participant-track capture pipeline."""

    room_count: int
    track_job_count: int
    tracks_per_room: dict[str, float]
    active_track_job_count: int

    @classmethod
    def empty(cls):
        """Return a stable zero sample when no recent room was captured."""

        return cls(
            room_count=0,
            track_job_count=0,
            tracks_per_room=dict.fromkeys(TOPOLOGY_QUANTILES, 0.0),
            active_track_job_count=0,
        )


@dataclass(frozen=True)
class PipelineMetrics:
    """Aggregated pipeline state, with no meeting or participant identifiers."""

    asr_queues: dict[str, tuple[int, int, int]]
    transcription_effects: dict[tuple[str, str], int]
    transcription_attempts: dict[str, int]
    closure_to_transcription_seconds: dict[str, float]
    native_capture_topology: NativeCaptureTopology


def _empty_pipeline_metrics() -> PipelineMetrics:
    return PipelineMetrics(
        asr_queues=dict.fromkeys(ASR_LANES, (0, 0, 0)),
        transcription_effects={
            (state, dispatch_state): 0
            for state in EFFECT_STATES
            for dispatch_state in DISPATCH_STATES
        },
        transcription_attempts=dict.fromkeys(ATTEMPT_STATES, 0),
        closure_to_transcription_seconds=dict.fromkeys(QUANTILES, 0),
        native_capture_topology=NativeCaptureTopology.empty(),
    )


def _rows(cursor, query: str, params: tuple = ()) -> Iterable[tuple]:
    cursor.execute(query, params)
    return cursor.fetchall()


def _read_asr_queues(cursor, defaults):
    queues = dict(defaults)
    for lane, pending, oldest_age, active in _rows(
        cursor,
        """
        SELECT lane, pending_count, oldest_due_age_seconds, active_count
        FROM meet_mastrao_native_asr_queue_metrics
        """,
    ):
        if lane in queues:
            queues[lane] = (int(pending), int(oldest_age), int(active))
    return queues


def _read_transcription_effects(cursor, history_start, defaults):
    effects = dict(defaults)
    for state, dispatch_state, count in _rows(
        cursor,
        """
        SELECT state, dispatch_state, COUNT(*)
        FROM meet_mastrao_transcription_effect
        WHERE created_at >= %s
        GROUP BY state, dispatch_state
        """,
        (history_start,),
    ):
        key = (state, dispatch_state)
        if key in effects:
            effects[key] = int(count)
    return effects


def _read_transcription_attempts(cursor, history_start, defaults):
    attempts = dict(defaults)
    for state, count in _rows(
        cursor,
        """
        SELECT state, COUNT(*)
        FROM meet_mastrao_transcription_provider_attempt
        WHERE created_at >= %s
        GROUP BY state
        """,
        (history_start,),
    ):
        if state in attempts:
            attempts[state] = int(count)
    return attempts


def _read_latency_quantiles(cursor, history_start, defaults):
    quantiles = dict(defaults)
    latency = _rows(
        cursor,
        """
        WITH first_available_transcription AS (
            SELECT recording_binding_id, MIN(updated_at) AS available_at
            FROM meet_mastrao_transcription_binding
            WHERE state = 'available'
              AND updated_at >= %s
            GROUP BY recording_binding_id
        )
        SELECT
            percentile_cont(0.5) WITHIN GROUP (
                ORDER BY EXTRACT(EPOCH FROM available_at - closure.applied_at)
            ),
            percentile_cont(0.95) WITHIN GROUP (
                ORDER BY EXTRACT(EPOCH FROM available_at - closure.applied_at)
            )
        FROM meet_mastrao_room_closure AS closure
        JOIN meet_mastrao_recording_binding AS recording
            ON recording.room_binding_id = closure.room_binding_id
        JOIN first_available_transcription
            ON first_available_transcription.recording_binding_id = recording.id
        WHERE closure.applied_at IS NOT NULL
          AND closure.applied_at >= %s
          AND first_available_transcription.available_at >= closure.applied_at
        """,
        (history_start, history_start),
    )
    if not latency:
        return quantiles

    p50, p95 = latency[0]
    if p50 is not None:
        quantiles["0.5"] = float(p50)
    if p95 is not None:
        quantiles["0.95"] = float(p95)
    return quantiles


def _read_native_capture_topology(cursor, history_start):
    rows = _rows(
        cursor,
        """
        WITH captures_per_room AS (
            SELECT
                connection.room_binding_id,
                COUNT(*) AS track_job_count,
                COUNT(*) FILTER (WHERE capture.drained_at IS NULL) AS active_count
            FROM meet_mastrao_native_capture_start AS capture
            JOIN meet_mastrao_rtc_track_epoch AS epoch
                ON epoch.id = capture.epoch_id
            JOIN meet_mastrao_rtc_connection AS connection
                ON connection.id = epoch.connection_id
            WHERE capture.created_at >= %s
            GROUP BY connection.room_binding_id
        )
        SELECT
            COUNT(*),
            COALESCE(SUM(track_job_count), 0),
            COALESCE(percentile_cont(0.5) WITHIN GROUP (
                ORDER BY track_job_count
            ), 0),
            COALESCE(percentile_cont(0.95) WITHIN GROUP (
                ORDER BY track_job_count
            ), 0),
            COALESCE(MAX(track_job_count), 0),
            COALESCE(SUM(active_count), 0)
        FROM captures_per_room
        """,
        (history_start,),
    )
    if not rows:
        return NativeCaptureTopology.empty()

    room_count, track_jobs, p50, p95, maximum, active = rows[0]
    return NativeCaptureTopology(
        room_count=int(room_count),
        track_job_count=int(track_jobs),
        tracks_per_room={
            "0.5": float(p50),
            "0.95": float(p95),
            "max": float(maximum),
        },
        active_track_job_count=int(active),
    )


def pipeline_metrics(now=None) -> PipelineMetrics:
    """Read bounded operational aggregates from the durable pipeline state."""

    now = now or timezone.now()
    history_start = now - HISTORY_WINDOW
    metrics = _empty_pipeline_metrics()
    with connection.cursor() as cursor:
        asr_queues = _read_asr_queues(cursor, metrics.asr_queues)
        effects = _read_transcription_effects(
            cursor, history_start, metrics.transcription_effects
        )
        attempts = _read_transcription_attempts(
            cursor, history_start, metrics.transcription_attempts
        )
        quantiles = _read_latency_quantiles(
            cursor, history_start, metrics.closure_to_transcription_seconds
        )
        topology = _read_native_capture_topology(cursor, history_start)

    return PipelineMetrics(asr_queues, effects, attempts, quantiles, topology)


def _render_queue_metrics(registry, metrics):
    queue_pending = Gauge(
        "mastrao_native_asr_queue_pending",
        "Eligible native ASR sources awaiting processing.",
        labelnames=("lane",),
        registry=registry,
    )
    queue_oldest_age = Gauge(
        "mastrao_native_asr_queue_oldest_due_age_seconds",
        "Age of the oldest eligible native ASR source.",
        labelnames=("lane",),
        registry=registry,
    )
    queue_active = Gauge(
        "mastrao_native_asr_queue_active",
        "Eligible native ASR sources with a live reservation or claim.",
        labelnames=("lane",),
        registry=registry,
    )
    for lane, (pending, oldest_age, active) in metrics.asr_queues.items():
        queue_pending.labels(lane=lane).set(pending)
        queue_oldest_age.labels(lane=lane).set(oldest_age)
        queue_active.labels(lane=lane).set(active)


def _render_pipeline_metrics(registry, metrics):
    effects = Gauge(
        "mastrao_transcription_effects",
        "Durable transcription effects by bounded lifecycle state.",
        labelnames=("state", "dispatch_state"),
        registry=registry,
    )
    for (state, dispatch_state), count in metrics.transcription_effects.items():
        effects.labels(state=state, dispatch_state=dispatch_state).set(count)

    attempts = Gauge(
        "mastrao_transcription_provider_attempts",
        "Durable provider attempts by bounded state.",
        labelnames=("state",),
        registry=registry,
    )
    for state, count in metrics.transcription_attempts.items():
        attempts.labels(state=state).set(count)

    closure_to_transcription = Gauge(
        "mastrao_closure_to_transcription_seconds",
        "Completed closure-to-transcription latency quantiles.",
        labelnames=("quantile",),
        registry=registry,
    )
    for quantile, seconds in metrics.closure_to_transcription_seconds.items():
        closure_to_transcription.labels(quantile=quantile).set(seconds)


def _render_topology_metrics(registry, topology):
    rooms_observed = Gauge(
        "mastrao_native_capture_rooms_observed",
        "Rooms using participant-track capture during the bounded history window.",
        registry=registry,
    )
    rooms_observed.set(topology.room_count)
    track_jobs_observed = Gauge(
        "mastrao_native_capture_track_jobs_observed",
        "TrackEgress jobs observed during the bounded history window.",
        registry=registry,
    )
    track_jobs_observed.set(topology.track_job_count)
    tracks_per_room = Gauge(
        "mastrao_native_capture_tracks_per_room",
        "Participant-track capture jobs per room over the bounded history window.",
        labelnames=("quantile",),
        registry=registry,
    )
    for quantile, value in topology.tracks_per_room.items():
        tracks_per_room.labels(quantile=quantile).set(value)
    active_track_jobs = Gauge(
        "mastrao_native_capture_active_track_jobs",
        "Participant-track captures not yet drained.",
        registry=registry,
    )
    active_track_jobs.set(topology.active_track_job_count)


def metrics_response(_request) -> HttpResponse:
    """Render current operational aggregates in Prometheus' text format."""

    metrics = pipeline_metrics()
    registry = CollectorRegistry()
    _render_queue_metrics(registry, metrics)
    _render_pipeline_metrics(registry, metrics)
    _render_topology_metrics(registry, metrics.native_capture_topology)

    return HttpResponse(generate_latest(registry), content_type=CONTENT_TYPE_LATEST)
