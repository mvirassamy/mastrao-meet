"""Execute pinned SDK retry/rotation loops with synthetic websocket transport."""

import asyncio
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import aiohttp
from livekit import rtc
from livekit.plugins import openai
from openai import AsyncOpenAI
from test_live_audio_timing import ProtocolSocket, completed
from test_live_send_control import Gateway, authority
from test_live_transcriber_stop import AudioSource, reached

from live_single_connection import AuthorizedOpenAI
from live_transcriber import TrackTranscriber


class SingleConnectionSDKTests(unittest.IsolatedAsyncioTestCase):
    """Prove physical connection counts using actual LiveKit OpenAI 1.8.3 code."""

    async def asyncSetUp(self):
        """Replace only transport and wall time; keep SDK stream machinery real."""
        client = await self.enterAsyncContext(AsyncOpenAI(api_key="fixture-key"))
        self.socket = ProtocolSocket()
        self.receiving = asyncio.Event()
        receive = self.socket.receive

        async def observed_receive():
            self.receiving.set()
            return await receive()

        self.socket.receive = observed_receive
        self.connector = self.enterContext(
            patch.object(openai.STT, "_connect_ws", AsyncMock(return_value=self.socket))
        )
        self.clock = Mock(return_value=1000)
        self.enterContext(
            patch("livekit.plugins.openai.stt.time", SimpleNamespace(time=self.clock))
        )
        self.gateway = Gateway()
        self.lease = authority(self.gateway)
        self.audio = AudioSource()
        self.enterContext(
            patch(
                "live_transcriber.rtc.AudioStream.from_track", return_value=self.audio
            )
        )
        self.provider = None

        def provider_factory(**kwargs):
            self.provider = AuthorizedOpenAI(client=client, **kwargs)
            return self.provider

        self.enterContext(
            patch("live_transcriber.AuthorizedOpenAI", side_effect=provider_factory)
        )
        self.publisher = SimpleNamespace(
            publish_segment=AsyncMock(), publish_gap=AsyncMock()
        )
        self.track = TrackTranscriber(
            track=object(),
            participant=SimpleNamespace(identity="fixture", sid="PA_participant"),
            track_sid="TR_track",
            publisher=self.publisher,
            vad=None,
            authority=self.lease,
        )
        self.run = None

    async def asyncTearDown(self):
        """Close and join even if an expected reconnect refusal fails to appear."""
        if self.run is not None:
            if not self.run.done():
                self.run.cancel()
            async with asyncio.timeout(2):
                await asyncio.gather(self.run, return_exceptions=True)
        if self.provider is not None:
            await self.provider.aclose()

    async def start_track(self):
        """Start through prepare/claim and wait for the first SDK receive loop."""
        self.run = asyncio.create_task(self.track.run())
        await reached(self.receiving)
        self.connector.assert_awaited_once()

    async def test_real_sdk_ten_minute_rotation_is_refused_with_explicit_gap(self):
        """The SDK's while-True rotation cannot acquire a second connection."""
        await self.start_track()
        self.clock.return_value = 1601
        self.socket.feed(completed("fixture-item", "synthetic final"))
        async with asyncio.timeout(2):
            result = await asyncio.gather(self.run, return_exceptions=True)
        self.assertIsInstance(result[0], BaseExceptionGroup)
        self.connector.assert_awaited_once()
        self.socket.close.assert_awaited_once()
        self.assertTrue(self.track._closing)
        self.assertIn(
            "canonical-provider-reconnect-refused", self.track.incomplete_reasons
        )
        self.publisher.publish_gap.assert_any_await(
            "canonical-provider-reconnect-refused"
        )
        self.publisher.publish_segment.assert_awaited_once()
        self.assertFalse(self.gateway.entered["stopped"].is_set())

    async def test_queued_sdk_audio_never_sends_after_expiry_fence(self):
        """Fence the real SDK sender with an admitted, ready queue of PCM frames."""
        await self.start_track()
        admitted = []

        async def observe_send(message):
            if message["type"] == "input_audio_buffer.append":
                admitted.append(self.lease.can_send())

        self.socket.send_json.side_effect = observe_send
        for _ in range(20):
            self.audio.frames.put_nowait(rtc.AudioFrame.create(24000, 1, 1200))
        async with asyncio.timeout(2):
            for _ in range(20):
                await self.audio.delivered.get()
        self.assertEqual(admitted, [])
        self.lease.deadline = time.monotonic() - 1
        await self.track.aclose()
        await self.run
        self.assertEqual(admitted.count(False), 0)
        self.connector.assert_awaited_once()

    async def test_fence_cancels_socket_write_before_held_io_can_resume(self):
        """Cancel the real SDK writer while its synthetic append awaits I/O."""
        await self.start_track()
        entered = asyncio.Event()
        cancelled = asyncio.Event()
        release = asyncio.Event()
        writes = []

        async def held_send(message):
            if message["type"] != "input_audio_buffer.append":
                return
            entered.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise
            writes.append(message)

        self.socket.send_json.side_effect = held_send
        self.audio.frames.put_nowait(rtc.AudioFrame.create(24000, 1, 1200))
        await reached(entered)
        self.track.fence_audio()
        await reached(cancelled)
        release.set()
        await self.track.aclose()
        await self.run
        self.assertEqual(writes, [])
        self.connector.assert_awaited_once()
        self.socket.close.assert_awaited_once()

    async def test_real_sdk_transport_failure_has_zero_retry_or_second_socket(self):
        """max_retry=0 also disables the separate outer API retry loop."""
        await self.start_track()
        self.socket.messages.put_nowait(
            aiohttp.WSMessage(aiohttp.WSMsgType.CLOSED, None, "")
        )
        async with asyncio.timeout(2):
            result = await asyncio.gather(self.run, return_exceptions=True)
        self.assertIsInstance(result[0], BaseExceptionGroup)
        self.connector.assert_awaited_once()
        self.assertFalse(self.lease.can_send())
        self.socket.close.assert_awaited_once()

    async def test_failed_first_handshake_is_sealed_before_io(self):
        """An uncertain first connection cannot be repeated through private API."""
        self.connector.side_effect = ConnectionError("synthetic failed handshake")
        refused = Mock()
        client = await self.enterAsyncContext(AsyncOpenAI(api_key="fixture-key"))
        provider = AuthorizedOpenAI(
            client=client,
            model="gpt-live-transcribe",
            use_realtime=True,
            vad=None,
            can_send=lambda: True,
            reconnect_refused=refused,
            failed=Mock(),
        )
        self.addAsyncCleanup(provider.aclose)
        with self.assertRaises(ConnectionError):
            await provider._connect_ws(1)
        with self.assertRaises(RuntimeError):
            await provider._connect_ws(1)
        self.connector.assert_awaited_once()
        refused.assert_called_once()

    async def test_sdk_version_change_fails_before_provider_construction(self):
        """A future SDK needs new source review and executed reconnect evidence."""
        with patch("live_single_connection.version", return_value="1.8.4"):
            with self.assertRaises(RuntimeError):
                AuthorizedOpenAI(
                    can_send=lambda: True, reconnect_refused=Mock(), failed=Mock()
                )
        self.connector.assert_not_awaited()
