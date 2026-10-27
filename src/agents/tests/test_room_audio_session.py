"""Tests for the room-bound audio worker lifecycle controller."""

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from livekit import rtc

from room_audio_authority import RoomAudioAuthorityRefused
from room_audio_mixer import RoomAudioMixerSnapshot
from room_audio_ogg import RoomAudioArtifact
from room_audio_session import (
    RoomAudioCompletion,
    RoomAudioSession,
    RoomAudioSessionHandlers,
)


class FakeRoom:
    """Minimal event emitter with LiveKit-compatible participant state."""

    def __init__(self, participants=()):
        """Expose participants by identity and collect room callbacks."""
        self.remote_participants = {
            participant.identity: participant for participant in participants
        }
        self.callbacks = {}

    def on(self, event, callback):
        """Register one callback."""
        self.callbacks[event] = callback

    def off(self, event, callback):
        """Remove the exact callback."""
        if self.callbacks.get(event) == callback:
            self.callbacks.pop(event)


def _participant(identity, *, agent=False, publications=()):
    kind = (
        rtc.ParticipantKind.PARTICIPANT_KIND_AGENT
        if agent
        else rtc.ParticipantKind.PARTICIPANT_KIND_STANDARD
    )
    return SimpleNamespace(
        identity=identity,
        kind=kind,
        track_publications={
            publication.sid: publication for publication in publications
        },
    )


def _publication(sid, *, audio=True):
    kind = rtc.TrackKind.KIND_AUDIO if audio else rtc.TrackKind.KIND_VIDEO
    return SimpleNamespace(
        sid=sid,
        subscribed=True,
        track=SimpleNamespace(kind=kind),
    )


def _effect(*, grace=30, maximum=600):
    return {
        "audio_run_ref": "audio_run_fixture_12345678",
        "empty_room_grace_seconds": grace,
        "maximum_duration_seconds": maximum,
    }


def _artifact():
    return RoomAudioArtifact(
        content=b"ogg",
        byte_size=3,
        sha256="a" * 64,
        duration_ms=20,
        frame_count=1,
    )


def _handlers(completion_handler, gap_handler=None):
    return RoomAudioSessionHandlers(
        complete=completion_handler,
        publish_gap=gap_handler,
    )


