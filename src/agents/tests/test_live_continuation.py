"""Full controlled sessions across accelerated planned 540-second rotations."""

import asyncio
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import aiohttp
from live_continuation_fixtures import (
    FixtureVAD,
    NumberedSocket,
    committed,
    eventually,
    numbered_frame,
)
from livekit.plugins import openai
from openai import AsyncOpenAI
from test_live_audio_timing import completed
from test_live_send_control import Gateway, authority
from test_live_transcriber_stop import AudioSource

from live_send_control import CONTROL_VERSION, ROTATION_SECONDS, ControlResponseLost
from live_single_connection import AuthorizedOpenAI
from live_transcriber import TrackTranscriber


class ContinuationSessionTests(unittest.IsolatedAsyncioTestCase):
    """Use the production lifecycle, authority, SDK loops and publication path."""

    async def asyncSetUp(self):
        """Accelerate only the planned timer; keep lease watchdogs independent."""
        self.network = self.enterContext(
            patch.object(
                aiohttp.ClientSession,
                "ws_connect",
                side_effect=AssertionError("external network forbidden"),
            )
        )
        self.gateway = Gateway()
        self.lease = authority(self.gateway)
        self.audio = AudioSource()
        self.enterContext(
            patch(
                "live_transcriber.rtc.AudioStream.from_track", return_value=self.audio
            )
        )
        self.rotation_timers = asyncio.Queue()
        self.ticks = asyncio.Queue()
        self.enterContext(patch("live_continuation.wait_rotation", self.wait_rotation))
        self.sockets = []
        self.providers = []
        self.factory_leg_refs = []
        self.auto_complete = True
        client = await self.enterAsyncContext(AsyncOpenAI(api_key="fixture-key"))

        async def connect(timeout):
            socket = NumberedSocket(auto_complete=self.auto_complete)
            self.sockets.append(socket)
            return socket

        self.connector = self.enterContext(
            patch.object(openai.STT, "_connect_ws", AsyncMock(side_effect=connect))
        )

        def factory(**kwargs):
            if self.sockets:
                self.assertTrue(self.sockets[-1].closed)
            provider = AuthorizedOpenAI(client=client, **kwargs)
            self.providers.append(provider)
            self.factory_leg_refs.append(self.lease.leg_ref)
            return provider

        self.factory = self.enterContext(
            patch("live_transcriber.AuthorizedOpenAI", side_effect=factory)
        )
        self.published = []

        async def publish(event):
            self.published.append((event.leg_id, event.item_id, event.revision))

        self.publisher = SimpleNamespace(
            publish_segment=AsyncMock(side_effect=publish), publish_gap=AsyncMock()
        )
        self.track = TrackTranscriber(
            track=object(),
            participant=SimpleNamespace(identity="fixture", sid="PA_participant"),
            track_sid="TR_track",
            publisher=self.publisher,
            vad=FixtureVAD(),
            authority=self.lease,
        )
        self.run = asyncio.create_task(self.track.run())
        await eventually(lambda: len(self.sockets) == 1)
        await self.rotation_timers.get()

    async def asyncTearDown(self):
        """Hard-stop the current leg and release blocked local fixture work."""
        for gate in self.gateway.release.values():
            gate.set()
        self.audio.stop_release.set()
        for socket in self.sockets:
            socket.append_release.set()
            socket.close_release.set()
        if not self.run.done():
            self.track.fence_audio()
        async with asyncio.timeout(3):
            await asyncio.gather(self.track.aclose(), self.run, return_exceptions=True)
        self.network.assert_not_called()

    async def wait_rotation(self, deadline):
        """Expose each actual 540-second deadline as a deterministic test gate."""
        remaining = deadline - time.monotonic()
        self.assertGreater(remaining, ROTATION_SECONDS - 2)
        self.assertLessEqual(remaining, ROTATION_SECONDS)
        self.rotation_timers.put_nowait(deadline)
        await self.ticks.get()

    async def feed(self, first, end):
        """Wait until the single RTC reader has admitted every numbered frame."""
        for number in range(first, end):
            self.audio.frames.put_nowait(numbered_frame(number))
        await eventually(
            lambda: self.track._pcm_input.captured_samples >= (end - 1) * 480
        )

    async def rotate(self):
        """Complete one accelerated timer and wait for the successor's timer."""
        self.ticks.put_nowait(None)
        await asyncio.wait_for(self.rotation_timers.get(), 2)

    async def test_multiple_540_second_rotations_preserve_exact_socket_pcm_and_leg_ids(
        self,
    ):
        """Three handoffs have disjoint PCM, separate grants, and no socket overlap."""
        self.assertEqual(ROTATION_SECONDS, 540)
        for first, end in ((1, 8), (8, 16), (16, 25)):
            await self.feed(first, end)
            await self.rotate()
        self.assertEqual(len(self.sockets), 4)
        self.assertEqual(self.factory.call_count, 4)
        self.assertEqual(self.connector.await_count, 4)
        self.assertEqual(len(set(self.factory_leg_refs)), 4)
        flattened = [sample for socket in self.sockets for sample in socket.samples]
        self.assertEqual(flattened, [n for n in range(1, 25) for _ in range(480)])
        self.assertEqual([len(s.samples) for s in self.sockets], [3360, 3840, 4320, 0])
        self.assertEqual(
            self.published,
            [(leg, "item-1", 1) for leg in self.factory_leg_refs[:3]],
        )
        self.assertEqual(self.track.incomplete_reasons, ())
        rotations = [b for b in self.gateway.requests if b["action"] == "rotate"]
        self.assertEqual(
            [b["audio_end_sample"] for b in rotations], [3360, 7200, 11520]
        )
        self.assertTrue(
            all(b["version"] == CONTROL_VERSION for b in self.gateway.requests)
        )
        self.assertEqual([b["leg_ref"] for b in rotations], self.factory_leg_refs[:3])
        for index, provider in enumerate(self.providers[:3]):
            self.assertFalse(provider._can_send())
            self.assertTrue(self.sockets[index].closed)
        self.assertTrue(self.providers[-1]._can_send())

    async def test_tail_ack_final_publication_and_socket_close_all_precede_rotate(self):
        """Capture new unsent PCM while every old-leg barrier is held in turn."""
        socket = self.sockets[0]
        socket.auto_complete = False
        socket.close_release.clear()
        publishing, release = asyncio.Event(), asyncio.Event()

        async def publish(event):
            publishing.set()
            await release.wait()
            self.published.append((event.leg_id, event.item_id, event.revision))

        self.publisher.publish_segment.side_effect = publish
        await self.feed(1, 8)
        self.ticks.put_nowait(None)
        await socket.commit_requests.get()
        await self.feed(8, 16)
        self.assertEqual(self.factory.call_count, 1)
        self.assertFalse(self.gateway.entered["rotate"].is_set())
        socket.feed(committed("tail"))
        await asyncio.sleep(0)
        self.assertFalse(socket.close_entered.is_set())
        socket.feed(completed("tail", "synthetic tail"))
        try:
            await asyncio.wait_for(publishing.wait(), 2)
            self.assertFalse(socket.close_entered.is_set())
            self.assertFalse(self.gateway.entered["rotate"].is_set())
            release.set()
            await asyncio.wait_for(socket.close_entered.wait(), 2)
            self.assertEqual(self.factory.call_count, 1)
            self.assertFalse(self.gateway.entered["rotate"].is_set())
            socket.close_release.set()
            await asyncio.wait_for(self.rotation_timers.get(), 2)
            await eventually(lambda: bool(self.sockets[1].samples))
            self.assertEqual(
                socket.samples, [n for n in range(1, 8) for _ in range(480)]
            )
            # Flush the new leg in a second real SDK rotation to observe its tail.
            await self.rotate()
            self.assertEqual(
                self.sockets[1].samples, [n for n in range(8, 16) for _ in range(480)]
            )
        finally:
            release.set()
            socket.close_release.set()

    async def test_lost_rotate_response_is_terminal_and_cannot_open_or_retry(self):
        """Only the exact unknown successor may be cleaned up after transport loss."""
        self.gateway.errors["rotate"] = ControlResponseLost("synthetic lost response")
        await self.feed(1, 8)
        self.ticks.put_nowait(None)
        result = await asyncio.wait_for(
            asyncio.gather(self.run, return_exceptions=True), 2
        )
        self.assertIsInstance(result[0], BaseExceptionGroup)
        self.assertEqual(self.factory.call_count, 1)
        self.assertFalse(self.lease.can_send())
        rotations = [b for b in self.gateway.requests if b["action"] == "rotate"]
        self.assertEqual(len(rotations), 1)
        self.assertTrue(self.sockets[0].closed)
        self.assertFalse(self.gateway.entered["stopped"].is_set())

    async def test_wrong_successor_or_offset_never_constructs_another_provider(self):
        """A malformed coherent-shaped reply cannot authorize continuation."""
        self.gateway.responses["rotate"] = {"audio_start_sample": 123}
        await self.feed(1, 8)
        self.ticks.put_nowait(None)
        result = await asyncio.wait_for(
            asyncio.gather(self.run, return_exceptions=True), 2
        )
        self.assertIsInstance(result[0], BaseExceptionGroup)
        self.assertEqual(self.factory.call_count, 1)
        self.assertFalse(self.lease.can_send())

    async def test_no_ack_drain_timeout_prevents_rotate(self):
        """A known close of an unconfirmed tail cannot reserve another leg."""
        self.sockets[0].auto_complete = False
        await self.feed(1, 8)
        with patch("live_single_connection.DRAIN_TIMEOUT_SECONDS", 0.02):
            self.ticks.put_nowait(None)
            result = await asyncio.wait_for(
                asyncio.gather(self.run, return_exceptions=True), 2
            )
        self.assertIsInstance(result[0], BaseExceptionGroup)
        self.assertEqual(self.factory.call_count, 1)
        self.assertFalse(self.gateway.entered["rotate"].is_set())

    async def test_queue_overflow_is_explicit_and_never_opens_another_leg(self):
        """Held old finals cannot cause unbounded retention or silent PCM eviction."""
        self.sockets[0].auto_complete = False
        await self.feed(1, 8)
        self.ticks.put_nowait(None)
        await self.sockets[0].commit_requests.get()
        with patch("live_continuation.MAX_QUEUED_SAMPLES", 480):
            self.audio.frames.put_nowait(numbered_frame(8))
            self.audio.frames.put_nowait(numbered_frame(9))
            result = await asyncio.wait_for(
                asyncio.gather(self.run, return_exceptions=True), 2
            )
        self.assertIsInstance(result[0], BaseExceptionGroup)
        self.assertIn("audio-consumption-failed", self.track.incomplete_reasons)
        self.assertFalse(self.gateway.entered["rotate"].is_set())
        self.assertEqual(self.factory.call_count, 1)

    async def test_lease_loss_while_draining_never_commits_more_or_rotates(self):
        """The existing independent watcher still hard-fences a planned drain."""
        socket = self.sockets[0]
        socket.append_release.clear()
        await self.feed(1, 8)
        await socket.append_entered.wait()
        self.ticks.put_nowait(None)
        await eventually(
            lambda: self.providers[0]._socket.drain.expected_samples is not None
        )
        self.lease.deadline = time.monotonic() - 1
        self.track.fence_audio()
        socket.append_release.set()
        await asyncio.wait_for(asyncio.gather(self.run, return_exceptions=True), 2)
        self.assertEqual(socket.samples, [])
        self.assertEqual(socket.commit_count, 0)
        self.assertFalse(self.gateway.entered["rotate"].is_set())
        self.assertEqual(self.factory.call_count, 1)

    async def test_publication_failure_prevents_rotate_and_is_shared_by_stoppers(self):
        """A published-final barrier must propagate failures to every stop caller."""
        self.publisher.publish_segment.side_effect = RuntimeError("fixture publication")
        await self.feed(1, 8)
        self.ticks.put_nowait(None)
        await asyncio.wait_for(asyncio.gather(self.run, return_exceptions=True), 2)
        results = await asyncio.gather(
            self.track.aclose(), self.track.aclose(), return_exceptions=True
        )
        self.assertIsInstance(results[0], BaseExceptionGroup)
        self.assertIs(results[0], results[1])
        self.assertFalse(self.gateway.entered["rotate"].is_set())
        self.assertEqual(self.factory.call_count, 1)

    async def test_empty_final_allows_rotation_without_inventing_a_publication(self):
        """A terminal empty transcript resolves the ACK but emits no SDK final."""
        socket = self.sockets[0]
        socket.auto_complete = False
        await self.feed(1, 8)
        self.ticks.put_nowait(None)
        await socket.commit_requests.get()
        socket.complete_commit(1, text="")
        await asyncio.wait_for(self.rotation_timers.get(), 2)
        self.assertEqual(self.published, [])
        self.assertEqual(self.factory.call_count, 2)
        self.assertTrue(socket.closed)
        self.assertEqual(self.track.incomplete_reasons, ())

    async def test_failed_raw_final_prevents_any_core_rotate(self):
        """A raw failure the SDK would ignore still terminates the whole session."""
        socket = self.sockets[0]
        socket.auto_complete = False
        await self.feed(1, 8)
        self.ticks.put_nowait(None)
        await socket.commit_requests.get()
        socket.feed(
            {
                "type": "conversation.item.input_audio_transcription.failed",
                "item_id": "item-1",
            }
        )
        result = await asyncio.wait_for(
            asyncio.gather(self.run, return_exceptions=True), 2
        )
        self.assertIsInstance(result[0], BaseExceptionGroup)
        self.assertEqual(self.factory.call_count, 1)
        self.assertFalse(self.gateway.entered["rotate"].is_set())

    async def test_failed_socket_close_cannot_be_swallowed_by_sdk_pool(self):
        """ConnectionPool swallows close exceptions; the worker must retain them."""
        self.sockets[0].close.side_effect = RuntimeError("fixture socket close")
        await self.feed(1, 8)
        self.ticks.put_nowait(None)
        result = await asyncio.wait_for(
            asyncio.gather(self.run, return_exceptions=True), 2
        )
        self.assertIsInstance(result[0], BaseExceptionGroup)
        self.assertFalse(self.sockets[0].closed)
        self.assertEqual(self.factory.call_count, 1)
        self.assertFalse(self.gateway.entered["rotate"].is_set())
        self.assertFalse(self.gateway.entered["stopped"].is_set())
        self.sockets[0].close.assert_awaited_once()

    async def test_cancelled_handoff_joins_slow_close_and_never_rotates(self):
        """Concurrent hard stops share a close already started by planned drain."""
        socket = self.sockets[0]
        socket.close_release.clear()
        await self.feed(1, 8)
        self.ticks.put_nowait(None)
        await asyncio.wait_for(socket.close_entered.wait(), 2)
        first = asyncio.create_task(self.track.aclose())
        second = asyncio.create_task(self.track.aclose())
        try:
            await asyncio.sleep(0)
            first.cancel()
            await asyncio.sleep(0)
            self.assertFalse(second.done())
            self.assertFalse(self.gateway.entered["rotate"].is_set())
            socket.close_release.set()
            results = await asyncio.wait_for(
                asyncio.gather(first, second, self.run, return_exceptions=True), 2
            )
            self.assertIsInstance(results[0], asyncio.CancelledError)
            self.assertIsNone(results[1])
            self.assertTrue(socket.closed)
            self.assertEqual(self.factory.call_count, 1)
            self.assertFalse(self.gateway.entered["rotate"].is_set())
        finally:
            socket.close_release.set()
            await asyncio.gather(first, second, return_exceptions=True)

    async def test_renew_held_at_cut_cannot_be_overtaken_by_rotate(self):
        """Audio drain/close can finish while serialized Core renew is pending."""
        self.gateway.release["renew"].clear()
        renew = asyncio.create_task(self.lease._send("renew"))
        await self.gateway.entered["renew"].wait()
        await self.feed(1, 8)
        self.ticks.put_nowait(None)
        try:
            await eventually(lambda: self.sockets[0].closed)
            self.assertFalse(self.gateway.entered["rotate"].is_set())
            self.assertEqual(self.factory.call_count, 1)
            self.gateway.release["renew"].set()
            await renew
            await asyncio.wait_for(self.rotation_timers.get(), 2)
            self.assertEqual(self.factory.call_count, 2)
        finally:
            self.gateway.release["renew"].set()
            await asyncio.gather(renew, return_exceptions=True)

    async def test_sdk_admission_is_bounded_while_a_socket_append_is_stalled(self):
        """The SDK input channel cannot accumulate an unlimited PCM backlog."""
        socket = self.sockets[0]
        socket.append_release.clear()
        with patch("live_continuation.MAX_QUEUED_SAMPLES", 2400):
            for number in range(1, 8):
                self.audio.frames.put_nowait(numbered_frame(number))
            result = await asyncio.wait_for(
                asyncio.gather(self.run, return_exceptions=True), 2
            )
        self.assertIsInstance(result[0], BaseExceptionGroup)
        self.assertIn(
            "live-continuation-sdk-buffer-capacity-exceeded",
            self.track.incomplete_reasons,
        )
        self.assertFalse(self.gateway.entered["rotate"].is_set())
        self.assertEqual(self.factory.call_count, 1)
