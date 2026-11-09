"""Build bounded participant speech evidence on the Room Audio clock."""

from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256

MAX_PARTICIPANTS = 200
MAX_EVENTS = 200_000
MAX_ARTIFACT_BYTES = 5_000_000


@dataclass(frozen=True)
class RoomAudioSpeakerEvidenceArtifact:
    """Canonical JSON bytes bound to one completed Room Audio source."""

    content: bytes
    byte_size: int
    sha256: str


class RoomAudioSpeakerTimeline:
    """Record speech transitions for admitted tracks on the mixer clock."""

    def __init__(self):
        """Prepare one empty timeline for an admitted Room Audio run."""
        self._participant_by_track: dict[str, tuple[str, str | None]] = {}
        self._speaking_track_refs: set[str] = set()
        self._events: list[dict] = []
        self._ended_at_ms = 0

    def register(
        self,
        track_ref: str,
        *,
        participant_ref: str,
        participant_label: str | None,
    ) -> None:
        """Bind one signed participant identity to its admitted RTC track."""
        binding = (participant_ref, participant_label)
        previous = self._participant_by_track.get(track_ref)
        if previous is not None and previous != binding:
            raise ValueError("room audio participant binding changed")
        self._participant_by_track[track_ref] = binding
        if len(set(self._participant_by_track.values())) > MAX_PARTICIPANTS:
            raise ValueError("room audio participant limit exceeded")

    def observe(self, speaking_track_refs: frozenset[str], *, at_ms: int) -> None:
        """Append only start and end transitions at one exact frame boundary."""
        current = {
            track_ref
            for track_ref in speaking_track_refs
            if track_ref in self._participant_by_track
        }
        for track_ref in sorted(current - self._speaking_track_refs):
            self._append("speech_start", track_ref, at_ms)
        for track_ref in sorted(self._speaking_track_refs - current):
            self._append("speech_end", track_ref, at_ms)
        self._speaking_track_refs = current
        self._ended_at_ms = max(self._ended_at_ms, at_ms)

    def finish(self, *, ended_at_ms: int) -> None:
        """Close every open interval at the completed audio duration."""
        self.observe(frozenset(), at_ms=ended_at_ms)
        self._ended_at_ms = ended_at_ms

    def artifact(
        self,
        *,
        meeting_ref: str,
        room_ref: str,
    ) -> RoomAudioSpeakerEvidenceArtifact:
        """Serialize one source-bound artifact without track identifiers."""
        participants = dict(self._participant_by_track.values())
        payload = {
            "version": 1,
            "meeting_ref": meeting_ref,
            "room_ref": room_ref,
            "timeline_started_at_ms": 0,
            "timeline_ended_at_ms": self._ended_at_ms,
            "participants": [
                {
                    "participant_ref": participant_ref,
                    "display_name_events": (
                        [
                            {
                                "effective_at_ms": 0,
                                "label": label,
                                "source": "meet_display_name",
                            }
                        ]
                        if label is not None
                        else []
                    ),
                }
                for participant_ref, label in sorted(participants.items())
            ],
            "events": list(self._events),
        }
        raw = json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(raw) > MAX_ARTIFACT_BYTES:
            raise ValueError("room audio speaker evidence exceeds size bound")
        return RoomAudioSpeakerEvidenceArtifact(
            content=raw,
            byte_size=len(raw),
            sha256=sha256(raw).hexdigest(),
        )

    def _append(self, event_type: str, track_ref: str, at_ms: int) -> None:
        self._events.append(
            {
                "type": event_type,
                "participant_ref": self._participant_by_track[track_ref][0],
                "at_ms": at_ms,
            }
        )
        if len(self._events) > MAX_EVENTS:
            raise ValueError("room audio speaker event limit exceeded")
