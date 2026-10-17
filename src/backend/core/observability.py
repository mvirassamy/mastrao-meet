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
HISTORY_WINDOW = timedelta(days=7)


@dataclass(frozen=True)
class PipelineMetrics:
    """Aggregated pipeline state, with no meeting or participant identifiers."""

    asr_queues: dict[str, tuple[int, int, int]]
    transcription_effects: dict[tuple[str, str], int]
    transcription_attempts: dict[str, int]
    closure_to_transcription_seconds: dict[str, float]


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
    )


def _rows(cursor, query: str, params: tuple = ()) -> Iterable[tuple]:
    cursor.execute(query, params)
    return cursor.fetchall()


def pipeline_metrics(now=None) -> PipelineMetrics:
    """Read bounded operational aggregates from the durable pipeline state."""

    now = now or timezone.now()
    history_start = now - HISTORY_WINDOW
    metrics = _empty_pipeline_metrics()
    with connection.cursor() as cursor:
        asr_queues = dict(metrics.asr_queues)
        for lane, pending, oldest_age, active in _rows(
            cursor,
            """
            SELECT lane, pending_count, oldest_due_age_seconds, active_count
            FROM meet_mastrao_native_asr_queue_metrics
            """,
        ):
            if lane in asr_queues:
                asr_queues[lane] = (int(pending), int(oldest_age), int(active))

        effects = dict(metrics.transcription_effects)
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

        attempts = dict(metrics.transcription_attempts)
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

        quantiles = dict(metrics.closure_to_transcription_seconds)
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
        if latency:
            p50, p95 = latency[0]
            if p50 is not None:
                quantiles["0.5"] = float(p50)
            if p95 is not None:
                quantiles["0.95"] = float(p95)

    return PipelineMetrics(asr_queues, effects, attempts, quantiles)


def metrics_response(_request) -> HttpResponse:
    """Render current operational aggregates in Prometheus' text format."""

    metrics = pipeline_metrics()
    registry = CollectorRegistry()

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

    return HttpResponse(generate_latest(registry), content_type=CONTENT_TYPE_LATEST)
