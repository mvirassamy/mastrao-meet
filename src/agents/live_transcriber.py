"""Track-scoped LiveKit transcription with OpenAI's live model."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import os
import time
import uuid
from collections import deque
from collections.abc import Awaitable
from dataclasses import dataclass

from livekit import rtc
from livekit.agents import JobContext, stt
from livekit.plugins import openai

from live_continuation import ContinuationInput, continue_legs
from live_delivery_codec import MAX_LEG_SAMPLES
from live_durable_publisher import DurablePublisher
from live_input_drain import InputDrainError
from live_provider_session import (
    CanonicalProviderSession,
    application_item_ref,
    create_canonical_provider,
)
from live_send_control import (
    MAX_SAFE_SAMPLE,
    PCM_SAMPLE_RATE,
    LiveControlClient,
    LiveSendAuthority,
)

logger = logging.getLogger("transcriber.live")

TRANSCRIPTION_TOPIC = "lk.transcription"
GAP_TOPIC = "mastrao.transcription.gap.v1"
SCHEMA_VERSION = 1
MODEL = "gpt-live-transcribe"
# Retain identities for one track leg up to the canonical transcript item cap.
MAX_ITEMS_PER_TRACK = 100_000
MAX_PENDING_EVENTS_PER_TRACK = 256


async def _await_shutdown(completion: asyncio.Task[None]) -> None:
    """Finish local cleanup before propagating cancellation of a waiter."""
    cancelled = False
    while not completion.done():
        try:
            await asyncio.shield(completion)
        except asyncio.CancelledError:
            if completion.cancelled():
                raise
            cancelled = True
    # Keep a failed shutdown observable to every subsequent caller.
    completion.result()
    if cancelled:
        raise asyncio.CancelledError


def configured_languages() -> str | list[str]:
    """Return the configured language hint, including code-switching hints."""
    values = [
        value.strip()
        for value in os.getenv("OPENAI_STT_LANGUAGES", "fr,en").split(",")
        if value.strip()
    ]
    if not values:
        return ["fr", "en"]
    return values[0] if len(values) == 1 else values


def create_openai_live_stt(vad: object) -> openai.STT:
    """Create the only supported live provider configuration."""
    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY is required for STT_PROVIDER=openai-live")
    return openai.STT(
        language=configured_languages(),
        model=MODEL,
        use_realtime=True,
        vad=vad,
    )


def _milliseconds(value: float) -> int | None:
    """Convert positive SDK seconds; zero is also SpeechData's absent default."""
    if not math.isfinite(value) or value <= 0:
        return None
    return int(value * 1000)


def _audio_timing_reason(start_ms: int | None, end_ms: int | None) -> str:
    """Describe the strongest timing evidence the current SDK leaves us."""
    if start_ms is None and end_ms is None:
        return "sdk-item-timing-unavailable"
    if start_ms is None or end_ms is None:
        return "sdk-item-timing-incomplete"
    if end_ms <= start_ms:
        return "sdk-item-timing-invalid"
    return "provider-stream-origin-unavailable"


def _audio_timing(data: stt.SpeechData, leg_id: str) -> dict[str, object]:
    """Keep SDK timing evidence separate from the legacy UI receipt fallback.

    OpenAI STT 1.8.3 drops raw item audio bounds and session identity before
    emitting FINAL_TRANSCRIPT. Its public client argument does not control the
    aiohttp realtime socket. There is no public raw-event/transport hook.
    Preserve any SpeechData bounds, but never certify their provider origin:
    an SDK stream can reset its socket without replacing this track leg.
    speech_start_time/speech_end_time are wall clocks, not item audio bounds.
    """
    start_ms = _milliseconds(data.start_time)
    end_ms = _milliseconds(data.end_time)
    return {
        "state": "uncertain",
        "reason": _audio_timing_reason(start_ms, end_ms),
        "clock": "sdk-speech-data",
        "unit": "ms",
        "startMs": start_ms,
        "endMs": end_ms,
        "streamOrigin": {
            "type": "sdk-stream",
            "id": leg_id,
            "providerSessionId": None,
        },
        "meetingOffsetMs": None,
    }


def _language(data: stt.SpeechData) -> str | None:
    """Return the detected language without serializing SDK internals."""
    language = str(data.language)
    return language or None


