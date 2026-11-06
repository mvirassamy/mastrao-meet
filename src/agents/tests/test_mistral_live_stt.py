"""Unit tests for the Mistral realtime adapter without network access."""

import asyncio
import base64
import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import aiohttp
from livekit import rtc

from live_transcript_event import LiveTranscriptEventKind
from mistral_live_stt import (
    _INPUT_END,
    DEFAULT_CONTEXT_BIAS,
    PROVIDER_CHUNK_BYTES,
    MistralRealtimeConfig,
    MistralRealtimeStream,
    MistralRealtimeSTT,
    configured_context_bias,
    load_config,
)


class FakeSocket:
    """Provide the WebSocket methods used by the adapter."""

    def __init__(self, *, handshake_events=None, messages=None):
        """Initialize deterministic provider events and sent payload capture."""
        self.handshake_events = list(handshake_events or [])
        self.messages = list(messages or [])
        self.sent: list[dict[str, object]] = []

    async def receive_json(self):
        """Return the next handshake event."""
        return self.handshake_events.pop(0)

    async def send_json(self, payload):
        """Capture one outgoing provider payload."""
        self.sent.append(payload)

    def __aiter__(self):
        """Return an async iterator over provider messages."""
        return self

    async def __anext__(self):
        """Yield one provider message or finish the stream."""
        if not self.messages:
            raise StopAsyncIteration
        return self.messages.pop(0)


def detached_stream(config: MistralRealtimeConfig) -> MistralRealtimeStream:
    """Build a stream without starting its network-owning background task."""
    stream = MistralRealtimeStream.__new__(MistralRealtimeStream)
    stream._config = config
    stream._input = asyncio.Queue()
    stream._output = asyncio.Queue()
    stream._closed = False
    stream._fully_closed = False
    stream._close_task = None
    stream._ready = asyncio.Event()
    stream._opening_error = None
    stream._sequence = 0
    stream._pending_text = ""
    stream._sent_chunk_count = 0
    stream._sent_byte_count = 0
    stream._non_silent_chunk_count = 0
    stream._provider_delta_count = 0
    return stream


class MistralRealtimeConfigurationTests(unittest.TestCase):
    """Verify fail-closed environment configuration."""

    def test_api_key_is_required(self):
        """Refuse to start a provider without its secret reference."""
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "MISTRAL_ASR_API_KEY"):
                load_config()

    def test_french_defaults_and_context_are_explicit(self):
        """Use the measured French configuration unless overridden."""
        with patch.dict(
            os.environ,
            {"MISTRAL_ASR_API_KEY": "test-key"},
            clear=True,
        ):
            config = load_config()

        self.assertEqual(config.language, "fr")
        self.assertEqual(config.streaming_delay_ms, 480)
        self.assertEqual(config.context_bias, DEFAULT_CONTEXT_BIAS)

    def test_context_bias_discards_empty_terms(self):
        """Avoid sending empty provider hints."""
        with patch.dict(
            os.environ,
            {"MISTRAL_STT_CONTEXT_BIAS": "Mastrao, ,LDS,Aurore"},
            clear=True,
        ):
            self.assertEqual(
                configured_context_bias(),
                ("Mastrao", "LDS", "Aurore"),
            )


