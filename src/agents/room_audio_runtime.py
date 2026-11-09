"""One bounded live-ASR and post-audio runtime for a meeting room."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from typing import Protocol

from livekit import rtc

from live_transcript_event import LiveTranscriptEvent
from room_audio_active_track_router import RoomAudioActiveTrackRouter
from room_audio_mixer import RoomAudioMixer, RoomAudioMixerSnapshot
from room_audio_ogg import RoomAudioArtifact
from room_audio_speaker_evidence import RoomAudioSpeakerTimeline

LIVEKIT_AUDIO_STREAM_CAPACITY = 0
LIVE_METRICS_INTERVAL_FRAMES = 500

logger = logging.getLogger("room-audio-worker.runtime")


class RoomSpeechStream(Protocol):
    """Single realtime provider stream bound to one microphone track."""

    def push_frame(self, frame: rtc.AudioFrame) -> None:
        """Accept one fixed-clock room frame."""

    def __aiter__(self):
        """Yield provider speech events."""

    async def wait_ready(self) -> None:
        """Wait until the provider accepted the session configuration."""

    async def finish_input_and_drain(self) -> None:
        """Finish input and drain every provider event."""


class RoomLiveProvider(Protocol):
    """Provider capable of opening one track-bound stream."""

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


TranscriptEventHandler = Callable[[LiveTranscriptEvent], Awaitable[None]]


@dataclass(frozen=True)
class TrackSourceBinding:
    """Immutable source authority fixed before one provider session opens."""

    participant_identity: str
    track_ref: str
    authority_generation: int
    session_generation: int


@dataclass(frozen=True)
class TrackParticipantBinding:
    """Signed participant identity attached to one admitted microphone track."""

    participant_ref: str
    display_name: str | None


TrackEventHandlerFactory = Callable[[TrackSourceBinding], TranscriptEventHandler]
LiveFailureHandler = Callable[[Exception], Awaitable[None]]
ProviderFactory = Callable[[], RoomLiveProvider]


@dataclass(frozen=True)
class RoomAudioRuntimeConfig:
    """Fixed room clock and application-owned per-track buffer bounds."""

    frame_samples: int = 480
    sample_rate_hz: int = 24_000
    maximum_buffered_frames: int = 10
    maximum_clock_lag_seconds: float = 0.25
    maximum_active_track_streams: int = 3
    maximum_provider_sessions: int = 3
    maximum_draining_sessions: int = 3
    active_track_hangover_ms: int = 5_000
    opening_timeout_seconds: float = 10.0
    shutdown_timeout_seconds: float = 10.0


DEFAULT_RUNTIME_CONFIG = RoomAudioRuntimeConfig()


@dataclass(frozen=True)
class RoomLiveOutput:
    """Exact dependencies for one live provider output."""

    provider_factory: ProviderFactory
    track_event_handler_factory: TrackEventHandlerFactory
    failure_handler: LiveFailureHandler


@dataclass
class _TrackBoundSession:
    """One provider stream bound to an exact microphone owner."""

    provider: RoomLiveProvider
    stream: RoomSpeechStream
    handler: TranscriptEventHandler
    binding: TrackSourceBinding
    event_task: asyncio.Task | None = None
    opening_task: asyncio.Task | None = None
    state: str = "opening"
    closing: bool = False


class ProviderSessionCapacityUnavailable(RuntimeError):
    """Report a recoverable provider-session admission refusal."""

    def __init__(self, scope: str):
        """Name the exhausted capacity boundary without provider details."""
        super().__init__(f"{scope} provider session capacity unavailable")
        self.scope = scope


class ProviderSessionQuota:
    """Share one provider-session cap across every room in this process."""

    def __init__(self, maximum_sessions: int):
        """Create a fail-closed process quota."""
        if maximum_sessions <= 0:
            raise ValueError("provider session quota must be positive")
        self._maximum_sessions = maximum_sessions
        self._active_sessions = 0
        self._release_version = 0
        self._lock = threading.Lock()

    @property
    def release_version(self) -> int:
        """Return the monotonic version of proven capacity releases."""
        with self._lock:
            return self._release_version

    def acquire(self) -> bool:
        """Reserve one session without waiting behind unbounded work."""
        with self._lock:
            if self._active_sessions >= self._maximum_sessions:
                return False
            self._active_sessions += 1
            return True

    def release(self) -> None:
        """Release exactly one prior reservation."""
        with self._lock:
            if self._active_sessions <= 0:
                raise RuntimeError("provider session quota release is unbalanced")
            self._active_sessions -= 1
            self._release_version += 1


PROCESS_PROVIDER_SESSION_QUOTA = ProviderSessionQuota(maximum_sessions=300)


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
        return RoomAudioRun(
            live_output=live_output,
            config=_live_runtime_config(effect, config),
        )
    raise ValueError("unsupported room audio pipeline")


def _live_runtime_config(
    effect: dict,
    base_config: RoomAudioRuntimeConfig,
) -> RoomAudioRuntimeConfig:
    """Bind provider lifecycle limits to the verified signed profile."""
    profile = effect.get("source_profile")
    if not isinstance(profile, dict):
        raise ValueError("live room audio source profile is required")
    return replace(
        base_config,
        maximum_active_track_streams=profile["maximum_concurrent_tracks"],
        maximum_provider_sessions=profile["maximum_provider_sessions"],
        maximum_draining_sessions=profile["maximum_draining_sessions"],
        active_track_hangover_ms=profile["hangover_ms"],
        opening_timeout_seconds=profile["opening_timeout_ms"] / 1_000,
        shutdown_timeout_seconds=profile["drain_timeout_ms"] / 1_000,
    )


class RoomAudioRun:
    """Own one room clock, one continuous live stream and bounded recoveries."""

    def __init__(
        self,
        *,
        live_output: RoomLiveOutput | None = None,
        artifact_sink: RoomAudioSink | None = None,
        config: RoomAudioRuntimeConfig = DEFAULT_RUNTIME_CONFIG,
        provider_session_quota: ProviderSessionQuota = PROCESS_PROVIDER_SESSION_QUOTA,
    ):
        """Prepare a stopped room run; no provider opens before start()."""
        if (live_output is None) == (artifact_sink is None):
            raise ValueError("room audio run requires a live or post output")
        if live_output is not None and (
            live_output.provider_factory is None
            or live_output.track_event_handler_factory is None
            or live_output.failure_handler is None
        ):
            raise ValueError("live room audio requires event and failure handlers")
        self._provider_factory = live_output.provider_factory if live_output else None
        self._track_event_handler_factory = (
            live_output.track_event_handler_factory if live_output else None
        )
        self._artifact_sink = artifact_sink
        self._speaker_timeline = (
            RoomAudioSpeakerTimeline() if artifact_sink is not None else None
        )
        self._timeline_elapsed_ms = 0
        self._live_failure_handler = (
            live_output.failure_handler if live_output else None
        )
        self._configure_runtime(config)
        self._provider_session_quota = provider_session_quota
        self._track_streams: dict[str, rtc.AudioStream] = {}
        self._track_tasks: dict[str, asyncio.Task] = {}
        self._participant_identity_by_track: dict[str, str] = {}
        self._authority_generation_by_track: dict[str, int] = {}
        self._session_generation_by_track: dict[str, int] = {}
        self._track_sessions: dict[str, _TrackBoundSession] = {}
        self._track_close_tasks: set[asyncio.Task] = set()
        self._track_close_errors: list[BaseException] = []
        self._capacity_waiters: OrderedDict[str, tuple[int, int]] = OrderedDict()
        self._room_capacity_version = 0
        self._capacity_gap_track_refs: set[str] = set()
        self._capacity_gap_dropped_frames: dict[str, int] = {}
        self._quarantined_provider_reservations = 0
        self._quarantined_provider_close_tasks: set[asyncio.Task] = set()
        self._failure_notification_tasks: set[asyncio.Task] = set()
        self._clock_task: asyncio.Task | None = None
        self._provider_lock = asyncio.Lock()
        self._terminal_failure = asyncio.Event()
        self._terminal_error: Exception | None = None
        self._started = False
        self._closing = False
        self._closed = False
        self._close_error: BaseException | None = None
        self._live_failed = False

    def _configure_runtime(self, config: RoomAudioRuntimeConfig) -> None:
        """Validate and install immutable audio and provider bounds."""
        frame_duration_seconds = config.frame_samples / config.sample_rate_hz
        if config.maximum_clock_lag_seconds <= frame_duration_seconds:
            raise ValueError("room audio clock lag bound must exceed one frame")
        provider_bounds = (
            config.maximum_active_track_streams,
            config.maximum_provider_sessions,
            config.maximum_draining_sessions,
        )
        if any(bound <= 0 for bound in provider_bounds):
            raise ValueError("provider session bounds must be positive")
        if config.maximum_active_track_streams > config.maximum_provider_sessions:
            raise ValueError("active track bound exceeds provider session bound")
        if config.maximum_draining_sessions != config.maximum_provider_sessions:
            raise ValueError("draining session bound must equal provider session bound")
        if config.opening_timeout_seconds <= 0 or config.shutdown_timeout_seconds <= 0:
            raise ValueError("provider lifecycle timeouts must be positive")
        if config.active_track_hangover_ms <= 0:
            raise ValueError("active track hangover must be positive")

        self._frame_samples = config.frame_samples
        self._sample_rate_hz = config.sample_rate_hz
        self._maximum_buffered_frames = config.maximum_buffered_frames
        self._frame_duration_seconds = frame_duration_seconds
        self._maximum_clock_lag_seconds = config.maximum_clock_lag_seconds
        self._opening_timeout_seconds = config.opening_timeout_seconds
        self._shutdown_timeout_seconds = config.shutdown_timeout_seconds
        self._mixer = RoomAudioMixer(
            frame_samples=config.frame_samples,
            sample_rate_hz=config.sample_rate_hz,
            maximum_buffered_frames=config.maximum_buffered_frames,
        )
        self._active_track_router = RoomAudioActiveTrackRouter(
            maximum_active_streams=config.maximum_active_track_streams,
            release_silence_frames=max(
                1,
                round(
                    config.active_track_hangover_ms / (frame_duration_seconds * 1_000)
                ),
            ),
        )
        self._maximum_active_track_streams = config.maximum_active_track_streams
        self._maximum_provider_sessions = config.maximum_provider_sessions

    @property
    def provider_session_count(self) -> int:
        """Expose the bounded provider cardinality for qualification."""
        return (
            len(self._track_sessions)
            + len(self._track_close_tasks)
            + self._quarantined_provider_reservations
        )

    @property
    def active_provider_session_count(self) -> int:
        """Return sessions that can still accept room audio frames."""
        return len(self._track_sessions)

    @property
    def draining_provider_session_count(self) -> int:
        """Return sessions finalizing provider output before closure."""
        return len(self._track_close_tasks)

    @property
    def has_live_output(self) -> bool:
        """Return whether this run owns a realtime provider boundary."""
        return self._provider_factory is not None

    def snapshot(self) -> RoomAudioMixerSnapshot:
        """Return content-free mixer metrics."""
        return self._mixer.snapshot()

    async def start(self) -> None:
        """Start the room clock; providers open only for speaking tracks."""
        if self._closed:
            raise RuntimeError("room audio run is closed")
        if self._started:
            return
        self._started = True
        self._clock_task = asyncio.create_task(self._run_clock())

    async def restart_live(self) -> None:
        """Replace one provider generation after a recovered room connection."""
        if not self._started or self._closed or self._provider_factory is None:
            raise RuntimeError("live room audio run is not restartable")
        if self._terminal_failure.is_set():
            raise RuntimeError("failed live room audio run is not restartable")
        async with self._provider_lock:
            await self._close_all_track_sessions()
            self._active_track_router.reset()
            self._clear_all_capacity_waiters()

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
        self._authority_generation_by_track.pop(track_ref, None)
        self._active_track_router.remove_track(track_ref)
        self._clear_track_capacity_waiter(track_ref)
        self._schedule_track_close(track_ref)
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
        participant: TrackParticipantBinding,
    ) -> None:
        """Apply exact Core admission to one observed track."""
        previous_generation = self._authority_generation_by_track.get(track_ref)
        self._mixer.admit_track(
            track_ref,
            generation=generation,
            expires_at_ms=expires_at_ms,
            authorization_digest=authorization_digest,
        )
        self._authority_generation_by_track[track_ref] = generation
        if self._speaker_timeline is not None:
            self._speaker_timeline.register(
                track_ref,
                participant_ref=participant.participant_ref,
                participant_label=participant.display_name,
            )
        if previous_generation is not None and previous_generation != generation:
            self._schedule_track_close(track_ref)

    def revoke_track(
        self, track_ref: str, *, generation: int, authorization_digest: str
    ) -> None:
        """Apply exact Core revocation and discard queued audio."""
        self._mixer.revoke_track(
            track_ref,
            generation=generation,
            authorization_digest=authorization_digest,
        )
        self._authority_generation_by_track.pop(track_ref, None)
        self._active_track_router.remove_track(track_ref)
        self._clear_track_capacity_waiter(track_ref)
        self._schedule_track_close(track_ref)

    async def aclose(self) -> RoomAudioArtifact | None:
        """Stop inputs and finalize the configured post output, when present."""
        if self._closed:
            if self._close_error is not None:
                raise RuntimeError(
                    "room audio close previously failed"
                ) from self._close_error
            return self._artifact_sink.finish() if self._artifact_sink else None
        if self._closing:
            raise RuntimeError("room audio close is already in progress")
        self._closing = True
        try:
            tracks = list(self._track_streams)
            await asyncio.gather(
                *(self.unobserve_track(track_ref) for track_ref in tracks),
                return_exceptions=True,
            )
            await self._cancel_task(self._clock_task)
            if self._speaker_timeline is not None:
                self._speaker_timeline.finish(ended_at_ms=self._timeline_elapsed_ms)
            async with self._provider_lock:
                await self._close_all_track_sessions()
                self._clear_all_capacity_waiters()
            return self._artifact_sink.finish() if self._artifact_sink else None
        except BaseException as error:
            self._close_error = error
            raise
        finally:
            self._closed = True
            self._closing = False

    @property
    def speaker_timeline(self) -> RoomAudioSpeakerTimeline | None:
        """Return the post evidence timeline owned by this room run."""
        return self._speaker_timeline

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
            if self._speaker_timeline is not None:
                self._speaker_timeline.observe(
                    self._mixer.speaking_track_refs,
                    at_ms=self._timeline_elapsed_ms,
                )
                self._timeline_elapsed_ms += round(self._frame_duration_seconds * 1_000)
            self._log_live_metrics()
            if self._artifact_sink is not None:
                self._artifact_sink.write_frame(pcm)
            if self._provider_factory is not None and not self._live_failed:
                try:
                    await self._push_live_frame(pcm)
                except Exception as error:  # provider SDK boundary
                    await self._fail_live(error)

    async def _push_live_frame(self, mixed_pcm: bytes) -> None:
        """Route one tick through bounded track-owned provider streams."""
        async with self._provider_lock:
            routed = self._active_track_router.route(
                speaking_frames=self._mixer.speaking_frames,
                frame_byte_count=len(mixed_pcm),
            )
            for track_ref in routed.saturated_track_refs:
                self._record_capacity_gap(track_ref, scope="active-track")
            await self._sync_track_sessions(routed.track_frames)

    async def _sync_track_sessions(
        self,
        track_frames: dict[str, bytes],
    ) -> None:
        """Open current speaking tracks and close completed speech windows."""
        for track_ref in tuple(self._capacity_waiters):
            if track_ref not in track_frames:
                self._clear_track_capacity_waiter(track_ref)
        for track_ref in self._track_sessions.keys() - track_frames.keys():
            self._schedule_track_close(track_ref)

        waiting_track_refs = [
            track_ref
            for track_ref in self._capacity_waiters
            if track_ref in track_frames
        ]
        new_track_refs = sorted(set(track_frames) - set(waiting_track_refs))
        for track_ref in [*waiting_track_refs, *new_track_refs]:
            pcm = track_frames[track_ref]
            session = self._track_sessions.get(track_ref)
            if session is None:
                if not self._track_can_retry_capacity(track_ref):
                    self._record_capacity_gap(track_ref, scope="provider")
                    continue
                try:
                    session = await self._open_track_session(track_ref)
                except ProviderSessionCapacityUnavailable as error:
                    self._wait_for_track_capacity(track_ref, error)
                    continue
                self._capacity_waiters.pop(track_ref, None)
                self._close_capacity_gap(track_ref)
            try:
                session.stream.push_frame(self._audio_frame(pcm))
            except Exception:
                self._schedule_track_close(track_ref)
                raise

    async def _open_track_session(
        self,
        track_ref: str,
    ) -> _TrackBoundSession:
        """Open one provider stream for an admitted speaking microphone."""
        if self._provider_factory is None or self._track_event_handler_factory is None:
            raise RuntimeError("live provider dependencies are unavailable")
        if self.active_provider_session_count >= self._maximum_active_track_streams:
            raise ProviderSessionCapacityUnavailable("active-track")
        if self.provider_session_count >= self._maximum_provider_sessions:
            raise ProviderSessionCapacityUnavailable("room")
        participant_identity = self._participant_identity_by_track.get(track_ref)
        authority_generation = self._authority_generation_by_track.get(track_ref)
        if participant_identity is None or authority_generation is None:
            raise RuntimeError("track source authority is unavailable")
        if not self._provider_session_quota.acquire():
            raise ProviderSessionCapacityUnavailable("process")
        session_generation = self._session_generation_by_track.get(track_ref, 0) + 1
        self._session_generation_by_track[track_ref] = session_generation
        binding = TrackSourceBinding(
            participant_identity=participant_identity,
            track_ref=track_ref,
            authority_generation=authority_generation,
            session_generation=session_generation,
        )
        provider: RoomLiveProvider | None = None
        try:
            provider = self._provider_factory()
            stream = provider.stream()
            handler = self._track_event_handler_factory(binding)
        except Exception as error:
            if provider is None:
                self._provider_session_quota.release()
            else:
                close_task = asyncio.create_task(provider.aclose())
                deadline = (
                    asyncio.get_running_loop().time() + self._shutdown_timeout_seconds
                )
                try:
                    await self._await_before_deadline(
                        close_task,
                        deadline=deadline,
                        cancel_on_timeout=False,
                    )
                except BaseException:
                    self._quarantine_provider_close(close_task)
                else:
                    self._provider_session_quota.release()
            raise RuntimeError("track provider session failed") from error
        session = _TrackBoundSession(
            provider=provider,
            stream=stream,
            handler=handler,
            binding=binding,
        )
        self._track_sessions[track_ref] = session
        session.event_task = asyncio.create_task(
            self._consume_track_events(track_ref, session)
        )
        session.opening_task = asyncio.create_task(
            self._wait_for_session_ready(session)
        )
        return session

    def _track_can_retry_capacity(self, track_ref: str) -> bool:
        """Retry only the oldest waiter after a proven capacity release."""
        observed_version = self._capacity_waiters.get(track_ref)
        if observed_version is None:
            return True
        oldest_waiter = next(iter(self._capacity_waiters), None)
        return (
            oldest_waiter == track_ref and self._capacity_version() != observed_version
        )

    def _wait_for_track_capacity(
        self,
        track_ref: str,
        error: ProviderSessionCapacityUnavailable,
    ) -> None:
        """Queue one track once without turning each frame into a retry."""
        self._capacity_waiters[track_ref] = self._capacity_version()
        self._record_capacity_gap(track_ref, scope=error.scope)

    def _capacity_version(self) -> tuple[int, int]:
        """Bind retries to local or process-wide proven capacity changes."""
        return (
            self._room_capacity_version,
            self._provider_session_quota.release_version,
        )

    def _record_capacity_gap(self, track_ref: str, *, scope: str) -> None:
        """Expose one bounded degraded episode while the room stays alive."""
        dropped_frames = self._capacity_gap_dropped_frames.get(track_ref, 0) + 1
        self._capacity_gap_dropped_frames[track_ref] = dropped_frames
        if track_ref in self._capacity_gap_track_refs:
            return
        self._capacity_gap_track_refs.add(track_ref)
        logger.warning(
            "Room live provider capacity gap opened scope=%s track_bound=%s",
            scope,
            track_ref in self._participant_identity_by_track,
        )
        self._schedule_failure_notification(ProviderSessionCapacityUnavailable(scope))

    def _close_capacity_gap(self, track_ref: str) -> None:
        """Close internal gap accounting after the track resumes."""
        if track_ref not in self._capacity_gap_track_refs:
            return
        self._capacity_gap_track_refs.remove(track_ref)
        dropped_frames = self._capacity_gap_dropped_frames.pop(track_ref, 0)
        logger.info(
            "Room live provider capacity gap closed dropped_frames=%d",
            dropped_frames,
        )

    def _clear_track_capacity_waiter(self, track_ref: str) -> None:
        """Forget one no-longer-eligible track and its degraded episode."""
        self._capacity_waiters.pop(track_ref, None)
        self._close_capacity_gap(track_ref)

    def _clear_all_capacity_waiters(self) -> None:
        """Clear capacity state when the room generation ends."""
        for track_ref in tuple(self._capacity_gap_track_refs):
            self._close_capacity_gap(track_ref)
        self._capacity_waiters.clear()

    async def _wait_for_session_ready(self, session: _TrackBoundSession) -> None:
        """Apply the signed provider opening deadline."""
        try:
            await asyncio.wait_for(
                session.stream.wait_ready(),
                timeout=self._opening_timeout_seconds,
            )
            if not session.closing:
                session.state = "active"
        except asyncio.CancelledError:
            raise
        except Exception as error:
            if not session.closing:
                asyncio.create_task(self._fail_live(error))

    async def _consume_track_events(
        self,
        track_ref: str,
        session: _TrackBoundSession,
    ) -> None:
        """Publish one microphone until its active speech window closes."""
        try:
            async for event in session.stream:
                await session.handler(event)
            if not session.closing and not self._closed:
                asyncio.create_task(
                    self._fail_live(RuntimeError("track provider closed"))
                )
        except asyncio.CancelledError:
            raise
        except Exception as error:  # provider SDK boundary
            if not session.closing:
                asyncio.create_task(self._fail_live(error))

    def _schedule_track_close(self, track_ref: str) -> None:
        """Drain one track stream without blocking the room clock."""
        session = self._track_sessions.pop(track_ref, None)
        if session is None:
            return
        self._room_capacity_version += 1
        session.closing = True
        session.state = "draining"
        if session.opening_task is not None:
            session.opening_task.cancel()
        task = asyncio.create_task(self._close_track_session(track_ref, session))
        self._track_close_tasks.add(task)
        task.add_done_callback(self._track_close_done)

    async def _close_track_session(
        self,
        track_ref: str,
        session: _TrackBoundSession,
    ) -> None:
        """Finish input and publish final provider text before release."""
        deadline = asyncio.get_running_loop().time() + self._shutdown_timeout_seconds
        drain_error: BaseException | None = None
        try:
            await self._await_before_deadline(
                asyncio.create_task(self._drain_session(session)),
                deadline=deadline,
                cancel_on_timeout=True,
            )
        except BaseException as error:
            drain_error = error

        close_task = asyncio.create_task(session.provider.aclose())
        close_error: BaseException | None = None
        try:
            await self._await_before_deadline(
                close_task,
                deadline=deadline,
                cancel_on_timeout=False,
            )
        except BaseException as error:
            close_error = error
            self._quarantine_provider_close(close_task)
        else:
            self._provider_session_quota.release()

        if drain_error is not None or close_error is not None:
            failure = drain_error or close_error
            details = []
            if drain_error is not None:
                details.append(self._shutdown_error_detail("drain", drain_error))
            if close_error is not None:
                details.append(self._shutdown_error_detail("close", close_error))
            raise RuntimeError(
                f"track provider drain failed: {'; '.join(details)}"
            ) from failure

    @staticmethod
    async def _drain_session(session: _TrackBoundSession) -> None:
        """Drain transcript events without owning provider closure."""
        await session.stream.finish_input_and_drain()
        if session.event_task is not None:
            await session.event_task

    @staticmethod
    async def _await_before_deadline(
        task: asyncio.Task,
        *,
        deadline: float,
        cancel_on_timeout: bool,
    ) -> None:
        """Wait for one task without waiting on cancellation past the deadline."""
        remaining = max(0.0, deadline - asyncio.get_running_loop().time())
        done, _ = await asyncio.wait({task}, timeout=remaining)
        if task in done:
            await task
            return
        if cancel_on_timeout:
            task.cancel()
            task.add_done_callback(RoomAudioRun._consume_task_outcome)
        raise TimeoutError("provider lifecycle deadline exceeded")

    @staticmethod
    def _consume_task_outcome(task: asyncio.Task) -> None:
        """Consume one detached task result without surfacing its exception."""
        if task.cancelled():
            return
        with contextlib.suppress(BaseException):
            task.exception()

    @staticmethod
    def _shutdown_error_detail(stage: str, error: BaseException) -> str:
        """Return one content-free lifecycle failure description."""
        if isinstance(error, TimeoutError):
            return f"{stage} shutdown timed out"
        return f"{stage} failed ({type(error).__name__})"

    def _quarantine_provider_close(self, task: asyncio.Task) -> None:
        """Keep uncertain provider capacity reserved until closure is proven."""
        self._quarantined_provider_reservations += 1
        self._quarantined_provider_close_tasks.add(task)
        task.add_done_callback(self._provider_close_quarantine_done)

    def _provider_close_quarantine_done(self, task: asyncio.Task) -> None:
        """Release quarantined capacity only after a successful provider close."""
        self._quarantined_provider_close_tasks.discard(task)
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            return
        self._quarantined_provider_reservations -= 1
        self._room_capacity_version += 1
        self._provider_session_quota.release()

    def _track_close_done(self, task: asyncio.Task) -> None:
        """Release bookkeeping and expose a gap on an unsuccessful drain."""
        self._track_close_tasks.discard(task)
        self._room_capacity_version += 1
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            self._track_close_errors.append(error)
        if (
            error is not None
            and not self._closing
            and not self._closed
            and not self._live_failed
        ):
            self._schedule_failure_notification(error)

    async def _close_all_track_sessions(self) -> None:
        """Drain every active stream and wait for in-flight closes."""
        errors: list[BaseException] = []
        for track_ref in tuple(self._track_sessions):
            self._schedule_track_close(track_ref)
        tasks = tuple(self._track_close_tasks)
        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            errors.extend(
                result for result in results if isinstance(result, BaseException)
            )
        errors.extend(self._track_close_errors)
        self._track_close_errors = []
        if errors:
            primary_error = errors[0]
            raise RuntimeError(
                f"track provider drain failed: {primary_error}"
            ) from primary_error

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
            "active_provider_sessions=%d draining_provider_sessions=%d "
            "provider_sessions=%d dropped_frames=%d underrun_frames=%d",
            snapshot.output_frame_count,
            snapshot.authorized_track_count,
            snapshot.pending_track_count,
            snapshot.excluded_track_count,
            snapshot.speaking_track_count,
            len(self._track_sessions),
            len(self._track_close_tasks),
            self.provider_session_count,
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
            cleanup_error: BaseException | None = None
            try:
                await self._close_all_track_sessions()
            except BaseException as caught_error:
                cleanup_error = caught_error
            finally:
                await self._record_terminal_failure(error)
            if cleanup_error is not None:
                logger.error(
                    "Room live provider cleanup failed error=%s",
                    type(cleanup_error).__name__,
                )

    async def _record_terminal_failure(self, error: Exception) -> None:
        """Publish an optional redacted gap and make the run fail closed."""
        if self._terminal_failure.is_set():
            return
        self._terminal_error = error
        self._terminal_failure.set()
        self._schedule_failure_notification(error)

    def _schedule_failure_notification(self, error: Exception) -> None:
        """Schedule one redacted lifecycle notification without blocking audio."""
        if self._live_failure_handler is None or self._closed:
            return
        task = asyncio.create_task(self._notify_failure(error))
        self._failure_notification_tasks.add(task)
        task.add_done_callback(self._failure_notification_tasks.discard)

    async def _notify_failure(self, error: Exception) -> None:
        """Publish one optional gap without delaying room state."""
        handler = self._live_failure_handler
        if handler is None:
            return
        task = asyncio.create_task(handler(error))
        deadline = asyncio.get_running_loop().time() + self._shutdown_timeout_seconds
        try:
            await self._await_before_deadline(
                task,
                deadline=deadline,
                cancel_on_timeout=True,
            )
        except TimeoutError:
            logger.warning("Room live failure notification timed out")
        except Exception as handler_error:
            logger.warning(
                "Room live failure notification failed error=%s",
                type(handler_error).__name__,
            )

    @staticmethod
    async def _cancel_task(task: asyncio.Task | None) -> None:
        """Cancel and drain one internal task."""
        if task is None:
            return
        task.cancel()
        with contextlib.suppress(BaseException):
            await task
