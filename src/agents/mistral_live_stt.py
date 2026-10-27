"""Mistral realtime speech-to-text adapter for LiveKit audio frames."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import os
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Final

import aiohttp
from livekit import rtc
from livekit.agents import LanguageCode, stt

MODEL: Final = "voxtral-mini-transcribe-realtime-3"
DEFAULT_LANGUAGE: Final = "fr"
DEFAULT_STREAMING_DELAY_MS: Final = 480
MIN_STREAMING_DELAY_MS: Final = 80
MAX_STREAMING_DELAY_MS: Final = 2_400
DEFAULT_CONTEXT_BIAS: Final = ("Mastrao", "LDS")
REALTIME_URL: Final = "wss://api.mistral.ai/v1/audio/transcriptions/realtime"
SAMPLE_RATE: Final = 24_000
INPUT_QUEUE_SIZE: Final = 200
OUTPUT_QUEUE_SIZE: Final = 200
CLOSE_TIMEOUT_SECONDS: Final = 10

_INPUT_END = object()
_OUTPUT_END = object()


@dataclass(frozen=True)
class MistralRealtimeConfig:
    """Validated configuration for one realtime transcription session."""

    api_key: str
    language: str
    streaming_delay_ms: int
    context_bias: tuple[str, ...]


def configured_context_bias() -> tuple[str, ...]:
    """Return non-empty context hints from the environment."""
    configured = os.getenv("MISTRAL_STT_CONTEXT_BIAS")
    if configured is None:
        return DEFAULT_CONTEXT_BIAS
    return tuple(value.strip() for value in configured.split(",") if value.strip())


def load_config() -> MistralRealtimeConfig:
    """Read and validate the Mistral realtime configuration."""
    api_key = os.getenv("MISTRAL_ASR_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError(
            "MISTRAL_ASR_API_KEY is required for STT_PROVIDER=mistral-live"
        )
    delay = int(
        os.getenv("MISTRAL_STT_STREAMING_DELAY_MS", str(DEFAULT_STREAMING_DELAY_MS))
    )
    if not MIN_STREAMING_DELAY_MS <= delay <= MAX_STREAMING_DELAY_MS:
        raise ValueError("MISTRAL_STT_STREAMING_DELAY_MS must be between 80 and 2400")
    language = os.getenv("MISTRAL_STT_LANGUAGE", DEFAULT_LANGUAGE).strip()
    if not language:
        raise ValueError("MISTRAL_STT_LANGUAGE must not be empty")
    return MistralRealtimeConfig(
        api_key=api_key,
        language=language,
        streaming_delay_ms=delay,
        context_bias=configured_context_bias(),
    )


class MistralRealtimeStream(AsyncIterator[stt.SpeechEvent]):
    """Bridge one LiveKit audio track to one Mistral WebSocket session."""

    def __init__(self, config: MistralRealtimeConfig):
        """Start the bounded input and output queues for one audio track."""
        self._config = config
        self._input: asyncio.Queue[bytes | object] = asyncio.Queue(
            maxsize=INPUT_QUEUE_SIZE
        )
        self._output: asyncio.Queue[stt.SpeechEvent | Exception | object] = (
            asyncio.Queue(maxsize=OUTPUT_QUEUE_SIZE)
        )
        self._task = asyncio.create_task(self._run())
        self._closed = False
        self._sequence = 0
        self._pending_text = ""

    def push_frame(self, frame: rtc.AudioFrame) -> None:
        """Queue one PCM frame without hiding backpressure failures."""
        if self._closed:
            return
        if frame.sample_rate != SAMPLE_RATE or frame.num_channels != 1:
            raise ValueError("Mistral realtime audio must be 24 kHz mono PCM")
        try:
            self._input.put_nowait(bytes(frame.data))
        except asyncio.QueueFull as error:
            raise RuntimeError("Mistral realtime audio queue is full") from error

    def __aiter__(self) -> MistralRealtimeStream:
        """Return this stream as its asynchronous iterator."""
        return self

    async def __anext__(self) -> stt.SpeechEvent:
        """Yield one final append-only transcript delta."""
        item = await self._output.get()
        if item is _OUTPUT_END:
            raise StopAsyncIteration
        if isinstance(item, Exception):
            raise item
        return item

    async def aclose(self) -> None:
        """Finish buffered audio and close the session once."""
        if self._closed:
            return
        self._closed = True
        with contextlib.suppress(asyncio.QueueFull):
            self._input.put_nowait(_INPUT_END)
        try:
            await asyncio.wait_for(self._task, timeout=CLOSE_TIMEOUT_SECONDS)
        except TimeoutError:
            self._task.cancel()
            with contextlib.suppress(BaseException):
                await self._task

    async def _run(self) -> None:
        """Own the HTTP session and coordinate sender and receiver tasks."""
        headers = {"Authorization": f"Bearer {self._config.api_key}"}
        url = f"{REALTIME_URL}?model={MODEL}"
        try:
            async with aiohttp.ClientSession(headers=headers) as session:
                async with session.ws_connect(url) as socket:
                    await self._configure(socket)
                    async with asyncio.TaskGroup() as tasks:
                        tasks.create_task(self._send_audio(socket))
                        tasks.create_task(self._receive_events(socket))
        except asyncio.CancelledError:
            raise
        except Exception as error:
            await self._output.put(error)
        finally:
            await self._output.put(_OUTPUT_END)

    async def _configure(self, socket: aiohttp.ClientWebSocketResponse) -> None:
        """Validate the handshake before any meeting audio is sent."""
        created = await socket.receive_json()
        self._expect_event(created, "session.created")
        session_config: dict[str, object] = {
            "audio_format": {
                "encoding": "pcm_s16le",
                "sample_rate": SAMPLE_RATE,
            },
            "target_streaming_delay_ms": self._config.streaming_delay_ms,
            "language": self._config.language,
        }
        if self._config.context_bias:
            session_config["context_bias"] = list(self._config.context_bias)
        await socket.send_json({"type": "session.update", "session": session_config})
        updated = await socket.receive_json()
        self._expect_event(updated, "session.updated")

    async def _send_audio(self, socket: aiohttp.ClientWebSocketResponse) -> None:
        """Send queued PCM frames until the LiveKit track closes."""
        while True:
            item = await self._input.get()
            if item is _INPUT_END:
                await socket.send_json({"type": "input_audio.end"})
                return
            await socket.send_json(
                {
                    "type": "input_audio.append",
                    "audio": base64.b64encode(item).decode("ascii"),
                }
            )

    async def _receive_events(self, socket: aiohttp.ClientWebSocketResponse) -> None:
        """Translate provider deltas without splitting words between events."""
        async for message in socket:
            if message.type == aiohttp.WSMsgType.ERROR:
                raise RuntimeError("Mistral realtime WebSocket failed")
            if message.type != aiohttp.WSMsgType.TEXT:
                continue
            event = json.loads(message.data)
            event_type = event.get("type")
            if event_type == "error":
                self._raise_provider_error(event)
            if event_type == "transcription.done":
                await self._publish_text(self._pending_text)
                self._pending_text = ""
                return
            if event_type != "transcription.text.delta":
                continue
            text = str(event.get("text", ""))
            if not text:
                continue
            self._pending_text += text
            complete, self._pending_text = _split_complete_words(self._pending_text)
            await self._publish_text(complete)
        raise RuntimeError("Mistral realtime WebSocket closed before completion")

    async def _publish_text(self, text: str) -> None:
        """Publish provider text only after its final word boundary is known."""
        normalized = text.strip()
        if not normalized:
            return
        self._sequence += 1
        await self._output.put(
            stt.SpeechEvent(
                type=stt.SpeechEventType.FINAL_TRANSCRIPT,
                request_id=f"mistral-{self._sequence}-{uuid.uuid4().hex}",
                alternatives=[
                    stt.SpeechData(
                        language=LanguageCode(self._config.language),
                        text=normalized,
                    )
                ],
            )
        )

    @staticmethod
    def _expect_event(event: dict[str, object], expected_type: str) -> None:
        """Reject a partial or incompatible provider handshake."""
        actual_type = event.get("type")
        if actual_type != expected_type:
            raise RuntimeError(
                f"unexpected Mistral realtime event: {actual_type!r}, "
                f"expected {expected_type!r}"
            )

    @staticmethod
    def _raise_provider_error(event: dict[str, object]) -> None:
        """Raise a redacted provider error without credentials or audio."""
        details = event.get("error")
        if isinstance(details, dict):
            code = details.get("code", "mistral_error")
            message = details.get("message", "unknown provider error")
            raise RuntimeError(f"{code}: {message}")
        raise RuntimeError("Mistral realtime provider error")


class MistralRealtimeSTT:
    """Create and close track-scoped Mistral realtime streams."""

    def __init__(self, config: MistralRealtimeConfig):
        """Initialize the provider with validated immutable configuration."""
        self._config = config
        self._streams: set[MistralRealtimeStream] = set()

    def stream(self) -> MistralRealtimeStream:
        """Create one independent provider session for one audio track."""
        stream = MistralRealtimeStream(self._config)
        self._streams.add(stream)
        return stream

    async def aclose(self) -> None:
        """Close every stream created by this provider."""
        streams = tuple(self._streams)
        self._streams.clear()
        await asyncio.gather(*(stream.aclose() for stream in streams))


def create_mistral_live_stt() -> MistralRealtimeSTT:
    """Create the configured Mistral realtime provider."""
    return MistralRealtimeSTT(load_config())


def _split_complete_words(text: str) -> tuple[str, str]:
    """Keep the last possibly partial word while releasing stable text."""
    boundary = max(
        (index for index, value in enumerate(text) if value.isspace()), default=-1
    )
    if boundary < 0:
        return "", text
    return text[:boundary], text[boundary + 1 :]
