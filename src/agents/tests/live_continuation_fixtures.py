"""Numbered PCM and websocket fixtures for execution of the pinned SDK loops."""

import asyncio
import base64
from array import array
from types import SimpleNamespace

from livekit import rtc
from livekit.agents import vad
from test_live_audio_timing import ProtocolSocket, completed


async def eventually(predicate):
    """Bound a task-state barrier without sleeping for a production deadline."""
    async with asyncio.timeout(2):
        while not predicate():
            await asyncio.sleep(0)


def numbered_frame(number, samples=480):
    """Encode a frame identity into every signed 16-bit mono sample."""
    return rtc.AudioFrame(
        data=array("h", [number] * samples).tobytes(),
        sample_rate=24000,
        num_channels=1,
        samples_per_channel=samples,
    )


def committed(item_id, previous=None):
    """Create a server ACK whose event_id is distinct from the item identity."""
    return {
        "type": "input_audio_buffer.committed",
        "event_id": f"server-event-{item_id}",
        "item_id": item_id,
        "previous_item_id": previous,
    }


class NumberedSocket(ProtocolSocket):
    """Inspect real SDK append bytes and hold ACKs/finals/close independently."""

    def __init__(self, *, auto_complete=False):
        """Keep every network boundary synthetic and individually observable."""
        super().__init__()
        self.samples = []
        self.chunks = []
        self.types = []
        self.commit_requests = asyncio.Queue()
        self.append_entered = asyncio.Event()
        self.append_release = asyncio.Event()
        self.append_release.set()
        self.close_entered = asyncio.Event()
        self.close_release = asyncio.Event()
        self.close_release.set()
        self.closed = False
        self.auto_complete = auto_complete
        self.commit_count = 0
        self.send_json.side_effect = self.send
        self.close.side_effect = self.finish_close

    async def send(self, message):
        """Record only successful sends, including partial SDK append chunks."""
        kind = message["type"]
        if kind == "input_audio_buffer.append":
            self.append_entered.set()
            await self.append_release.wait()
            pcm = array("h")
            pcm.frombytes(base64.b64decode(message["audio"]))
            self.samples.extend(pcm)
            self.chunks.append(len(pcm))
        self.types.append(kind)
        if kind == "input_audio_buffer.commit":
            self.commit_count += 1
            self.commit_requests.put_nowait(self.commit_count)
            if self.auto_complete:
                self.complete_commit(self.commit_count)

    def complete_commit(self, number, text="synthetic final"):
        """Reuse IDs across sockets, but preserve the per-socket ACK chain."""
        previous = None
        if number > 1:
            previous = f"item-{number - 1}"
        item_id = f"item-{number}"
        self.feed(committed(item_id, previous), completed(item_id, text))

    async def finish_close(self):
        """Prove physical close finishes before a successor can be constructed."""
        self.close_entered.set()
        await self.close_release.wait()
        self.closed = True


class FixtureVAD:
    """Replace inference only, retaining the SDK's actual VAD sender task."""

    def __init__(self, *, commit_on_frame=False):
        """Emit speech-end events only when a race test requests them."""
        self.commit_on_frame = commit_on_frame
        self.streams = []

    def stream(self):
        """Create fresh VAD state for each physical SDK connection."""
        stream = FixtureVADStream(self.commit_on_frame)
        self.streams.append(stream)
        return stream


class FixtureVADStream:
    """Match the VAD input/event methods consumed by the installed SDK."""

    def __init__(self, commit_on_frame):
        """Start a synthetic event channel without any audio retention."""
        self.commit_on_frame = commit_on_frame
        self.events = asyncio.Queue()

    def push_frame(self, frame):
        """Optionally make a commit contend with the matching append."""
        if self.commit_on_frame:
            self.events.put_nowait(SimpleNamespace(type=vad.VADEventType.END_OF_SPEECH))

    def end_input(self):
        """Silero EOF does not synthesize a final speech-end event."""
        self.events.put_nowait(None)

    def __aiter__(self):
        """Retain a separate task consuming VAD events inside the real SDK."""
        return self

    async def __anext__(self):
        """Wait until inference/EOF would produce an event."""
        event = await self.events.get()
        if event is None:
            raise StopAsyncIteration
        return event

    async def aclose(self):
        """Release the fixture without adding a late provider commit."""
        self.events.put_nowait(None)
