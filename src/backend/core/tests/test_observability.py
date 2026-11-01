"""Operational metric proofs for the canonical Room Audio pipeline."""

from django.test import RequestFactory
from django.urls import resolve

from core import observability


class _Cursor:
    def __init__(self, results):
        self._results = iter(results)
        self.executed = []

    def execute(self, query, params):
        """Record one SQL call for later assertions."""
        self.executed.append((query, params))

    def fetchall(self):
        """Return the next deterministic result set."""
        return next(self._results)


class _CursorContext:
    def __init__(self, cursor):
        self.cursor = cursor

    def __enter__(self):
        return self.cursor

    def __exit__(self, *_args):
        return False


def test_pipeline_metrics_reads_only_room_audio_tables(monkeypatch):
    """Read canonical Room Audio queues without querying removed tables."""

    cursor = _Cursor(
        [
            [("pending", 2), ("delivered", 8)],
            [("pending", 1), ("retrying", 2), ("acknowledged", 5)],
            [(12.5, 42.5)],
        ]
    )
    monkeypatch.setattr(
        observability.connection,
        "cursor",
        lambda: _CursorContext(cursor),
    )

    metrics = observability.pipeline_metrics()

    assert metrics.room_audio_sources == {
        "pending": 2,
        "delivered": 8,
        "exhausted": 0,
    }
    assert metrics.room_audio_asr == {
        "pending": 1,
        "retrying": 2,
        "acknowledged": 5,
    }
    assert metrics.closure_to_transcription_seconds == {"0.5": 12.5, "0.95": 42.5}
    queries = "\n".join(query for query, _params in cursor.executed)
    assert "meet_mastrao_room_audio_source_outbox" in queries
    assert "meet_mastrao_transcription_" not in queries


def test_metrics_response_exposes_room_audio_series(monkeypatch):
    """Expose canonical Room Audio source and ASR metrics."""

    metrics = observability.PipelineMetrics(
        room_audio_sources={"pending": 1, "delivered": 2, "exhausted": 0},
        room_audio_asr={"pending": 0, "retrying": 1, "acknowledged": 1},
        closure_to_transcription_seconds={"0.5": 8.0, "0.95": 16.0},
    )
    monkeypatch.setattr(observability, "pipeline_metrics", lambda: metrics)

    response = observability.metrics_response(RequestFactory().get("/metrics"))
    body = response.content.decode()

    assert 'mastrao_room_audio_sources{state="delivered"} 2.0' in body
    assert 'mastrao_room_audio_asr_sources{state="retrying"} 1.0' in body
    assert 'mastrao_closure_to_transcription_seconds{quantile="0.95"} 16.0' in body


def test_metrics_route_uses_room_audio_observability():
    """Route the metrics endpoint to the canonical collector."""

    assert resolve("/metrics").func is observability.metrics_response
