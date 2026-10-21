"""Safety regressions for terminal failures racing an authorized SDK handoff."""

import asyncio
import unittest

import test_live_continuation as sessions
from live_continuation_fixtures import eventually, numbered_frame

from live_continuation import MAX_QUEUED_SAMPLES
from live_input_drain import InputDrainError

FRAME_SAMPLES = 480
HANDOFF_CAPACITY = 240_000
PROVIDER_FAILURE = "live-input-provider-terminal-error"
OVERFLOW = "live-continuation-buffer-capacity-exceeded"


class ContinuationFailureTests(unittest.IsolatedAsyncioTestCase):
    """Reuse fixture lifecycle only; discovery must not repeat its 14 tests."""

    asyncSetUp = sessions.ContinuationSessionTests.asyncSetUp
    asyncTearDown = sessions.ContinuationSessionTests.asyncTearDown
    wait_rotation = sessions.ContinuationSessionTests.wait_rotation
    feed = sessions.ContinuationSessionTests.feed

    async def test_late_raw_error_cannot_hide_in_sdk_unwind_and_open_successor(self):
        """F1: publication must not turn a cached drain into permission after error."""
        await self.assert_late_provider_failure(
            {"type": "error", "error": {"code": "fixture-late-error"}}
        )

    async def test_late_failed_transcription_cannot_hide_in_sdk_unwind(self):
        """F1: the failed-transcription boundary has the same terminal guarantee."""
        await self.assert_late_provider_failure(
            {
                "type": "conversation.item.input_audio_transcription.failed",
                "item_id": "item-2",
            }
        )

    async def assert_late_provider_failure(self, event):
        """Hold publication and the real SDK's asynchronous VAD unwind separately."""
        physical = self.sockets[0]
        provider = self.providers[0]
        physical.auto_complete = False
        publishing = asyncio.Event()
        publish_release = asyncio.Event()
        vad_closing = asyncio.Event()
        vad_release = asyncio.Event()

        async def publish(segment):
            publishing.set()
            await publish_release.wait()

        async def slow_vad_close():
            vad_closing.set()
            await vad_release.wait()

        self.publisher.publish_segment.side_effect = publish
        await eventually(lambda: bool(self.track._vad.streams))
        self.track._vad.streams[0].aclose = slow_vad_close
        try:
            await self.feed(1, 8)
            self.ticks.put_nowait(None)
            await asyncio.wait_for(physical.commit_requests.get(), 2)
            physical.complete_commit(1)
            await asyncio.wait_for(publishing.wait(), 2)
            await eventually(provider._drain_task.done)
            self.assertEqual(provider._drain_task.result(), frozenset({"item-1"}))
            physical.feed(event)
            # Immediate terminal fencing may cancel the SDK before VAD unwind
            # begins. Without that fence, hold its actual asynchronous unwind.
            await eventually(lambda: vad_closing.is_set() or self.track._closing)
            if vad_closing.is_set() and not self.track._closing:
                self.assertFalse(self.track._speech_stream._task.done())
            with self.assertRaisesRegex(InputDrainError, PROVIDER_FAILURE):
                provider._socket.drain.check()
            publish_release.set()
            # The bad implementation constructs the successor; a terminal one
            # starts shutdown instead. Neither needs the held SDK unwind to end.
            await eventually(
                lambda: (
                    len(self.providers) > 1 or self.track._closing or self.run.done()
                )
            )
            vad_release.set()
            if len(self.providers) > 1:
                # Join the forbidden successor on the red baseline, retaining
                # the recorded factory/rotate evidence for the safety assertions.
                self.track.fence_audio()
            result = await asyncio.wait_for(
                asyncio.gather(self.run, return_exceptions=True), 2
            )
            self.assertEqual(self.commands("rotate"), [])
            self.assertEqual(len(self.providers), 1)
            self.assertEqual(self.connector.await_count, 1)
            self.assertFalse(self.lease.can_send())
            self.assertIn(PROVIDER_FAILURE, self.track.incomplete_reasons)
            self.publisher.publish_gap.assert_any_await(PROVIDER_FAILURE)
            self.assertIsInstance(result[0], BaseExceptionGroup)
            await self.assert_shared_stop_failure()
            self.assertTrue(physical.closed)
        finally:
            publish_release.set()
            vad_release.set()

    async def test_overflow_during_committed_rotate_reply_cleans_exact_successor(self):
        """F2: real-capacity capture overflow fences before a valid reply can open."""
        self.assertEqual(MAX_QUEUED_SAMPLES, HANDOFF_CAPACITY)
        await self.feed(1, 8)
        original_post = self.gateway.post
        observed = {}

        async def post(body):
            response = await original_post(body)
            if body["action"] == "rotate":
                # Core has reserved the exact successor. Fill the real bounded
                # handoff queue before returning its otherwise valid receipt.
                frame_count = HANDOFF_CAPACITY // FRAME_SAMPLES + 1
                for number in range(8, 8 + frame_count):
                    self.audio.frames.put_nowait(numbered_frame(number))
                try:
                    await asyncio.sleep(0)
                except asyncio.CancelledError:
                    # The committed reply may already be readable when capture
                    # fences and cancels control I/O. Deliver that exact receipt.
                    pass
                observed.update(
                    response=response.copy(),
                    audio_done=self.track._audio_task.done(),
                    error=self.track._audio_task.exception(),
                    queued_samples=self.track._pcm_input._queued_samples,
                    can_send=self.lease.can_send(),
                )
            return response

        self.gateway.post = post
        self.ticks.put_nowait(None)
        result = await asyncio.wait_for(
            asyncio.gather(self.run, return_exceptions=True), 2
        )
        self.assertIn("audio_done", observed, self.track.incomplete_reasons)
        self.assertTrue(observed["audio_done"])
        self.assertIsInstance(observed["error"], InputDrainError)
        self.assertEqual(str(observed["error"]), OVERFLOW)
        self.assertEqual(observed["queued_samples"], HANDOFF_CAPACITY)
        self.assertEqual(len(self.providers), 1)
        self.assertEqual(len(self.sockets), 1)
        self.assertEqual(self.connector.await_count, 1)
        self.assertFalse(observed["can_send"])
        self.assertFalse(self.lease.can_send())
        self.assertIsInstance(result[0], BaseExceptionGroup)
        self.assertIn(OVERFLOW, self.track.incomplete_reasons)
        self.publisher.publish_gap.assert_any_await(OVERFLOW)
        rotations = self.commands("rotate")
        self.assertEqual(len(rotations), 1)
        rotation = rotations[0]
        receipt = observed["response"]
        self.assertTrue(receipt["start_authorized"])
        self.assertFalse(receipt["replayed"])
        self.assertEqual(receipt["leg_ref"], rotation["next_leg_ref"])
        self.assertEqual(receipt["audio_start_sample"], rotation["audio_end_sample"])
        self.assertTrue(self.sockets[0].closed)
        self.assertEqual(self.commands("stopped"), [])
        # Failed delivery must not automatically certify stopped. The retained
        # authority cleanup binding must nevertheless name the reserved successor.
        await self.lease.stopped()
        stopped = self.commands("stopped")
        self.assertEqual(len(stopped), 1)
        self.assertEqual(stopped[0]["leg_ref"], rotation["next_leg_ref"])
        self.assertEqual(len(self.providers), 1)
        self.assertEqual(self.connector.await_count, 1)
        self.assertEqual(len(self.commands("rotate")), 1)

    def commands(self, action):
        """Inspect recorded gateway commands without replacing authority logic."""
        return [body for body in self.gateway.requests if body["action"] == action]

    async def assert_shared_stop_failure(self):
        """Concurrent stop callers must retain the same failed delivery outcome."""
        results = await asyncio.gather(
            self.track.aclose(), self.track.aclose(), return_exceptions=True
        )
        self.assertIsInstance(results[0], BaseExceptionGroup)
        self.assertIs(results[0], results[1])
        with self.assertRaises(BaseExceptionGroup) as subsequent:
            await self.track.aclose()
        self.assertIs(subsequent.exception, results[0])
