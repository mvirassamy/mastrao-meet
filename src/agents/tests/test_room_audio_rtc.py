"""Tests for content-free room RTC evidence."""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from room_audio_rtc import RoomAudioRtcObserver


class _Report:
    def __init__(self, report_type, value):
        self._report_type = report_type
        setattr(self, report_type, value)

    def WhichOneof(self, name):
        if name != "stats":
            raise ValueError("unexpected oneof")
        return self._report_type


def _inbound(report_id, *, received, lost, concealed, jitter):
    return _Report(
        "inbound_rtp",
        SimpleNamespace(
            rtc=SimpleNamespace(id=report_id),
            received=SimpleNamespace(
                packets_received=received,
                packets_lost=lost,
                jitter=jitter,
            ),
            inbound=SimpleNamespace(concealed_samples=concealed),
        ),
    )


def _candidate_pair(*, nominated, round_trip_time):
    return _Report(
        "candidate_pair",
        SimpleNamespace(
            candidate_pair=SimpleNamespace(
                nominated=nominated,
                current_round_trip_time=round_trip_time,
            )
        ),
    )


def _stats(*reports):
    return SimpleNamespace(subscriber_stats=list(reports))


class RoomAudioRtcObserverTests(unittest.IsolatedAsyncioTestCase):
    """Exercise public RTC aggregation and fail-closed evidence rules."""

    async def test_aggregates_deltas_without_exposing_report_ids(self):
        """Only counter deltas and extrema leave the observer."""
        room = SimpleNamespace(
            get_rtc_stats=AsyncMock(
                side_effect=[
                    _stats(
                        _inbound(
                            "private-track-a",
                            received=100,
                            lost=2,
                            concealed=5,
                            jitter=0.003,
                        ),
                        _candidate_pair(nominated=True, round_trip_time=0.020),
                    ),
                    _stats(
                        _inbound(
                            "private-track-a",
                            received=130,
                            lost=3,
                            concealed=9,
                            jitter=0.007,
                        ),
                        _inbound(
                            "private-track-b",
                            received=10,
                            lost=1,
                            concealed=2,
                            jitter=0.004,
                        ),
                        _candidate_pair(nominated=True, round_trip_time=0.030),
                    ),
                ]
            )
        )
        observer = RoomAudioRtcObserver(room, interval_seconds=60)

        await observer.start()
        result = await observer.finish()

        self.assertEqual(result.sample_count, 2)
        self.assertEqual(result.collection_error_count, 0)
        self.assertEqual(result.packets_received, 40)
        self.assertEqual(result.packets_lost, 2)
        self.assertEqual(result.concealed_samples, 6)
        self.assertEqual(result.maximum_jitter_ms, 7)
        self.assertEqual(result.maximum_round_trip_time_ms, 30)
        self.assertNotIn("private", repr(result))

    async def test_requires_one_successful_snapshot(self):
        """An unavailable public stats surface cannot yield empty evidence."""
        room = SimpleNamespace(
            get_rtc_stats=AsyncMock(side_effect=RuntimeError("not connected"))
        )
        observer = RoomAudioRtcObserver(room)

        with self.assertRaisesRegex(RuntimeError, "not connected"):
            await observer.start()
        with self.assertRaisesRegex(
            RuntimeError, "RTC evidence contains no successful snapshot"
        ):
            await observer.finish()

    async def test_reappearing_report_continues_from_its_last_counter(self):
        """A temporarily absent report cannot double-count its lifetime total."""
        room = SimpleNamespace(
            get_rtc_stats=AsyncMock(
                side_effect=[
                    _stats(
                        _inbound(
                            "track-a",
                            received=100,
                            lost=2,
                            concealed=5,
                            jitter=0.001,
                        )
                    ),
                    _stats(),
                    _stats(
                        _inbound(
                            "track-a",
                            received=110,
                            lost=3,
                            concealed=7,
                            jitter=0.002,
                        )
                    ),
                ]
            )
        )
        observer = RoomAudioRtcObserver(room, interval_seconds=60)

        await observer.start()
        await observer._collect_once()  # pylint: disable=protected-access
        result = await observer.finish()

        self.assertEqual(result.packets_received, 10)
        self.assertEqual(result.packets_lost, 1)
        self.assertEqual(result.concealed_samples, 2)


if __name__ == "__main__":
    unittest.main()
