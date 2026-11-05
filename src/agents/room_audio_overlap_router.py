"""Route simultaneous room microphones through bounded ASR streams."""

from __future__ import annotations

from dataclasses import dataclass

PRIMARY_RELEASE_SILENCE_FRAMES = 25


@dataclass(frozen=True)
class RoutedRoomAudioFrame:
    """One continuous-stream frame plus optional temporary recovery frames."""

    main_pcm: bytes
    main_track_refs: frozenset[str]
    recovery_frames: dict[str, bytes]


class RoomAudioOverlapRouter:
    """Keep one stable primary microphone while an overlap window is active."""

    def __init__(
        self,
        *,
        maximum_recovery_streams: int,
        primary_release_silence_frames: int = PRIMARY_RELEASE_SILENCE_FRAMES,
    ):
        """Create one router with an explicit temporary-stream bound."""
        if maximum_recovery_streams <= 0:
            raise ValueError("overlap recovery stream bound must be positive")
        if primary_release_silence_frames <= 0:
            raise ValueError("overlap primary release bound must be positive")
        self._maximum_recovery_streams = maximum_recovery_streams
        self._primary_release_silence_frames = primary_release_silence_frames
        self._primary_track_ref: str | None = None
        self._recovery_track_refs: set[str] = set()
        self._room_silent_frames = 0

    def route(
        self,
        *,
        mixed_pcm: bytes,
        speaking_frames: dict[str, bytes],
    ) -> RoutedRoomAudioFrame:
        """Isolate overlapping microphones without changing normal room audio."""
        speaking_track_refs = frozenset(speaking_frames)
        if not speaking_track_refs:
            return self._route_silence(mixed_pcm)

        self._room_silent_frames = 0
        if self._primary_track_ref is None:
            self._primary_track_ref = min(speaking_track_refs)

        primary_track_ref = self._primary_track_ref
        secondary_track_refs = sorted(speaking_track_refs - {primary_track_ref})
        available_recovery_slots = self._maximum_recovery_streams - len(
            self._recovery_track_refs
        )
        self._recovery_track_refs.update(
            secondary_track_refs[:available_recovery_slots]
        )

        silence = bytes(len(mixed_pcm))
        primary_is_speaking = primary_track_ref in speaking_frames
        only_primary_is_speaking = (
            primary_is_speaking
            and len(speaking_track_refs) == 1
            and not self._recovery_track_refs
        )
        main_pcm = speaking_frames.get(primary_track_ref, silence)
        if only_primary_is_speaking:
            main_pcm = mixed_pcm
        return RoutedRoomAudioFrame(
            main_pcm=main_pcm,
            main_track_refs=(
                frozenset({primary_track_ref}) if primary_is_speaking else frozenset()
            ),
            recovery_frames={
                track_ref: speaking_frames.get(track_ref, silence)
                for track_ref in sorted(self._recovery_track_refs)
            },
        )

    def _route_silence(self, mixed_pcm: bytes) -> RoutedRoomAudioFrame:
        """Hold stream ownership through short pauses, then release the turn."""
        if self._primary_track_ref is None:
            return RoutedRoomAudioFrame(
                main_pcm=mixed_pcm,
                main_track_refs=frozenset(),
                recovery_frames={},
            )

        self._room_silent_frames += 1
        if self._room_silent_frames >= self._primary_release_silence_frames:
            self._primary_track_ref = None
            self._recovery_track_refs.clear()
            self._room_silent_frames = 0
            return RoutedRoomAudioFrame(
                main_pcm=mixed_pcm,
                main_track_refs=frozenset(),
                recovery_frames={},
            )

        silence = bytes(len(mixed_pcm))
        return RoutedRoomAudioFrame(
            main_pcm=silence,
            main_track_refs=frozenset(),
            recovery_frames=dict.fromkeys(sorted(self._recovery_track_refs), silence),
        )
