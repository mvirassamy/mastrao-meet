"""Publish ordered room-audio transcript envelopes to LiveKit."""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass

from livekit import rtc
from livekit.agents import stt

TRANSCRIPTION_TOPIC = "lk.transcription"
GAP_TOPIC = "mastrao.transcription.gap.v1"
SCHEMA_VERSION = 1


def _milliseconds(value: float) -> int | None:
    if value <= 0:
        return None
    return int(value * 1000)


def _language(data: stt.SpeechData) -> str | None:
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
    """Publish ordered, replaceable transcript envelopes to one LiveKit room."""

    def __init__(self, room: rtc.Room, *, initial_sequence: int = 0):
        """Bind the publisher to one room and monotonic sequence."""
        if initial_sequence < 0:
            raise ValueError("initial_sequence must be non-negative")
        self._room = room
        self._sequence = initial_sequence
        self._lock = asyncio.Lock()

    @property
    def sequence(self) -> int:
        """Return the latest published transcript sequence."""
        return self._sequence

    async def publish_segment(self, event: TranscriptionEvent) -> None:
        """Publish one non-empty transcript revision in sequence order."""
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
        """Publish one content-free reason for a missing transcript interval."""
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


class SpeechEventOutput:
    """Map provider events to ordered LiveKit transcript revisions."""

    def __init__(
        self,
        *,
        publisher: TranscriptionPublisher,
        participant_identity: str,
        track_sid: str,
        leg_id: str | None = None,
    ):
        """Bind provider events to one room-level presentation identity."""
        if not participant_identity or not track_sid:
            raise ValueError("transcription presentation identity is required")
        self._publisher = publisher
        self._participant_identity = participant_identity
        self._track_sid = track_sid
        self._leg_id = leg_id or uuid.uuid4().hex
        self._revisions: dict[str, int] = {}
        self._item_start_times: dict[str, int] = {}

    @property
    def leg_id(self) -> str:
        """Return the immutable provider-stream generation identifier."""
        return self._leg_id

    async def consume(self, event: stt.SpeechEvent) -> None:
        """Translate one provider revision into the canonical room envelope."""
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
                participant_identity=self._participant_identity,
                track_sid=self._track_sid,
                leg_id=self._leg_id,
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
