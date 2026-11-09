"""Tests for bounded active-track ASR routing."""

import unittest

from room_audio_active_track_router import RoomAudioActiveTrackRouter


class RoomAudioActiveTrackRouterTests(unittest.TestCase):
    """Prove exact per-track routing and its concurrency bound."""

    def test_routes_each_speaker_to_its_own_stream(self):
        """Concurrent microphones remain separate before provider input."""
        router = RoomAudioActiveTrackRouter(maximum_active_streams=3)

        routed = router.route(
            speaking_frames={"TR_A": b"alice", "TR_B": b"bob"},
            frame_byte_count=5,
        )

        self.assertEqual(
            routed.track_frames,
            {"TR_A": b"alice", "TR_B": b"bob"},
        )
        self.assertEqual(routed.saturated_track_refs, frozenset())

    def test_keeps_silence_inside_an_active_speech_window(self):
        """Brief pauses do not churn the provider session."""
        router = RoomAudioActiveTrackRouter(
            maximum_active_streams=1,
            release_silence_frames=2,
        )
        router.route(speaking_frames={"TR_A": b"voice"}, frame_byte_count=5)

        first_pause = router.route(speaking_frames={}, frame_byte_count=5)
        released = router.route(speaking_frames={}, frame_byte_count=5)

        self.assertEqual(first_pause.track_frames, {"TR_A": bytes(5)})
        self.assertEqual(released.track_frames, {})

    def test_keeps_each_microphone_session_across_a_conversational_pause(self):
        """A four-second pause cannot churn two alternating speaker sessions."""
        router = RoomAudioActiveTrackRouter(maximum_active_streams=3)
        router.route(
            speaking_frames={"TR_A": b"alice", "TR_B": b"bob!!"},
            frame_byte_count=5,
        )

        paused = None
        for _ in range(200):
            paused = router.route(speaking_frames={}, frame_byte_count=5)

        self.assertEqual(
            paused.track_frames,
            {"TR_A": bytes(5), "TR_B": bytes(5)},
        )

        resumed = router.route(
            speaking_frames={"TR_A": b"alice", "TR_B": b"bob!!"},
            frame_byte_count=5,
        )
        self.assertEqual(
            resumed.track_frames,
            {"TR_A": b"alice", "TR_B": b"bob!!"},
        )
        self.assertEqual(resumed.saturated_track_refs, frozenset())

    def test_reports_tracks_beyond_the_concurrency_bound(self):
        """The router fails closed instead of mixing a fourth speaker."""
        router = RoomAudioActiveTrackRouter(maximum_active_streams=2)

        routed = router.route(
            speaking_frames={"TR_A": b"a", "TR_B": b"b", "TR_C": b"c"},
            frame_byte_count=1,
        )

        self.assertEqual(routed.track_frames, {"TR_A": b"a", "TR_B": b"b"})
        self.assertEqual(routed.saturated_track_refs, frozenset({"TR_C"}))

    def test_waiting_speakers_are_admitted_in_arrival_order(self):
        """A continuously speaking track cannot be starved by track ordering."""
        router = RoomAudioActiveTrackRouter(
            maximum_active_streams=1,
            release_silence_frames=1,
        )
        router.route(speaking_frames={"TR_B": b"b"}, frame_byte_count=1)
        saturated = router.route(
            speaking_frames={"TR_A": b"a", "TR_B": b"b"},
            frame_byte_count=1,
        )

        admitted = router.route(
            speaking_frames={"TR_A": b"a"},
            frame_byte_count=1,
        )

        self.assertEqual(saturated.saturated_track_refs, frozenset({"TR_A"}))
        self.assertEqual(admitted.track_frames, {"TR_A": b"a"})
        self.assertEqual(admitted.saturated_track_refs, frozenset())

    def test_remove_track_clears_hangover_immediately(self):
        """Revocation cannot reopen a track retained by the silence window."""
        router = RoomAudioActiveTrackRouter(maximum_active_streams=1)
        router.route(speaking_frames={"TR_A": b"voice"}, frame_byte_count=5)

        router.remove_track("TR_A")
        routed = router.route(speaking_frames={}, frame_byte_count=5)

        self.assertEqual(routed.track_frames, {})
