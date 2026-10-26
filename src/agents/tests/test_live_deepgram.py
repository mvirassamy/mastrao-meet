"""Real pinned SDK identity, terminal drain and cancellation at the socket seam."""

import asyncio
import unittest
from unittest.mock import Mock, patch

import aiohttp
from live_continuation_fixtures import eventually, numbered_frame
from live_deepgram_fixtures import (
    DeepgramSocket,
    metadata_frame,
    result_frame,
    session_for,
)
from livekit.agents import APIConnectionError

from live_deepgram import AuthorizedDeepgram
from live_input_drain import InputDrainError
from live_provider_session import application_item_ref


class DeepgramSDKTests(unittest.IsolatedAsyncioTestCase):
    """No SDK parser, sender, receive loop, or generic iterator is replaced."""

    async def asyncSetUp(self):
        """Asyncsetup through the synthetic SDK fixture."""
        self.socket = DeepgramSocket()
        self.session = session_for(self.socket)
        self.failed = Mock()
        self.reconnect = Mock()
        self.authorized = True
        self.provider = AuthorizedDeepgram(
            api_key="fixture-unused-key",
            http_session=self.session,
            can_send=lambda: self.authorized,
            reconnect_refused=self.reconnect,
            failed=self.failed,
            model="nova-3",
            language="multi",
            sample_rate=24000,
            interim_results=True,
            punctuate=True,
            smart_format=False,
            endpointing_ms=25,
            filler_words=True,
            mip_opt_out=True,
        )
        self.stream = self.provider.stream()
        self.events = []

        async def collect():
            """Collect through the synthetic SDK fixture."""
            async for event in self.stream:
                self.events.append(event)

        self.reader = asyncio.create_task(collect())
        await eventually(lambda: self.provider._socket is not None)

    async def asyncTearDown(self):
        """Asyncteardown through the synthetic SDK fixture."""
        self.socket.write_release.set()
        self.socket.control_release.set()
        self.socket.close_release.set()
        self.provider.fence_audio()
        async with asyncio.timeout(2):
            await self.provider.aclose()
            await asyncio.gather(self.reader, return_exceptions=True)

    async def finish(self, samples):
        """Finish through the synthetic SDK fixture."""
        self.provider.seal_input(self.stream, samples)
        expected = await self.provider.drain_input()
        await self.reader
        return expected

    async def test_native_span_identity_and_revisions(self):
        """Distinct starts survive; duration and word corrections retain one item."""
        first = result_frame()
        correction = result_frame(duration=0.06, text="corrected first")
        # A later native word-only correction must survive too.
        word_correction = result_frame(duration=0.06, text="corrected first")
        word_correction["channel"]["alternatives"][0]["words"][0]["confidence"] = 0.8
        self.socket.terminal_frames = [
            first,
            result_frame(0.05, 0.07, "second final"),
            correction,
            word_correction,
            word_correction,
            metadata_frame(),
        ]
        for number in range(1, 7):
            self.stream.push_frame(numbered_frame(number))
        expected = await self.finish(2880)
        finals = [
            event for event in self.events if event.type.value == "final_transcript"
        ]
        self.assertEqual(len(finals), 5)
        refs = [application_item_ref(event) for event in finals]
        self.assertEqual(refs[0], refs[2])
        self.assertEqual(refs[2:], [refs[0]] * 3)
        self.assertNotEqual(refs[0], refs[1])
        self.assertEqual(expected, frozenset(refs))
        self.assertEqual(len({event.request_id for event in finals}), 1)
        self.assertNotIn(finals[0].request_id, expected)
        self.assertEqual(
            finals[0].alternatives[0].metadata["native_provenance"]["results"], first
        )
        self.assertEqual(self.provider.terminal_metadata, metadata_frame())
        self.assertTrue(self.provider.terminal_drain_complete(self.stream))
        self.assertIsNone(await self.provider.input_window(refs[0]))
        self.assertEqual(
            self.socket.samples, [n for n in range(1, 7) for _ in range(480)]
        )
        self.assertEqual(self.socket.chunks, [1200, 1200, 480])
        self.assertEqual(self.socket.controls.count("Finalize"), 1)
        self.assertEqual(self.socket.controls.count("CloseStream"), 1)
        self.assertTrue(self.socket.closed)
        self.session.ws_connect.assert_awaited_once()
        self.failed.assert_not_called()

    async def test_stop_cancels_blocked_pcm_and_queued_audio_without_finalize(self):
        """Stop cancels blocked pcm and queued audio without finalize."""
        self.socket.write_release.clear()
        for number in range(1, 9):
            self.stream.push_frame(numbered_frame(number))
        await self.socket.write_entered.wait()
        self.provider.fence_audio()
        self.socket.write_release.set()
        await self.provider.aclose()
        self.assertEqual(self.socket.samples, [])
        self.assertNotIn("Finalize", self.socket.controls)
        self.assertNotIn("CloseStream", self.socket.controls)
        self.session.ws_connect.assert_awaited_once()

    async def test_revoked_control_write_and_waiting_writer_never_reach_socket(self):
        """Revoked control write and waiting writer never reach socket."""
        self.socket.control_release.clear()
        self.provider.seal_input(self.stream, 0)
        await self.socket.control_entered.wait()
        queued = asyncio.create_task(
            self.provider._socket.send_str('{"type":"KeepAlive"}')
        )
        await asyncio.sleep(0)
        self.authorized = False
        self.provider.fence_audio()
        self.socket.control_release.set()
        results = await asyncio.gather(queued, return_exceptions=True)
        self.assertIsInstance(results[0], BaseException)
        await self.provider.aclose()
        self.assertNotIn("Finalize", self.socket.controls)
        self.assertNotIn("CloseStream", self.socket.controls)

    async def test_missing_terminal_metadata_refuses_drain_even_with_finals_and_close(
        self,
    ):
        """Missing terminal metadata refuses drain even with finals and close."""
        self.socket.terminal_frames = [result_frame()]
        with self.assertRaises(InputDrainError):
            await self.finish(0)
        self.assertIn(
            "deepgram-terminal-close-unproved", self.failed.call_args_list[0].args
        )
        self.assertEqual(self.session.ws_connect.await_count, 1)

    async def test_metadata_alone_is_not_a_close_or_a_pcm_ack(self):
        """Metadata alone is not a close or a pcm ack."""
        self.socket.auto_terminal = False
        self.provider.seal_input(self.stream, 0)
        await eventually(lambda: "CloseStream" in self.socket.controls)
        self.socket.feed(metadata_frame())
        drain = asyncio.create_task(self.provider.drain_input())
        await eventually(lambda: self.provider.terminal_metadata is not None)
        self.assertFalse(drain.done())
        self.assertIsNone(await self.provider.input_window("invented-item"))
        self.socket.peer_close()
        self.assertEqual(await drain, frozenset())
        await self.reader

    async def test_unexpected_close_or_error_never_reconnects(self):
        """Unexpected close or error never reconnects."""
        self.socket.peer_close(1001)
        result = (await asyncio.gather(self.reader, return_exceptions=True))[0]
        self.assertIsInstance(result, InputDrainError)
        self.assertEqual(self.session.ws_connect.await_count, 1)
        with self.assertRaises(InputDrainError):
            self.provider.stream()

    async def test_malformed_native_frame_is_terminal_not_silently_swallowed(self):
        """Malformed native frame is terminal not silently swallowed."""
        self.socket.messages.put_nowait(
            aiohttp.WSMessage(aiohttp.WSMsgType.TEXT, "{", "")
        )
        result = (await asyncio.gather(self.reader, return_exceptions=True))[0]
        self.assertIsInstance(result, InputDrainError)
        self.failed.assert_called_once()

    async def test_terminal_drain_timeout_is_bounded_without_metadata(self):
        """Terminal drain timeout is bounded without metadata."""
        self.socket.auto_terminal = False
        self.provider.seal_input(self.stream, 0)
        with patch("live_deepgram.DRAIN_TIMEOUT_SECONDS", 0.01):
            with self.assertRaises(TimeoutError):
                await self.provider.drain_input()
        self.failed.assert_called_once_with("deepgram-input-drain-incomplete")

    async def test_empty_finalized_tail_preserves_metadata_without_an_item(self):
        """Empty Finalize Results add no transcript or fabricated acknowledgement."""
        empty = result_frame(0.12, 0.0, "")
        empty["channel"]["alternatives"][0]["words"] = []
        empty["from_finalize"] = True
        self.socket.terminal_frames = [empty, metadata_frame()]
        self.assertEqual(await self.finish(0), frozenset())
        self.assertFalse(any(event.alternatives for event in self.events))
        self.assertEqual(self.provider.terminal_metadata, metadata_frame())
        self.assertTrue(self.provider.terminal_drain_complete(self.stream))

    async def test_metadata_cannot_complete_an_active_or_finalize_only_stream(self):
        """Metadata belongs to planned CloseStream, not a live stream ACK chain."""
        self.socket.feed(metadata_frame())
        result = (await asyncio.gather(self.reader, return_exceptions=True))[0]
        self.assertIsInstance(result, InputDrainError)
        self.failed.assert_called_once_with("deepgram-metadata-outside-graceful-close")
        self.assertIsNone(self.provider.terminal_metadata)

    async def test_sdk_completion_before_terminal_metadata_refuses_drain(self):
        """A cancelled real SDK task cannot certify a planned terminal drain."""
        self.socket.auto_terminal = False
        self.provider.seal_input(self.stream, 0)
        await eventually(lambda: "CloseStream" in self.socket.controls)
        self.stream._task.cancel()
        await asyncio.gather(self.stream._task, return_exceptions=True)
        with self.assertRaises(asyncio.CancelledError):
            await self.provider.drain_input()
        self.assertFalse(self.provider._terminal_complete)
        self.failed.assert_called_once_with("deepgram-input-drain-incomplete")

    async def test_internal_option_rotation_cannot_acquire_another_socket(self):
        """The SDK's internal reconnect loop still meets once-only acquisition."""
        self.stream.update_options(language="en")
        result = (await asyncio.gather(self.reader, return_exceptions=True))[0]
        self.assertIsInstance(result, InputDrainError)
        self.reconnect.assert_called_once_with()
        self.session.ws_connect.assert_awaited_once()

    async def test_failed_initial_handshake_is_never_retried(self):
        """max_retry=0 prevents an ambiguous websocket handshake from retrying."""
        session = session_for(DeepgramSocket())
        session.ws_connect.side_effect = aiohttp.ClientConnectionError("fixture")
        provider = AuthorizedDeepgram(
            api_key="fixture-unused-key",
            http_session=session,
            can_send=lambda: True,
            reconnect_refused=Mock(),
            failed=Mock(),
        )
        stream = provider.stream()
        try:
            with self.assertRaises(APIConnectionError):
                async with asyncio.timeout(2):
                    async for _ in stream:
                        pass
            session.ws_connect.assert_awaited_once()
            with self.assertRaises(InputDrainError):
                provider.stream()
        finally:
            await provider.aclose()

    async def test_unreadable_native_result_fences_before_sdk_can_swallow_it(self):
        """The SDK's catch-and-log parser path cannot hide invalid native content."""
        broken = result_frame()
        broken["channel"]["alternatives"][0]["transcript"] = ["invalid type"]
        self.socket.feed(broken)
        result = (await asyncio.gather(self.reader, return_exceptions=True))[0]
        self.assertIsInstance(result, InputDrainError)
        self.failed.assert_called_once_with("deepgram-sdk-event-unreadable")
