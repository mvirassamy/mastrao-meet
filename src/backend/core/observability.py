"""Privacy-safe Prometheus metrics for the canonical Room Audio pipeline."""

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

SOURCE_STATES = ("pending", "delivered", "exhausted")
ASR_STATES = ("pending", "retrying", "acknowledged")
QUANTILES = ("0.5", "0.95")
HISTORY_WINDOW = timedelta(days=7)


@dataclass(frozen=True)
class PipelineMetrics:
    """Aggregated Room Audio state without meeting or participant identifiers."""

    room_audio_sources: dict[str, int]
    room_audio_asr: dict[str, int]
    closure_to_transcription_seconds: dict[str, float]


def _empty_pipeline_metrics() -> PipelineMetrics:
    return PipelineMetrics(
        room_audio_sources=dict.fromkeys(SOURCE_STATES, 0),
        room_audio_asr=dict.fromkeys(ASR_STATES, 0),
        closure_to_transcription_seconds=dict.fromkeys(QUANTILES, 0),
    )


def _rows(cursor, query: str, params: tuple = ()) -> Iterable[tuple]:
    cursor.execute(query, params)
    return cursor.fetchall()


def _read_source_states(cursor, history_start, defaults):
    sources = dict(defaults)
    for state, count in _rows(
        cursor,
        """
        SELECT state, COUNT(*)
        FROM meet_mastrao_room_audio_source_outbox
        WHERE created_at >= %s
        GROUP BY state
        """,
        (history_start,),
    ):
        if state in sources:
            sources[state] = int(count)
    return sources


def _read_asr_states(cursor, history_start, defaults):
    asr = dict(defaults)
    for state, count in _rows(
        cursor,
        """
        SELECT
            CASE
                WHEN asr_acknowledged THEN 'acknowledged'
                WHEN asr_attempts > 0 THEN 'retrying'
                ELSE 'pending'
            END AS asr_state,
            COUNT(*)
        FROM meet_mastrao_room_audio_source_outbox
        WHERE state = 'delivered'
          AND created_at >= %s
        GROUP BY asr_state
        """,
        (history_start,),
    ):
        if state in asr:
            asr[state] = int(count)
    return asr


def _read_latency_quantiles(cursor, history_start, defaults):
    quantiles = dict(defaults)
    latency = _rows(
        cursor,
        """
        SELECT
            percentile_cont(0.5) WITHIN GROUP (
                ORDER BY EXTRACT(EPOCH FROM source.updated_at - closure.applied_at)
            ),
            percentile_cont(0.95) WITHIN GROUP (
                ORDER BY EXTRACT(EPOCH FROM source.updated_at - closure.applied_at)
            )
        FROM meet_mastrao_room_audio_source_outbox AS source
        JOIN meet_mastrao_room_audio_run_effect AS run
            ON run.id = source.run_effect_id
        JOIN meet_mastrao_room_closure AS closure
            ON closure.room_binding_id = run.room_binding_id
        WHERE source.asr_acknowledged
          AND source.updated_at >= %s
          AND closure.applied_at IS NOT NULL
          AND closure.applied_at >= %s
          AND source.updated_at >= closure.applied_at
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


def pipeline_metrics(now=None) -> PipelineMetrics:
    """Read bounded operational aggregates from durable Room Audio state."""

    now = now or timezone.now()
    history_start = now - HISTORY_WINDOW
    metrics = _empty_pipeline_metrics()
    with connection.cursor() as cursor:
        sources = _read_source_states(cursor, history_start, metrics.room_audio_sources)
        asr = _read_asr_states(cursor, history_start, metrics.room_audio_asr)
        quantiles = _read_latency_quantiles(
            cursor, history_start, metrics.closure_to_transcription_seconds
        )
    return PipelineMetrics(sources, asr, quantiles)


def _render_pipeline_metrics(registry, metrics):
    sources = Gauge(
        "mastrao_room_audio_sources",
        "Durable room-audio sources by delivery state.",
        labelnames=("state",),
        registry=registry,
    )
    for state, count in metrics.room_audio_sources.items():
        sources.labels(state=state).set(count)

    asr = Gauge(
        "mastrao_room_audio_asr_sources",
        "Delivered room-audio sources by ASR state.",
        labelnames=("state",),
        registry=registry,
    )
    for state, count in metrics.room_audio_asr.items():
        asr.labels(state=state).set(count)

    latency = Gauge(
        "mastrao_closure_to_transcription_seconds",
        "Completed room-closure to canonical-transcription latency quantiles.",
        labelnames=("quantile",),
        registry=registry,
    )
    for quantile, seconds in metrics.closure_to_transcription_seconds.items():
        latency.labels(quantile=quantile).set(seconds)


def metrics_response(_request) -> HttpResponse:
    """Render current operational aggregates in Prometheus' text format."""

    registry = CollectorRegistry()
    _render_pipeline_metrics(registry, pipeline_metrics())
    return HttpResponse(generate_latest(registry), content_type=CONTENT_TYPE_LATEST)