def _native_content(data: stt.SpeechData) -> str | None:
    """Retain native-only final corrections without receipt-time revisions."""
    if data.metadata is None or "native_provenance" not in data.metadata:
        return None
    native = json.dumps(
        data.metadata["native_provenance"], sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(native.encode()).hexdigest()


def _has_transcript(event: stt.SpeechEvent) -> bool:
    """Identify events that need space in the publication queue."""
    return bool(
        event.type
        in (
            stt.SpeechEventType.INTERIM_TRANSCRIPT,
            stt.SpeechEventType.FINAL_TRANSCRIPT,
        )
        and event.alternatives
        and event.alternatives[0].text.strip()
    )


@dataclass(frozen=True)
class TranscriptionEvent:
    """Data needed to publish one transcript revision."""

    participant_identity: str
    track_sid: str
    leg_id: str
    item_id: str
    revision: int
    final: bool
    data: stt.SpeechData
    start_time: int | None
    end_time: int | None


@dataclass(frozen=True)
class _FinalContent:
    """Snapshot semantic SDK fields, excluding receipt-derived timestamps."""

    text: str
    language: str | None
    data_start_time: float
    data_end_time: float
    speech_start_time: float | None
    speech_end_time: float | None
    native_content: str | None = None


@dataclass(frozen=True)
class _ItemState:
    """Keep revisions and finality for one item within its track leg."""

    start_time: int
    revision: int = 0
    end_time: int | None = None
    final_content: _FinalContent | None = None
    durable_timing: dict[str, object] | None = None


def _transcription_times(
    event: stt.SpeechEvent, item: _ItemState, received_at: int
) -> tuple[int, int | None]:
    """Return legacy UI times only; these may mix SDK and receipt clocks."""
    data = event.alternatives[0]
    start_time = _milliseconds(data.start_time)
    end_time = _milliseconds(data.end_time)
    if event.speech_start_time is not None:
        start_time = int(event.speech_start_time * 1000)
    if start_time is None:
        start_time = item.start_time
    if event.speech_end_time is not None:
        end_time = int(event.speech_end_time * 1000)
    if event.type == stt.SpeechEventType.FINAL_TRANSCRIPT and end_time is None:
        end_time = item.end_time
        if item.final_content is None:
            end_time = received_at
    return start_time, end_time


class TranscriptionPublisher:
    """Publish ordered, replaceable transcription envelopes to a LiveKit room."""

    def __init__(self, room: rtc.Room):
        """Initialize a publisher for one worker session."""
        self._room = room
        self._sequence = 0
        self._lock = asyncio.Lock()

    @property
    def sequence(self) -> int:
        """Return the last emitted segment sequence."""
        return self._sequence

    async def publish_segment(self, event: TranscriptionEvent) -> None:
        """Publish one interim or final revision for an OpenAI item."""
        text = event.data.text
        if not text.strip():
            return
        async with self._lock:
            self._sequence += 1
            sequence = self._sequence
            envelope = {
                "schemaVersion": SCHEMA_VERSION,
                "participantIdentity": event.participant_identity,
                "trackSid": event.track_sid,
                "legId": event.leg_id,
                "itemId": event.item_id,
                "sequence": sequence,
                "revision": event.revision,
                "state": "final" if event.final else "interim",
                "text": text,
                "language": _language(event.data),
                "startTime": event.start_time,
                "endTime": event.end_time,
                "audioTiming": _audio_timing(event.data, event.leg_id),
                "receivedAt": int(time.time() * 1000),
            }
            attributes = {
                "mastrao.participant_identity": event.participant_identity,
                "lk.transcribed_track_id": event.track_sid,
                "mastrao.leg_id": event.leg_id,
                "lk.segment_id": event.item_id,
                "lk.transcription_final": str(event.final).lower(),
                "mastrao.sequence": str(sequence),
                "mastrao.revision": str(event.revision),
            }
            await self._room.local_participant.send_text(
                json.dumps(envelope, separators=(",", ":")),
                topic=TRANSCRIPTION_TOPIC,
                attributes=attributes,
            )

    async def publish_gap(self, reason: str) -> None:
        """Publish a versioned marker for audio that may have been missed."""
        gap = {
            "schemaVersion": SCHEMA_VERSION,
            "gapId": uuid.uuid4().hex,
            "reason": reason,
            "receivedAt": int(time.time() * 1000),
        }
        await self._room.local_participant.send_text(
            json.dumps(gap, separators=(",", ":")),
            topic=GAP_TOPIC,
        )


class TrackTranscriber:
    """Transcribe one participant audio track and preserve its identity."""

    def __init__(  # noqa: PLR0913 - track resources plus optional send authority
        self,
        *,
        track: rtc.Track,
        participant: object,
        track_sid: str,
        publisher: TranscriptionPublisher,
        vad: object,
        authority: LiveSendAuthority | None = None,
    ):
        """Initialize an isolated OpenAI stream for a single track."""
        self.track = track
        self.participant = participant
        self.participant_identity = getattr(participant, "identity", "unknown")
        self.track_sid = track_sid
        self._publisher = publisher
        self._vad = vad
        self._authority = authority
        self._durable: DurablePublisher | None = None
        self._control_tasks: list[asyncio.Task[None]] = []
        self._control_join_task: asyncio.Task[None] | None = None
        self._authority_finish_task: asyncio.Task[None] | None = None
        self.leg_id = uuid.uuid4().hex
        self._leg_audio_start_sample = 0
        self._speech_stream: stt.RecognizeStream | None = None
        self._audio_stream: rtc.AudioStream | None = None
        self._provider: CanonicalProviderSession | openai.STT | None = None
        self._pcm_input: ContinuationInput | None = None
        self._continuation_task: asyncio.Task[None] | None = None
        self._leg_close_task: asyncio.Task[None] | None = None
        self._leg_drained = False
        self._published_items: set[str] = set()
        self._publication_ready = asyncio.Event()
        self._audio_task: asyncio.Task[None] | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._publication_task: asyncio.Task[None] | None = None
        self._delivery_close_task: asyncio.Task[None] | None = None
        self._pending_events: deque[stt.SpeechEvent] = deque()
        self._events_ready = asyncio.Event()
        self._speech_read_done = False
        self._incomplete_reasons: list[str] = []
        self._continuation_error: InputDrainError | None = None
        self._run_task: asyncio.Task[None] | None = None
        self._run_done = asyncio.Event()
        self._resource_close_task: asyncio.Task[None] | None = None
        self._close_task: asyncio.Task[None] | None = None
        self._closing = False
        self._items: dict[str, _ItemState] = {}

    @property
    def incomplete_reasons(self) -> tuple[str, ...]:
        """Expose local delivery uncertainty even if publishing a gap fails."""
        return tuple(self._incomplete_reasons)

    def _mark_incomplete(self, reason: str) -> None:
        """Retain uncertainty without claiming durable transcript collection."""
        if reason not in self._incomplete_reasons:
            self._incomplete_reasons.append(reason)
            logger.warning("incomplete transcription for %s: %s", self.leg_id, reason)

    async def run(self) -> None:
        """Pump one LiveKit audio track through its selected provider session."""
        if self._closing:
            return
        self._run_task = asyncio.current_task()
        try:
            if self._authority is not None:
                if not await self._authority.claim() or self._closing:
                    return
                self._durable = DurablePublisher(
                    self._authority, self._fail_durable_delivery
                )
                self._open_authorized_leg()
                self._control_tasks = [
                    asyncio.create_task(
                        self._watch_authority(self._authority.watch_expiry())
                    ),
                    asyncio.create_task(
                        self._watch_authority(self._authority.renew_until_fenced())
                    ),
                ]
            else:
                self._provider = create_openai_live_stt(self._vad)
            await self._run_audio()
        finally:
            self.fence_audio()
            for task in self._control_tasks:
                task.cancel()
            try:
                # A failed local close or delivery prevents a stopped ACK.
                try:
                    await self._close_resources()
                finally:
                    await self._finish_delivery()
                await self._join_control()
                if self._authority is not None:
                    if self._authority_finish_task is None:
                        self._authority_finish_task = asyncio.create_task(
                            self._authority.stopped()
                        )
                    await _await_shutdown(self._authority_finish_task)
            finally:
                try:
                    await self._join_control()
                finally:
                    self._run_done.set()

    async def _run_audio(self) -> None:
        """Run one RTC reader with legacy speech or planned canonical legs."""
        self._audio_stream = rtc.AudioStream.from_track(
            track=self.track,
            sample_rate=24000,
            num_channels=1,
        )
        if self._authority is None:
            self._start_speech()
            self._audio_task = asyncio.create_task(
                self._push_audio(self._audio_stream, self._speech_stream)
            )
            await asyncio.wait(
                (self._reader_task, self._publication_task),
                return_when=asyncio.FIRST_COMPLETED,
            )
        else:
            self._pcm_input = ContinuationInput(
                self._authority.can_send, lambda: self._provider.appended_samples
            )
            self._pcm_input.attach(
                self._speech_stream, self._authority.audio_start_sample
            )
            self._audio_task = asyncio.create_task(self._capture_authorized_audio())
            self._continuation_task = asyncio.create_task(
                continue_legs(
                    self._authority,
                    self._pcm_input,
                    speech_tasks=lambda: (self._reader_task, self._publication_task),
                    drain_leg=self._drain_leg,
                    open_leg=self._open_authorized_leg,
                )
            )
            await asyncio.wait(
                (self._continuation_task, self._audio_task, *self._control_tasks),
                return_when=asyncio.FIRST_COMPLETED,
            )

    def _fail_continuation(self, reason: str) -> None:
        """Latch failure before another task can accept a successor receipt."""
        if self._continuation_error is None:
            self._continuation_error = InputDrainError(reason)
        self._mark_incomplete(reason)
        self.fence_audio()

    def _fail_durable_delivery(self) -> None:
        """Fence in the failing delivery task, before deferred supervision."""
        self._mark_incomplete("durable-delivery-failed")
        self.fence_audio()

    async def _capture_authorized_audio(self) -> None:
        """Fence synchronously on capture failure, before supervision resumes."""
        try:
            await self._pcm_input.run(self._audio_stream)
        except Exception as error:
            reason = "audio-consumption-failed"
            if isinstance(error, InputDrainError):
                reason = str(error)
            self._fail_continuation(reason)
            raise

    def _start_speech(self) -> None:
        """Start the existing independent reader and ordered publisher for a leg."""
        self._speech_stream = self._provider.stream()
        self._reader_task = asyncio.create_task(self._read_speech(self._speech_stream))
        self._reader_task.add_done_callback(self._on_speech_read_done)
        self._publication_task = asyncio.create_task(self._publish_events())

    def _open_authorized_leg(self) -> None:
        """Capture the exact Core leg in a fresh single-acquisition provider guard."""
        authority = self._authority
        leg_ref = authority.leg_ref
        if self._closing or not authority.can_send_leg(leg_ref):
            raise InputDrainError("live-continuation-open-not-authorized")
        self.leg_id = leg_ref
        self._leg_audio_start_sample = authority.audio_start_sample
        self._provider = create_canonical_provider(
            authority.live_profile,
            can_send=lambda: authority.can_send_leg(leg_ref),
            reconnect_refused=lambda: self._mark_incomplete(
                "canonical-provider-reconnect-refused"
            ),
            failed=self._fail_continuation,
            vad=self._vad,
        )
        self._leg_close_task = None
        self._leg_drained = False
        self._speech_read_done = False
        self._items.clear()
        self._published_items.clear()
        self._publication_ready.clear()
        self._start_speech()
        if self._pcm_input is not None:
            self._pcm_input.attach(self._speech_stream, authority.audio_start_sample)

    async def _drain_leg(self, end_sample: int) -> None:
        """Prove old PCM, finals, publication and close before sending Core rotate."""
        if self._incomplete_reasons or self._closing:
            raise InputDrainError("live-continuation-leg-incomplete")
        self._provider.seal_input(
            self._speech_stream, end_sample - self._authority.audio_start_sample
        )
        expected = await self._provider.drain_input()
        if self._provider.terminal_drain_complete(self._speech_stream):
            # Deepgram's proved terminal close ends its SDK iterator. Join every
            # accepted correction, including revisions of already-published IDs.
            await asyncio.gather(self._reader_task, self._publication_task)
        while not expected <= self._published_items:
            for task in (self._reader_task, self._publication_task):
                if task.done():
                    task.result()
                    raise InputDrainError("live-continuation-publication-ended")
            self._publication_ready.clear()
            changed = asyncio.create_task(self._publication_ready.wait())
            try:
                await asyncio.wait(
                    (changed, self._reader_task, self._publication_task),
                    return_when=asyncio.FIRST_COMPLETED,
                )
            finally:
                changed.cancel()
                await asyncio.gather(changed, return_exceptions=True)
        self._provider.terminal_drain_complete(self._speech_stream)
        self._leg_drained = True
        self._provider.fence_audio()
        await self._close_leg()
        await asyncio.gather(self._reader_task, self._publication_task)
        if self._incomplete_reasons:
            raise InputDrainError("live-continuation-delivery-incomplete")

    async def _join_control(self) -> None:
        """Join cancelled I/O after closing audio, preserving run waiters."""
        if not self._control_tasks:
            return
        if self._control_join_task is None:
            self._control_join_task = asyncio.create_task(self._wait_for_control())
        await _await_shutdown(self._control_join_task)

    async def _wait_for_control(self) -> None:
        """Collect cancellation outcomes without propagating transport exceptions."""
        await asyncio.gather(*self._control_tasks, return_exceptions=True)

    def fence_audio(self) -> None:
        """Stop admission synchronously before any await, close or acknowledgement."""
        self._closing = True
        if self._authority is not None:
            self._authority.fence()
            if self._provider is not None:
                self._provider.fence_audio()
        if self._audio_task is not None and not self._audio_task.cancelling():
            self._audio_task.cancel()
        if self._continuation_task is not None and not self._continuation_task.done():
            self._continuation_task.cancel()

    async def _watch_authority(self, operation: Awaitable[None]) -> None:
        """Fence immediately on expiry/refusal, independently of control I/O."""
        try:
            await operation
        except Exception as error:
            logger.warning("live send control lost (%s)", type(error).__name__)
        finally:
            self.fence_audio()
        self._mark_incomplete("live-send-authority-lost")

    async def _read_speech(self, speech_stream: stt.RecognizeStream) -> None:
        """Consume SDK events independently of ordered room publication."""
        async for event in speech_stream:
            if not _has_transcript(event):
                continue
            if len(self._pending_events) >= MAX_PENDING_EVENTS_PER_TRACK:
                self._mark_incomplete("publication-capacity-exceeded")
                if self._authority is not None:
                    self.fence_audio()
                raise RuntimeError("live transcription publication capacity exceeded")
            self._pending_events.append(event)
            self._events_ready.set()

    def _on_speech_read_done(self, _: asyncio.Task[None]) -> None:
        """Wake publication even if the reader was cancelled before starting."""
        self._speech_read_done = True
        self._events_ready.set()

    async def _publish_events(self) -> None:
        """Publish the bounded accepted queue in SDK order, including at stop."""
        try:
            await self._publish_pending_events()
        except BaseException:
            if self._authority is not None:
                self._mark_incomplete("publication-failed")
                self.fence_audio()
            raise

    async def _publish_pending_events(self) -> None:
        """A final becomes rotation-ready only after durable and UI publication."""
        while True:
            await self._events_ready.wait()
            while self._pending_events:
                event = self._pending_events.popleft()
                await self._handle_event(event)
                if event.type == stt.SpeechEventType.FINAL_TRANSCRIPT:
                    self._published_items.add(application_item_ref(event))
                    self._publication_ready.set()
            if self._speech_read_done:
                return
            self._events_ready.clear()

    async def _finish_delivery(self) -> None:
        """Join accepted publications despite cancellation of the run waiter."""
        if self._delivery_close_task is None:
            self._delivery_close_task = asyncio.create_task(self._drain_delivery())
        await _await_shutdown(self._delivery_close_task)

    async def _drain_delivery(self) -> None:
        """Report losses after draining what remains publicly readable."""
        errors: list[BaseException] = []
        if self._continuation_error is not None:
            errors.append(self._continuation_error)
        for task, reason in (
            (self._reader_task, "speech-consumption-failed"),
            (self._publication_task, "publication-failed"),
            (self._audio_task, "audio-consumption-failed"),
            (self._continuation_task, "live-continuation-failed"),
        ):
            if task is None:
                continue
            result = (await asyncio.gather(task, return_exceptions=True))[0]
            if isinstance(result, BaseException):
                if (
                    task in (self._audio_task, self._continuation_task)
                    and task.cancelled()
                ):
                    continue
                if (
                    task is self._reader_task
                    and task.cancelled()
                    and "sdk-close-failed" in self._incomplete_reasons
                ):
                    # The failed SDK close is already retained by _close_resources.
                    continue
                self._mark_incomplete(reason)
                if isinstance(result, InputDrainError):
                    self._mark_incomplete(str(result))
                errors.append(result)
        if self._durable is not None:
            try:
                await self._durable.finish(self._durable_gap_reason())
            except BaseException as error:
                errors.append(error)
        for reason in self._incomplete_reasons:
            try:
                await self._publisher.publish_gap(reason)
            except Exception as error:
                logger.error(
                    "failed to publish transcription gap: %s (%s)",
                    reason,
                    type(error).__name__,
                )
                errors.append(error)
        if errors:
            raise BaseExceptionGroup("live transcription delivery incomplete", errors)

    def _durable_gap_reason(self) -> str | None:
        """Describe known loss separately from the unproved SDK close drain."""
        reasons = set(self._incomplete_reasons) - {"sdk-close-final-drain-unconfirmed"}
        if not reasons:
            return None
        if "live-send-authority-lost" in reasons:
            return "connection_lost"
        if reasons & {"publication-failed", "publication-capacity-exceeded"}:
            return "delivery_failed"
        if "speech-consumption-failed" in reasons:
            return "provider_failed"
        return "capture_incomplete"

    async def aclose(self) -> None:
        """Fence new audio and await the shared local shutdown outcome.

        This does not revoke audio already admitted to the SDK or certify that
        a remote provider has stopped processing previously transmitted audio.
        Completion joins local delivery; incomplete_reasons and gap events
        describe uncertainty at the SDK iterator boundary. Delivery failures
        are retained for every caller, including when reporting a gap fails.
        """
        self.fence_audio()
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._stop())
        await _await_shutdown(self._close_task)

    async def _stop(self) -> None:
        """Join SDK closure and any transcript publication already in progress."""
        errors: list[BaseException] = []
        try:
            await self._close_resources()
        except BaseException as error:
            errors.append(error)
        if self._run_task is not None:
            await self._run_done.wait()
        try:
            await self._finish_delivery()
        except BaseException as error:
            errors.append(error)
        if errors:
            raise BaseExceptionGroup("live transcription stop incomplete", errors)

    async def _close_resources(self) -> None:
        """Share cleanup between run-finally and explicit shutdown callers."""
        if self._resource_close_task is None:
            self._resource_close_task = asyncio.create_task(self._shutdown_resources())
        await _await_shutdown(self._resource_close_task)

    async def _shutdown_resources(self) -> None:
        """Close the SDK immediately, then join the cancelled audio pump."""
        errors: list[BaseException] = []
        if self._audio_task is not None and not self._audio_task.cancelling():
            self._audio_task.cancel()
        if (
            self._reader_task is not None
            and not self._reader_task.done()
            and not self._leg_drained
        ):
            # SDK 1.8.3 aclose() closes its public iterator too. There is no
            # public stop-sender-then-drain operation or unread-final count.
            # Keep reading concurrently, but do not certify exhaustive recovery.
            self._mark_incomplete("sdk-close-final-drain-unconfirmed")
        # Do not let the SDK drain queued audio behind a slow input finalizer.
        # Neither end_input() nor flush() belongs in this stop path.
        try:
            await self._close_leg()
        except BaseException as error:
            errors.append(error)
        if self._audio_stream is not None:
            try:
                await self._audio_stream.aclose()
            except (Exception, asyncio.CancelledError) as error:
                logger.error("failed to close audio stream (%s)", type(error).__name__)
                errors.append(error)
        if self._audio_task is not None:
            await asyncio.gather(self._audio_task, return_exceptions=True)
        if errors:
            self._mark_incomplete("sdk-close-failed")
            if self._reader_task is not None:
                self._reader_task.cancel()
            raise BaseExceptionGroup("live transcription shutdown failed", errors)
        self._speech_stream = None
        self._audio_stream = None
        self._provider = None

    async def _close_leg(self) -> None:
        if self._leg_close_task is None:
            self._leg_close_task = asyncio.create_task(self._close_leg_resources())
        await _await_shutdown(self._leg_close_task)

    async def _close_leg_resources(self) -> None:
        """Share physical leg closure between planned handoff and hard stop."""
        errors: list[BaseException] = []
        for resource in (self._speech_stream, self._provider):
            if resource is None:
                continue
            try:
                await resource.aclose()
            except (Exception, asyncio.CancelledError) as error:
                logger.error("failed to close speech leg (%s)", type(error).__name__)
                errors.append(error)
        if errors:
            raise BaseExceptionGroup("live speech leg close failed", errors)

    async def _push_audio(
        self,
        audio_stream: rtc.AudioStream,
        speech_stream: stt.RecognizeStream,
    ) -> None:
        """Forward decoded LiveKit frames to the provider."""
        if self._closing:
            return
        async for audio_event in audio_stream:
            if self._closing:
                return
            if self._authority is not None and not self._authority.can_send():
                self.fence_audio()
                return
            speech_stream.push_frame(audio_event.frame)

    async def _handle_event(self, event: stt.SpeechEvent) -> None:
        """Publish transcript-bearing events and ignore lifecycle events."""
        if not _has_transcript(event):
            return
        data = event.alternatives[0]
        # Without a provider request_id, replay/correction identity is unknown.
        # Keep those events independent rather than guessing from their text.
        item_id = application_item_ref(event)
        if not item_id:
            if self._authority is not None:
                raise InputDrainError("canonical-provider-item-identity-unavailable")
            item_id = uuid.uuid4().hex
        item = self._items.get(item_id)
        if item is None and len(self._items) >= MAX_ITEMS_PER_TRACK:
            # Fail through run() cleanup and the existing provider-error gap.
            # Never evict revisions or silently discard new finals at capacity.
            raise RuntimeError("live transcription item capacity exceeded")
        final = event.type == stt.SpeechEventType.FINAL_TRANSCRIPT
        final_content = None
        if final:
            final_content = _FinalContent(
                text=data.text,
                language=_language(data),
                data_start_time=data.start_time,
                data_end_time=data.end_time,
                speech_start_time=event.speech_start_time,
                speech_end_time=event.speech_end_time,
                native_content=_native_content(data),
            )
        if item is not None and item.final_content is not None:
            if not final or final_content == item.final_content:
                return
        received_at = int(time.time() * 1000)
        if item is None:
            item = _ItemState(start_time=received_at)
        start_time, end_time = _transcription_times(event, item, received_at)
        timing = item.durable_timing
        if final and self._durable is not None and timing is None:
            timing = await self._final_buffer_timing(item_id)
        revision = item.revision + 1
        next_item = _ItemState(
            revision=revision,
            start_time=start_time,
            end_time=end_time,
            final_content=final_content,
            durable_timing=timing,
        )
        if final and self._durable is not None:
            # Controlled finals require the provider's actual item identity.
            await self._durable.publish_final(
                item_id, revision, data.text, data.language, timing=timing
            )
            # Retain the durable revision before the fallible UI effect.
            self._items[item_id] = next_item
        await self._publisher.publish_segment(
            TranscriptionEvent(
                participant_identity=self.participant_identity,
                track_sid=self.track_sid,
                leg_id=self.leg_id,
                item_id=item_id,
                revision=revision,
                final=final,
                data=data,
                start_time=start_time,
                end_time=end_time,
            )
        )
        self._items[item_id] = next_item

    async def _final_buffer_timing(self, item_id: str) -> dict[str, object]:
        """Translate proved commit bounds only within this immutable source leg."""
        provider = self._provider
        if provider is None:
            # Physical shutdown may finish before accepted text publication.
            return {"state": "unavailable"}
        window = await provider.input_window(item_id)
        if window is None:
            return {"state": "unavailable"}
        offset = self._leg_audio_start_sample
        start = offset + window[0]
        end = offset + window[1]
        if not offset <= start < end <= min(MAX_SAFE_SAMPLE, offset + MAX_LEG_SAMPLES):
            return {"state": "unavailable"}
        return {
            "state": "source_buffer",
            "sample_rate": PCM_SAMPLE_RATE,
            "start_sample": start,
            "end_sample": end,
        }


