"""Content-free RTC evidence for one connected room-audio session."""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass

from livekit import rtc


@dataclass(frozen=True)
class RoomAudioRtcSnapshot:
    """Aggregated transport evidence without participant or track labels."""

    sample_count: int
    collection_error_count: int
    packets_received: int
    packets_lost: int
    concealed_samples: int
    maximum_jitter_ms: int
    maximum_round_trip_time_ms: int


@dataclass(frozen=True)
class _InboundCounters:
    packets_received: int
    packets_lost: int
    concealed_samples: int


class RoomAudioRtcObserver:
    """Sample public LiveKit RTC statistics for one room-owned run."""

    def __init__(self, room: rtc.Room, *, interval_seconds: float = 1.0):
        """Bind one observer to a connected room and a positive interval."""
        if interval_seconds <= 0:
            raise ValueError("RTC sample interval must be positive")
        self._room = room
        self._interval_seconds = interval_seconds
        self._task: asyncio.Task | None = None
        self._previous_inbound: dict[str, _InboundCounters] = {}
        self._sample_count = 0
        self._collection_error_count = 0
        self._packets_received = 0
        self._packets_lost = 0
        self._concealed_samples = 0
        self._maximum_jitter_ms = 0
        self._maximum_round_trip_time_ms = 0
        self._started = False
        self._finished = False

    async def start(self) -> None:
        """Require one initial public RTC snapshot, then poll periodically."""
        if self._finished:
            raise RuntimeError("RTC observer is finished")
        if self._started:
            return
        await self._collect_once()
        self._started = True
        self._task = asyncio.create_task(self._collect_periodically())

    async def finish(self) -> RoomAudioRtcSnapshot:
        """Take a final snapshot and return the exact aggregated evidence."""
        if self._finished:
            return self.snapshot()
        self._finished = True
        await self._cancel_task()
        if self._started:
            try:
                await self._collect_once()
            except Exception:
                self._collection_error_count += 1
        if self._sample_count == 0:
            raise RuntimeError("RTC evidence contains no successful snapshot")
        return self.snapshot()

    def snapshot(self) -> RoomAudioRtcSnapshot:
        """Return current content-free evidence without provider identifiers."""
        return RoomAudioRtcSnapshot(
            sample_count=self._sample_count,
            collection_error_count=self._collection_error_count,
            packets_received=self._packets_received,
            packets_lost=self._packets_lost,
            concealed_samples=self._concealed_samples,
            maximum_jitter_ms=self._maximum_jitter_ms,
            maximum_round_trip_time_ms=self._maximum_round_trip_time_ms,
        )

    async def _collect_periodically(self) -> None:
        while True:
            await asyncio.sleep(self._interval_seconds)
            try:
                await self._collect_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                self._collection_error_count += 1

    async def _collect_once(self) -> None:
        stats = await self._room.get_rtc_stats()
        current_inbound: dict[str, _InboundCounters] = {}
        for report in stats.subscriber_stats:
            report_type = report.WhichOneof("stats")
            if report_type == "inbound_rtp":
                self._read_inbound(report.inbound_rtp, current_inbound)
            elif report_type == "candidate_pair":
                self._read_candidate_pair(report.candidate_pair)
        self._advance_inbound_counters(current_inbound)
        self._sample_count += 1

    def _read_inbound(self, inbound, current: dict[str, _InboundCounters]) -> None:
        report_id = inbound.rtc.id
        if not report_id:
            raise RuntimeError("RTC inbound report is missing its public id")
        current[report_id] = _InboundCounters(
            packets_received=max(0, int(inbound.received.packets_received)),
            packets_lost=max(0, int(inbound.received.packets_lost)),
            concealed_samples=max(0, int(inbound.inbound.concealed_samples)),
        )
        jitter_ms = round(max(0.0, float(inbound.received.jitter)) * 1_000)
        self._maximum_jitter_ms = max(self._maximum_jitter_ms, jitter_ms)

    def _read_candidate_pair(self, wrapper) -> None:
        pair = wrapper.candidate_pair
        if not pair.nominated:
            return
        round_trip_time_ms = round(
            max(0.0, float(pair.current_round_trip_time)) * 1_000
        )
        self._maximum_round_trip_time_ms = max(
            self._maximum_round_trip_time_ms,
            round_trip_time_ms,
        )

    def _advance_inbound_counters(self, current: dict[str, _InboundCounters]) -> None:
        if self._sample_count == 0:
            self._previous_inbound = current
            return
        for report_id, counters in current.items():
            previous = self._previous_inbound.get(report_id, _InboundCounters(0, 0, 0))
            self._packets_received += max(
                0, counters.packets_received - previous.packets_received
            )
            self._packets_lost += max(0, counters.packets_lost - previous.packets_lost)
            self._concealed_samples += max(
                0, counters.concealed_samples - previous.concealed_samples
            )
        self._previous_inbound.update(current)

    async def _cancel_task(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None
