"""Execute graceful input drain through real LiveKit/OpenAI 1.8.3 SDK loops."""

import asyncio
import unittest
from unittest.mock import AsyncMock, Mock, patch

import aiohttp
from live_continuation_fixtures import (
    FixtureVAD,
    NumberedSocket,
    committed,
    eventually,
    numbered_frame,
)
from livekit.agents import stt
from livekit.plugins import openai
from openai import AsyncOpenAI
from test_live_audio_timing import completed

from live_input_drain import InputDrainError
from live_single_connection import AuthorizedOpenAI


class SDKInputDrainTests(unittest.IsolatedAsyncioTestCase):
    """Replace only the physical transport and VAD inference, never SDK loops."""

    async def asyncSetUp(self):
        """Forbid network calls and retain one real, single-acquisition provider."""
        self.network = self.enterContext(
            patch.object(
                aiohttp.ClientSession,
                "ws_connect",
                side_effect=AssertionError("external network forbidden"),
            )
        )
        self.socket = NumberedSocket()
        self.connector = self.enterContext(
            patch.object(openai.STT, "_connect_ws", AsyncMock(return_value=self.socket))
        )
        client = await self.enterAsyncContext(AsyncOpenAI(api_key="fixture-key"))
        self.allowed = True
        self.vad = FixtureVAD()
        self.provider = AuthorizedOpenAI(
            client=client,
            model="gpt-live-transcribe",
            language=["fr", "en"],
            use_realtime=True,
            vad=self.vad,
            can_send=lambda: self.allowed,
            reconnect_refused=Mock(),
            failed=Mock(),
        )
        self.stream = self.provider.stream()
        self.finals = []
        self.reader = asyncio.create_task(self.read())
        await eventually(lambda: self.provider._socket is not None)
        self.tasks = []

    async def asyncTearDown(self):
        """Release held I/O and join all SDK work even after an assertion fails."""
        self.socket.append_release.set()
        self.socket.close_release.set()
        await self.stream.aclose()
        await self.provider.aclose()
        await asyncio.gather(self.reader, *self.tasks, return_exceptions=True)
        self.network.assert_not_called()

    async def read(self):
        """Observe the public SDK iterator alongside raw ACK bookkeeping."""
        async for event in self.stream:
            if event.type == stt.SpeechEventType.FINAL_TRANSCRIPT:
                self.finals.append(event.request_id)

    def spawn(self, operation):
        """Keep ownership of concurrent drain callers through fixture cleanup."""
        task = asyncio.create_task(operation)
        self.tasks.append(task)
        return task

    def push(self, first, end):
        """Admit numbered 20-ms PCM frames with a deliberately partial 50-ms tail."""
        for number in range(first, end):
            self.stream.push_frame(numbered_frame(number))

    async def test_flush_and_end_input_keep_receive_alive_and_send_pcm_exactly_once(
        self,
    ):
        """Multiple flushes never replay the SDK's partial append buffer."""
        self.push(1, 8)
        self.stream.flush()
        self.stream.flush()
        self.provider.seal_input(self.stream, 3360)
        await eventually(lambda: len(self.socket.samples) == 7 * 480)
        self.assertEqual(self.socket.chunks, [1200, 1200, 960])
        self.assertEqual(
            self.socket.samples, [n for n in range(1, 8) for _ in range(480)]
        )
        self.assertFalse(self.stream._task.done())
        self.assertEqual(self.socket.commit_count, 0)
        drain = self.spawn(self.provider.drain_input())
        await self.socket.commit_requests.get()
        self.socket.feed(committed("tail"))
        await asyncio.sleep(0)
        self.assertFalse(drain.done())
        self.socket.feed(completed("tail", "synthetic final"))
        self.assertEqual(await drain, frozenset({"tail"}))
        self.assertFalse(self.stream._task.done())
        self.connector.assert_awaited_once()

    async def test_every_ack_and_out_of_order_final_including_empty_are_required(self):
        """A successor final cannot hide a pending predecessor completion."""
        self.push(1, 6)
        await eventually(lambda: len(self.socket.samples) == 5 * 480)
        await self.provider._socket.send_json({"type": "input_audio_buffer.commit"})
        await self.socket.commit_requests.get()
        self.push(6, 11)
        self.provider.seal_input(self.stream, 4800)
        drain = self.spawn(self.provider.drain_input())
        await self.socket.commit_requests.get()
        self.socket.feed(
            committed("one"),
            committed("two", "one"),
            committed("two", "one"),
            completed("two", "second"),
        )
        await eventually(lambda: self.finals == ["two"])
        self.assertFalse(drain.done())
        self.socket.feed(completed("one", ""))
        self.assertEqual(await drain, frozenset({"two"}))

    async def test_final_before_ack_does_not_complete_drain(self):
        """Keep raw item and ACK sets independent of server event arrival order."""
        self.push(1, 6)
        self.provider.seal_input(self.stream, 2400)
        drain = self.spawn(self.provider.drain_input())
        await self.socket.commit_requests.get()
        self.socket.feed(completed("tail", "tail"))
        await eventually(lambda: self.finals == ["tail"])
        self.assertFalse(drain.done())
        self.socket.feed(committed("tail"))
        self.assertEqual(await drain, frozenset({"tail"}))

    async def test_buffer_bounds_follow_commit_chain_not_ack_or_final_order(self):
        """Real SDK appends bind two distinct input windows through reordered ACKs."""
        self.push(1, 6)
        first_commit_samples = 2400
        await eventually(lambda: len(self.socket.samples) == first_commit_samples)
        await self.provider._socket.send_json({"type": "input_audio_buffer.commit"})
        await self.socket.commit_requests.get()
        self.push(6, 8)
        self.provider.seal_input(self.stream, 3360)
        drain = self.spawn(self.provider.drain_input())
        await self.socket.commit_requests.get()
        self.socket.feed(completed("two", "second"), committed("two", "one"))
        await eventually(lambda: self.finals == ["two"])
        second = self.spawn(self.provider.input_window("two"))
        await asyncio.sleep(0)
        self.assertFalse(second.done())
        self.socket.feed(committed("one"), completed("one", ""))
        self.assertEqual(await second, (2400, 3360))
        self.assertEqual(await self.provider.input_window("one"), (0, 2400))
        self.assertEqual(await drain, frozenset({"two"}))
        self.assertEqual(self.socket.chunks, [1200, 1200, 960])
        self.connector.assert_awaited_once()

    async def test_client_vad_keeps_silence_inside_the_committed_buffer(self):
        """The input window includes quiet PCM and does not claim speech onset."""
        self.stream.push_frame(numbered_frame(0, 1200))
        self.stream.push_frame(numbered_frame(7, 1200))
        self.provider.seal_input(self.stream, 2400)
        drain = self.spawn(self.provider.drain_input())
        await self.socket.commit_requests.get()
        self.socket.complete_commit(1, text="speech after silence")
        self.assertEqual(await drain, frozenset({"item-1"}))
        self.assertEqual(await self.provider.input_window("item-1"), (0, 2400))
        self.assertEqual(self.socket.samples, [0] * 1200 + [7] * 1200)

    async def test_unknown_buffer_bounds_timeout_without_fabricated_evidence(self):
        """An unacknowledged real final gets no received-time or count fallback."""
        self.push(1, 6)
        first_commit_samples = 2400
        await eventually(lambda: len(self.socket.samples) == first_commit_samples)
        await self.provider._socket.send_json({"type": "input_audio_buffer.commit"})
        self.socket.feed(completed("unknown", "final before missing ACK"))
        await eventually(lambda: self.finals == ["unknown"])
        with patch("live_single_connection.DRAIN_TIMEOUT_SECONDS", 0.01):
            self.assertIsNone(await self.provider.input_window("unknown"))
        self.assertTrue(self.allowed)
        self.connector.assert_awaited_once()

    async def test_early_ack_and_final_wait_for_underlying_commit_send_success(self):
        """Real receive can race a held send without certifying its reserved window."""
        self.push(1, 6)
        expected_samples = 2400
        await eventually(lambda: len(self.socket.samples) == expected_samples)
        entered = asyncio.Event()
        release = asyncio.Event()

        async def held_send(message):
            await self.socket.send(message)
            if message["type"] == "input_audio_buffer.commit":
                self.socket.complete_commit(1)
                entered.set()
                await release.wait()

        self.socket.send_json.side_effect = held_send
        commit = self.spawn(
            self.provider._socket.send_json({"type": "input_audio_buffer.commit"})
        )
        try:
            await entered.wait()
            await eventually(lambda: self.finals == ["item-1"])
            proof = self.spawn(self.provider.input_window("item-1"))
            self.provider.seal_input(self.stream, expected_samples)
            drain = self.spawn(self.provider.drain_input())
            await asyncio.sleep(0)
            self.assertFalse(proof.done())
            self.assertFalse(drain.done())
            release.set()
            await commit
            self.assertEqual(await proof, (0, expected_samples))
            self.assertEqual(await drain, frozenset({"item-1"}))
        finally:
            release.set()

    async def test_one_sample_tail_after_recent_commit_is_sent_exactly_once(self):
        """Synthetic acceptance proves framing, not the real provider's minimum."""
        await self.assert_tiny_tail(1)

    async def test_2399_sample_tail_after_recent_commit_is_sent_exactly_once(self):
        """A partial append below 100 ms is neither padded nor replayed."""
        await self.assert_tiny_tail(2399)

    async def test_rejected_tiny_tail_is_terminal_without_retry(self):
        """An unqualified provider minimum cannot silently lose the tiny tail."""
        await self.assert_tiny_tail(1, rejected=True)

    async def assert_tiny_tail(self, samples, *, rejected=False):
        """Commit a preceding turn before sealing a genuinely tiny new remainder."""
        self.push(1, 6)
        preceding_samples = 5 * 480
        await eventually(lambda: len(self.socket.samples) == preceding_samples)
        await self.provider._socket.send_json({"type": "input_audio_buffer.commit"})
        await self.socket.commit_requests.get()
        self.socket.complete_commit(1)
        await eventually(lambda: self.finals == ["item-1"])
        self.stream.push_frame(numbered_frame(6, samples))
        self.provider.seal_input(self.stream, preceding_samples + samples)
        drain = self.spawn(self.provider.drain_input())
        await self.socket.commit_requests.get()
        self.assertEqual(
            self.socket.samples,
            [n for n in range(1, 6) for _ in range(480)] + [6] * samples,
        )
        self.assertEqual(self.socket.commit_count, 2)
        if rejected:
            self.socket.feed({"type": "error", "error": {"code": "fixture-minimum"}})
            with self.assertRaises(InputDrainError):
                await drain
            with self.assertRaises(InputDrainError):
                await self.provider.drain_input()
        else:
            self.socket.complete_commit(2)
            self.assertEqual(await drain, frozenset({"item-1", "item-2"}))
        self.connector.assert_awaited_once()

    async def test_seal_suppresses_sdk_commits_queued_behind_blocked_append(self):
        """Check sealing after the write lock, not before waiting to acquire it."""
        await eventually(lambda: bool(self.vad.streams))
        self.vad.streams[0].commit_on_frame = True
        self.socket.append_release.clear()
        self.push(1, 8)
        await self.socket.append_entered.wait()
        queued_commit = self.spawn(
            self.provider._socket.send_json({"type": "input_audio_buffer.commit"})
        )
        await asyncio.sleep(0)
        self.provider.seal_input(self.stream, 3360)
        drain = self.spawn(self.provider.drain_input())
        self.socket.append_release.set()
        await self.socket.commit_requests.get()
        await queued_commit
        self.assertEqual(len(self.socket.samples), 3360)
        self.assertEqual(self.socket.types[-1], "input_audio_buffer.commit")
        self.assertEqual(self.socket.commit_count, 1)
        self.socket.complete_commit(1)
        self.assertEqual(await drain, frozenset({"item-1"}))

    async def test_missing_ack_is_bounded_and_same_failure_is_shared(self):
        """No caller can reinterpret a timed-out drain as a later success."""
        self.push(1, 6)
        self.provider.seal_input(self.stream, 2400)
        with patch("live_single_connection.DRAIN_TIMEOUT_SECONDS", 0.02):
            first = self.spawn(self.provider.drain_input())
            second = self.spawn(self.provider.drain_input())
            results = await asyncio.gather(first, second, return_exceptions=True)
        self.assertIsInstance(results[0], TimeoutError)
        self.assertIs(results[0], results[1])
        with self.assertRaises(TimeoutError) as later:
            await self.provider.drain_input()
        self.assertIs(later.exception, results[0])
        self.connector.assert_awaited_once()

    async def test_failed_transcription_is_terminal_and_not_an_empty_success(self):
        """The SDK ignores raw failed events; the socket boundary must reject them."""
        await self.assert_provider_failure(
            {
                "type": "conversation.item.input_audio_transcription.failed",
                "item_id": "tail",
            }
        )

    async def test_raw_provider_error_fails_drain_without_another_connection(self):
        """A remote error never becomes authorization for a replacement socket."""
        await self.assert_provider_failure(
            {"type": "error", "error": {"code": "fixture"}}
        )

    async def assert_provider_failure(self, message):
        """Feed a terminal raw failure while the real receive loop is draining."""
        self.push(1, 6)
        self.provider.seal_input(self.stream, 2400)
        first = self.spawn(self.provider.drain_input())
        second = self.spawn(self.provider.drain_input())
        await self.socket.commit_requests.get()
        self.socket.feed(message)
        results = await asyncio.gather(first, second, return_exceptions=True)
        self.assertIsInstance(results[0], InputDrainError)
        self.assertIs(results[0], results[1])
        self.connector.assert_awaited_once()

    async def test_fence_cancels_pending_append_and_never_flushes_or_commits(self):
        """Planned drain cannot bypass a later lease revocation at the socket."""
        self.socket.append_release.clear()
        self.push(1, 8)
        await self.socket.append_entered.wait()
        self.provider.seal_input(self.stream, 3360)
        drain = self.spawn(self.provider.drain_input())
        self.allowed = False
        self.provider.fence_audio()
        self.socket.append_release.set()
        with self.assertRaises(InputDrainError):
            await drain
        self.assertEqual(self.socket.samples, [])
        self.assertEqual(self.socket.commit_count, 0)

    async def test_misleading_sample_count_cannot_certify_a_cut(self):
        """Reject a smaller input watermark than PCM already written to the socket."""
        self.push(1, 6)
        await eventually(lambda: len(self.socket.samples) == 5 * 480)
        with self.assertRaises(InputDrainError):
            self.provider.seal_input(self.stream, 1200)

    async def test_inconsistent_ack_chain_prevents_success(self):
        """An unresolved predecessor is evidence of an unconfirmed drain."""
        self.push(1, 6)
        self.provider.seal_input(self.stream, 2400)
        drain = self.spawn(self.provider.drain_input())
        await self.socket.commit_requests.get()
        self.socket.feed(committed("tail", "unknown"), completed("tail", "tail"))
        with self.assertRaises(InputDrainError):
            await drain