class LiveTranscriber:
    """Manage one independent OpenAI stream per subscribed audio track."""

    def __init__(self, ctx: JobContext):
        """Initialize track lifecycle management for one LiveKit job."""
        self.ctx = ctx
        self._publisher = TranscriptionPublisher(ctx.room)
        self._vad = ctx.proc.userdata.get("vad")
        metadata = getattr(getattr(ctx, "job", None), "metadata", "")
        self._control = LiveControlClient.from_metadata(metadata)
        self._worker_instance_ref = str(uuid.uuid4())
        self._epochs: set[tuple[str, str, str]] = set()
        self._sessions: dict[tuple[str, str], TrackTranscriber] = {}
        self._session_tasks: dict[tuple[str, str], asyncio.Task] = {}
        self._session_task_set: set[asyncio.Task] = set()
        self._tasks: set[asyncio.Task] = set()
        self._lifecycle_lock = asyncio.Lock()
        self._reconnecting = False
        self._closed = False
        self._close_task: asyncio.Task[None] | None = None

    def start(self) -> None:
        """Register room callbacks for track and connection lifecycle."""
        room = self.ctx.room
        room.on("track_subscribed", self.on_track_subscribed)
        room.on("track_unsubscribed", self.on_track_unsubscribed)
        room.on("track_subscription_failed", self.on_track_subscription_failed)
        room.on("participant_disconnected", self.on_participant_disconnected)
        room.on("reconnecting", self.on_reconnecting)
        room.on("reconnected", self.on_reconnected)

    def start_existing_tracks(self) -> None:
        """Attach to audio publications already subscribed at connect time."""
        for participant in self.ctx.room.remote_participants.values():
            for publication in participant.track_publications.values():
                track = getattr(publication, "track", None)
                if getattr(publication, "subscribed", False) and track is not None:
                    self.on_track_subscribed(track, publication, participant)

    async def aclose(self) -> None:
        """Share and await shutdown, including failures, across all callers."""
        self._closed = True
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._close_all())
        await _await_shutdown(self._close_task)

    async def _close_all(self) -> None:
        """Detach room callbacks and join all local lifecycle work."""
        room = self.ctx.room
        room.off("track_subscribed", self.on_track_subscribed)
        room.off("track_unsubscribed", self.on_track_unsubscribed)
        room.off("track_subscription_failed", self.on_track_subscription_failed)
        room.off("participant_disconnected", self.on_participant_disconnected)
        room.off("reconnecting", self.on_reconnecting)
        room.off("reconnected", self.on_reconnected)
        session_results = await asyncio.gather(
            *(self._stop_session(key) for key in list(self._sessions)),
            return_exceptions=True,
        )
        lifecycle_results = await asyncio.gather(*self._tasks, return_exceptions=True)
        errors = [
            result
            for result in (*session_results, *lifecycle_results)
            if isinstance(result, BaseException)
        ]
        if errors:
            raise BaseExceptionGroup("live transcriber shutdown failed", errors)

    def on_track_subscribed(
        self,
        track: rtc.Track,
        publication: rtc.RemoteTrackPublication,
        participant: rtc.RemoteParticipant,
    ) -> None:
        """Start one session for a newly subscribed audio track."""
        if (
            not self._eligible_track(track, publication)
            or self._closed
            or self._reconnecting
        ):
            return
        self._spawn(self._ensure_session(track, publication.sid, participant))

    def on_track_unsubscribed(
        self,
        track: rtc.Track,
        publication: rtc.RemoteTrackPublication,
        participant: rtc.RemoteParticipant,
    ) -> None:
        """Stop one track session and announce a possible transcription gap."""
        if self._closed:
            return
        key = (participant.identity, publication.sid)
        self._spawn(self._remove_session(key, "track-unsubscribed", track))

    def on_track_subscription_failed(
        self,
        participant: rtc.RemoteParticipant,
        track_sid: str,
        error: str,
    ) -> None:
        """Mark a track subscription failure as a transcription gap."""
        if self._closed:
            return
        logger.warning(
            "track subscription failed for %s/%s",
            participant.identity,
            track_sid,
        )
        self._spawn(self._publish_gap("track-subscription-failed"))

    def on_participant_disconnected(self, participant: rtc.RemoteParticipant) -> None:
        """Stop every track owned by a disconnected participant."""
        if self._closed:
            return
        keys = [key for key in self._sessions if key[0] == participant.identity]
        self._spawn(self._remove_many(keys, "participant-disconnected", participant))

    def on_reconnecting(self) -> None:
        """Stop streams before a room reconnect and publish a gap marker."""
        if self._closed:
            return
        self._reconnecting = True
        if getattr(self, "_control", None) is not None:
            for session in self._sessions.values():
                session.fence_audio()
        self._spawn(self._handle_reconnecting())

    def on_reconnected(self) -> None:
        """Reattach to the audio tracks that survived room reconnection."""
        if self._closed:
            return
        self._spawn(self._resubscribe_existing_tracks())

    async def _ensure_session(
        self,
        track: rtc.Track,
        track_sid: str,
        participant: rtc.RemoteParticipant,
    ) -> None:
        """Replace a stale session and launch a fresh one for a track."""
        if self._vad is None and self._control is None:
            raise RuntimeError("Silero VAD is required for gpt-live-transcribe")
        async with self._lifecycle_lock:
            await self._ensure_session_unlocked(track, track_sid, participant)

    async def _ensure_session_unlocked(
        self,
        track: rtc.Track,
        track_sid: str,
        participant: rtc.RemoteParticipant,
    ) -> None:
        """Create a session while the lifecycle lock is held."""
        if self._closed or self._reconnecting:
            return
        participant_identity = participant.identity
        key = (participant_identity, track_sid)
        authority = None
        if getattr(self, "_control", None) is not None:
            room_sid = await self.ctx.room.sid
            if self._closed or self._reconnecting:
                return
            epoch = (room_sid, participant.sid, track_sid)
            if epoch in self._epochs:
                return
            # Tombstone before prepare/claim: loss or reconnect cannot retry it.
            self._epochs.add(epoch)
            authority = LiveSendAuthority(
                self._control,
                room_sid=room_sid,
                participant_sid=participant.sid,
                track_sid=track_sid,
                attempt_ref=str(uuid.uuid4()),
                worker_instance_ref=self._worker_instance_ref,
            )
        await self._stop_session_unlocked(key)
        if self._closed or self._reconnecting:
            return
        session = TrackTranscriber(
            track=track,
            participant=participant,
            track_sid=track_sid,
            publisher=self._publisher,
            vad=self._vad,
            authority=authority,
        )
        self._sessions[key] = session
        task = asyncio.create_task(self._run_session(key, session))
        self._session_tasks[key] = task
        self._session_task_set.add(task)
        task.add_done_callback(self._on_task_done)

    async def _run_session(
        self, key: tuple[str, str], session: TrackTranscriber
    ) -> None:
        """Keep one provider failure isolated to its track."""
        try:
            await session.run()
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.error(
                "live transcription failed for %s/%s (%s)",
                *key,
                type(error).__name__,
            )
            if not self._closed:
                try:
                    await self._publisher.publish_gap("provider-error")
                except Exception as gap_error:
                    logger.error(
                        "failed to publish provider error gap (%s)",
                        type(gap_error).__name__,
                    )
        finally:
            # Retain a failed stop so a later subscription cannot reopen it.
            await session.aclose()
            if self._sessions.get(key) is session:
                self._sessions.pop(key, None)
                self._session_tasks.pop(key, None)

    async def _remove_session(
        self,
        key: tuple[str, str],
        reason: str,
        expected_track: rtc.Track | None = None,
    ) -> None:
        """Stop one session and publish its lifecycle gap."""
        async with self._lifecycle_lock:
            removed = await self._stop_session_unlocked(key, expected_track)
        if removed and not self._closed:
            await self._publisher.publish_gap(reason)

    async def _remove_many(
        self,
        keys: list[tuple[str, str]],
        reason: str,
        participant: rtc.RemoteParticipant | None = None,
    ) -> None:
        """Stop a set of sessions without racing individual callbacks."""
        removed = False
        async with self._lifecycle_lock:
            for key in keys:
                removed = (
                    await self._stop_session_unlocked(
                        key, expected_participant=participant
                    )
                    or removed
                )
        if removed and not self._closed:
            await self._publisher.publish_gap(reason)

    async def _publish_gap(self, reason: str) -> None:
        """Publish a lifecycle gap without mutating a current track session."""
        if not self._closed:
            await self._publisher.publish_gap(reason)

    async def _stop_session(self, key: tuple[str, str]) -> None:
        """Stop and await one track task, if present."""
        async with self._lifecycle_lock:
            await self._stop_session_unlocked(key)

    async def _stop_session_unlocked(
        self,
        key: tuple[str, str],
        expected_track: rtc.Track | None = None,
        expected_participant: rtc.RemoteParticipant | None = None,
    ) -> bool:
        """Join one track stop before allowing replacement under the lock."""
        session = self._sessions.get(key)
        if expected_track is not None and (
            session is None or session.track is not expected_track
        ):
            return False
        if expected_participant is not None and (
            session is None or session.participant is not expected_participant
        ):
            return False
        task = self._session_tasks.get(key)
        if session is None and task is None:
            return False
        try:
            if session is not None:
                await session.aclose()
        finally:
            if task is not None:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        if self._sessions.get(key) is session:
            self._sessions.pop(key, None)
            self._session_tasks.pop(key, None)
        return True

    async def _handle_reconnecting(self) -> None:
        """Fence all provider streams while the room connection is unstable."""
        async with self._lifecycle_lock:
            keys = list(self._sessions)
            for key in keys:
                await self._stop_session_unlocked(key)
            if keys and not self._closed:
                await self._publisher.publish_gap("room-reconnecting")

    async def _resubscribe_existing_tracks(self) -> None:
        """Start sessions for currently subscribed audio publications."""
        async with self._lifecycle_lock:
            if self._closed or not self._reconnecting:
                return
            self._reconnecting = False
            for participant in self.ctx.room.remote_participants.values():
                for publication in participant.track_publications.values():
                    track = getattr(publication, "track", None)
                    if (
                        getattr(publication, "subscribed", False)
                        and track is not None
                        and self._eligible_track(track, publication)
                    ):
                        await self._ensure_session_unlocked(
                            track, publication.sid, participant
                        )

    def _spawn(self, awaitable: Awaitable[object]) -> None:
        """Schedule room callback work and retain it for clean shutdown."""
        task = asyncio.create_task(awaitable)
        self._tasks.add(task)
        task.add_done_callback(self._on_task_done)

    def _on_task_done(self, task: asyncio.Task) -> None:
        """Retire background work without logging sensitive upstream exceptions."""
        self._session_task_set.discard(task)
        self._tasks.discard(task)
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            logger.error(
                "live transcription background work failed (%s)", type(error).__name__
            )

    def _eligible_track(
        self, track: rtc.Track, publication: rtc.RemoteTrackPublication
    ) -> bool:
        """Canonical capture accepts only an actual microphone publication."""
        if not self._is_audio(track):
            return False
        if getattr(self, "_control", None) is None:
            return True
        return publication.source == rtc.TrackSource.SOURCE_MICROPHONE

    @staticmethod
    def _is_audio(track: rtc.Track) -> bool:
        """Return whether a subscribed track carries audio."""
        return track.kind == rtc.TrackKind.KIND_AUDIO
