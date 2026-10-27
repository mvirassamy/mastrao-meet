"""Unit tests for the Mistral realtime adapter without network access."""

import asyncio
import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import aiohttp
from livekit import rtc
from livekit.agents import stt

from mistral_live_stt import (
    DEFAULT_CONTEXT_BIAS,
    MistralRealtimeConfig,
    MistralRealtimeStream,
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
    stream._sequence = 0
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

    async def test_append_only_deltas_become_final_livekit_events(self):
        """Publish provider deltas once without false interim replacement."""
        stream = detached_stream(self.config)
        socket = FakeSocket(
            messages=[
                SimpleNamespace(
                    type=aiohttp.WSMsgType.TEXT,
                    data=json.dumps(
                        {"type": "transcription.text.delta", "text": "Bonjour"}
                    ),
                ),
                SimpleNamespace(
                    type=aiohttp.WSMsgType.TEXT,
                    data=json.dumps({"type": "transcription.text.delta", "text": ","}),
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
        self.assertEqual(first.type, stt.SpeechEventType.FINAL_TRANSCRIPT)
        self.assertEqual(first.alternatives[0].text, "Bonjour")
        self.assertEqual(second.alternatives[0].text, ",")
        self.assertNotEqual(first.request_id, second.request_id)

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

    async def test_unexpected_socket_close_is_an_error(self):
        """Surface a provider disconnect so the room receives a gap marker."""
        stream = detached_stream(self.config)

        with self.assertRaisesRegex(RuntimeError, "closed before completion"):
            await stream._receive_events(FakeSocket())


if __name__ == "__main__":
    unittest.main()
