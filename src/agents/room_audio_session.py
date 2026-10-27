"""Room-bound lifecycle controller for one authorized audio run."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from livekit import rtc

from room_audio_authority import RoomAudioAuthority, RoomAudioAuthorityRefused
from room_audio_ogg import RoomAudioArtifact
from room_audio_runtime import RoomAudioRun

logger = logging.getLogger("room-audio-worker.session")

CompletionHandler = Callable[[dict, RoomAudioArtifact | None, str], Awaitable[None]]


class RoomAudioSession:
    """Bind one authorized room run to LiveKit tracks and room lifecycle."""

    def __init__(
        self,
        *,
        room: rtc.Room,
        effect: dict,
        runtime: RoomAudioRun,
        authority: RoomAudioAuthority,
        completion_handler: CompletionHandler,
    ):
        """Prepare an inactive session without opening provider resources."""
        self._room = room
        self._effect = dict(effect)
        self._runtime = runtime
        self._authority = authority
        self._completion_handler = completion_handler
        self._active_participants: set[str] = set()
        self._tasks: set[asyncio.Task] = set()
        self._maximum_duration_task: asyncio.Task | None = None
        self._empty_room_task: asyncio.Task | None = None
        self._close_lock = asyncio.Lock()
        self._closed = asyncio.Event()
        self._artifact: RoomAudioArtifact | None = None
        self._terminal_error: Exception | None = None
        self._completed = False
        self._started = False

    @property
    def artifact(self) -> RoomAudioArtifact | None:
        """Return the finalized artifact after successful closure."""
        return self._artifact

    async def start(self) -> None:
        """Start the room clock and register exact LiveKit callbacks once."""
        if self._started:
            return
        await self._runtime.start()
        self._started = True
        self._register_callbacks()
        self._active_participants = {
            participant.identity
            for participant in self._room.remote_participants.values()
            if self._is_human(participant)
        }
        self._observe_existing_tracks()
        self._maximum_duration_task = asyncio.create_task(
            self._close_after(
                self._effect["maximum_duration_seconds"],
                "maximum-duration",
            )
        )
        self._refresh_empty_room_timer()

    async def wait_closed(self) -> RoomAudioArtifact | None:
        """Wait until one lifecycle boundary finalizes the configured outputs."""
        await self._closed.wait()
        if self._terminal_error is not None:
            raise RuntimeError("room audio session finalization failed") from (
                self._terminal_error
            )
        return self._artifact

    async def aclose(self, reason: str = "job-shutdown") -> RoomAudioArtifact | None:
        """Detach callbacks, stop work and finalize exactly once."""
        async with self._close_lock:
            if self._completed:
                return self._artifact
            if self._terminal_error is not None:
                raise RuntimeError("room audio session finalization failed") from (
                    self._terminal_error
                )
            self._unregister_callbacks()
            self._cancel_timer(self._maximum_duration_task)
            self._cancel_timer(self._empty_room_task)
            current = asyncio.current_task()
            pending = [task for task in self._tasks if task is not current]
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            try:
                artifact = await self._runtime.aclose()
                await self._completion_handler(dict(self._effect), artifact, reason)
            except Exception as error:
                self._terminal_error = error
                self._closed.set()
                raise
            else:
                self._artifact = artifact
                self._completed = True
                self._closed.set()
                return artifact

    def on_track_subscribed(
        self,
        track: rtc.Track,
        publication: rtc.RemoteTrackPublication,
        participant: rtc.RemoteParticipant,
    ) -> None:
        """Observe a human audio track as silence until Core admits it."""
        if (
            self._closed.is_set()
            or track.kind != rtc.TrackKind.KIND_AUDIO
            or not self._is_human(participant)
        ):
            return
        try:
            self._runtime.observe_track(publication.sid, track)
        except ValueError:
            logger.warning("Ignored duplicate room-audio track subscription")

    def on_track_unsubscribed(
        self,
        _track: rtc.Track,
        publication: rtc.RemoteTrackPublication,
        _participant: rtc.RemoteParticipant,
    ) -> None:
        """Stop consuming a physically absent track."""
        if not self._closed.is_set():
            self._spawn(self._runtime.unobserve_track(publication.sid))

    def on_data_received(self, packet: rtc.DataPacket) -> None:
        """Apply only server-originated, signed Core track authority."""
        if self._closed.is_set():
            return
        try:
            self._authority.consume(packet)
        except RoomAudioAuthorityRefused:
            logger.warning("Refused room-audio track authority")

    def on_participant_connected(self, participant: rtc.RemoteParticipant) -> None:
        """Cancel empty-room closure when a human joins."""
        if self._closed.is_set() or not self._is_human(participant):
            return
        self._active_participants.add(participant.identity)
        self._cancel_timer(self._empty_room_task)
        self._empty_room_task = None

    def on_participant_disconnected(self, participant: rtc.RemoteParticipant) -> None:
        """Start the bounded grace period only after the last human leaves."""
        if self._closed.is_set() or not self._is_human(participant):
            return
        self._active_participants.discard(participant.identity)
        self._refresh_empty_room_timer()

    def _register_callbacks(self) -> None:
        self._room.on("track_subscribed", self.on_track_subscribed)
        self._room.on("track_unsubscribed", self.on_track_unsubscribed)
        self._room.on("data_received", self.on_data_received)
        self._room.on("participant_connected", self.on_participant_connected)
        self._room.on("participant_disconnected", self.on_participant_disconnected)

    def _unregister_callbacks(self) -> None:
        if not self._started:
            return
        self._room.off("track_subscribed", self.on_track_subscribed)
        self._room.off("track_unsubscribed", self.on_track_unsubscribed)
        self._room.off("data_received", self.on_data_received)
        self._room.off("participant_connected", self.on_participant_connected)
        self._room.off("participant_disconnected", self.on_participant_disconnected)

    def _observe_existing_tracks(self) -> None:
        for participant in self._room.remote_participants.values():
            for publication in participant.track_publications.values():
                track = getattr(publication, "track", None)
                if getattr(publication, "subscribed", False) and track is not None:
                    self.on_track_subscribed(track, publication, participant)

    def _refresh_empty_room_timer(self) -> None:
        if self._active_participants or self._empty_room_task is not None:
            return
        self._empty_room_task = asyncio.create_task(
            self._close_after(
                self._effect["empty_room_grace_seconds"],
                "empty-room-grace",
            )
        )

    async def _close_after(self, delay_seconds: int, reason: str) -> None:
        try:
            await asyncio.sleep(delay_seconds)
            await self.aclose(reason)
        except asyncio.CancelledError:
            raise

    def _spawn(self, awaitable: Awaitable[object]) -> None:
        task = asyncio.create_task(awaitable)
        self._tasks.add(task)
        task.add_done_callback(self._task_done)

    def _task_done(self, task: asyncio.Task) -> None:
        self._tasks.discard(task)
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            logger.error(
                "Room-audio lifecycle task failed",
                exc_info=(type(error), error, error.__traceback__),
            )

    @staticmethod
    def _cancel_timer(task: asyncio.Task | None) -> None:
        current = asyncio.current_task()
        if task is not None and task is not current:
            task.cancel()

    @staticmethod
    def _is_human(participant: rtc.RemoteParticipant) -> bool:
        return participant.kind != rtc.ParticipantKind.PARTICIPANT_KIND_AGENT
