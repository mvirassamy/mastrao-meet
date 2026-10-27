"""Bounded, fail-closed PCM mixer for one authorized meeting room."""

from __future__ import annotations

import struct
import time
from collections import deque
from dataclasses import dataclass, field

PCM_MIN = -32_768
PCM_MAX = 32_767
SHA256_HEX_LENGTH = 64


class TrackAuthorizationError(ValueError):
    """Refuse stale or contradictory track authority."""


@dataclass
class _TrackState:
    """Current authority and bounded audio queue for one provider track."""

    observed: bool = False
    generation: int = 0
    status: str = "pending"
    expires_at_ms: int = 0
    authorization_digest: str = ""
    frames: deque[bytes] = field(default_factory=deque)


@dataclass(frozen=True)
class RoomAudioMixerSnapshot:
    """Content-free metrics for one room mixer."""

    published_track_count: int
    authorized_track_count: int
    pending_track_count: int
    excluded_track_count: int
    speaking_track_count: int
    output_frame_count: int
    audio_bytes: int
    dropped_frame_count: int
    track_underrun_frame_count: int
    mixer_queue_peak_frames: int


class RoomAudioMixer:
    """Mix authorized mono PCM tracks into one room-owned output stream."""

    def __init__(
        self,
        *,
        frame_samples: int = 480,
        sample_rate_hz: int = 24_000,
        maximum_buffered_frames: int = 10,
        speaking_peak_threshold: int = 256,
    ):
        """Create a mixer with fixed frame geometry and bounded track queues."""
        if frame_samples <= 0 or sample_rate_hz <= 0:
            raise ValueError("room audio frame geometry must be positive")
        if maximum_buffered_frames <= 0:
            raise ValueError("room audio buffer bound must be positive")
        self._frame_samples = frame_samples
        self._sample_rate_hz = sample_rate_hz
        self._maximum_buffered_frames = maximum_buffered_frames
        self._speaking_peak_threshold = speaking_peak_threshold
        self._tracks: dict[str, _TrackState] = {}
        self._speaking_track_count = 0
        self._output_frame_count = 0
        self._audio_bytes = 0
        self._dropped_frame_count = 0
        self._track_underrun_frame_count = 0
        self._mixer_queue_peak_frames = 0

    def observe_track(self, track_ref: str) -> None:
        """Register a published track as pending, without admitting its audio."""
        if not track_ref:
            raise ValueError("track_ref is required")
        state = self._tracks.setdefault(track_ref, _TrackState())
        state.observed = True

    def unobserve_track(self, track_ref: str) -> None:
        """Tombstone a physically absent track without losing Core generation."""
        state = self._tracks.get(track_ref)
        if state is not None:
            state.observed = False
            state.status = "excluded"
            state.expires_at_ms = 0
            state.frames.clear()

    def admit_track(
        self,
        track_ref: str,
        *,
        generation: int,
        expires_at_ms: int,
        authorization_digest: str,
    ) -> None:
        """Apply current Core authority to one exact provider track."""
        if (
            generation <= 0
            or expires_at_ms <= 0
            or not self._is_sha256(authorization_digest)
        ):
            raise TrackAuthorizationError("invalid track authority")
        state = self._tracks.setdefault(track_ref, _TrackState())
        if generation < state.generation:
            raise TrackAuthorizationError("stale track authority")
        if generation == state.generation:
            if (
                state.status == "authorized"
                and state.authorization_digest == authorization_digest
            ):
                state.expires_at_ms = max(state.expires_at_ms, expires_at_ms)
                return
            raise TrackAuthorizationError("contradictory track authority")
        state.generation = generation
        state.status = "authorized"
        state.expires_at_ms = expires_at_ms
        state.authorization_digest = authorization_digest
        state.frames.clear()

    def revoke_track(
        self, track_ref: str, *, generation: int, authorization_digest: str
    ) -> None:
        """Remove one track from the mix and discard its queued media."""
        if generation <= 0 or not self._is_sha256(authorization_digest):
            raise TrackAuthorizationError("invalid track authority")
        state = self._tracks.setdefault(track_ref, _TrackState())
        if generation < state.generation:
            raise TrackAuthorizationError("stale track authority")
        if generation == state.generation:
            if state.status == "excluded":
                return
            raise TrackAuthorizationError("contradictory track authority")
        state.generation = generation
        state.status = "excluded"
        state.expires_at_ms = 0
        state.authorization_digest = authorization_digest
        state.frames.clear()

    def push_frame(
        self, track_ref: str, frame: bytes, *, observed_at_ms: int | None = None
    ) -> bool:
        """Queue one authorized frame; return false for pending or excluded audio."""
        self._require_frame_geometry(frame)
        state = self._tracks.setdefault(track_ref, _TrackState())
        self._expire(state, observed_at_ms)
        if not state.observed or state.status != "authorized":
            return False
        if len(state.frames) == self._maximum_buffered_frames:
            state.frames.popleft()
            self._dropped_frame_count += 1
        state.frames.append(frame)
        queued_frames = sum(len(track.frames) for track in self._tracks.values())
        self._mixer_queue_peak_frames = max(
            self._mixer_queue_peak_frames, queued_frames
        )
        return True

    def mix_next_frame(self, *, observed_at_ms: int | None = None) -> bytes:
        """Return one room frame, inserting silence for missing authorized audio."""
        speaking_frames: list[list[int]] = []
        for state in self._tracks.values():
            self._expire(state, observed_at_ms)
            if not state.observed or state.status != "authorized":
                continue
            if not state.frames:
                self._track_underrun_frame_count += 1
                continue
            decoded = self._decode(state.frames.popleft())
            if max((abs(sample) for sample in decoded), default=0) >= (
                self._speaking_peak_threshold
            ):
                speaking_frames.append(decoded)

        mixed = self._average_speakers(speaking_frames)
        encoded = struct.pack(f"<{self._frame_samples}h", *mixed)
        self._speaking_track_count = len(speaking_frames)
        self._output_frame_count += 1
        self._audio_bytes += len(encoded)
        return encoded

    def snapshot(self) -> RoomAudioMixerSnapshot:
        """Return bounded metrics with no room, participant or track labels."""
        statuses = [state.status for state in self._tracks.values() if state.observed]
        return RoomAudioMixerSnapshot(
            published_track_count=len(statuses),
            authorized_track_count=statuses.count("authorized"),
            pending_track_count=statuses.count("pending"),
            excluded_track_count=statuses.count("excluded"),
            speaking_track_count=self._speaking_track_count,
            output_frame_count=self._output_frame_count,
            audio_bytes=self._audio_bytes,
            dropped_frame_count=self._dropped_frame_count,
            track_underrun_frame_count=self._track_underrun_frame_count,
            mixer_queue_peak_frames=self._mixer_queue_peak_frames,
        )

    def _expire(self, state: _TrackState, observed_at_ms: int | None) -> None:
        """Fail closed when a short-lived track authorization expires."""
        now_ms = observed_at_ms
        if now_ms is None:
            now_ms = int(time.time() * 1_000)
        if state.status == "authorized" and now_ms > state.expires_at_ms:
            state.status = "excluded"
            state.expires_at_ms = 0
            state.frames.clear()

    @staticmethod
    def _is_sha256(value: str) -> bool:
        """Accept only the canonical lowercase digest used by Core contracts."""
        return len(value) == SHA256_HEX_LENGTH and all(
            character in "0123456789abcdef" for character in value
        )

    def _require_frame_geometry(self, frame: bytes) -> None:
        """Reject frames that cannot align with the single room clock."""
        if len(frame) != self._frame_samples * 2:
            raise ValueError("room audio frame has unexpected geometry")

    def _decode(self, frame: bytes) -> list[int]:
        """Decode one explicit little-endian signed 16-bit frame."""
        return [sample[0] for sample in struct.iter_unpack("<h", frame)]

    def _average_speakers(self, frames: list[list[int]]) -> list[int]:
        """Preserve one speaker level and average simultaneous speakers safely."""
        if not frames:
            return [0] * self._frame_samples
        divisor = len(frames)
        return [
            max(
                PCM_MIN,
                min(PCM_MAX, int(sum(frame[index] for frame in frames) / divisor)),
            )
            for index in range(self._frame_samples)
        ]
