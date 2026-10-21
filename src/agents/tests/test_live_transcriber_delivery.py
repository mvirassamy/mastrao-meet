"""Offline delivery evidence across publication stalls and SDK closure."""

import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from livekit import rtc
from livekit.plugins import openai
from openai import AsyncOpenAI
from test_live_audio_timing import ProtocolSocket, completed
from test_live_transcriber_stop import AudioSource, reached

from live_transcriber import (
    GAP_TOPIC,
    MODEL,
    TRANSCRIPTION_TOPIC,
    LiveTranscriber,
    TrackTranscriber,
    TranscriptionPublisher,
)


class PublicReadGate:
    """Hold a public SDK read before or after it acquires a final."""

    def __init__(self, stream, *, before_read):
        """Gate the first read following final a without using SDK internals."""
        self.stream = stream
        self.before_read = before_read
        self.after_a = False
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    def __aiter__(self):
        """Keep the wrapper as the public event iterator."""
        return self

    async def __anext__(self):
        """Delay either the read following a or delivery of final b."""
        if self.before_read and self.after_a:
            self.entered.set()
            await self.release.wait()
        event = await self.stream.__anext__()
        if event.request_id == "a":
            self.after_a = True
        if not self.before_read and event.request_id == "b":
            self.entered.set()
            await self.release.wait()
        return event

    def push_frame(self, frame):
        """Delegate admitted audio through the SDK's public method."""
        self.stream.push_frame(frame)

    async def aclose(self):
        """Close only through the real public SDK method."""
        await self.stream.aclose()


