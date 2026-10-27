"""Publish one unattributed live transcript stream for a mixed meeting room."""

from __future__ import annotations

import asyncio
import logging
import time

from livekit import rtc
from livekit.agents import stt

from live_transcriber import SpeechEventOutput, TranscriptionPublisher
from room_audio_session import RoomAudioCompletion

logger = logging.getLogger("room-audio-worker.live-output")

ROOM_PARTICIPANT_IDENTITY = "unknown-participant"
ROOM_MIX_TRACK_SID = "room-mix"
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
        self._generation = 0
        self._publication_lock = asyncio.Lock()

    def create_event_handler(self):
        """Create one immutable transcript leg for a provider generation."""
        self._generation += 1
        generation = self._generation
        leg_id = self._initial_leg_id if self._generation == 1 else None
        output = SpeechEventOutput(
            publisher=self._publisher,
            participant_identity=ROOM_PARTICIPANT_IDENTITY,
            track_sid=ROOM_MIX_TRACK_SID,
            leg_id=leg_id,
        )

        async def handle_event(event: stt.SpeechEvent) -> None:
            async with self._publication_lock:
                if generation != self._generation:
                    return
                await output.consume(event)

        return handle_event

    async def invalidate_generation(self) -> None:
        """Fence the current provider leg before its stream is drained."""
        async with self._publication_lock:
            self._generation += 1

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
