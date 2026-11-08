"""Tests for the bounded track-owned room audio runtime."""

import asyncio
import time
import unittest
from unittest.mock import Mock, patch

from room_audio_ogg import RoomAudioOggEncoder
from room_audio_runtime import (
    ProviderSessionCapacityUnavailable,
    ProviderSessionQuota,
    RoomAudioRun,
    RoomAudioRuntimeConfig,
    RoomLiveOutput,
    create_room_audio_run_for_effect,
)
from room_audio_start_authority import LIVE_SOURCE_PROFILE


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
        """Collect one track-owned room frame."""
        if self.fail_push:
            raise RuntimeError("live backpressure")
        self.frames.append(frame)

    def emit_event(self, event):
        """Release one provider event at a caller-controlled time."""
        self._events.put_nowait(event)

    def __aiter__(self):
        """Return the provider event iterator."""
        return self

    async def wait_ready(self):
        """Model an immediately configured provider session."""
        return None

    async def __anext__(self):
        """Yield an event or wait until the stream closes."""
        while not self.closed:
            try:
                return self._events.get_nowait()
            except asyncio.QueueEmpty:
                await asyncio.sleep(0)
        raise StopAsyncIteration

    async def finish_input_and_drain(self):
        """Finish the fake event stream."""
        self.closed = True

    async def aclose(self):
        """Close a fake LiveKit input stream."""
        self.closed = True


class FakeProvider:
    """Expose exactly one deterministic speech stream."""

    def __init__(
        self,
        stream,
        *,
        fail_stream=False,
        fail_close=False,
        close_gate=None,
    ):
        """Prepare a provider with optional stream failure."""
        self.stream_instance = stream
        self.fail_stream = fail_stream
        self.fail_close = fail_close
        self.close_gate = close_gate
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
        if self.close_gate is not None:
            await self.close_gate.wait()
        self.closed = True
        if self.fail_close:
            raise RuntimeError("provider close failed")


class BlockingDrainSpeechStream(FakeSpeechStream):
    """Keep one old provider generation draining until the test releases it."""

    def __init__(self):
        """Prepare observable drain boundaries."""
        super().__init__()
        self.finish_started = asyncio.Event()
        self.allow_finish = asyncio.Event()

    async def finish_input_and_drain(self):
        """Wait without blocking a successor generation from opening."""
        self.finish_started.set()
        await self.allow_finish.wait()
        self.closed = True


class FailingDrainSpeechStream(FakeSpeechStream):
    """Expose one provider that cannot prove terminal drain."""

    async def finish_input_and_drain(self):
        """Fail instead of reporting a false successful close."""
        raise RuntimeError("provider drain failed")


class BlockingReadySpeechStream(FakeSpeechStream):
    """Keep the provider handshake in OPENING for timeout tests."""

    def __init__(self):
        """Prepare a provider that never reports readiness."""
        super().__init__()
        self.ready_gate = asyncio.Event()

    async def wait_ready(self):
        """Wait until explicitly released by the test."""
        await self.ready_gate.wait()


async def wait_for_frames(stream, count):
    """Wait a bounded interval for the real room clock."""
    for _ in range(100):
        if len(stream.frames) >= count:
            return
        await asyncio.sleep(0.002)
    raise AssertionError(f"expected {count} room frames, got {len(stream.frames)}")


def live_output(
    provider_factory,
    handler_factory,
    failure_handler,
    track_handler_factory=None,
):
    """Build one complete live-output boundary for runtime tests."""
    if track_handler_factory is None:

        def track_handler_factory(_binding):
            return handler_factory()

    return RoomLiveOutput(
        provider_factory=provider_factory,
        track_event_handler_factory=track_handler_factory,
        failure_handler=failure_handler,
    )


def authorize_speaking_track(
    run,
    *,
    track_ref="TR_A",
    identity="alice",
    pcm=b"\xe8\x03" * 480,
):
    """Admit one fake microphone and queue one audible frame."""
    with patch(
        "room_audio_runtime.rtc.AudioStream.from_track",
        return_value=FakeSpeechStream(),
    ):
        run.observe_track(track_ref, object(), identity)
    run.admit_track(
        track_ref,
        generation=1,
        expires_at_ms=int(time.time() * 1_000) + 10_000,
        authorization_digest="a" * 64,
    )
    run._mixer.push_frame(track_ref, pcm)


