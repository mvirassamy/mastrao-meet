"""Synthetic native frames and socket I/O for the actual Deepgram SDK."""

import asyncio
import json
from array import array
from types import SimpleNamespace
from unittest.mock import AsyncMock

import aiohttp
from test_live_audio_timing import ProtocolSocket

REQUEST_ID = "00000000-0000-4000-8000-000000000001"


def result_frame(start=0.0, duration=0.05, text="first final", *, final=True):
    """Native segment starts survive text, duration and word-bound revisions."""
    return {
        "type": "Results",
        "start": start,
        "duration": duration,
        "channel_index": [0, 1],
        "is_final": final,
        "speech_final": final,
        "from_finalize": False,
        "metadata": {
            "request_id": REQUEST_ID,
            "model_info": {"name": "synthetic-nova-3"},
        },
        "channel": {
            "alternatives": [
                {
                    "transcript": text,
                    "confidence": 0.9,
                    "languages": ["fr", "en"],
                    "words": [
                        {
                            "word": text,
                            "start": start,
                            "end": start + duration,
                            "confidence": 0.9,
                        }
                    ],
                }
            ]
        },
    }


def metadata_frame():
    """Metadata frame through the synthetic SDK fixture."""
    return {
        "type": "Metadata",
        "request_id": REQUEST_ID,
        "duration": 0.12,
        "channels": 1,
        "sha256": "synthetic-only",
        "created": "2026-10-26T00:00:00Z",
        "model_info": {"model": {"name": "synthetic-nova-3", "version": "fixture"}},
    }


class DeepgramSocket(ProtocolSocket):
    """Expose real SDK binary tail writes and terminal protocol barriers."""

    def __init__(self):
        """Expose independent gates for binary writes, controls and physical close."""
        super().__init__()
        self._response = SimpleNamespace(headers={"dg-model-name": "synthetic-nova-3"})
        self.close_code = 1000
        self.controls = []
        self.samples = []
        self.chunks = []
        self.write_entered = asyncio.Event()
        self.write_release = asyncio.Event()
        self.write_release.set()
        self.control_entered = asyncio.Event()
        self.control_release = asyncio.Event()
        self.control_release.set()
        self.close_entered = asyncio.Event()
        self.close_release = asyncio.Event()
        self.close_release.set()
        self.closed = False
        self.terminal_frames = [
            result_frame(),
            result_frame(0.05, 0.07, "second final"),
            metadata_frame(),
        ]
        self.auto_terminal = True
        self.close.side_effect = self.finish_close

    async def send_bytes(self, data):
        """Send bytes through the synthetic SDK fixture."""
        self.write_entered.set()
        await self.write_release.wait()
        samples = array("h")
        samples.frombytes(data)
        self.samples.extend(samples)
        self.chunks.append(len(samples))

    async def send_str(self, message):
        """Send str through the synthetic SDK fixture."""
        kind = json.loads(message)["type"]
        if kind != "KeepAlive":
            self.control_entered.set()
            await self.control_release.wait()
        self.controls.append(kind)
        if kind == "CloseStream" and self.auto_terminal:
            self.feed(*self.terminal_frames)
            self.peer_close()

    def peer_close(self, code=1000):
        """Peer close through the synthetic SDK fixture."""
        self.close_code = code
        self.messages.put_nowait(
            aiohttp.WSMessage(aiohttp.WSMsgType.CLOSE, code, "synthetic close")
        )

    async def finish_close(self):
        """Finish close through the synthetic SDK fixture."""
        self.close_entered.set()
        await self.close_release.wait()
        self.closed = True


def session_for(socket):
    """Use the SDK's real URL/header construction, with no HTTP client transport."""
    return SimpleNamespace(closed=False, ws_connect=AsyncMock(return_value=socket))
