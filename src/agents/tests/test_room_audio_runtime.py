"""Tests for the single-session room audio runtime."""

import asyncio
import unittest
from unittest.mock import Mock, patch

from room_audio_ogg import RoomAudioOggEncoder
from room_audio_runtime import (
    RoomAudioRun,
    RoomLiveOutput,
    create_room_audio_run_for_effect,
)


class FakeSpeechStream:
    """Collect room frames and expose a bounded provider event stream."""

    def __init__(self, events=(), *, fail_push=False):
        """Prepare frames and provider events."""
        self.frames = []
        self._events = asyncio.Queue()
        for event in events:
            self._events.put_nowait(event)
        self.closed = False
        self.fail_push = fail_push

    def push_frame(self, frame):
        """Collect one mixed room frame."""
        if self.fail_push:
            raise RuntimeError("live backpressure")
        self.frames.append(frame)

    def __aiter__(self):
        """Return the provider event iterator."""
        return self

    async def __anext__(self):
        """Yield an event or wait until the stream closes."""
        while not self.closed:
            try:
                return self._events.get_nowait()
            except asyncio.QueueEmpty:
                await asyncio.sleep(0)
        raise StopAsyncIteration

    async def aclose(self):
        """Close the fake event stream."""
        self.closed = True


class FakeProvider:
    """Expose exactly one deterministic speech stream."""

    def __init__(self, stream, *, fail_stream=False):
        """Prepare a provider with optional stream failure."""
        self.stream_instance = stream
        self.fail_stream = fail_stream
        self.stream_calls = 0
        self.closed = False

    def stream(self):
        """Return the only provider stream."""
        self.stream_calls += 1
        if self.fail_stream:
            raise RuntimeError("provider stream failed")
        return self.stream_instance

    async def aclose(self):
        """Close fake provider resources."""
        self.closed = True


async def wait_for_frames(stream, count):
    """Wait a bounded interval for the real room clock."""
    for _ in range(100):
        if len(stream.frames) >= count:
            return
        await asyncio.sleep(0.002)
    raise AssertionError(f"expected {count} room frames, got {len(stream.frames)}")


async def invalidate_generation():
    """Model a successful provider-generation fence."""


def live_output(provider_factory, handler_factory, failure_handler):
    """Build one complete live-output boundary for runtime tests."""
    return RoomLiveOutput(
        provider_factory=provider_factory,
        speech_event_handler_factory=handler_factory,
        failure_handler=failure_handler,
        generation_invalidator=invalidate_generation,
    )