class MistralRealtimeStreamTests(unittest.IsolatedAsyncioTestCase):
    """Verify provider protocol translation without external calls."""

    def setUp(self):
        """Create the same configuration used by staging qualification."""
        self.config = MistralRealtimeConfig(
            api_key="test-key",
            language="fr",
            streaming_delay_ms=480,
            context_bias=("Mastrao", "LDS"),
        )

    async def test_handshake_declares_pcm_and_french(self):
        """Configure the provider before accepting meeting audio."""
        stream = detached_stream(self.config)
        socket = FakeSocket(
            handshake_events=[
                {"type": "session.created"},
                {"type": "session.updated"},
            ]
        )

        await stream._configure(socket)

        session = socket.sent[0]["session"]
        self.assertEqual(session["audio_format"]["sample_rate"], 24_000)
        self.assertEqual(session["language"], "fr")
        self.assertEqual(session["context_bias"], ["Mastrao", "LDS"])

    async def test_append_only_deltas_preserve_words_split_by_provider(self):
        """Publish stable words without introducing spaces inside them."""
        stream = detached_stream(self.config)
        socket = FakeSocket(
            messages=[
                SimpleNamespace(
                    type=aiohttp.WSMsgType.TEXT,
                    data=json.dumps(
                        {"type": "transcription.text.delta", "text": "Aur"}
                    ),
                ),
                SimpleNamespace(
                    type=aiohttp.WSMsgType.TEXT,
                    data=json.dumps(
                        {"type": "transcription.text.delta", "text": "ore "}
                    ),
                ),
                SimpleNamespace(
                    type=aiohttp.WSMsgType.TEXT,
                    data=json.dumps(
                        {"type": "transcription.text.delta", "text": "arrive"}
                    ),
                ),
                SimpleNamespace(
                    type=aiohttp.WSMsgType.TEXT,
                    data=json.dumps({"type": "transcription.text.delta", "text": "."}),
                ),
                SimpleNamespace(
                    type=aiohttp.WSMsgType.TEXT,
                    data=json.dumps({"type": "transcription.done", "text": ""}),
                ),
            ]
        )

        await stream._receive_events(socket)

        first = stream._output.get_nowait()
        second = stream._output.get_nowait()
        self.assertEqual(first.kind, LiveTranscriptEventKind.COMPLETE)
        self.assertEqual(first.text, "Aurore")
        self.assertEqual(second.text, "arrive.")
        self.assertNotEqual(first.logical_unit_id, second.logical_unit_id)

    async def test_apostrophe_split_stays_inside_one_word(self):
        """Do not turn an append-only apostrophe split into two UI words."""
        stream = detached_stream(self.config)
        socket = FakeSocket(
            messages=[
                SimpleNamespace(
                    type=aiohttp.WSMsgType.TEXT,
                    data=json.dumps(
                        {"type": "transcription.text.delta", "text": "Aujourd'"}
                    ),
                ),
                SimpleNamespace(
                    type=aiohttp.WSMsgType.TEXT,
                    data=json.dumps(
                        {"type": "transcription.text.delta", "text": "hui "}
                    ),
                ),
                SimpleNamespace(
                    type=aiohttp.WSMsgType.TEXT,
                    data=json.dumps({"type": "transcription.done", "text": ""}),
                ),
            ]
        )

        await stream._receive_events(socket)

        event = stream._output.get_nowait()
        self.assertEqual(event.text, "Aujourd'hui")

    async def test_audio_frames_are_rejected_if_format_drifts(self):
        """Fail rather than silently sending an incompatible audio format."""
        stream = detached_stream(self.config)
        frame = rtc.AudioFrame(
            data=bytes(320),
            sample_rate=16_000,
            num_channels=1,
            samples_per_channel=160,
        )

        with self.assertRaisesRegex(ValueError, "24 kHz mono"):
            stream.push_frame(frame)

    async def test_audio_is_batched_into_100ms_provider_chunks(self):
        """Reduce WebSocket pressure without dropping a partial final chunk."""
        stream = detached_stream(self.config)
        socket = FakeSocket()
        frame = bytes(960)
        for _ in range(6):
            stream._input.put_nowait(frame)
        stream._input.put_nowait(_INPUT_END)

        await stream._send_audio(socket)

        appended = [
            base64.b64decode(payload["audio"])
            for payload in socket.sent
            if payload["type"] == "input_audio.append"
        ]
        self.assertEqual(
            [len(chunk) for chunk in appended],
            [PROVIDER_CHUNK_BYTES, 960],
        )
        self.assertEqual(socket.sent[-1], {"type": "input_audio.end"})

    def test_transport_health_is_content_free(self):
        """Expose only counts while proving that provider input is non-silent."""
        stream = detached_stream(self.config)
        chunk = bytes([0, 1]) * (PROVIDER_CHUNK_BYTES // 2)

        with self.assertLogs("room-audio-worker.mistral", level="INFO") as logs:
            for _ in range(100):
                stream._record_sent_chunk(chunk)

        self.assertIn("sent_chunks=100", logs.output[0])
        self.assertIn(f"sent_bytes={PROVIDER_CHUNK_BYTES * 100}", logs.output[0])
        self.assertIn("non_silent_chunks=100", logs.output[0])
        self.assertIn("provider_deltas=0 published_events=0", logs.output[0])

    async def test_unexpected_socket_close_is_an_error(self):
        """Surface a provider disconnect so the room receives a gap marker."""
        stream = detached_stream(self.config)

        with self.assertRaisesRegex(RuntimeError, "closed before completion"):
            await stream._receive_events(FakeSocket())

    async def test_provider_rate_limit_is_an_explicit_failure(self):
        """A provider 429 cannot become an empty successful transcript."""
        stream = detached_stream(self.config)
        socket = FakeSocket(
            messages=[
                SimpleNamespace(
                    type=aiohttp.WSMsgType.TEXT,
                    data=json.dumps(
                        {
                            "type": "error",
                            "error": {
                                "code": "rate_limit_exceeded",
                                "message": "too many requests",
                            },
                        }
                    ),
                )
            ]
        )

        with self.assertRaisesRegex(RuntimeError, "rate_limit_exceeded"):
            await stream._receive_events(socket)

    async def test_full_input_queue_makes_close_fail_closed(self):
        """A saturated sender cannot silently discard the terminal marker."""
        stream = detached_stream(self.config)
        stream._input = asyncio.Queue(maxsize=1)
        stream._input.put_nowait(bytes(2))
        stream._task = asyncio.create_task(asyncio.sleep(10))

        with (
            patch("mistral_live_stt.CLOSE_TIMEOUT_SECONDS", 0.001),
            self.assertRaisesRegex(RuntimeError, "input drain timed out"),
        ):
            await stream.finish_input_and_drain()

        self.assertTrue(stream._task.cancelled())

    async def test_provider_drain_timeout_is_not_reported_as_success(self):
        """A hung provider task produces an explicit terminal failure."""
        stream = detached_stream(self.config)
        stream._task = asyncio.create_task(asyncio.sleep(10))

        with (
            patch("mistral_live_stt.CLOSE_TIMEOUT_SECONDS", 0.001),
            self.assertRaisesRegex(RuntimeError, "provider drain timed out"),
        ):
            await stream.finish_input_and_drain()

        self.assertTrue(stream._task.cancelled())

    async def test_cancelled_close_cannot_report_success_with_live_network_task(self):
        """Provider close reuses the terminal future after caller cancellation."""
        stream = detached_stream(self.config)
        stream._input = asyncio.Queue(maxsize=1)
        stream._input.put_nowait(bytes(2))
        stream._task = asyncio.create_task(asyncio.sleep(10))
        provider = MistralRealtimeSTT.__new__(MistralRealtimeSTT)
        provider._config = self.config
        provider._streams = {stream}

        with patch("mistral_live_stt.CLOSE_TIMEOUT_SECONDS", 0.01):
            interrupted = asyncio.create_task(stream.finish_input_and_drain())
            await asyncio.sleep(0)
            interrupted.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await interrupted

            self.assertFalse(stream._task.done())
            with self.assertRaisesRegex(RuntimeError, "input drain timed out"):
                await provider.aclose()

        self.assertTrue(stream._task.cancelled())
        self.assertFalse(stream._fully_closed)


if __name__ == "__main__":
    unittest.main()
