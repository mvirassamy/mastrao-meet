"""Publish ordered room-audio transcript envelopes to LiveKit."""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass

from livekit import rtc

from live_transcript_event import LiveTranscriptEvent, LiveTranscriptEventKind

TRANSCRIPTION_TOPIC = "lk.transcription"
GAP_TOPIC = "mastrao.transcription.gap.v1"
SCHEMA_VERSION = 1
MAXIMUM_TERMINAL_UNIT_TOMBSTONES = 256


@dataclass(frozen=True)
class TranscriptionEvent:
    """Data needed to publish one transcript revision."""

    participant_identity: str
    track_sid: str
    leg_id: str
    item_id: str
    revision: int
    final: bool
    text: str
    language: str | None
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
        text = event.text.strip()
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
                "receivedAt": int(time.time() * 1000),
            }
            if event.language is not None:
                envelope["language"] = event.language
            if event.start_time is not None:
                envelope["startTime"] = event.start_time
            if event.end_time is not None:
                envelope["endTime"] = event.end_time
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


class TrackTranscriptOutput:
    """Map canonical ASR events to ordered LiveKit transcript revisions."""

    def __init__(
        self,
        *,
        publisher: TranscriptionPublisher,
        track_sid: str,
        leg_id: str | None = None,
    ):
        """Bind canonical events to one admitted microphone track."""
        if not track_sid:
            raise ValueError("transcription presentation track is required")
        self._publisher = publisher
        self._track_sid = track_sid
        self._leg_id = leg_id or uuid.uuid4().hex
        self._revisions: dict[str, int] = {}
        self._item_start_times: dict[str, int] = {}
        self._texts: dict[str, str] = {}
        self._last_local_sequence = 0
        self._terminal_units: OrderedDict[str, None] = OrderedDict()

    @property
    def leg_id(self) -> str:
        """Return the immutable provider-stream generation identifier."""
        return self._leg_id

    async def consume(
        self,
        event: LiveTranscriptEvent,
        *,
        participant_identity: str,
    ) -> None:
        """Publish one canonical revision under its pre-bound source."""
        if not participant_identity:
            raise ValueError("transcription participant identity is required")
        if event.local_sequence <= self._last_local_sequence:
            return
        if event.logical_unit_id in self._terminal_units:
            self._last_local_sequence = event.local_sequence
            return
        if event.kind is LiveTranscriptEventKind.FAILED:
            self._last_local_sequence = event.local_sequence
            self._record_terminal_unit(event.logical_unit_id)
            raise RuntimeError("live transcript unit failed")
        item_id = event.logical_unit_id
        revision = self._revisions.get(item_id, 0) + 1
        received_at = int(time.time() * 1000)
        start_time = (
            event.start_time_ms
            if event.start_time_ms is not None
            else self._item_start_times.get(item_id, received_at)
        )
        final = event.kind is LiveTranscriptEventKind.COMPLETE
        text = self._event_text(event)
        end_time = event.end_time_ms
        if final and end_time is None:
            end_time = received_at
        await self._publisher.publish_segment(
            TranscriptionEvent(
                participant_identity=participant_identity,
                track_sid=self._track_sid,
                leg_id=self._leg_id,
                item_id=item_id,
                revision=revision,
                final=final,
                text=text,
                language=event.language,
                start_time=start_time,
                end_time=end_time,
            )
        )
        self._last_local_sequence = event.local_sequence
        if final:
            self._revisions.pop(item_id, None)
            self._item_start_times.pop(item_id, None)
            self._texts.pop(item_id, None)
            self._record_terminal_unit(item_id)
            return
        self._revisions[item_id] = revision
        self._item_start_times[item_id] = start_time
        self._texts[item_id] = text

    def _event_text(self, event: LiveTranscriptEvent) -> str:
        """Apply canonical append, snapshot and complete text semantics."""
        if event.kind is LiveTranscriptEventKind.APPEND:
            return self._texts.get(event.logical_unit_id, "") + event.text
        return event.text

    def _record_terminal_unit(self, item_id: str) -> None:
        """Retain bounded tombstones so retries cannot republish terminal text."""
        self._terminal_units[item_id] = None
        self._terminal_units.move_to_end(item_id)
        while len(self._terminal_units) > MAXIMUM_TERMINAL_UNIT_TOMBSTONES:
            self._terminal_units.popitem(last=False)
