"""Retain one RTC reader and disjoint PCM intervals across authorized SDK legs."""

import asyncio
import time
import uuid
from collections import deque
from collections.abc import Awaitable, Callable

from livekit import rtc
from livekit.agents import stt

from live_input_drain import InputDrainError
from live_send_control import PCM_SAMPLE_RATE, LiveSendAuthority, audio_sample

MAX_QUEUED_SAMPLES = PCM_SAMPLE_RATE * 10
MAX_QUEUED_FRAMES = 512
HANDOFF_TIMEOUT_SECONDS = 30


async def wait_rotation(deadline: float) -> None:
    """Wait for the monotonic 540-second authorization deadline."""
    await asyncio.sleep(max(0, deadline - time.monotonic()))


class ContinuationInput:
    """Route each captured sample once, retaining only unsent handoff frames."""

    def __init__(
        self, can_send: Callable[[], bool], appended_samples: Callable[[], int]
    ):
        """Start before the initial leg consumes its first 24-kHz mono frame."""
        self._can_send = can_send
        self._appended_samples = appended_samples
        self._leg_start = 0
        self._stream: stt.RecognizeStream | None = None
        self._queued: deque[rtc.AudioFrame] = deque()
        self._queued_samples = 0
        self.captured_samples = 0
        self.routed_samples = 0

    def attach(self, stream: stt.RecognizeStream, start_sample: int) -> None:
        """Start the next disjoint interval at the exact Core-authorized offset."""
        if start_sample != self.routed_samples or not self._can_send():
            raise InputDrainError("live-continuation-start-mismatch")
        self._stream = stream
        self._leg_start = start_sample
        while self._queued:
            frame = self._queued.popleft()
            self._queued_samples -= frame.samples_per_channel
            self._route(frame)

    def seal(self) -> int:
        """Synchronously cut input admission; capture continues into the queue."""
        self._stream = None
        return self.routed_samples

    def _route(self, frame: rtc.AudioFrame) -> None:
        if self._stream is None or not self._can_send():
            raise InputDrainError("live-continuation-admission-fenced")
        pending = self.routed_samples - self._leg_start - self._appended_samples()
        if pending < 0:
            raise InputDrainError("live-continuation-progress-count-invalid")
        if pending + frame.samples_per_channel > MAX_QUEUED_SAMPLES:
            raise InputDrainError("live-continuation-sdk-buffer-capacity-exceeded")
        self._stream.push_frame(frame)
        self.routed_samples = audio_sample(
            self.routed_samples + frame.samples_per_channel
        )

    async def run(self, audio: rtc.AudioStream) -> None:
        """Keep exactly one RTC iterator alive while provider legs are closed."""
        async for event in audio:
            if not self._can_send():
                return
            frame = event.frame
            if (
                frame.sample_rate != PCM_SAMPLE_RATE
                or frame.num_channels != 1
                or frame.samples_per_channel <= 0
                or len(frame.data) != frame.samples_per_channel
            ):
                raise InputDrainError("live-continuation-pcm-format-invalid")
            self.captured_samples = audio_sample(
                self.captured_samples + frame.samples_per_channel
            )
            if self._stream is not None:
                self._route(frame)
                continue
            if (
                self._queued_samples + frame.samples_per_channel > MAX_QUEUED_SAMPLES
                or len(self._queued) >= MAX_QUEUED_FRAMES
            ):
                raise InputDrainError("live-continuation-buffer-capacity-exceeded")
            self._queued.append(frame)
            self._queued_samples += frame.samples_per_channel


async def continue_legs(
    authority: LiveSendAuthority,
    pcm: ContinuationInput,
    *,
    speech_tasks: Callable[[], tuple[asyncio.Task, asyncio.Task]],
    drain_leg: Callable[[int], Awaitable[None]],
    open_leg: Callable[[], None],
) -> None:
    """Request a new leg only after planned drain, publication and socket close."""
    while authority.can_send():
        timer = asyncio.create_task(wait_rotation(authority.rotation_deadline))
        try:
            done, _ = await asyncio.wait(
                (*speech_tasks(), timer), return_when=asyncio.FIRST_COMPLETED
            )
            for task in done:
                if task is not timer:
                    task.result()
                    raise InputDrainError("live-continuation-speech-ended")
            end_sample = pcm.seal()
            async with asyncio.timeout(HANDOFF_TIMEOUT_SECONDS):
                await drain_leg(end_sample)
            if not authority.can_send():
                raise InputDrainError("live-continuation-lease-ended")
            await authority.rotate(str(uuid.uuid4()), end_sample)
            if not authority.can_send():
                raise InputDrainError("live-continuation-start-fenced")
            open_leg()
        finally:
            timer.cancel()
            await asyncio.gather(timer, return_exceptions=True)
