"""Route speaking microphones through bounded track-owned ASR streams."""

from __future__ import annotations

from dataclasses import dataclass

ACTIVE_TRACK_RELEASE_SILENCE_FRAMES = 250


@dataclass(frozen=True)
class RoutedActiveTrackFrames:
    """Frames for the currently active microphone sessions."""

    track_frames: dict[str, bytes]
    saturated_track_refs: frozenset[str]


class RoomAudioActiveTrackRouter:
    """Keep a bounded ASR session for each currently active microphone."""

    def __init__(
        self,
        *,
        maximum_active_streams: int,
        release_silence_frames: int = ACTIVE_TRACK_RELEASE_SILENCE_FRAMES,
    ):
        """Create one router with an explicit active-stream bound."""
        if maximum_active_streams <= 0:
            raise ValueError("active track stream bound must be positive")
        if release_silence_frames <= 0:
            raise ValueError("active track release bound must be positive")
        self._maximum_active_streams = maximum_active_streams
        self._release_silence_frames = release_silence_frames
        self._active_track_refs: set[str] = set()
        self._waiting_track_refs: dict[str, None] = {}
        self._silent_frames_by_track: dict[str, int] = {}

    def route(
        self,
        *,
        speaking_frames: dict[str, bytes],
        frame_byte_count: int,
    ) -> RoutedActiveTrackFrames:
        """Return exact source frames and bounded silence for active sessions."""
        if frame_byte_count <= 0:
            raise ValueError("active track frame byte count must be positive")
        speaking_track_refs = set(speaking_frames)
        routed_frames: dict[str, bytes] = {}
        silence = bytes(frame_byte_count)
        for track_ref in sorted(self._active_track_refs):
            frame = speaking_frames.get(track_ref)
            if frame is not None:
                self._silent_frames_by_track[track_ref] = 0
                routed_frames[track_ref] = frame
                continue
            silent_frames = self._silent_frames_by_track.get(track_ref, 0) + 1
            if silent_frames >= self._release_silence_frames:
                self._silent_frames_by_track.pop(track_ref, None)
                continue
            self._silent_frames_by_track[track_ref] = silent_frames
            routed_frames[track_ref] = silence

        self._active_track_refs.intersection_update(routed_frames)
        waiting_candidates = (
            speaking_track_refs
            - self._active_track_refs
            - self._waiting_track_refs.keys()
        )
        for track_ref in sorted(waiting_candidates):
            self._waiting_track_refs[track_ref] = None
        for track_ref in tuple(self._waiting_track_refs):
            if track_ref not in speaking_track_refs:
                self._waiting_track_refs.pop(track_ref)

        available_slots = self._maximum_active_streams - len(self._active_track_refs)
        for track_ref in tuple(self._waiting_track_refs):
            if available_slots <= 0:
                break
            self._waiting_track_refs.pop(track_ref)
            self._active_track_refs.add(track_ref)
            self._silent_frames_by_track[track_ref] = 0
            routed_frames[track_ref] = speaking_frames[track_ref]
            available_slots -= 1

        return RoutedActiveTrackFrames(
            track_frames=routed_frames,
            saturated_track_refs=frozenset(
                speaking_track_refs - self._active_track_refs
            ),
        )

    def reset(self) -> None:
        """Forget active windows after a room connection generation changes."""
        self._active_track_refs.clear()
        self._waiting_track_refs.clear()
        self._silent_frames_by_track.clear()

    def remove_track(self, track_ref: str) -> None:
        """Forget one revoked or physically absent microphone immediately."""
        self._active_track_refs.discard(track_ref)
        self._waiting_track_refs.pop(track_ref, None)
        self._silent_frames_by_track.pop(track_ref, None)