class LiveTranscriptionDeliveryTests(unittest.IsolatedAsyncioTestCase):
    """Run the locked SDK with only its websocket transport replaced."""

    async def asyncSetUp(self):
        """Prepare deterministic gates and a real, offline OpenAI plugin."""
        self.socket = ProtocolSocket()
        self.processed = asyncio.Event()
        self.closed = asyncio.Event()
        receive = self.socket.receive
        calls = 0
        received_pair = 3

        async def observed_receive():
            nonlocal calls
            calls += 1
            if calls == received_pair:
                self.processed.set()
            return await receive()

        self.socket.receive = observed_receive
        self.socket.close.side_effect = self.closed.set
        client = await self.enterAsyncContext(AsyncOpenAI(api_key="test-key"))
        self.connector = AsyncMock(return_value=self.socket)
        # The plugin has no public transport injection; this seam is test-only.
        with patch.object(openai.STT, "_connect_ws", self.connector):
            self.provider = openai.STT(
                client=client, model=MODEL, use_realtime=True, vad=None
            )
        self.addAsyncCleanup(self.provider.aclose)
        self.audio = AudioSource()
        self.publishing = asyncio.Event()
        self.release = asyncio.Event()
        self.published = []

        async def publish(event):
            if event.item_id == "a":
                self.publishing.set()
                await self.release.wait()
            self.published.append(event.item_id)

        self.publisher = SimpleNamespace(
            publish_segment=AsyncMock(side_effect=publish), publish_gap=AsyncMock()
        )
        self.track = TrackTranscriber(
            track=object(),
            participant=SimpleNamespace(identity="alice"),
            track_sid="TR_A",
            publisher=self.publisher,
            vad=None,
        )
        self.enterContext(
            patch("live_transcriber.create_openai_live_stt", return_value=self.provider)
        )
        self.enterContext(
            patch(
                "live_transcriber.rtc.AudioStream.from_track", return_value=self.audio
            )
        )
        self.tasks = []
        self.releases = [self.release]

    async def asyncTearDown(self):
        """Release every gate and join tasks even on a regression."""
        for gate in self.releases:
            gate.set()
        for task in self.tasks:
            if not task.done():
                task.cancel()
        async with asyncio.timeout(2):
            await asyncio.gather(*self.tasks, return_exceptions=True)

    def spawn(self, coroutine):
        """Keep ownership of all test tasks through cleanup."""
        task = asyncio.create_task(coroutine)
        self.tasks.append(task)
        return task

    async def blocked_pair(self):
        """Reproduce exactly: publishing a blocks while the SDK emits b."""
        run = self.spawn(self.track.run())
        self.socket.feed(completed("a", "premier"))
        await reached(self.publishing)
        self.socket.feed(completed("b", "second"))
        await reached(self.processed)
        return run

    async def test_stop_retains_b_already_emitted_while_a_publication_blocks(self):
        """The exact preexisting witness must publish a then b before stop."""
        run = await self.blocked_pair()
        close = self.spawn(self.track.aclose())
        await reached(self.closed)
        self.assertFalse(close.done())
        self.release.set()
        async with asyncio.timeout(2):
            await asyncio.gather(run, close)
        self.assertEqual(self.published, ["a", "b"])
        self.assertIn(
            "sdk-close-final-drain-unconfirmed", self.track.incomplete_reasons
        )
        self.publisher.publish_gap.assert_awaited_once_with(
            "sdk-close-final-drain-unconfirmed"
        )
        self.connector.assert_awaited_once()
        self.socket.close.assert_awaited_once()
        message_types = [
            call.args[0]["type"] for call in self.socket.send_json.await_args_list
        ]
        self.assertNotIn("input_audio_buffer.commit", message_types)
        self.assertNotIn("input_audio_buffer.append", message_types)

    async def test_actual_transport_envelopes_keep_a_b_identity_order_and_gap(self):
        """Observe real room payloads without claiming receipt by durable storage."""
        messages = []

        async def send_text(payload, *, topic, **kwargs):
            envelope = json.loads(payload)
            if topic == TRANSCRIPTION_TOPIC and envelope["itemId"] == "a":
                self.publishing.set()
                await self.release.wait()
            messages.append((topic, envelope))

        room = SimpleNamespace(local_participant=SimpleNamespace(send_text=send_text))
        self.track._publisher = TranscriptionPublisher(room)
        run = await self.blocked_pair()
        close = self.spawn(self.track.aclose())
        await reached(self.closed)
        self.release.set()
        async with asyncio.timeout(2):
            await asyncio.gather(run, close)
        self.assertEqual(
            [topic for topic, _ in messages],
            [TRANSCRIPTION_TOPIC, TRANSCRIPTION_TOPIC, GAP_TOPIC],
        )
        finals = [envelope for _, envelope in messages[:2]]
        self.assertEqual([event["itemId"] for event in finals], ["a", "b"])
        self.assertEqual([event["text"] for event in finals], ["premier", "second"])
        self.assertEqual([event["sequence"] for event in finals], [1, 2])
        for event in finals:
            self.assertEqual(event["revision"], 1)
            self.assertEqual(event["state"], "final")
            self.assertEqual(event["legId"], self.track.leg_id)
            self.assertEqual(event["trackSid"], "TR_A")
            self.assertEqual(event["participantIdentity"], "alice")
        self.assertEqual(messages[-1][1]["reason"], "sdk-close-final-drain-unconfirmed")

    def gate_public_read(self, *, before_read):
        """Wrap the real stream without replacing recognition or closure."""
        gate = PublicReadGate(self.provider.stream(), before_read=before_read)
        self.enterContext(patch.object(self.provider, "stream", return_value=gate))
        self.releases.append(gate.release)
        return gate

    async def test_sdk_close_can_discard_an_unread_final_but_reports_uncertainty(self):
        """Demonstrate the precise public-API limit, rather than claim recovery."""
        gate = self.gate_public_read(before_read=True)
        run = await self.blocked_pair()
        await reached(gate.entered)
        close = self.spawn(self.track.aclose())
        await reached(self.closed)
        self.release.set()
        self.assertFalse(close.done())
        gate.release.set()
        async with asyncio.timeout(2):
            await asyncio.gather(run, close)
        self.assertEqual(self.published, ["a"])
        self.assertEqual(
            self.track.incomplete_reasons, ("sdk-close-final-drain-unconfirmed",)
        )
        self.publisher.publish_gap.assert_awaited_once_with(
            "sdk-close-final-drain-unconfirmed"
        )
        self.connector.assert_awaited_once()

    async def test_final_returning_from_public_read_during_close_is_retained(self):
        """A read owning b before SDK close still delivers it after close starts."""
        gate = self.gate_public_read(before_read=False)
        run = await self.blocked_pair()
        await reached(gate.entered)
        close = self.spawn(self.track.aclose())
        await reached(self.closed)
        self.assertFalse(close.done())
        gate.release.set()
        self.release.set()
        async with asyncio.timeout(2):
            await asyncio.gather(run, close)
        self.assertEqual(self.published, ["a", "b"])
        self.connector.assert_awaited_once()

    async def close_results(self, run):
        """Collect two concurrent stops and bound the entire delivery barrier."""
        first = self.spawn(self.track.aclose())
        second = self.spawn(self.track.aclose())
        async with asyncio.timeout(2):
            return await asyncio.gather(run, first, second, return_exceptions=True)

    async def assert_shared_failure(self, results, reason):
        """Every stop waiter must receive the cached error and local evidence."""
        self.assertIsInstance(results[0], BaseExceptionGroup)
        self.assertIsInstance(results[1], BaseExceptionGroup)
        self.assertIs(results[1], results[2])
        with self.assertRaises(BaseExceptionGroup) as later:
            await self.track.aclose()
        self.assertIs(later.exception, results[1])
        self.assertIn(reason, self.track.incomplete_reasons)
        self.assertTrue(self.track._reader_task.done())
        self.assertTrue(self.track._publication_task.done())
        self.connector.assert_awaited_once()

    async def test_publication_failure_is_shared_by_all_stop_callers(self):
        """No success is returned after an accepted final fails publication."""
        run = await self.blocked_pair()

        async def fail(event):
            raise RuntimeError("private transcript from an upstream exception")

        self.publisher.publish_segment.side_effect = fail
        # The in-flight a keeps its original side effect; b will fail.
        self.release.set()
        with self.assertLogs("transcriber.live", level="WARNING") as logs:
            results = await self.close_results(run)
        await self.assert_shared_failure(results, "publication-failed")
        self.assertEqual(self.published, ["a"])
        self.publisher.publish_gap.assert_any_await("publication-failed")
        self.assertNotIn("private transcript", "\n".join(logs.output))

    async def test_cancelled_publication_is_incomplete_for_all_stop_callers(self):
        """Cancellation of the publisher itself cannot be mistaken for delivery."""
        self.publisher.publish_segment.side_effect = asyncio.CancelledError()
        run = self.spawn(self.track.run())
        self.socket.feed(completed("a", "premier"))
        await reached(self.closed)
        results = await self.close_results(run)
        await self.assert_shared_failure(results, "publication-failed")
        self.assertEqual(self.published, [])
        self.publisher.publish_gap.assert_any_await("publication-failed")

    async def test_saturation_drains_admitted_finals_and_reports_loss(self):
        """The bounded queue never evicts a final or silently drops overflow."""
        with patch("live_transcriber.MAX_PENDING_EVENTS_PER_TRACK", 1):
            run = await self.blocked_pair()
            self.socket.feed(completed("c", "troisième"))
            await reached(self.closed)
            self.assertFalse(run.done())
            self.release.set()
            results = await self.close_results(run)
        await self.assert_shared_failure(results, "publication-capacity-exceeded")
        self.assertEqual(self.published, ["a", "b"])
        self.publisher.publish_gap.assert_any_await("publication-capacity-exceeded")

    async def test_failed_gap_publication_cannot_become_successful_stop(self):
        """Local incomplete state and shared failure survive transport failure."""
        run = await self.blocked_pair()
        self.publisher.publish_gap.side_effect = RuntimeError("private gap error")
        self.release.set()
        with self.assertLogs("transcriber.live", level="WARNING") as logs:
            results = await self.close_results(run)
        await self.assert_shared_failure(results, "sdk-close-final-drain-unconfirmed")
        self.assertEqual(self.published, ["a", "b"])
        self.assertNotIn("private gap error", "\n".join(logs.output))
        self.publisher.publish_gap.assert_awaited_once()

    async def test_cancelled_stop_waiter_still_joins_both_finals(self):
        """Repeated cancellation cannot interrupt the collector or other closes."""
        run = await self.blocked_pair()
        first = self.spawn(self.track.aclose())
        await reached(self.closed)
        first.cancel()
        second = self.spawn(self.track.aclose())
        first.cancel()
        self.assertFalse(second.done())
        self.assertFalse(first.done())
        self.release.set()
        async with asyncio.timeout(2):
            results = await asyncio.gather(run, first, second, return_exceptions=True)
        self.assertIsNone(results[0])
        self.assertIsInstance(results[1], asyncio.CancelledError)
        self.assertIsNone(results[2])
        self.assertEqual(self.published, ["a", "b"])

    async def test_run_cancellation_during_finally_keeps_delivery_barrier(self):
        """Cancel the run repeatedly while a is blocked and b already emitted."""
        run = await self.blocked_pair()
        run.cancel()
        await reached(self.closed)
        run.cancel()
        close = self.spawn(self.track.aclose())
        run.cancel()
        self.assertFalse(close.done())
        self.release.set()
        async with asyncio.timeout(2):
            results = await asyncio.gather(run, close, return_exceptions=True)
        self.assertIsInstance(results[0], asyncio.CancelledError)
        self.assertIsNone(results[1])
        self.assertEqual(self.published, ["a", "b"])
        self.assertTrue(self.track._reader_task.done())
        self.assertTrue(self.track._publication_task.done())

    async def test_failed_sdk_close_terminates_a_blocked_public_reader(self):
        """A close failure joins a stalled iterator and publications, without reopen."""
        gate = self.gate_public_read(before_read=True)
        run = await self.blocked_pair()
        await reached(gate.entered)
        original_close = gate.aclose

        async def fail_close():
            await original_close()
            raise RuntimeError("private SDK error")

        gate.aclose = fail_close
        self.release.set()
        with self.assertLogs("transcriber.live", level="WARNING") as logs:
            results = await self.close_results(run)
        await self.assert_shared_failure(results, "sdk-close-failed")
        self.assertFalse(gate.release.is_set())
        self.assertEqual(self.published, ["a"])
        self.assertNotIn("private SDK error", "\n".join(logs.output))
        self.publisher.publish_gap.assert_any_await("sdk-close-failed")

    async def test_real_manager_callbacks_keep_failure_private_and_prevent_reopen(self):
        """Exercise both actual task callbacks, not only sanitized direct logging."""
        room = SimpleNamespace(
            local_participant=SimpleNamespace(send_text=AsyncMock()),
            off=MagicMock(),
            remote_participants={},
        )
        manager = LiveTranscriber(
            SimpleNamespace(room=room, proc=SimpleNamespace(userdata={"vad": object()}))
        )
        manager._publisher = self.publisher
        private_text = "private transcript from an upstream exception"
        self.publisher.publish_segment.side_effect = RuntimeError(private_text)
        participant = SimpleNamespace(identity="alice")
        track = SimpleNamespace(kind=rtc.TrackKind.KIND_AUDIO)
        key = ("alice", "TR_A")
        with self.assertLogs(level="WARNING") as logs:
            await manager._ensure_session(track, "TR_A", participant)
            self.track = manager._sessions[key]
            run = manager._session_tasks[key]
            self.tasks.append(run)
            finished = asyncio.Event()
            run.add_done_callback(lambda _: finished.set())
            self.socket.feed(completed("a", "premier"))
            await reached(finished)
            self.assertIsInstance(run.exception(), ExceptionGroup)
            self.assertIn(private_text, repr(run.exception()))
            self.assertEqual(manager._session_task_set, set())
            self.assertIs(manager._sessions[key], self.track)

            with patch("live_transcriber.TrackTranscriber") as replacement:
                manager.on_track_subscribed(
                    track, SimpleNamespace(sid="TR_A"), participant
                )
                callbacks = tuple(manager._tasks)
                self.assertEqual(len(callbacks), 1)
                async with asyncio.timeout(2):
                    results = await asyncio.gather(*callbacks, return_exceptions=True)
                self.assertIsInstance(results[0], ExceptionGroup)
                replacement.assert_not_called()
            self.assertEqual(manager._tasks, set())
            with self.assertRaises(ExceptionGroup) as first:
                await manager.aclose()
            with self.assertRaises(ExceptionGroup) as second:
                await manager.aclose()
            self.assertIs(first.exception, second.exception)
            self.assertIs(manager._sessions[key], self.track)
        self.assertNotIn(private_text, "\n".join(logs.output))
        self.assertTrue(all(record.exc_info is None for record in logs.records))
        callback_logs = [
            record for record in logs.records if "background work failed" in record.msg
        ]
        self.assertEqual(len(callback_logs), 2)
        self.connector.assert_awaited_once()
