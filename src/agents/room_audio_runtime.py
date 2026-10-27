"""One bounded live-ASR and post-audio runtime for a meeting room."""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

from livekit import rtc
from livekit.agents import stt

from room_audio_mixer import RoomAudioMixer, RoomAudioMixerSnapshot
from room_audio_ogg import RoomAudioArtifact


class RoomSpeechStream(Protocol):
    """Single realtime provider stream used by one room run."""

    def push_frame(self, frame: rtc.AudioFrame) -> None:
        """Accept one fixed-clock room frame."""

    def __aiter__(self):
        """Yield provider speech events."""

    async def aclose(self) -> None:
        """Close the provider stream."""


class RoomLiveProvider(Protocol):
    """Provider capable of opening exactly one room stream."""

    def stream(self) -> RoomSpeechStream:
        """Open the room stream."""

    async def aclose(self) -> None:
        """Close provider resources."""


class RoomAudioSink(Protocol):
    """Durable post-audio sink fed by the same room clock as live ASR."""

    def write_frame(self, pcm_s16le: bytes) -> None:
        """Persist one ordered room frame."""

    def finish(self) -> RoomAudioArtifact:
        """Finalize exactly one immutable room artifact."""


SpeechEventHandler = Callable[[stt.SpeechEvent], Awaitable[None]]
LiveFailureHandler = Callable[[Exception], Awaitable[None]]
ProviderFactory = Callable[[], RoomLiveProvider]


@dataclass(frozen=True)
class RoomAudioRuntimeConfig:
    """Fixed room clock and per-track buffer bounds."""

    frame_samples: int = 480
    sample_rate_hz: int = 24_000
    maximum_buffered_frames: int = 10


DEFAULT_RUNTIME_CONFIG = RoomAudioRuntimeConfig()


