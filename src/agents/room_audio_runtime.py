"""One bounded live-ASR and post-audio runtime for a meeting room."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

from livekit import rtc
from livekit.agents import stt

from room_audio_mixer import RoomAudioMixer, RoomAudioMixerSnapshot
from room_audio_ogg import RoomAudioArtifact
from room_audio_overlap_router import RoomAudioOverlapRouter
from room_audio_speaker_attribution import RoomAudioSpeakerAttribution

LIVEKIT_AUDIO_STREAM_CAPACITY = 0
LIVE_METRICS_INTERVAL_FRAMES = 500

logger = logging.getLogger("room-audio-worker.runtime")


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
SpeechEventHandlerFactory = Callable[[], SpeechEventHandler]
RecoveryEventHandlerFactory = Callable[[str, str], SpeechEventHandler]
LiveFailureHandler = Callable[[Exception], Awaitable[None]]
LiveGenerationInvalidator = Callable[[], Awaitable[None]]
ProviderFactory = Callable[[], RoomLiveProvider]


@dataclass(frozen=True)
class RoomAudioRuntimeConfig:
    """Fixed room clock and application-owned per-track buffer bounds."""

    frame_samples: int = 480
    sample_rate_hz: int = 24_000
    maximum_buffered_frames: int = 10
    maximum_clock_lag_seconds: float = 0.25
    maximum_overlap_recovery_streams: int = 2


DEFAULT_RUNTIME_CONFIG = RoomAudioRuntimeConfig()


@dataclass(frozen=True)
class RoomLiveOutput:
    """Exact dependencies for one live provider output."""

    provider_factory: ProviderFactory
    speech_event_handler_factory: SpeechEventHandlerFactory
    recovery_event_handler_factory: RecoveryEventHandlerFactory
    failure_handler: LiveFailureHandler
    generation_invalidator: LiveGenerationInvalidator
    speaker_attribution: RoomAudioSpeakerAttribution


@dataclass
class _OverlapRecoverySession:
    """One temporary provider stream bound to an exact microphone owner."""

    provider: RoomLiveProvider
    stream: RoomSpeechStream
    handler: SpeechEventHandler
    event_task: asyncio.Task | None = None
    closing: bool = False


def create_room_audio_run_for_effect(
    effect: dict,
    *,
    live_output: RoomLiveOutput | None = None,
    artifact_sink: RoomAudioSink | None = None,
    config: RoomAudioRuntimeConfig = DEFAULT_RUNTIME_CONFIG,
) -> RoomAudioRun:
    """Create only the output declared by one verified Core start effect."""
    pipeline = effect.get("pipeline")
    if pipeline == "post":
        if artifact_sink is None or live_output is not None:
            raise ValueError("post room audio requires only an artifact sink")
        return RoomAudioRun(artifact_sink=artifact_sink, config=config)
    if pipeline == "live":
        if artifact_sink is not None or live_output is None:
            raise ValueError("live room audio requires only a provider")
        return RoomAudioRun(live_output=live_output, config=config)
    raise ValueError("unsupported room audio pipeline")


class RoomAudioRun:
    """Own one room clock, one continuous live stream and bounded recoveries."""

    def __init__(
        self,
        *,
        live_output: RoomLiveOutput | None = None,
        artifact_sink: RoomAudioSink | None = None,
        config: RoomAudioRuntimeConfig = DEFAULT_RUNTIME_CONFIG,
    ):
        """Prepare a stopped room run; no provider opens before start()."""
        if live_output is None and artifact_sink is None:
            raise ValueError("room audio run requires a live or post output")
        if live_output is not None and (
            live_output.provider_factory is None
            or live_output.speech_event_handler_factory is None
            or live_output.recovery_event_handler_factory is None
            or live_output.failure_handler is None
            or live_output.generation_invalidator is None
        ):
            raise ValueError("live room audio requires event and failure handlers")
        self._provider_factory = live_output.provider_factory if live_output else None
        self._speech_event_handler_factory = (
            live_output.speech_event_handler_factory if live_output else None
        )
        self._recovery_event_handler_factory = (
            live_output.recovery_event_handler_factory if live_output else None
        )
        self._speech_event_handler: SpeechEventHandler | None = None
        self._artifact_sink = artifact_sink
        self._live_failure_handler = (
            live_output.failure_handler if live_output else None
        )
        self._live_generation_invalidator = (
            live_output.generation_invalidator if live_output else None
        )
        self._speaker_attribution = (
            live_output.speaker_attribution if live_output else None
        )
        self._frame_samples = config.frame_samples
        self._sample_rate_hz = config.sample_rate_hz
        self._maximum_buffered_frames = config.maximum_buffered_frames
        self._frame_duration_seconds = config.frame_samples / config.sample_rate_hz
        if config.maximum_clock_lag_seconds <= self._frame_duration_seconds:
            raise ValueError("room audio clock lag bound must exceed one frame")
        self._maximum_clock_lag_seconds = config.maximum_clock_lag_seconds
        self._mixer = RoomAudioMixer(
            frame_samples=config.frame_samples,
            sample_rate_hz=config.sample_rate_hz,
            maximum_buffered_frames=config.maximum_buffered_frames,
        )
        self._overlap_router = RoomAudioOverlapRouter(
            maximum_recovery_streams=config.maximum_overlap_recovery_streams
        )
        self._maximum_overlap_recovery_streams = config.maximum_overlap_recovery_streams
        self._track_streams: dict[str, rtc.AudioStream] = {}
        self._track_tasks: dict[str, asyncio.Task] = {}
        self._participant_identity_by_track: dict[str, str] = {}
        self._recovery_sessions: dict[str, _OverlapRecoverySession] = {}
        self._recovery_close_tasks: set[asyncio.Task] = set()
        self._closing_recovery_track_refs: set[str] = set()
        self._provider: RoomLiveProvider | None = None
        self._speech_stream: RoomSpeechStream | None = None
        self._clock_task: asyncio.Task | None = None
        self._event_task: asyncio.Task | None = None
        self._provider_lock = asyncio.Lock()
        self._terminal_failure = asyncio.Event()
        self._terminal_error: Exception | None = None
        self._started = False
        self._closed = False
        self._live_failed = False

    @property
    def provider_session_count(self) -> int:
        """Expose the bounded provider cardinality for qualification."""
        return (
            int(self._provider is not None)
            + len(self._recovery_sessions)
            + len(self._recovery_close_tasks)
        )

    @property
    def has_live_output(self) -> bool:
        """Return whether this run owns a realtime provider boundary."""
        return self._provider_factory is not None

    def snapshot(self) -> RoomAudioMixerSnapshot:
        """Return content-free mixer metrics."""
        return self._mixer.snapshot()

    async def start(self) -> None:
        """Open exactly one provider stream and start the room clock."""
        if self._closed:
            raise RuntimeError("room audio run is closed")
        if self._started:
            return
        if self._provider_factory is not None:
            await self._open_live_provider()
        self._started = True
        self._clock_task = asyncio.create_task(self._run_clock())
        if self._speech_stream is not None:
            self._event_task = asyncio.create_task(self._consume_speech_events())

    async def restart_live(self) -> None:
        """Replace one provider generation after a recovered room connection."""
        if not self._started or self._closed or self._provider_factory is None:
            raise RuntimeError("live room audio run is not restartable")
        if self._terminal_failure.is_set():
            raise RuntimeError("failed live room audio run is not restartable")
        async with self._provider_lock:
            await self._live_generation_invalidator()
            await self._close_all_recovery_sessions()
            await self._close_live_provider()
            try:
                await self._open_live_provider()
            except Exception as error:
                await self._record_terminal_failure(error)
                return
            self._event_task = asyncio.create_task(self._consume_speech_events())

    async def wait_terminal_failure(self) -> Exception:
        """Wait until the room clock or realtime provider fails permanently."""
        await self._terminal_failure.wait()
        if self._terminal_error is None:
            raise RuntimeError("room audio failed without a terminal error")
        return self._terminal_error

    def observe_track(
        self,
        track_ref: str,
        track: rtc.Track,
        participant_identity: str,
    ) -> None:
        """Subscribe to one track as pending until exact Core admission."""
        if not self._started or self._closed:
            raise RuntimeError("room audio run is not active")
        if track_ref in self._track_streams:
            raise ValueError("track is already observed")
        stream = rtc.AudioStream.from_track(
            track=track,
            # LiveKit drops the oldest frame silently when this public SDK queue
            # is bounded. Keep it unbounded so every SDK-delivered frame reaches
            # the application queue, where eviction is explicit and counted.
            capacity=LIVEKIT_AUDIO_STREAM_CAPACITY,
            sample_rate=self._sample_rate_hz,
            num_channels=1,
            frame_size_ms=round(self._frame_duration_seconds * 1_000),
        )
        self._mixer.observe_track(track_ref)
        if self._speaker_attribution is not None:
            self._speaker_attribution.register_track(
                track_ref,
                participant_identity,
            )
        self._participant_identity_by_track[track_ref] = participant_identity
        self._track_streams[track_ref] = stream
        self._track_tasks[track_ref] = asyncio.create_task(
            self._pump_track(track_ref, stream)
        )

    async def unobserve_track(self, track_ref: str) -> None:
        """Stop consuming a physically absent track and clear its audio."""
        stream = self._track_streams.pop(track_ref, None)
        task = self._track_tasks.pop(track_ref, None)
        self._participant_identity_by_track.pop(track_ref, None)
        self._schedule_recovery_close(track_ref)
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

    async def aclose(self) -> RoomAudioArtifact | None:
        """Stop inputs and finalize the configured post output, when present."""
        if self._closed:
            return self._artifact_sink.finish() if self._artifact_sink else None
        self._closed = True
        tracks = list(self._track_streams)
        await asyncio.gather(
            *(self.unobserve_track(track_ref) for track_ref in tracks),
            return_exceptions=True,
        )
        await self._cancel_task(self._clock_task)
        await self._close_all_recovery_sessions()
        async with self._provider_lock:
            await self._close_live_provider()
        return self._artifact_sink.finish() if self._artifact_sink else None

    async def _pump_track(self, track_ref: str, stream: rtc.AudioStream) -> None:
        """Copy decoded frames into the bounded mixer queue."""
        async for event in stream:
            self._mixer.push_frame(track_ref, bytes(event.frame.data))

    async def _run_clock(self) -> None:
        """Produce one room frame on a monotonic fixed-rate clock."""
        deadline = time.monotonic()
        while True:
            deadline += self._frame_duration_seconds
            await asyncio.sleep(max(0, deadline - time.monotonic()))
            if self._clock_lag_exceeded(deadline, time.monotonic()):
                await self._fail_clock()
                return
            pcm = self._mixer.mix_next_frame()
            self._log_live_metrics()
            if self._artifact_sink is not None:
                self._artifact_sink.write_frame(pcm)
            if self._speech_stream is not None and not self._live_failed:
                try:
                    await self._push_live_frame(pcm)
                except Exception as error:  # provider SDK boundary
                    await self._fail_live(error)

    async def _push_live_frame(self, mixed_pcm: bytes) -> None:
        """Route one tick through the continuous and bounded recovery streams."""
        if self._speech_stream is None:
            return
        routed = self._overlap_router.route(
            mixed_pcm=mixed_pcm,
            speaking_frames=self._mixer.speaking_frames,
        )
        if self._speaker_attribution is not None:
            self._speaker_attribution.observe_frame(routed.main_track_refs)
        await self._sync_recovery_sessions(routed.recovery_frames)
        self._speech_stream.push_frame(self._audio_frame(routed.main_pcm))

    async def _sync_recovery_sessions(
        self,
        recovery_frames: dict[str, bytes],
    ) -> None:
        """Open only current secondary tracks and close completed windows."""
        for track_ref in self._recovery_sessions.keys() - recovery_frames.keys():
            self._schedule_recovery_close(track_ref)
        for track_ref, pcm in recovery_frames.items():
            session = self._recovery_sessions.get(track_ref)
            if session is None:
                session = await self._open_recovery_session(track_ref)
            if session is None:
                continue
            try:
                session.stream.push_frame(self._audio_frame(pcm))
            except Exception as error:
                self._schedule_recovery_close(track_ref)
                await self._report_recovery_failure(error)

    async def _open_recovery_session(
        self,
        track_ref: str,
    ) -> _OverlapRecoverySession | None:
        """Open one temporary provider stream for an overlapping microphone."""
        if (
            self._provider_factory is None
            or self._recovery_event_handler_factory is None
            or track_ref in self._closing_recovery_track_refs
            or len(self._recovery_sessions) + len(self._recovery_close_tasks)
            >= self._maximum_overlap_recovery_streams
        ):
            return None
        participant_identity = self._participant_identity_by_track.get(track_ref)
        if participant_identity is None:
            return None
        provider = self._provider_factory()
        try:
            stream = provider.stream()
            handler = self._recovery_event_handler_factory(
                participant_identity,
                track_ref,
            )
        except Exception as error:
            await provider.aclose()
            await self._report_recovery_failure(error)
            return None
        session = _OverlapRecoverySession(
            provider=provider,
            stream=stream,
            handler=handler,
        )
        self._recovery_sessions[track_ref] = session
        session.event_task = asyncio.create_task(
            self._consume_recovery_events(track_ref, session)
        )
        return session

    async def _consume_recovery_events(
        self,
        track_ref: str,
        session: _OverlapRecoverySession,
    ) -> None:
        """Publish one secondary microphone until its overlap window closes."""
        try:
            async for event in session.stream:
                await session.handler(event)
            if not session.closing and not self._closed:
                await self._report_recovery_failure(
                    RuntimeError("overlap recovery provider closed")
                )
        except asyncio.CancelledError:
            raise
        except Exception as error:  # provider SDK boundary
            if not session.closing:
                await self._report_recovery_failure(error)
        finally:
            if self._recovery_sessions.get(track_ref) is session:
                self._recovery_sessions.pop(track_ref, None)

    def _schedule_recovery_close(self, track_ref: str) -> None:
        """Close one temporary stream without blocking the room clock."""
        session = self._recovery_sessions.pop(track_ref, None)
        if session is None:
            return
        session.closing = True
        self._closing_recovery_track_refs.add(track_ref)
        task = asyncio.create_task(self._close_recovery_session(track_ref, session))
        self._recovery_close_tasks.add(task)
        task.add_done_callback(self._recovery_close_tasks.discard)

    async def _close_recovery_session(
        self,
        track_ref: str,
        session: _OverlapRecoverySession,
    ) -> None:
        """Flush final provider text before releasing temporary resources."""
        try:
            await session.stream.aclose()
            if session.event_task is not None:
                await session.event_task
            await session.provider.aclose()
        except Exception as error:  # provider SDK boundary
            await self._report_recovery_failure(error)
        finally:
            self._closing_recovery_track_refs.discard(track_ref)

    async def _close_all_recovery_sessions(self) -> None:
        """Flush every temporary stream and wait for in-flight closes."""
        for track_ref in tuple(self._recovery_sessions):
            self._schedule_recovery_close(track_ref)
        tasks = tuple(self._recovery_close_tasks)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _report_recovery_failure(self, error: Exception) -> None:
        """Expose a recovery gap without failing the continuous room stream."""
        logger.warning("Room overlap recovery failed: %s", type(error).__name__)
        if self._live_failure_handler is not None:
            await self._live_failure_handler(error)

    def _audio_frame(self, pcm: bytes) -> rtc.AudioFrame:
        """Wrap one exact room-clock PCM frame for a provider stream."""
        return rtc.AudioFrame(
            data=pcm,
            sample_rate=self._sample_rate_hz,
            num_channels=1,
            samples_per_channel=self._frame_samples,
        )

    def _log_live_metrics(self) -> None:
        """Publish periodic content-free mixer health for live diagnosis."""
        if self._provider_factory is None:
            return
        snapshot = self._mixer.snapshot()
        if snapshot.output_frame_count % LIVE_METRICS_INTERVAL_FRAMES != 0:
            return
        logger.info(
            "Room live mixer health output_frames=%d authorized_tracks=%d "
            "pending_tracks=%d excluded_tracks=%d speaking_tracks=%d "
            "dropped_frames=%d underrun_frames=%d",
            snapshot.output_frame_count,
            snapshot.authorized_track_count,
            snapshot.pending_track_count,
            snapshot.excluded_track_count,
            snapshot.speaking_track_count,
            snapshot.dropped_frame_count,
            snapshot.track_underrun_frame_count,
        )

    async def _fail_clock(self) -> None:
        """Stop a run whose fixed clock can no longer prove audio continuity."""
        error = RuntimeError("room audio clock exceeded maximum lag")
        if self._provider_factory is not None:
            await self._fail_live(error)
            return
        await self._record_terminal_failure(error)

    def _clock_lag_exceeded(self, deadline: float, observed_at: float) -> bool:
        """Return whether one clock observation crossed the continuity bound."""
        return observed_at - deadline > self._maximum_clock_lag_seconds

    async def _fail_live(self, error: Exception) -> None:
        """Fence a broken provider and expose one terminal run failure."""
        async with self._provider_lock:
            if self._live_failed or self._closed:
                return
            self._live_failed = True
            await self._close_all_recovery_sessions()
            await self._close_live_provider()
            await self._record_terminal_failure(error)

    async def _consume_speech_events(self) -> None:
        """Forward provider output to the room-owned publisher."""
        if self._speech_stream is None:
            raise RuntimeError("room speech stream is unavailable")
        if self._speech_event_handler is None:
            raise RuntimeError("room speech event handler is unavailable")
        try:
            async for event in self._speech_stream:
                await self._speech_event_handler(event)
            if not self._closed and not self._live_failed:
                await self._fail_live(RuntimeError("room live provider closed"))
        except asyncio.CancelledError:
            raise
        except Exception as error:  # provider SDK boundary
            await self._fail_live(error)

    async def _open_live_provider(self) -> None:
        """Open one provider generation and bind a fresh output leg."""
        if self._provider_factory is None or self._speech_event_handler_factory is None:
            raise RuntimeError("live provider configuration is incomplete")
        provider = self._provider_factory()
        try:
            stream = provider.stream()
            handler = self._speech_event_handler_factory()
        except Exception:
            await provider.aclose()
            raise
        self._provider = provider
        self._speech_stream = stream
        self._speech_event_handler = handler
        self._live_failed = False

    async def _close_live_provider(self) -> None:
        """Close the current provider generation and drain its event task."""
        stream = self._speech_stream
        provider = self._provider
        event_task = self._event_task
        self._speech_stream = None
        self._provider = None
        self._speech_event_handler = None
        self._event_task = None
        if stream is not None:
            await stream.aclose()
        if event_task is not None and event_task is not asyncio.current_task():
            await self._cancel_task(event_task)
        if provider is not None:
            await provider.aclose()

    async def _record_terminal_failure(self, error: Exception) -> None:
        """Publish an optional redacted gap and make the run fail closed."""
        if self._terminal_failure.is_set():
            return
        if self._live_failure_handler is not None:
            try:
                await self._live_failure_handler(error)
            except Exception as handler_error:
                error = handler_error
        self._terminal_error = error
        self._terminal_failure.set()

    @staticmethod
    async def _cancel_task(task: asyncio.Task | None) -> None:
        """Cancel and drain one internal task."""
        if task is None:
            return
        task.cancel()
        with contextlib.suppress(BaseException):
            await task