class RoomAudioRunTests(unittest.IsolatedAsyncioTestCase):
    """Validate cardinality, forwarding and shutdown invariants."""

    def test_requires_at_least_one_pipeline_output(self):
        """A run cannot consume room audio without a declared output."""
        with self.assertRaisesRegex(
            ValueError, "room audio run requires a live or post output"
        ):
            RoomAudioRun()

    def test_live_pipeline_requires_both_handlers(self):
        """Live output cannot start without explicit publication and failure paths."""
        with self.assertRaisesRegex(
            ValueError, "live room audio requires event and failure handlers"
        ):
            RoomAudioRun(
                live_output=RoomLiveOutput(
                    provider_factory=object,
                    speech_event_handler_factory=None,
                    failure_handler=None,
                    generation_invalidator=None,
                )
            )

    def test_signed_pipeline_selects_exactly_one_output(self):
        """Normal construction cannot silently open an undeclared output."""

        async def handle_event(_event):
            return None

        async def handle_failure(_error):
            return None

        sink = Mock()
        post = create_room_audio_run_for_effect(
            {"pipeline": "post"}, artifact_sink=sink
        )
        live = create_room_audio_run_for_effect(
            {"pipeline": "live"},
            live_output=RoomLiveOutput(
                provider_factory=object,
                speech_event_handler_factory=lambda: handle_event,
                failure_handler=handle_failure,
                generation_invalidator=invalidate_generation,
            ),
        )

        self.assertEqual(post.provider_session_count, 0)
        self.assertEqual(live.provider_session_count, 0)
        with self.assertRaisesRegex(
            ValueError, "post room audio requires only an artifact sink"
        ):
            create_room_audio_run_for_effect(
                {"pipeline": "post"},
                live_output=RoomLiveOutput(
                    provider_factory=object,
                    speech_event_handler_factory=lambda: handle_event,
                    failure_handler=handle_failure,
                    generation_invalidator=invalidate_generation,
                ),
                artifact_sink=sink,
            )
        with self.assertRaisesRegex(ValueError, "unsupported room audio pipeline"):
            create_room_audio_run_for_effect(
                {"pipeline": "combined"},
                artifact_sink=sink,
            )

    async def test_post_only_run_creates_no_provider_session(self):
        """The signed post pipeline writes one artifact without live ASR."""
        run = RoomAudioRun(
            artifact_sink=RoomAudioOggEncoder(),
        )

        await run.start()
        await asyncio.sleep(0.05)
        artifact = await run.aclose()

        self.assertEqual(run.provider_session_count, 0)
        self.assertIsNotNone(artifact)
        self.assertGreaterEqual(artifact.duration_ms, 40)

    async def test_live_only_run_creates_no_post_artifact(self):
        """The signed live pipeline opens one provider without stray storage."""
        stream = FakeSpeechStream()
        provider = FakeProvider(stream)

        async def handle_event(_event):
            return None

        async def handle_failure(_error):
            return None

        run = RoomAudioRun(
            live_output=live_output(
                lambda: provider,
                lambda: handle_event,
                handle_failure,
            ),
        )

        await run.start()
        await wait_for_frames(stream, 2)
        artifact = await run.aclose()

        self.assertIsNone(artifact)
        self.assertTrue(provider.closed)

    async def test_start_opens_one_provider_and_close_returns_one_artifact(self):
        """Repeated start never creates a provider session per participant."""
        stream = FakeSpeechStream()
        provider = FakeProvider(stream)
        factory_calls = 0

        def factory():
            nonlocal factory_calls
            factory_calls += 1
            return provider

        async def handle_event(_event):
            return None

        async def handle_failure(_error):
            return None

        run = RoomAudioRun(
            live_output=live_output(factory, lambda: handle_event, handle_failure),
            artifact_sink=RoomAudioOggEncoder(),
        )

        await run.start()
        await run.start()
        await wait_for_frames(stream, 2)
        artifact = await run.aclose()

        self.assertEqual(factory_calls, 1)
        self.assertEqual(provider.stream_calls, 1)
        self.assertTrue(provider.closed)
        self.assertEqual(run.provider_session_count, 0)
        self.assertGreaterEqual(artifact.duration_ms, 40)
        self.assertGreater(artifact.byte_size, 0)

    async def test_provider_events_are_forwarded_to_room_handler(self):
        """One provider output path feeds the room-owned publisher callback."""
        event = object()
        stream = FakeSpeechStream([event])
        provider = FakeProvider(stream)
        received = []

        async def handle_event(provider_event):
            received.append(provider_event)

        async def handle_failure(_error):
            return None

        run = RoomAudioRun(
            live_output=live_output(
                lambda: provider,
                lambda: handle_event,
                handle_failure,
            ),
            artifact_sink=RoomAudioOggEncoder(),
        )

        await run.start()
        for _ in range(100):
            if received:
                break
            await asyncio.sleep(0)
        await wait_for_frames(stream, 1)
        await run.aclose()

        self.assertEqual(received, [event])

    async def test_restart_replaces_provider_and_event_generation(self):
        """A recovered room connection cannot reuse the prior transcript leg."""
        first_event = object()
        second_event = object()
        providers = [
            FakeProvider(FakeSpeechStream([first_event])),
            FakeProvider(FakeSpeechStream([second_event])),
        ]
        generations = []

        def handler_factory():
            received = []
            generations.append(received)

            async def handle_event(event):
                received.append(event)

            return handle_event

        async def handle_failure(_error):
            return None

        run = RoomAudioRun(
            live_output=live_output(
                lambda: providers.pop(0),
                handler_factory,
                handle_failure,
            ),
        )

        await run.start()
        for _ in range(100):
            if generations[0]:
                break
            await asyncio.sleep(0)
        await run.restart_live()
        for _ in range(100):
            if len(generations) > 1 and generations[1]:
                break
            await asyncio.sleep(0)
        await run.aclose()

        self.assertEqual(generations, [[first_event], [second_event]])

    async def test_restart_invalidates_generation_before_closing_old_stream(self):
        """Provider close cannot emit an event into the prior live leg."""
        order = []

        class OrderedCloseStream(FakeSpeechStream):
            async def aclose(self):
                order.append("close")
                await super().aclose()

        providers = [
            FakeProvider(OrderedCloseStream()),
            FakeProvider(FakeSpeechStream()),
        ]

        async def handle_event(_event):
            return None

        async def handle_failure(_error):
            return None

        async def invalidate():
            order.append("invalidate")

        output = RoomLiveOutput(
            provider_factory=lambda: providers.pop(0),
            speech_event_handler_factory=lambda: handle_event,
            failure_handler=handle_failure,
            generation_invalidator=invalidate,
        )
        run = RoomAudioRun(live_output=output)

        await run.start()
        await run.restart_live()
        await run.aclose()

        self.assertEqual(order[:2], ["invalidate", "close"])

    async def test_provider_failure_is_terminal_and_observable(self):
        """A broken live stream cannot degrade into a silent successful run."""
        stream = FakeSpeechStream(fail_push=True)
        provider = FakeProvider(stream)
        failures = []

        async def handle_event(_event):
            return None

        async def handle_failure(error):
            failures.append(str(error))

        run = RoomAudioRun(
            live_output=live_output(
                lambda: provider,
                lambda: handle_event,
                handle_failure,
            ),
        )

        await run.start()
        error = await asyncio.wait_for(run.wait_terminal_failure(), timeout=1)
        await run.aclose()

        self.assertEqual(str(error), "live backpressure")
        self.assertEqual(failures, ["live backpressure"])
        self.assertTrue(provider.closed)

    async def test_provider_start_failure_closes_partial_resources(self):
        """A failed stream creation leaves no billed provider resource open."""
        provider = FakeProvider(FakeSpeechStream(), fail_stream=True)

        async def handle_event(_event):
            return None

        async def handle_failure(_error):
            return None

        run = RoomAudioRun(
            live_output=live_output(
                lambda: provider,
                lambda: handle_event,
                handle_failure,
            ),
            artifact_sink=RoomAudioOggEncoder(),
        )

        with self.assertRaisesRegex(RuntimeError, "provider stream failed"):
            await run.start()

        self.assertTrue(provider.closed)
        self.assertEqual(run.provider_session_count, 0)

    async def test_live_failure_does_not_stop_post_audio_clock(self):
        """Provider backpressure is explicit while the room artifact continues."""
        stream = FakeSpeechStream(fail_push=True)
        provider = FakeProvider(stream)
        failures = []

        async def handle_event(_event):
            return None

        async def handle_failure(error):
            failures.append(str(error))

        run = RoomAudioRun(
            live_output=live_output(
                lambda: provider,
                lambda: handle_event,
                handle_failure,
            ),
            artifact_sink=RoomAudioOggEncoder(),
        )

        await run.start()
        for _ in range(100):
            if failures:
                break
            await asyncio.sleep(0.002)
        await asyncio.sleep(0.04)
        artifact = await run.aclose()

        self.assertEqual(failures, ["live backpressure"])
        self.assertEqual(run.provider_session_count, 0)
        self.assertGreaterEqual(artifact.duration_ms, 40)

    async def test_observed_track_uses_bounded_twenty_millisecond_frames(self):
        """The LiveKit reader matches mixer geometry and has finite capacity."""
        provider_stream = FakeSpeechStream()
        provider = FakeProvider(provider_stream)
        track_stream = FakeSpeechStream()

        async def handle_event(_event):
            return None

        async def handle_failure(_error):
            return None

        run = RoomAudioRun(
            live_output=live_output(
                lambda: provider,
                lambda: handle_event,
                handle_failure,
            ),
            artifact_sink=RoomAudioOggEncoder(),
        )
        await run.start()

        with patch(
            "room_audio_runtime.rtc.AudioStream.from_track",
            return_value=track_stream,
        ) as from_track:
            track = object()
            run.observe_track("TR_A", track)

        from_track.assert_called_once_with(
            track=track,
            capacity=10,
            sample_rate=24_000,
            num_channels=1,
            frame_size_ms=20,
        )
        self.assertEqual(run.snapshot().pending_track_count, 1)
        await run.unobserve_track("TR_A")
        await wait_for_frames(provider_stream, 1)
        await run.aclose()

    async def test_forty_tracks_share_one_provider_session(self):
        """Classroom cardinality changes mixer inputs, not ASR sessions."""
        provider_stream = FakeSpeechStream()
        provider = FakeProvider(provider_stream)
        factory_calls = 0

        def factory():
            nonlocal factory_calls
            factory_calls += 1
            return provider

        async def handle_event(_event):
            return None

        async def handle_failure(_error):
            return None

        run = RoomAudioRun(
            live_output=live_output(factory, lambda: handle_event, handle_failure),
        )
        await run.start()

        streams = [FakeSpeechStream() for _index in range(40)]
        with patch(
            "room_audio_runtime.rtc.AudioStream.from_track",
            side_effect=streams,
        ):
            for index in range(40):
                run.observe_track(f"TR_{index:02d}", object())

        self.assertEqual(run.snapshot().published_track_count, 40)
        self.assertEqual(run.provider_session_count, 1)
        self.assertEqual(factory_calls, 1)
        self.assertEqual(provider.stream_calls, 1)
        await run.aclose()


if __name__ == "__main__":
    unittest.main()