class RoomAudioRunTests(unittest.IsolatedAsyncioTestCase):
    """Validate cardinality, forwarding and shutdown invariants."""

    def test_requires_at_least_one_pipeline_output(self):
        """A run cannot consume room audio without a declared output."""
        with self.assertRaisesRegex(
            ValueError, "room audio run requires a live or post output"
        ):
            RoomAudioRun()

    def test_clock_lag_bound_must_exceed_one_frame(self):
        """A watchdog threshold cannot be shorter than the room clock tick."""
        with self.assertRaisesRegex(
            ValueError, "room audio clock lag bound must exceed one frame"
        ):
            RoomAudioRun(
                artifact_sink=Mock(),
                config=RoomAudioRuntimeConfig(maximum_clock_lag_seconds=0.02),
            )

    def test_rejects_combined_live_and_post_outputs(self):
        """One run cannot hide a shadow output behind the selected pipeline."""

        async def handle_event(_event):
            return None

        async def handle_failure(_error):
            return None

        with self.assertRaisesRegex(
            ValueError, "room audio run requires a live or post output"
        ):
            RoomAudioRun(
                live_output=live_output(
                    lambda: FakeProvider(FakeSpeechStream()),
                    lambda: handle_event,
                    handle_failure,
                ),
                artifact_sink=RoomAudioOggEncoder(),
            )

    def test_live_pipeline_requires_both_handlers(self):
        """Live output cannot start without explicit publication and failure paths."""
        with self.assertRaisesRegex(
            ValueError, "live room audio requires event and failure handlers"
        ):
            RoomAudioRun(
                live_output=RoomLiveOutput(
                    provider_factory=object,
                    track_event_handler_factory=None,
                    failure_handler=None,
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
            {"pipeline": "live", "source_profile": LIVE_SOURCE_PROFILE},
            live_output=RoomLiveOutput(
                provider_factory=object,
                track_event_handler_factory=lambda _binding: handle_event,
                failure_handler=handle_failure,
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
                    track_event_handler_factory=lambda _binding: handle_event,
                    failure_handler=handle_failure,
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
        authorize_speaking_track(run)
        await wait_for_frames(stream, 2)
        artifact = await run.aclose()

        self.assertIsNone(artifact)
        self.assertTrue(provider.closed)

    def test_live_pipeline_logs_periodic_content_free_mixer_health(self):
        """Live diagnosis exposes mixer counters without meeting content."""

        async def handle_event(_event):
            return None

        async def handle_failure(_error):
            return None

        run = RoomAudioRun(
            live_output=live_output(
                lambda: FakeProvider(FakeSpeechStream()),
                lambda: handle_event,
                handle_failure,
            ),
        )
        run._mixer.mix_next_frame()

        with (
            patch("room_audio_runtime.LIVE_METRICS_INTERVAL_FRAMES", 1),
            self.assertLogs("room-audio-worker.runtime", level="INFO") as logs,
        ):
            run._log_live_metrics()

        self.assertIn("output_frames=1", logs.output[0])
        self.assertIn("authorized_tracks=0", logs.output[0])
        self.assertIn("speaking_tracks=0", logs.output[0])

    async def test_start_opens_provider_only_for_speaking_track(self):
        """Repeated start is inert until one admitted microphone speaks."""
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
        )

        await run.start()
        await run.start()
        self.assertEqual(factory_calls, 0)
        authorize_speaking_track(run)
        await wait_for_frames(stream, 2)
        artifact = await run.aclose()

        self.assertEqual(factory_calls, 1)
        self.assertEqual(provider.stream_calls, 1)
        self.assertTrue(provider.closed)
        self.assertEqual(run.provider_session_count, 0)
        self.assertIsNone(artifact)

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
        )

        await run.start()
        authorize_speaking_track(run)
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
        authorize_speaking_track(run)
        for _ in range(100):
            if generations and generations[0]:
                break
            await asyncio.sleep(0.002)
        await run.restart_live()
        run._mixer.push_frame("TR_A", b"\xd0\x07" * 480)
        for _ in range(100):
            if len(generations) > 1 and generations[1]:
                break
            await asyncio.sleep(0.002)
        await run.aclose()

        self.assertEqual(generations, [[first_event], [second_event]])

    async def test_reauthorization_opens_successor_while_predecessor_drains(self):
        """Speech resumed during drain is routed to a new bound session."""
        predecessor_stream = BlockingDrainSpeechStream()
        successor_stream = FakeSpeechStream()
        providers = [
            FakeProvider(predecessor_stream),
            FakeProvider(successor_stream),
        ]
        bindings = []

        async def handle_event(_event):
            return None

        async def handle_failure(_error):
            return None

        def handler_factory(binding):
            bindings.append(binding)
            return handle_event

        run = RoomAudioRun(
            live_output=live_output(
                lambda: providers.pop(0),
                lambda: handle_event,
                handle_failure,
                handler_factory,
            ),
        )
        await run.start()
        authorize_speaking_track(run)
        await wait_for_frames(predecessor_stream, 1)

        run.revoke_track("TR_A", generation=2, authorization_digest="b" * 64)
        run.admit_track(
            "TR_A",
            generation=3,
            expires_at_ms=int(time.time() * 1_000) + 10_000,
            authorization_digest="c" * 64,
        )
        run._mixer.push_frame("TR_A", b"\xd0\x07" * 480)
        await asyncio.wait_for(predecessor_stream.finish_started.wait(), timeout=1)
        await wait_for_frames(successor_stream, 1)

        predecessor_stream.allow_finish.set()
        await run.aclose()

        self.assertEqual(
            [
                (binding.authority_generation, binding.session_generation)
                for binding in bindings
            ],
            [(1, 1), (3, 2)],
        )

    async def test_capacity_gap_keeps_room_alive_and_resumes_after_drain(self):
        """Repeated speaker turnover cannot terminate the room live worker."""
        first_stream = BlockingDrainSpeechStream()
        second_stream = BlockingDrainSpeechStream()
        resumed_stream = FakeSpeechStream()
        providers = [
            FakeProvider(first_stream),
            FakeProvider(second_stream),
            FakeProvider(resumed_stream),
        ]
        failures = []

        async def handle_event(_event):
            return None

        async def handle_failure(error):
            failures.append(error)

        run = RoomAudioRun(
            live_output=live_output(
                lambda: providers.pop(0),
                lambda: handle_event,
                handle_failure,
            ),
            config=RoomAudioRuntimeConfig(
                maximum_active_track_streams=2,
                maximum_provider_sessions=2,
                maximum_draining_sessions=2,
            ),
            provider_session_quota=ProviderSessionQuota(maximum_sessions=2),
        )
        await run.start()
        authorize_speaking_track(run, track_ref="TR_A", identity="alice")
        await wait_for_frames(first_stream, 1)

        run.revoke_track("TR_A", generation=2, authorization_digest="b" * 64)
        authorize_speaking_track(run, track_ref="TR_B", identity="bob")
        await wait_for_frames(second_stream, 1)
        await asyncio.wait_for(first_stream.finish_started.wait(), timeout=1)

        run.revoke_track("TR_B", generation=2, authorization_digest="c" * 64)
        run.admit_track(
            "TR_A",
            generation=3,
            expires_at_ms=int(time.time() * 1_000) + 10_000,
            authorization_digest="d" * 64,
        )
        run._mixer.push_frame("TR_A", b"\xd0\x07" * 480)
        await asyncio.wait_for(second_stream.finish_started.wait(), timeout=1)

        for _ in range(100):
            if failures:
                break
            await asyncio.sleep(0.002)
        self.assertEqual(len(failures), 1)
        self.assertIn("capacity", str(failures[0]))
        self.assertFalse(run._terminal_failure.is_set())
        self.assertEqual(run.provider_session_count, 2)

        first_stream.allow_finish.set()
        await wait_for_frames(resumed_stream, 1)

        self.assertFalse(run._terminal_failure.is_set())
        self.assertEqual(len(failures), 1)
        second_stream.allow_finish.set()
        await run.aclose()

    async def test_two_speakers_resume_while_predecessors_drain_without_gaps(self):
        """Normal two-speaker turnover keeps both microphones transcribed."""
        alice_predecessor = BlockingDrainSpeechStream()
        bob_predecessor = BlockingDrainSpeechStream()
        alice_successor = FakeSpeechStream()
        bob_successor = FakeSpeechStream()
        providers = [
            FakeProvider(alice_predecessor),
            FakeProvider(bob_predecessor),
            FakeProvider(alice_successor),
            FakeProvider(bob_successor),
        ]
        failures = []

        async def handle_event(_event):
            return None

        async def handle_failure(error):
            failures.append(error)

        run = create_room_audio_run_for_effect(
            {"pipeline": "live", "source_profile": LIVE_SOURCE_PROFILE},
            live_output=live_output(
                lambda: providers.pop(0),
                lambda: handle_event,
                handle_failure,
            ),
        )
        await run.start()
        authorize_speaking_track(run, track_ref="TR_ALICE", identity="alice")
        authorize_speaking_track(run, track_ref="TR_BOB", identity="bob")
        await wait_for_frames(alice_predecessor, 1)
        await wait_for_frames(bob_predecessor, 1)

        run.revoke_track("TR_ALICE", generation=2, authorization_digest="b" * 64)
        run.revoke_track("TR_BOB", generation=2, authorization_digest="c" * 64)
        await asyncio.wait_for(alice_predecessor.finish_started.wait(), timeout=1)
        await asyncio.wait_for(bob_predecessor.finish_started.wait(), timeout=1)

        expires_at_ms = int(time.time() * 1_000) + 10_000
        for track_ref, digest in (("TR_ALICE", "d" * 64), ("TR_BOB", "e" * 64)):
            run.admit_track(
                track_ref,
                generation=3,
                expires_at_ms=expires_at_ms,
                authorization_digest=digest,
            )
            run._mixer.push_frame(track_ref, b"\xd0\x07" * 480)

        await wait_for_frames(alice_successor, 1)
        await wait_for_frames(bob_successor, 1)
        self.assertEqual(failures, [])

        alice_predecessor.allow_finish.set()
        bob_predecessor.allow_finish.set()
        await run.aclose()

    async def test_capacity_retry_is_gated_by_each_observed_release(self):
        """An irrelevant release cannot create a per-frame retry storm."""
        quota = ProviderSessionQuota(maximum_sessions=2)

        async def handle_event(_event):
            return None

        async def handle_failure(_error):
            return None

        run = RoomAudioRun(
            live_output=live_output(
                lambda: FakeProvider(FakeSpeechStream()),
                lambda: handle_event,
                handle_failure,
            ),
            provider_session_quota=quota,
        )
        error = ProviderSessionCapacityUnavailable("room")
        run._wait_for_track_capacity("TR_A", error)
        self.assertFalse(run._track_can_retry_capacity("TR_A"))

        self.assertTrue(quota.acquire())
        quota.release()
        self.assertTrue(run._track_can_retry_capacity("TR_A"))

        run._wait_for_track_capacity("TR_A", error)
        self.assertFalse(run._track_can_retry_capacity("TR_A"))
        run._clear_all_capacity_waiters()
        await asyncio.gather(*run._failure_notification_tasks)

    async def test_revocation_removes_a_track_waiting_for_capacity(self):
        """A revoked waiter cannot reopen after later capacity release."""
        quota = ProviderSessionQuota(maximum_sessions=1)
        self.assertTrue(quota.acquire())
        providers = []

        async def handle_event(_event):
            return None

        async def handle_failure(_error):
            return None

        run = RoomAudioRun(
            live_output=live_output(
                lambda: providers.append(FakeProvider(FakeSpeechStream())),
                lambda: handle_event,
                handle_failure,
            ),
            provider_session_quota=quota,
        )
        await run.start()
        authorize_speaking_track(run, track_ref="TR_REVOKED")
        for _ in range(100):
            if "TR_REVOKED" in run._capacity_waiters:
                break
            await asyncio.sleep(0.002)

        run.revoke_track(
            "TR_REVOKED",
            generation=2,
            authorization_digest="e" * 64,
        )
        quota.release()
        await asyncio.sleep(0.05)

        self.assertNotIn("TR_REVOKED", run._capacity_waiters)
        self.assertEqual(providers, [])
        self.assertFalse(run._terminal_failure.is_set())
        await run.aclose()

    async def test_unexpected_provider_close_releases_session_and_fails(self):
        """An ended provider iterator cannot leak a billed session."""
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
        authorize_speaking_track(run)
        await wait_for_frames(stream, 1)

        stream.closed = True
        error = await asyncio.wait_for(run.wait_terminal_failure(), timeout=1)
        await run.aclose()

        self.assertEqual(str(error), "track provider closed")
        self.assertTrue(provider.closed)
        self.assertEqual(run.provider_session_count, 0)

    async def test_blocked_failure_notification_cannot_hide_terminal_state(self):
        """Optional gap publication is bounded and never delays STOP."""
        stream = FakeSpeechStream()
        cancellation_observed = asyncio.Event()
        notification_release = asyncio.Event()

        async def handle_event(_event):
            return None

        async def handle_failure(_error):
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancellation_observed.set()
                await notification_release.wait()

        run = RoomAudioRun(
            live_output=live_output(
                lambda: FakeProvider(stream),
                lambda: handle_event,
                handle_failure,
            ),
            config=RoomAudioRuntimeConfig(shutdown_timeout_seconds=0.01),
        )
        await run.start()
        authorize_speaking_track(run)
        await wait_for_frames(stream, 1)

        stream.closed = True
        error = await asyncio.wait_for(run.wait_terminal_failure(), timeout=1)
        self.assertEqual(str(error), "track provider closed")

        await asyncio.sleep(0.02)
        self.assertTrue(cancellation_observed.is_set())
        self.assertFalse(run._failure_notification_tasks)
        notification_release.set()
        await asyncio.sleep(0)
        await run.aclose()

    async def test_close_propagates_provider_drain_failure(self):
        """A caller cannot finalize a live run after losing terminal text."""
        stream = FailingDrainSpeechStream()

        async def handle_event(_event):
            return None

        async def handle_failure(_error):
            return None

        run = RoomAudioRun(
            live_output=live_output(
                lambda: FakeProvider(stream),
                lambda: handle_event,
                handle_failure,
            ),
        )
        await run.start()
        authorize_speaking_track(run)
        await wait_for_frames(stream, 1)

        with self.assertRaisesRegex(RuntimeError, "track provider drain failed"):
            await run.aclose()

        with self.assertRaisesRegex(RuntimeError, "previously failed"):
            await run.aclose()

    async def test_blocked_event_handler_is_bounded_by_shutdown_deadline(self):
        """A stuck publisher cannot hold provider capacity indefinitely."""
        stream = FakeSpeechStream([object()])
        handler_gate = asyncio.Event()

        async def handle_event(_event):
            await handler_gate.wait()

        async def handle_failure(_error):
            return None

        run = RoomAudioRun(
            live_output=live_output(
                lambda: FakeProvider(stream),
                lambda: handle_event,
                handle_failure,
            ),
            config=RoomAudioRuntimeConfig(shutdown_timeout_seconds=0.01),
        )
        await run.start()
        authorize_speaking_track(run)
        await wait_for_frames(stream, 1)

        with self.assertRaisesRegex(RuntimeError, "shutdown timed out"):
            await run.aclose()
        self.assertEqual(run.provider_session_count, 0)

    async def test_blocked_provider_close_is_bounded_by_shutdown_deadline(self):
        """An unproved provider close keeps its capacity quarantined."""
        stream = FakeSpeechStream()
        close_gate = asyncio.Event()
        provider = FakeProvider(stream, close_gate=close_gate)

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
            config=RoomAudioRuntimeConfig(shutdown_timeout_seconds=0.01),
        )
        await run.start()
        authorize_speaking_track(run)
        await wait_for_frames(stream, 1)

        with self.assertRaisesRegex(RuntimeError, "shutdown timed out"):
            await run.aclose()
        self.assertEqual(run.provider_session_count, 1)

        close_gate.set()
        for _ in range(100):
            if run.provider_session_count == 0:
                break
            await asyncio.sleep(0.001)
        self.assertEqual(run.provider_session_count, 0)

    async def test_blocked_drain_and_close_respect_one_shutdown_deadline(self):
        """Cancellation-resistant cleanup cannot extend the signed deadline."""
        stream = BlockingDrainSpeechStream()
        close_gate = asyncio.Event()
        provider = FakeProvider(stream, close_gate=close_gate)

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
            config=RoomAudioRuntimeConfig(shutdown_timeout_seconds=0.01),
        )
        await run.start()
        authorize_speaking_track(run)
        await wait_for_frames(stream, 1)

        started_at = time.monotonic()
        with self.assertRaisesRegex(RuntimeError, "shutdown timed out"):
            await asyncio.wait_for(run.aclose(), timeout=0.1)
        self.assertLess(time.monotonic() - started_at, 0.1)
        self.assertEqual(run.provider_session_count, 1)

        close_gate.set()
        for _ in range(100):
            if run.provider_session_count == 0:
                break
            await asyncio.sleep(0.001)
        self.assertEqual(run.provider_session_count, 0)

    async def test_revoked_track_is_not_reopened_during_hangover(self):
        """Revocation removes router state before the next clock tick."""
        stream = FakeSpeechStream()
        factory_calls = 0

        def factory():
            nonlocal factory_calls
            factory_calls += 1
            return FakeProvider(stream)

        async def handle_event(_event):
            return None

        async def handle_failure(_error):
            return None

        run = RoomAudioRun(
            live_output=live_output(factory, lambda: handle_event, handle_failure)
        )
        await run.start()
        authorize_speaking_track(run)
        await wait_for_frames(stream, 1)

        run.revoke_track("TR_A", generation=2, authorization_digest="b" * 64)
        await asyncio.sleep(0.05)
        await run.aclose()

        self.assertEqual(factory_calls, 1)

    async def test_unobserved_track_is_not_reopened_or_made_terminal(self):
        """A physical unsubscribe clears hangover without failing the room."""
        stream = FakeSpeechStream()
        factory_calls = 0

        def factory():
            nonlocal factory_calls
            factory_calls += 1
            return FakeProvider(stream)

        async def handle_event(_event):
            return None

        async def handle_failure(_error):
            return None

        run = RoomAudioRun(
            live_output=live_output(factory, lambda: handle_event, handle_failure)
        )
        await run.start()
        authorize_speaking_track(run)
        await wait_for_frames(stream, 1)

        await run.unobserve_track("TR_A")
        await asyncio.sleep(0.05)
        self.assertFalse(run._terminal_failure.is_set())
        await run.aclose()

        self.assertEqual(factory_calls, 1)

    async def test_process_quota_waits_then_resumes_across_rooms(self):
        """A room resumes after another room proves process-capacity release."""
        quota = ProviderSessionQuota(maximum_sessions=1)
        first_stream = FakeSpeechStream()
        second_stream = FakeSpeechStream()

        async def handle_event(_event):
            return None

        async def handle_failure(_error):
            return None

        first = RoomAudioRun(
            live_output=live_output(
                lambda: FakeProvider(first_stream),
                lambda: handle_event,
                handle_failure,
            ),
            provider_session_quota=quota,
        )
        second = RoomAudioRun(
            live_output=live_output(
                lambda: FakeProvider(second_stream),
                lambda: handle_event,
                handle_failure,
            ),
            provider_session_quota=quota,
        )
        await first.start()
        await second.start()
        authorize_speaking_track(first, track_ref="TR_FIRST")
        await wait_for_frames(first_stream, 1)
        authorize_speaking_track(second, track_ref="TR_SECOND")

        await asyncio.sleep(0.05)
        self.assertFalse(second._terminal_failure.is_set())
        self.assertEqual(second_stream.frames, [])
        await first.aclose()
        await wait_for_frames(second_stream, 1)
        await second.aclose()

        self.assertFalse(second._terminal_failure.is_set())

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
        authorize_speaking_track(run)
        error = await asyncio.wait_for(run.wait_terminal_failure(), timeout=1)
        await run.aclose()

        self.assertEqual(str(error), "live backpressure")
        self.assertEqual(failures, ["live backpressure"])
        self.assertTrue(provider.closed)

    async def test_provider_failure_remains_terminal_when_drain_also_fails(self):
        """Cleanup failure cannot hide the original terminal provider error."""
        stream = FailingDrainSpeechStream(fail_push=True)
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
        authorize_speaking_track(run)

        error = await asyncio.wait_for(run.wait_terminal_failure(), timeout=1)
        await run.aclose()

        self.assertEqual(str(error), "live backpressure")
        self.assertEqual(failures, ["live backpressure"])
        self.assertTrue(provider.closed)

    async def test_uncertain_close_blocks_shared_quota_until_proven(self):
        """Another room cannot use capacity held by an uncertain close."""
        quota = ProviderSessionQuota(maximum_sessions=1)
        close_gate = asyncio.Event()
        first_stream = FakeSpeechStream()

        async def handle_event(_event):
            return None

        async def handle_failure(_error):
            return None

        first = RoomAudioRun(
            live_output=live_output(
                lambda: FakeProvider(first_stream, close_gate=close_gate),
                lambda: handle_event,
                handle_failure,
            ),
            config=RoomAudioRuntimeConfig(shutdown_timeout_seconds=0.01),
            provider_session_quota=quota,
        )
        await first.start()
        authorize_speaking_track(first, track_ref="TR_FIRST")
        await wait_for_frames(first_stream, 1)
        with self.assertRaisesRegex(RuntimeError, "shutdown timed out"):
            await first.aclose()

        second_stream = FakeSpeechStream()
        second = RoomAudioRun(
            live_output=live_output(
                lambda: FakeProvider(second_stream),
                lambda: handle_event,
                handle_failure,
            ),
            provider_session_quota=quota,
        )
        await second.start()
        authorize_speaking_track(second, track_ref="TR_SECOND")
        await asyncio.sleep(0.05)
        self.assertFalse(second._terminal_failure.is_set())
        self.assertEqual(second_stream.frames, [])

        close_gate.set()
        for _ in range(100):
            if first.provider_session_count == 0:
                break
            await asyncio.sleep(0.001)
        await wait_for_frames(second_stream, 1)
        await second.aclose()

    async def test_uncertain_close_consumes_the_room_session_limit(self):
        """A quarantined close remains part of the signed per-room cap."""
        close_gate = asyncio.Event()
        provider = FakeProvider(FakeSpeechStream(), close_gate=close_gate)

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
            config=RoomAudioRuntimeConfig(
                maximum_active_track_streams=1,
                maximum_provider_sessions=1,
                maximum_draining_sessions=1,
                shutdown_timeout_seconds=0.01,
            ),
            provider_session_quota=ProviderSessionQuota(maximum_sessions=2),
        )
        await run.start()
        authorize_speaking_track(run, track_ref="TR_FIRST")
        await wait_for_frames(provider.stream_instance, 1)

        with patch(
            "room_audio_runtime.rtc.AudioStream.from_track",
            return_value=FakeSpeechStream(),
        ):
            run.observe_track("TR_SECOND", object(), "bob")
        run.admit_track(
            "TR_SECOND",
            generation=1,
            expires_at_ms=int(time.time() * 1_000) + 10_000,
            authorization_digest="c" * 64,
        )

        run.revoke_track("TR_FIRST", generation=2, authorization_digest="b" * 64)
        close_tasks = tuple(run._track_close_tasks)
        await asyncio.gather(*close_tasks, return_exceptions=True)
        self.assertEqual(run.provider_session_count, 1)
        with self.assertRaisesRegex(
            ProviderSessionCapacityUnavailable,
            "capacity unavailable",
        ):
            await run._open_track_session("TR_SECOND")

        close_gate.set()
        for _ in range(100):
            if run.provider_session_count == 0:
                break
            await asyncio.sleep(0.001)
        with self.assertRaisesRegex(RuntimeError, "shutdown timed out"):
            await run.aclose()

    async def test_clock_lag_is_terminal_for_post_audio(self):
        """A stalled event loop cannot produce a deceptively complete artifact."""
        run = RoomAudioRun(artifact_sink=Mock())

        self.assertFalse(run._clock_lag_exceeded(10.0, 10.25))
        self.assertTrue(run._clock_lag_exceeded(10.0, 10.251))

        await run._fail_clock()
        error = await asyncio.wait_for(run.wait_terminal_failure(), timeout=1)

        await run.aclose()
        self.assertEqual(str(error), "room audio clock exceeded maximum lag")

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
        )

        await run.start()
        authorize_speaking_track(run)
        error = await asyncio.wait_for(run.wait_terminal_failure(), timeout=1)

        self.assertTrue(provider.closed)
        self.assertIn("track provider session failed", str(error))
        self.assertEqual(run.provider_session_count, 0)

    async def test_partial_open_uncertain_close_quarantines_process_quota(self):
        """A handler failure cannot hide an unproved provider close."""
        quota = ProviderSessionQuota(maximum_sessions=1)
        close_gate = asyncio.Event()
        provider = FakeProvider(FakeSpeechStream(), close_gate=close_gate)

        def fail_handler(_binding):
            raise RuntimeError("handler construction failed")

        async def handle_failure(_error):
            return None

        failing = RoomAudioRun(
            live_output=live_output(
                lambda: provider,
                lambda: None,
                handle_failure,
                track_handler_factory=fail_handler,
            ),
            config=RoomAudioRuntimeConfig(shutdown_timeout_seconds=0.01),
            provider_session_quota=quota,
        )
        await failing.start()
        authorize_speaking_track(failing, track_ref="TR_FAIL")
        error = await asyncio.wait_for(failing.wait_terminal_failure(), timeout=1)
        self.assertIn("track provider session failed", str(error))
        self.assertEqual(failing.provider_session_count, 1)

        successor_stream = FakeSpeechStream()

        async def handle_event(_event):
            return None

        successor = RoomAudioRun(
            live_output=live_output(
                lambda: FakeProvider(successor_stream),
                lambda: handle_event,
                handle_failure,
            ),
            provider_session_quota=quota,
        )
        await successor.start()
        authorize_speaking_track(successor, track_ref="TR_SUCCESSOR")
        await asyncio.sleep(0.05)
        self.assertFalse(successor._terminal_failure.is_set())
        self.assertEqual(successor_stream.frames, [])

        close_gate.set()
        for _ in range(100):
            if failing.provider_session_count == 0:
                break
            await asyncio.sleep(0.001)
        self.assertEqual(failing.provider_session_count, 0)
        await wait_for_frames(successor_stream, 1)
        await failing.aclose()
        await successor.aclose()

    async def test_opening_timeout_is_terminal_and_releases_provider(self):
        """The signed opening deadline is enforced before false readiness."""
        stream = BlockingReadySpeechStream()
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
            config=RoomAudioRuntimeConfig(opening_timeout_seconds=0.01),
        )
        await run.start()
        authorize_speaking_track(run)

        error = await asyncio.wait_for(run.wait_terminal_failure(), timeout=1)
        await run.aclose()

        self.assertIsInstance(error, TimeoutError)
        self.assertTrue(provider.closed)
        self.assertEqual(run.provider_session_count, 0)

    async def test_observed_track_uses_lossless_sdk_queue_and_bounded_mixer(self):
        """Only the counted application queue may evict a decoded frame."""
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
        )
        await run.start()

        with patch(
            "room_audio_runtime.rtc.AudioStream.from_track",
            return_value=track_stream,
        ) as from_track:
            track = object()
            run.observe_track("TR_A", track, "alice")

        from_track.assert_called_once_with(
            track=track,
            capacity=0,
            sample_rate=24_000,
            num_channels=1,
            frame_size_ms=20,
        )
        self.assertEqual(run.snapshot().pending_track_count, 1)
        await run.unobserve_track("TR_A")
        await run.aclose()

    async def test_forty_silent_tracks_open_no_provider_session(self):
        """Classroom membership alone never creates billed ASR sessions."""
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
                run.observe_track(
                    f"TR_{index:02d}",
                    object(),
                    f"participant-{index:02d}",
                )

        self.assertEqual(run.snapshot().published_track_count, 40)
        self.assertEqual(run.provider_session_count, 0)
        self.assertEqual(factory_calls, 0)
        self.assertEqual(provider.stream_calls, 0)
        await run.aclose()

    async def test_overlap_routes_each_microphone_to_a_named_provider_stream(self):
        """Two simultaneous tracks never reach one mixed ASR input."""
        alice_stream = FakeSpeechStream()
        bob_stream = FakeSpeechStream()
        providers = [
            FakeProvider(alice_stream),
            FakeProvider(bob_stream),
        ]
        track_bindings = []

        async def handle_event(_event):
            return None

        async def handle_failure(_error):
            return None

        def track_handler_factory(binding):
            track_bindings.append(
                (
                    binding.participant_identity,
                    binding.track_ref,
                    binding.authority_generation,
                    binding.session_generation,
                )
            )
            return handle_event

        run = RoomAudioRun(
            live_output=live_output(
                lambda: providers.pop(0),
                lambda: handle_event,
                handle_failure,
                track_handler_factory,
            ),
        )
        await run.start()
        with patch(
            "room_audio_runtime.rtc.AudioStream.from_track",
            side_effect=[FakeSpeechStream(), FakeSpeechStream()],
        ):
            run.observe_track("TR_A", object(), "alice")
            run.observe_track("TR_B", object(), "bob")
        expires_at_ms = int(time.time() * 1_000) + 10_000
        for track_ref in ("TR_A", "TR_B"):
            run.admit_track(
                track_ref,
                generation=1,
                expires_at_ms=expires_at_ms,
                authorization_digest="a" * 64,
            )
        alice_pcm = b"\xe8\x03" * 480
        bob_pcm = b"\xd0\x07" * 480
        run._mixer.push_frame("TR_A", alice_pcm)
        run._mixer.push_frame("TR_B", bob_pcm)

        await wait_for_frames(bob_stream, 1)
        bob_continues_pcm = b"\xb8\x0b" * 480
        run._mixer.push_frame("TR_B", bob_continues_pcm)
        for _ in range(100):
            bob_output_pcm = [bytes(frame.data) for frame in bob_stream.frames]
            if bob_continues_pcm in bob_output_pcm:
                break
            await asyncio.sleep(0.002)
        await run.aclose()

        alice_output_pcm = [bytes(frame.data) for frame in alice_stream.frames]
        bob_output_pcm = [bytes(frame.data) for frame in bob_stream.frames]
        self.assertIn(alice_pcm, alice_output_pcm)
        self.assertNotIn(bob_pcm, alice_output_pcm)
        self.assertNotIn(bob_continues_pcm, alice_output_pcm)
        self.assertIn(bob_pcm, bob_output_pcm)
        self.assertIn(bob_continues_pcm, bob_output_pcm)
        self.assertEqual(
            track_bindings,
            [("alice", "TR_A", 1, 1), ("bob", "TR_B", 1, 1)],
        )

    async def test_delayed_out_of_order_provider_events_keep_track_owners(self):
        """Provider timing cannot move text between microphone identities."""
        alice_stream = FakeSpeechStream()
        bob_stream = FakeSpeechStream()
        providers = [FakeProvider(alice_stream), FakeProvider(bob_stream)]
        received = []

        async def handle_failure(_error):
            return None

        def track_handler_factory(binding):
            async def handle_event(event):
                received.append(
                    (binding.participant_identity, binding.track_ref, event)
                )

            return handle_event

        run = RoomAudioRun(
            live_output=live_output(
                lambda: providers.pop(0),
                lambda: None,
                handle_failure,
                track_handler_factory,
            ),
        )
        await run.start()
        with patch(
            "room_audio_runtime.rtc.AudioStream.from_track",
            side_effect=[FakeSpeechStream(), FakeSpeechStream()],
        ):
            run.observe_track("TR_A", object(), "alice")
            run.observe_track("TR_B", object(), "bob")
        expires_at_ms = int(time.time() * 1_000) + 10_000
        for track_ref in ("TR_A", "TR_B"):
            run.admit_track(
                track_ref,
                generation=1,
                expires_at_ms=expires_at_ms,
                authorization_digest="a" * 64,
            )
        run._mixer.push_frame("TR_A", b"\xe8\x03" * 480)
        run._mixer.push_frame("TR_B", b"\xd0\x07" * 480)
        await wait_for_frames(bob_stream, 1)

        bob_stream.emit_event("bob-first")
        await asyncio.sleep(0)
        alice_stream.emit_event("alice-late")
        expected_event_count = 2
        for _ in range(100):
            if len(received) == expected_event_count:
                break
            await asyncio.sleep(0.002)
        await run.aclose()

        self.assertEqual(
            received,
            [
                ("bob", "TR_B", "bob-first"),
                ("alice", "TR_A", "alice-late"),
            ],
        )


if __name__ == "__main__":
    unittest.main()
