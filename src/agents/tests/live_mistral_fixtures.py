"""Synthetic wire messages and physical I/O gates for the real Mistral adapter."""

import asyncio
import base64
import json
from array import array
from collections import defaultdict
from types import SimpleNamespace
from unittest.mock import AsyncMock

import aiohttp
from livekit import rtc

from live_mistral import MODEL

REQUEST_ID = "00000000-0000-4000-8000-000000000031"


async def eventually(predicate):
    """Wait for an observable transition under a short offline bound."""
    async with asyncio.timeout(2):
        while not predicate():
            await asyncio.sleep(0)


def frame(number, samples=480):
    """Encode numbered mono PCM16, including arbitrarily short tails."""
    return rtc.AudioFrame(
        data=array("h", [number] * samples).tobytes(),
        sample_rate=24000,
        num_channels=1,
        samples_per_channel=samples,
    )


def session_event(kind, request_id=REQUEST_ID, **changes):
    """Match the official native session model, not an invented item protocol."""
    session = {
        "request_id": request_id,
        "model": MODEL,
        "audio_format": {"encoding": "pcm_s16le", "sample_rate": 24000},
        "target_streaming_delay_ms": 480,
        "language": None,
    }
    session.update(changes)
    return {"type": kind, "session": session}


def done_event(text, language="fr"):
    """Retain the provider's full terminal text and model/usage fields."""
    return {
        "type": "transcription.done",
        "text": text,
        "language": language,
        "model": MODEL,
        "usage": {"prompt_audio_seconds": 0.1, "total_tokens": 1},
    }


class MistralSocket:
    """Replace network I/O only; keep parser, channels, SDK tee and iterator real."""

    def __init__(self, *, request_id=REQUEST_ID, created=True):
        """Expose independently blocked update, PCM, EOF and physical close."""
        self.request_id = request_id
        self.messages = asyncio.Queue()
        self.sent = []
        self.samples = []
        self.chunks = []
        self.text = ""
        self.updated = session_event("session.updated", request_id)
        self.auto_update = True
        self.auto_done = True
        self.done_text = None
        self.done_language = "fr"
        self.terminal_frames = []
        self.closed = False
        self.close_code = None
        self.close_result_code = 1000
        self.close_error = None
        self.close_calls = 0
        self.send_error = None
        self.after_send = None
        self.entered = defaultdict(asyncio.Event)
        self.release = defaultdict(self._open_gate)
        self.pongs = []
        if created:
            self.feed(session_event("session.created", request_id))

    @staticmethod
    def _open_gate():
        gate = asyncio.Event()
        gate.set()
        return gate

    def feed(self, *events):
        """Queue raw JSON messages for the actual transport receive path."""
        for event in events:
            self.messages.put_nowait(
                aiohttp.WSMessage(aiohttp.WSMsgType.TEXT, json.dumps(event), "")
            )

    def delta(self, text):
        """Track only native text; done defaults to its exact concatenation."""
        self.text += text
        self.feed({"type": "transcription.text.delta", "text": text})

    def peer_close(self, code=1000):
        """An early close is never sufficient terminal evidence."""
        self.close_code = code
        self.messages.put_nowait(aiohttp.WSMessage(aiohttp.WSMsgType.CLOSE, code, ""))

    async def receive(self):
        """Read the synthetic socket with no adapter receive logic replaced."""
        return await self.messages.get()

    async def send_str(self, raw):
        """Record only successful physical sends, after their cancellable gate."""
        event = json.loads(raw)
        kind = event["type"]
        self.entered[kind].set()
        await self.release[kind].wait()
        if self.send_error is not None:
            raise self.send_error
        self.sent.append(event)
        if kind == "session.update" and self.auto_update:
            self.feed(self.updated)
        if kind == "input_audio.append":
            pcm = array("h")
            pcm.frombytes(base64.b64decode(event["audio"], validate=True))
            self.samples.extend(pcm)
            self.chunks.append(len(pcm))
        if kind == "input_audio.end" and self.auto_done:
            self.feed(*self.terminal_frames)
            text = self.done_text
            if text is None:
                text = self.text
            self.feed(done_event(text, self.done_language))
        if self.after_send is not None:
            await self.after_send(kind)

    async def pong(self, data):
        """Pong is subject to the same explicit lease-fenced socket boundary."""
        self.entered["pong"].set()
        await self.release["pong"].wait()
        self.pongs.append(data)

    async def close(self):
        """A completed close includes its peer result, like aiohttp.close()."""
        self.close_calls += 1
        self.entered["close"].set()
        await self.release["close"].wait()
        self.closed = True
        self.close_code = self.close_result_code

    def exception(self):
        """Expose close errors through the actual aiohttp response surface."""
        return self.close_error


def session_for(socket):
    """Avoid constructing a network client; only inject physical transport."""
    return SimpleNamespace(
        closed=False,
        ws_connect=AsyncMock(return_value=socket),
        close=AsyncMock(),
    )