class RoomAudioRun:
    """Own one room clock, one live provider and one post-audio artifact."""

    def __init__(
        self,
        *,
        provider_factory: ProviderFactory,
        speech_event_handler: SpeechEventHandler,
        artifact_sink: RoomAudioSink,
        live_failure_handler: LiveFailureHandler,
        config: RoomAudioRuntimeConfig = DEFAULT_RUNTIME_CONFIG,
    ):
        """Prepare a stopped room run; no provider opens before start()."""
        self._provider_factory = provider_factory
        self._speech_event_handler = speech_event_handler
        self._artifact_sink = artifact_sink
        self._live_failure_handler = live_failure_handler
        self._frame_samples = config.frame_samples
        self._sample_rate_hz = config.sample_rate_hz
        self._maximum_buffered_frames = config.maximum_buffered_frames
        self._frame_duration_seconds = config.frame_samples / config.sample_rate_hz
        self._mixer = RoomAudioMixer(
            frame_samples=config.frame_samples,
            sample_rate_hz=config.sample_rate_hz,
            maximum_buffered_frames=config.maximum_buffered_frames,
        )
        self._track_streams: dict[str, rtc.AudioStream] = {}
        self._track_tasks: dict[str, asyncio.Task] = {}
        self._provider: RoomLiveProvider | None = None
        self._speech_stream: RoomSpeechStream | None = None
        self._clock_task: asyncio.Task | None = None
        self._event_task: asyncio.Task | None = None
        self._started = False
        self._closed = False
        self._live_failed = False

    @property
    def provider_session_count(self) -> int:
        """Expose the bounded provider cardinality for qualification."""
        return int(self._provider is not None)

    def snapshot(self) -> RoomAudioMixerSnapshot:
        """Return content-free mixer metrics."""
        return self._mixer.snapshot()

    async def start(self) -> None:
        """Open exactly one provider stream and start the room clock."""
        if self._closed:
            raise RuntimeError("room audio run is closed")
        if self._started:
            return
        self._provider = self._provider_factory()
        try:
            self._speech_stream = self._provider.stream()
        except Exception:
            await self._provider.aclose()
            self._provider = None
            raise
        self._started = True
        self._clock_task = asyncio.create_task(self._run_clock())
        self._event_task = asyncio.create_task(self._consume_speech_events())

    def observe_track(self, track_ref: str, track: rtc.Track) -> None:
        """Subscribe to one track as pending until exact Core admission."""
        if not self._started or self._closed:
            raise RuntimeError("room audio run is not active")
        if track_ref in self._track_streams:
            raise ValueError("track is already observed")
        stream = rtc.AudioStream.from_track(
            track=track,
            capacity=self._maximum_buffered_frames,
            sample_rate=self._sample_rate_hz,
            num_channels=1,
            frame_size_ms=round(self._frame_duration_seconds * 1_000),
        )
        self._mixer.observe_track(track_ref)
        self._track_streams[track_ref] = stream
        self._track_tasks[track_ref] = asyncio.create_task(
            self._pump_track(track_ref, stream)
        )

    async def unobserve_track(self, track_ref: str) -> None:
        """Stop consuming a physically absent track and clear its audio."""
        stream = self._track_streams.pop(track_ref, None)
        task = self._track_tasks.pop(track_ref, None)
        self._mixer.unobserve_track(track_ref)
        if task is not None:
            task.cancel()
        if stream is not None:
            await stream.aclose()
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)

    def admit_track(
        self,
        track_ref: str,
        *,
        generation: int,
        expires_at_ms: int,
        authorization_digest: str,
    ) -> None:
        """Apply exact Core admission to one observed track."""
        self._mixer.admit_track(
            track_ref,
            generation=generation,
            expires_at_ms=expires_at_ms,
            authorization_digest=authorization_digest,
        )

    def revoke_track(
        self, track_ref: str, *, generation: int, authorization_digest: str
    ) -> None:
        """Apply exact Core revocation and discard queued audio."""
        self._mixer.revoke_track(
            track_ref,
            generation=generation,
            authorization_digest=authorization_digest,
        )

    async def aclose(self) -> RoomAudioArtifact:
        """Stop all inputs, close the provider and finalize one artifact."""
        if self._closed:
            return self._artifact_sink.finish()
        self._closed = True
        tracks = list(self._track_streams)
        await asyncio.gather(
            *(self.unobserve_track(track_ref) for track_ref in tracks),
            return_exceptions=True,
        )
        await self._cancel_task(self._clock_task)
        if self._speech_stream is not None:
            await self._speech_stream.aclose()
        await self._cancel_task(self._event_task)
        if self._provider is not None:
            await self._provider.aclose()
        self._provider = None
        self._speech_stream = None
        return self._artifact_sink.finish()

    async def _pump_track(self, track_ref: str, stream: rtc.AudioStream) -> None:
        """Copy decoded frames into the bounded mixer queue."""
        async for event in stream:
            self._mixer.push_frame(track_ref, bytes(event.frame.data))

    async def _run_clock(self) -> None:
        """Produce one room frame on a monotonic fixed-rate clock."""
        if self._speech_stream is None:
            raise RuntimeError("room speech stream is unavailable")
        deadline = time.monotonic()
        while True:
            deadline += self._frame_duration_seconds
            await asyncio.sleep(max(0, deadline - time.monotonic()))
            pcm = self._mixer.mix_next_frame()
            self._artifact_sink.write_frame(pcm)
            if not self._live_failed:
                try:
                    self._speech_stream.push_frame(
                        rtc.AudioFrame(
                            data=pcm,
                            sample_rate=self._sample_rate_hz,
                            num_channels=1,
                            samples_per_channel=self._frame_samples,
                        )
                    )
                except Exception as error:  # provider SDK boundary
                    await self._fail_live(error)

    async def _fail_live(self, error: Exception) -> None:
        """Fence a broken live provider while post-audio keeps its room clock."""
        if self._live_failed:
            return
        self._live_failed = True
        if self._speech_stream is not None:
            await self._speech_stream.aclose()
        if self._provider is not None:
            await self._provider.aclose()
        self._speech_stream = None
        self._provider = None
        await self._live_failure_handler(error)

    async def _consume_speech_events(self) -> None:
        """Forward provider output to the room-owned publisher."""
        if self._speech_stream is None:
            raise RuntimeError("room speech stream is unavailable")
        try:
            async for event in self._speech_stream:
                await self._speech_event_handler(event)
            if not self._closed and not self._live_failed:
                await self._fail_live(RuntimeError("room live provider closed"))
        except asyncio.CancelledError:
            raise
        except Exception as error:  # provider SDK boundary
            await self._fail_live(error)

    @staticmethod
    async def _cancel_task(task: asyncio.Task | None) -> None:
        """Cancel and drain one internal task."""
        if task is None:
            return
        task.cancel()
        with contextlib.suppress(BaseException):
            await task
