"""Route simultaneous room microphones through bounded ASR streams."""

from __future__ import annotations

from dataclasses import dataclass

OVERLAP_TRACK_COUNT = 2


@dataclass(frozen=True)
class RoutedRoomAudioFrame:
    """One continuous-stream frame plus optional temporary recovery frames."""

    main_pcm: bytes
    main_track_refs: frozenset[str]
    recovery_frames: dict[str, bytes]


class RoomAudioOverlapRouter:
    """Keep one stable primary microphone while an overlap window is active."""

    def __init__(self, *, maximum_recovery_streams: int):
        """Create one router with an explicit temporary-stream bound."""
        if maximum_recovery_streams <= 0:
            raise ValueError("overlap recovery stream bound must be positive")
        self._maximum_recovery_streams = maximum_recovery_streams
        self._primary_track_ref: str | None = None

    def route(
        self,
        *,
        mixed_pcm: bytes,
        speaking_frames: dict[str, bytes],
    ) -> RoutedRoomAudioFrame:
        """Isolate overlapping microphones without changing normal room audio."""
        speaking_track_refs = frozenset(speaking_frames)
        if not speaking_track_refs:
            self._primary_track_ref = None
            return RoutedRoomAudioFrame(
                main_pcm=mixed_pcm,
                main_track_refs=speaking_track_refs,
                recovery_frames={},
            )
        if len(speaking_track_refs) < OVERLAP_TRACK_COUNT:
            self._primary_track_ref = next(iter(speaking_track_refs))
            return RoutedRoomAudioFrame(
                main_pcm=mixed_pcm,
                main_track_refs=speaking_track_refs,
                recovery_frames={},
            )

        if self._primary_track_ref not in speaking_track_refs:
            self._primary_track_ref = min(speaking_track_refs)
        primary_track_ref = self._primary_track_ref
        if primary_track_ref is None:
            raise RuntimeError("overlap primary track is unavailable")

        secondary_track_refs = sorted(speaking_track_refs - {primary_track_ref})
        recovery_track_refs = secondary_track_refs[: self._maximum_recovery_streams]
        return RoutedRoomAudioFrame(
            main_pcm=speaking_frames[primary_track_ref],
            main_track_refs=frozenset({primary_track_ref}),
            recovery_frames={
                track_ref: speaking_frames[track_ref]
                for track_ref in recovery_track_refs
            },
        )
