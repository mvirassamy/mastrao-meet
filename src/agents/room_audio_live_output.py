"""Publish live transcript streams under their exact microphone owners."""

from __future__ import annotations

import asyncio
import logging
import time

from livekit import rtc

from live_transcript_event import LiveTranscriptEvent
from live_transcription_publisher import TrackTranscriptOutput, TranscriptionPublisher
from room_audio_runtime import TrackSourceBinding
from room_audio_session import RoomAudioCompletion

logger = logging.getLogger("room-audio-worker.live-output")

ALLOWED_GAP_REASONS = frozenset(
    {
        "provider-error",
        "source-reconnect",
        "track-subscription-failed",
    }
)


class RoomLiveTranscriptionOutput:
    """Own ordered transcript and gap publication for one room provider."""

    def __init__(
        self,
        room: rtc.Room,
        *,
        sequence_seed: int | None = None,
        leg_id: str | None = None,
    ):
        """Start after the current epoch so a replacement leg sorts later."""
        if sequence_seed is None:
            sequence_seed = time.time_ns() // 1_000
        self._publisher = TranscriptionPublisher(
            room,
            initial_sequence=sequence_seed,
        )
        self._initial_leg_id = leg_id
        self._publication_lock = asyncio.Lock()

    def create_track_event_handler(
        self,
        binding: TrackSourceBinding,
    ):
        """Create one transcript leg bound to an admitted microphone track."""
        if not binding.participant_identity or not binding.track_ref:
            raise ValueError("track transcript identity and track are required")
        output = TrackTranscriptOutput(
            publisher=self._publisher,
            track_sid=binding.track_ref,
            leg_id=self._initial_leg_id,
        )
        self._initial_leg_id = None

        async def handle_event(event: LiveTranscriptEvent) -> None:
            async with self._publication_lock:
                await output.consume(
                    event,
                    participant_identity=binding.participant_identity,
                )

        return handle_event

    async def handle_failure(self, error: Exception) -> None:
        """Expose a content-free provider gap and keep credentials private."""
        logger.warning("Room live provider failed: %s", type(error).__name__)
        await self.publish_gap("provider-error")

    async def publish_gap(self, reason: str) -> None:
        """Publish only bounded lifecycle reasons, never provider text."""
        if reason not in ALLOWED_GAP_REASONS:
            raise ValueError("unsupported room live gap reason")
        await self._publisher.publish_gap(reason)

    async def complete(self, completion: RoomAudioCompletion) -> None:
        """Validate that a live run cannot silently create a post artifact."""
        if completion.effect.get("pipeline") != "live":
            raise ValueError("room live completion requires live pipeline")
        if completion.artifact is not None:
            raise ValueError("room live completion cannot contain an artifact")
        logger.info(
            "Completed room live transcription authorized_tracks=%d "
            "pending_tracks=%d excluded_tracks=%d output_frames=%d "
            "audio_bytes=%d dropped_frames=%d underrun_frames=%d",
            completion.mixer.authorized_track_count,
            completion.mixer.pending_track_count,
            completion.mixer.excluded_track_count,
            completion.mixer.output_frame_count,
            completion.mixer.audio_bytes,
            completion.mixer.dropped_frame_count,
            completion.mixer.track_underrun_frame_count,
        )
