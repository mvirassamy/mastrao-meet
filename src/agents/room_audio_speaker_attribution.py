"""Source-derived speaker attribution for one mixed room transcript."""

from __future__ import annotations

import time
from collections import Counter, deque
from dataclasses import dataclass


@dataclass(frozen=True)
class _SpeakerFrame:
    """Known contributors to one frame sent to the room ASR stream."""

    observed_at_ms: int
    participant_identities: frozenset[str]
    has_unknown_contributor: bool


class RoomAudioSpeakerAttribution:
    """Resolve a human identity only from clearly dominant source audio."""

    def __init__(  # noqa: PLR0913 - one explicit attribution policy
        self,
        *,
        provider_delay_ms: int,
        initial_window_ms: int = 2_000,
        retention_ms: int = 10_000,
        minimum_speech_frames: int = 3,
        minimum_dominance_ratio: float = 0.8,
        maximum_ambiguous_ratio: float = 0.2,
    ):
        """Create one bounded attribution timeline aligned to provider delay."""
        if provider_delay_ms < 0 or initial_window_ms <= 0 or retention_ms <= 0:
            raise ValueError("speaker attribution timing must be valid")
        if minimum_speech_frames <= 0:
            raise ValueError("speaker attribution requires speech frames")
        if not 0 < minimum_dominance_ratio <= 1:
            raise ValueError("speaker dominance ratio must be bounded")
        if not 0 <= maximum_ambiguous_ratio < 1:
            raise ValueError("speaker ambiguity ratio must be bounded")
        self._provider_delay_ms = provider_delay_ms
        self._initial_window_ms = initial_window_ms
        self._retention_ms = retention_ms
        self._minimum_speech_frames = minimum_speech_frames
        self._minimum_dominance_ratio = minimum_dominance_ratio
        self._maximum_ambiguous_ratio = maximum_ambiguous_ratio
        self._participant_by_track: dict[str, str] = {}
        self._frames: deque[_SpeakerFrame] = deque()
        self._resolved_through_ms: int | None = None
        self._last_resolution: str | None = None

    def register_track(self, track_ref: str, participant_identity: str) -> None:
        """Bind one LiveKit microphone track to its stable participant identity."""
        if not track_ref or not participant_identity:
            raise ValueError("speaker attribution track identity is required")
        existing = self._participant_by_track.get(track_ref)
        if existing is not None and existing != participant_identity:
            raise ValueError("speaker attribution track identity changed")
        self._participant_by_track[track_ref] = participant_identity

    def observe_frame(
        self,
        speaking_track_refs: set[str] | frozenset[str] | tuple[str, ...],
        *,
        observed_at_ms: int | None = None,
    ) -> None:
        """Record contributors to the exact frame sent to realtime ASR."""
        now_ms = observed_at_ms if observed_at_ms is not None else self._now_ms()
        identities = {
            identity
            for track_ref in speaking_track_refs
            if (identity := self._participant_by_track.get(track_ref)) is not None
        }
        has_unknown = any(
            track_ref not in self._participant_by_track
            for track_ref in speaking_track_refs
        )
        self._frames.append(
            _SpeakerFrame(
                observed_at_ms=now_ms,
                participant_identities=frozenset(identities),
                has_unknown_contributor=has_unknown,
            )
        )
        self._prune(now_ms)

    def resolve(self, *, observed_at_ms: int | None = None) -> str | None:
        """Return one dominant participant, or none when attribution is unsafe."""
        now_ms = observed_at_ms if observed_at_ms is not None else self._now_ms()
        window_end_ms = now_ms - self._provider_delay_ms
        window_start_ms = self._resolved_through_ms
        if window_start_ms is None:
            window_start_ms = window_end_ms - self._initial_window_ms
        self._resolved_through_ms = max(window_end_ms, window_start_ms)

        identity_frames: Counter[str] = Counter()
        ambiguous_frames = 0
        for frame in self._frames:
            if not window_start_ms < frame.observed_at_ms <= window_end_ms:
                continue
            if frame.has_unknown_contributor or len(frame.participant_identities) != 1:
                if frame.has_unknown_contributor or frame.participant_identities:
                    ambiguous_frames += 1
                continue
            identity_frames[next(iter(frame.participant_identities))] += 1

        identified_frames = sum(identity_frames.values())
        total_speech_frames = identified_frames + ambiguous_frames
        if total_speech_frames < self._minimum_speech_frames:
            return self._last_resolution
        if not identity_frames:
            self._last_resolution = None
            return None
        identity, dominant_frames = identity_frames.most_common(1)[0]
        if dominant_frames / total_speech_frames < self._minimum_dominance_ratio:
            self._last_resolution = None
            return None
        if ambiguous_frames / total_speech_frames > self._maximum_ambiguous_ratio:
            self._last_resolution = None
            return None
        self._last_resolution = identity
        return identity

    def _prune(self, observed_at_ms: int) -> None:
        oldest_allowed_ms = observed_at_ms - self._retention_ms
        while self._frames and self._frames[0].observed_at_ms < oldest_allowed_ms:
            self._frames.popleft()

    @staticmethod
    def _now_ms() -> int:
        return time.monotonic_ns() // 1_000_000