class RoomAudioSessionTests(unittest.IsolatedAsyncioTestCase):
    """Exercise tracks, authority, closure and host-independence."""

    async def test_observes_only_human_audio_and_finalizes_once(self):
        """Video and agent tracks never enter the consent-filtered mixer."""
        human_audio = _publication("TR_human_audio")
        human_video = _publication("TR_human_video", audio=False)
        agent_audio = _publication("TR_agent_audio")
        room = FakeRoom(
            [
                _participant("human", publications=[human_audio, human_video]),
                _participant("agent", agent=True, publications=[agent_audio]),
            ]
        )
        runtime = self._runtime()
        authority = Mock()
        completion_handler = AsyncMock()
        effect = _effect()
        session = RoomAudioSession(
            room=room,
            effect=effect,
            runtime=runtime,
            authority=authority,
            handlers=_handlers(completion_handler),
        )

        await session.start()
        room.callbacks["data_received"](SimpleNamespace())
        first = await session.aclose("test-complete")
        second = await session.aclose("ignored-second-close")

        runtime.observe_track.assert_called_once_with(
            "TR_human_audio", human_audio.track
        )
        authority.consume.assert_called_once()
        runtime.start.assert_awaited_once_with()
        runtime.aclose.assert_awaited_once_with()
        completion = completion_handler.await_args.args[0]
        self.assertIsInstance(completion, RoomAudioCompletion)
        self.assertEqual(completion.effect, effect)
        self.assertIs(completion.artifact, first)
        self.assertEqual(completion.reason, "test-complete")
        self.assertEqual(completion.generation_count, 1)
        self.assertEqual(completion.gaps, ())
        self.assertEqual(completion.mixer, runtime.snapshot.return_value)
        self.assertGreaterEqual(completion.ended_at_ms, completion.started_at_ms)
        self.assertIs(first, second)
        self.assertEqual(room.callbacks, {})

    async def test_last_human_departure_closes_after_grace(self):
        """The host can leave while another human keeps the room run alive."""
        host = _participant("host")
        guest = _participant("guest")
        room = FakeRoom([host, guest])
        runtime = self._runtime()
        completion_handler = AsyncMock()
        effect = _effect(grace=0)
        session = RoomAudioSession(
            room=room,
            effect=effect,
            runtime=runtime,
            authority=Mock(),
            handlers=_handlers(completion_handler),
        )

        await session.start()
        room.callbacks["participant_disconnected"](host)
        await asyncio.sleep(0)
        runtime.aclose.assert_not_awaited()

        room.callbacks["participant_disconnected"](guest)
        artifact = await asyncio.wait_for(session.wait_closed(), timeout=1)

        runtime.aclose.assert_awaited_once_with()
        completion = completion_handler.await_args.args[0]
        self.assertEqual(completion.effect, effect)
        self.assertIs(completion.artifact, artifact)
        self.assertEqual(completion.reason, "empty-room-grace")

    async def test_join_during_grace_keeps_session_open(self):
        """A returning human cancels an empty-room closure timer."""
        room = FakeRoom()
        runtime = self._runtime()
        session = RoomAudioSession(
            room=room,
            effect=_effect(grace=1),
            runtime=runtime,
            authority=Mock(),
            handlers=_handlers(AsyncMock()),
        )

        await session.start()
        room.callbacks["participant_connected"](_participant("returning"))
        await asyncio.sleep(0.01)

        runtime.aclose.assert_not_awaited()
        await session.aclose()

    async def test_refused_authority_leaves_track_pending(self):
        """A bad authority packet is ignored rather than crashing the run."""
        room = FakeRoom([_participant("human")])
        runtime = self._runtime()
        authority = Mock()
        authority.consume.side_effect = RoomAudioAuthorityRefused("bad effect")
        session = RoomAudioSession(
            room=room,
            effect=_effect(),
            runtime=runtime,
            authority=authority,
            handlers=_handlers(AsyncMock()),
        )

        await session.start()
        room.callbacks["data_received"](SimpleNamespace())

        runtime.admit_track.assert_not_called()
        await session.aclose()

    async def test_finalization_failure_is_visible_to_waiter(self):
        """Artifact persistence failure terminates the session as an error."""
        room = FakeRoom([_participant("human")])
        handler = AsyncMock(side_effect=RuntimeError("storage unavailable"))
        session = RoomAudioSession(
            room=room,
            effect=_effect(),
            runtime=self._runtime(),
            authority=Mock(),
            handlers=_handlers(handler),
        )

        await session.start()
        with self.assertRaisesRegex(RuntimeError, "storage unavailable"):
            await session.aclose()
        with self.assertRaisesRegex(
            RuntimeError, "room audio session finalization failed"
        ):
            await session.wait_closed()

    async def test_live_completion_is_idempotent_without_post_artifact(self):
        """A live-only run completes successfully without creating an object."""
        room = FakeRoom([_participant("human")])
        runtime = self._runtime()
        runtime.aclose.return_value = None
        handler = AsyncMock()
        effect = _effect()
        session = RoomAudioSession(
            room=room,
            effect=effect,
            runtime=runtime,
            authority=Mock(),
            handlers=_handlers(handler),
        )

        await session.start()
        first = await session.aclose("live-complete")
        second = await session.aclose("ignored-second-close")

        self.assertIsNone(first)
        self.assertIsNone(second)
        self.assertIsNone(await session.wait_closed())
        runtime.aclose.assert_awaited_once_with()
        completion = handler.await_args.args[0]
        self.assertEqual(completion.effect, effect)
        self.assertIsNone(completion.artifact)
        self.assertEqual(completion.reason, "live-complete")

    async def test_reconnect_records_gap_and_advances_generation(self):
        """A LiveKit reconnect produces one bounded, content-free source gap."""
        room = FakeRoom([_participant("human")])
        runtime = self._runtime()
        runtime.has_live_output = True
        handler = AsyncMock()
        gap_handler = AsyncMock()
        session = RoomAudioSession(
            room=room,
            effect=_effect(),
            runtime=runtime,
            authority=Mock(),
            handlers=_handlers(handler, gap_handler),
        )

        await session.start()
        room.callbacks["reconnecting"]()
        room.callbacks["reconnecting"]()
        room.callbacks["reconnected"]()
        await asyncio.sleep(0)
        await session.aclose("reconnected-run-complete")

        completion = handler.await_args.args[0]
        self.assertEqual(completion.generation_count, 2)
        self.assertEqual(len(completion.gaps), 1)
        gap = completion.gaps[0]
        self.assertEqual(gap.generation, 1)
        self.assertEqual(gap.reason, "source_reconnect")
        self.assertGreater(gap.ended_at_ms, gap.started_at_ms)
        self.assertEqual(completion.mixer, runtime.snapshot.return_value)
        gap_handler.assert_awaited_once_with("source-reconnect")
        runtime.restart_live.assert_awaited_once_with()

    async def test_terminal_provider_failure_fails_the_live_session(self):
        """A dead ASR stream cannot leave a silently successful room run."""
        room = FakeRoom([_participant("human")])
        runtime = self._runtime()
        runtime.has_live_output = True
        runtime.wait_terminal_failure.side_effect = None
        runtime.wait_terminal_failure.return_value = RuntimeError("provider stopped")
        handler = AsyncMock()
        session = RoomAudioSession(
            room=room,
            effect=_effect(),
            runtime=runtime,
            authority=Mock(),
            handlers=_handlers(handler),
        )

        await session.start()
        with self.assertRaisesRegex(
            RuntimeError, "room audio session finalization failed"
        ):
            await asyncio.wait_for(session.wait_closed(), timeout=1)

        completion = handler.await_args.args[0]
        self.assertEqual(completion.reason, "live-provider-failed")
        runtime.aclose.assert_awaited_once_with()

    async def test_subscription_failure_publishes_content_free_gap(self):
        """A failed LiveKit subscription exposes no track or provider details."""
        room = FakeRoom([_participant("human")])
        gap_handler = AsyncMock()
        session = RoomAudioSession(
            room=room,
            effect=_effect(),
            runtime=self._runtime(),
            authority=Mock(),
            handlers=_handlers(AsyncMock(), gap_handler),
        )

        await session.start()
        room.callbacks["track_subscription_failed"](
            _participant("human"),
            "TR_private",
            "provider-private-detail",
        )
        await asyncio.sleep(0)

        gap_handler.assert_awaited_once_with("track-subscription-failed")
        await session.aclose()

    async def test_finalization_closes_an_open_reconnect_gap(self):
        """Shutdown while reconnecting retains the interrupted source interval."""
        room = FakeRoom([_participant("human")])
        handler = AsyncMock()
        session = RoomAudioSession(
            room=room,
            effect=_effect(),
            runtime=self._runtime(),
            authority=Mock(),
            handlers=_handlers(handler),
        )

        await session.start()
        room.callbacks["reconnecting"]()
        await session.aclose("shutdown-during-reconnect")

        completion = handler.await_args.args[0]
        self.assertEqual(completion.generation_count, 1)
        self.assertEqual(len(completion.gaps), 1)
        self.assertEqual(completion.gaps[0].generation, 1)

    @staticmethod
    def _runtime():
        artifact = _artifact()
        runtime = Mock()
        never_fails = asyncio.Event()

        async def wait_terminal_failure():
            await never_fails.wait()

        runtime.start = AsyncMock()
        runtime.aclose = AsyncMock(return_value=artifact)
        runtime.has_live_output = False
        runtime.restart_live = AsyncMock()
        runtime.wait_terminal_failure = AsyncMock(side_effect=wait_terminal_failure)
        runtime.snapshot = Mock(return_value=_mixer_snapshot())
        runtime.observe_track = Mock()
        runtime.unobserve_track = AsyncMock()
        runtime.admit_track = Mock()
        return runtime


def _mixer_snapshot():
    return RoomAudioMixerSnapshot(
        published_track_count=1,
        authorized_track_count=1,
        pending_track_count=0,
        excluded_track_count=0,
        speaking_track_count=0,
        output_frame_count=10,
        audio_bytes=9_600,
        dropped_frame_count=0,
        track_underrun_frame_count=0,
        mixer_queue_peak_frames=1,
    )


if __name__ == "__main__":
    unittest.main()
