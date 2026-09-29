"""Track-scoped LiveKit transcription with OpenAI's live model."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import time
import uuid
from collections.abc import Awaitable
from dataclasses import dataclass

from livekit import rtc
from livekit.agents import JobContext, stt
from livekit.plugins import openai

from tasks import done_callback

logger = logging.getLogger("transcriber.live")

TRANSCRIPTION_TOPIC = "lk.transcription"
GAP_TOPIC = "mastrao.transcription.gap.v1"
SCHEMA_VERSION = 1
MODEL = "gpt-live-transcribe"


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
    """Convert provider-relative seconds to a frontend-safe millisecond value."""
    if value <= 0:
        return None
    return int(value * 1000)


def _language(data: stt.SpeechData) -> str | None:
    """Return the detected language without serializing SDK internals."""
    language = str(data.language)
    return language or None


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
        text = event.data.text.strip()
        if not text:
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

    def __init__(
        self,
        *,
        track: rtc.Track,
        participant: object,
        track_sid: str,
        publisher: TranscriptionPublisher,
        vad: object,
    ):
        """Initialize an isolated OpenAI stream for a single track."""
        self.track = track
        self.participant = participant
        self.participant_identity = getattr(participant, "identity", "unknown")
        self.track_sid = track_sid
        self._publisher = publisher
        self._vad = vad
        self.leg_id = uuid.uuid4().hex
        self._speech_stream: stt.RecognizeStream | None = None
        self._audio_stream: rtc.AudioStream | None = None
        self._provider: openai.STT | None = None
        self._resource_lock = asyncio.Lock()
        self._resources_closed = False
        self._closing = False
        self._revisions: dict[str, int] = {}
        self._item_start_times: dict[str, int] = {}

    async def run(self) -> None:
        """Pump one LiveKit audio track into one OpenAI realtime stream."""
        if self._closing:
            return
        audio_task: asyncio.Task | None = None
        try:
            self._provider = create_openai_live_stt(self._vad)
            self._audio_stream = rtc.AudioStream.from_track(
                track=self.track,
                sample_rate=24000,
                num_channels=1,
            )
            self._speech_stream = self._provider.stream()
            audio_task = asyncio.create_task(
                self._push_audio(self._audio_stream, self._speech_stream)
            )
            async for event in self._speech_stream:
                await self._handle_event(event)
        finally:
            if audio_task is not None:
                audio_task.cancel()
                with contextlib.suppress(BaseException):
                    await audio_task
            await self._close_resources()

    async def aclose(self) -> None:
        """Stop the current provider and audio stream, idempotently."""
        if self._closing:
            return
        self._closing = True
        await self._close_resources()

    async def _close_resources(self) -> None:
        """Close provider resources once, even when task cancellation races."""
        async with self._resource_lock:
            if self._resources_closed:
                return
            self._resources_closed = True
            speech_stream = self._speech_stream
            audio_stream = self._audio_stream
            provider = self._provider
            self._speech_stream = None
            self._audio_stream = None
            self._provider = None
            await self._close_resource(speech_stream, "speech stream")
            await self._close_resource(audio_stream, "audio stream")
            await self._close_resource(provider, "OpenAI provider")

    @staticmethod
    async def _close_resource(resource: object | None, label: str) -> None:
        """Close one SDK resource while preserving shutdown progress."""
        if resource is None:
            return
        try:
            await resource.aclose()
        except asyncio.CancelledError:
            logger.debug("cancelled while closing %s", label)
        except Exception:
            logger.exception("failed to close %s", label)

    async def _push_audio(
        self,
        audio_stream: rtc.AudioStream,
        speech_stream: stt.RecognizeStream,
    ) -> None:
        """Forward decoded LiveKit frames to the provider."""
        async for audio_event in audio_stream:
            speech_stream.push_frame(audio_event.frame)

    async def _handle_event(self, event: stt.SpeechEvent) -> None:
        """Publish transcript-bearing events and ignore lifecycle events."""
        if event.type not in (
            stt.SpeechEventType.INTERIM_TRANSCRIPT,
            stt.SpeechEventType.FINAL_TRANSCRIPT,
        ):
            return
        if not event.alternatives:
            return
        item_id = event.request_id or uuid.uuid4().hex
        revision = self._revisions.get(item_id, 0) + 1
        self._revisions[item_id] = revision
        received_at = int(time.time() * 1000)
        start_time = _milliseconds(event.alternatives[0].start_time)
        end_time = _milliseconds(event.alternatives[0].end_time)
        start_time = start_time or self._item_start_times.setdefault(
            item_id, received_at
        )
        if event.speech_start_time is not None:
            start_time = int(event.speech_start_time * 1000)
        if event.speech_end_time is not None:
            end_time = int(event.speech_end_time * 1000)
        elif event.type == stt.SpeechEventType.FINAL_TRANSCRIPT:
            end_time = received_at
        await self._publisher.publish_segment(
            TranscriptionEvent(
                participant_identity=self.participant_identity,
                track_sid=self.track_sid,
                leg_id=self.leg_id,
                item_id=item_id,
                revision=revision,
                final=event.type == stt.SpeechEventType.FINAL_TRANSCRIPT,
                data=event.alternatives[0],
                start_time=start_time,
                end_time=end_time,
            )
        )
        if event.type == stt.SpeechEventType.FINAL_TRANSCRIPT:
            self._revisions.pop(item_id, None)
            self._item_start_times.pop(item_id, None)


class LiveTranscriber:
    """Manage one independent OpenAI stream per subscribed audio track."""

    def __init__(self, ctx: JobContext):
        """Initialize track lifecycle management for one LiveKit job."""
        self.ctx = ctx
        self._publisher = TranscriptionPublisher(ctx.room)
        self._vad = ctx.proc.userdata.get("vad")
        self._sessions: dict[tuple[str, str], TrackTranscriber] = {}
        self._session_tasks: dict[tuple[str, str], asyncio.Task] = {}
        self._session_task_set: set[asyncio.Task] = set()
        self._tasks: set[asyncio.Task] = set()
        self._lifecycle_lock = asyncio.Lock()
        self._reconnecting = False
        self._closed = False

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
        """Cancel all streams and detach every room callback."""
        if self._closed:
            return
        self._closed = True
        room = self.ctx.room
        room.off("track_subscribed", self.on_track_subscribed)
        room.off("track_unsubscribed", self.on_track_unsubscribed)
        room.off("track_subscription_failed", self.on_track_subscription_failed)
        room.off("participant_disconnected", self.on_participant_disconnected)
        room.off("reconnecting", self.on_reconnecting)
        room.off("reconnected", self.on_reconnected)
        await asyncio.gather(
            *(self._stop_session(key) for key in list(self._sessions)),
            return_exceptions=True,
        )
        await asyncio.gather(*self._tasks, return_exceptions=True)

    def on_track_subscribed(
        self,
        track: rtc.Track,
        publication: rtc.RemoteTrackPublication,
        participant: rtc.RemoteParticipant,
    ) -> None:
        """Start one session for a newly subscribed audio track."""
        if not self._is_audio(track) or self._closed or self._reconnecting:
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
            "track subscription failed for %s/%s: %s",
            participant.identity,
            track_sid,
            error,
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
        if self._vad is None:
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
        participant_identity = participant.identity
        key = (participant_identity, track_sid)
        await self._stop_session_unlocked(key)
        if self._closed:
            return
        session = TrackTranscriber(
            track=track,
            participant=participant,
            track_sid=track_sid,
            publisher=self._publisher,
            vad=self._vad,
        )
        self._sessions[key] = session
        task = asyncio.create_task(self._run_session(key, session))
        self._session_tasks[key] = task
        self._session_task_set.add(task)
        task.add_done_callback(
            done_callback(
                logger,
                self._session_task_set,
                f"run live transcription for {participant_identity}/{track_sid}",
            )
        )

    async def _run_session(
        self, key: tuple[str, str], session: TrackTranscriber
    ) -> None:
        """Keep one provider failure isolated to its track."""
        try:
            await session.run()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("live transcription failed for %s/%s", *key)
            if not self._closed:
                try:
                    await self._publisher.publish_gap("provider-error")
                except Exception:
                    logger.exception("failed to publish provider error gap")
        finally:
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
        """Cancel and await one track task, if present."""
        async with self._lifecycle_lock:
            await self._stop_session_unlocked(key)

    async def _stop_session_unlocked(
        self,
        key: tuple[str, str],
        expected_track: rtc.Track | None = None,
        expected_participant: rtc.RemoteParticipant | None = None,
    ) -> bool:
        """Cancel one track task while the lifecycle lock is held."""
        session = self._sessions.get(key)
        if expected_track is not None and (
            session is None or session.track is not expected_track
        ):
            return False
        if expected_participant is not None and (
            session is None or session.participant is not expected_participant
        ):
            return False
        session = self._sessions.pop(key, None)
        task = self._session_tasks.pop(key, None)
        if session is None and task is None:
            return False
        if task is not None:
            task.cancel()
        try:
            if session is not None:
                await session.aclose()
        finally:
            if task is not None:
                await asyncio.gather(task, return_exceptions=True)
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
                        and self._is_audio(track)
                    ):
                        await self._ensure_session_unlocked(
                            track, publication.sid, participant
                        )

    def _spawn(self, awaitable: Awaitable[object]) -> None:
        """Schedule room callback work and retain it for clean shutdown."""
        task = asyncio.create_task(awaitable)
        self._tasks.add(task)
        task.add_done_callback(
            done_callback(logger, self._tasks, "live transcription lifecycle work")
        )

    @staticmethod
    def _is_audio(track: rtc.Track) -> bool:
        """Return whether a subscribed track carries audio."""
        return track.kind == rtc.TrackKind.KIND_AUDIO
