"""Tests for bounded per-track routing during room-audio overlap."""

import unittest

from room_audio_overlap_router import RoomAudioOverlapRouter


class RoomAudioOverlapRouterTests(unittest.TestCase):
    """Keep one continuous stream while isolating simultaneous microphones."""

    def setUp(self):
        """Create one router supporting one primary and two recovery tracks."""
        self.router = RoomAudioOverlapRouter(maximum_recovery_streams=2)

    def test_routes_one_speaker_through_the_continuous_room_stream(self):
        """Normal speech keeps the canonical mixed room frame unchanged."""
        routed = self.router.route(
            mixed_pcm=b"mixed",
            speaking_frames={"TR_matthias": b"matthias"},
        )

        self.assertEqual(routed.main_pcm, b"mixed")
        self.assertEqual(routed.main_track_refs, frozenset({"TR_matthias"}))
        self.assertEqual(routed.recovery_frames, {})

    def test_isolates_the_secondary_microphone_during_overlap(self):
        """Two simultaneous microphones never enter the same ASR stream."""
        self.router.route(
            mixed_pcm=b"matthias-before-overlap",
            speaking_frames={"TR_matthias": b"matthias"},
        )
        routed = self.router.route(
            mixed_pcm=b"mixed",
            speaking_frames={
                "TR_matthias": b"matthias",
                "TR_lucas": b"lucas",
            },
        )

        self.assertEqual(routed.main_pcm, b"matthias")
        self.assertEqual(routed.main_track_refs, frozenset({"TR_matthias"}))
        self.assertEqual(routed.recovery_frames, {"TR_lucas": b"lucas"})

    def test_keeps_the_current_speaker_on_the_continuous_stream(self):
        """An interruption cannot switch the main ASR input mid-phrase."""
        self.router.route(
            mixed_pcm=b"speaker-b",
            speaking_frames={"TR_b": b"b1"},
        )

        routed = self.router.route(
            mixed_pcm=b"overlap",
            speaking_frames={"TR_a": b"a1", "TR_b": b"b2"},
        )

        self.assertEqual(routed.main_pcm, b"b2")
        self.assertEqual(routed.main_track_refs, frozenset({"TR_b"}))
        self.assertEqual(routed.recovery_frames, {"TR_a": b"a1"})

    def test_keeps_the_primary_track_stable_across_one_overlap_window(self):
        """Track ordering changes cannot swap names midway through a phrase."""
        first = self.router.route(
            mixed_pcm=b"first-mix",
            speaking_frames={"TR_b": b"b1", "TR_a": b"a1"},
        )
        second = self.router.route(
            mixed_pcm=b"second-mix",
            speaking_frames={"TR_a": b"a2", "TR_b": b"b2"},
        )

        self.assertEqual(first.main_track_refs, frozenset({"TR_a"}))
        self.assertEqual(second.main_track_refs, frozenset({"TR_a"}))
        self.assertEqual(second.recovery_frames, {"TR_b": b"b2"})

    def test_bounds_recovery_cardinality_for_many_simultaneous_tracks(self):
        """One continuous stream opens at most two temporary recoveries."""
        routed = self.router.route(
            mixed_pcm=b"mixed",
            speaking_frames={
                "TR_a": b"a",
                "TR_b": b"b",
                "TR_c": b"c",
                "TR_d": b"d",
            },
        )

        self.assertEqual(routed.main_track_refs, frozenset({"TR_a"}))
        self.assertEqual(routed.recovery_frames, {"TR_b": b"b", "TR_c": b"c"})

    def test_keeps_the_interrupter_isolated_until_a_real_pause(self):
        """The end of overlap cannot mix two identities in one ASR phrase."""
        self.router.route(
            mixed_pcm=b"primary",
            speaking_frames={"TR_a": b"a"},
        )
        self.router.route(
            mixed_pcm=b"overlap",
            speaking_frames={"TR_a": b"a", "TR_b": b"b1"},
        )
        routed = self.router.route(
            mixed_pcm=b"interrupter-continues",
            speaking_frames={"TR_b": b"b2"},
        )

        self.assertEqual(routed.main_pcm, bytes(len(b"interrupter-continues")))
        self.assertEqual(routed.main_track_refs, frozenset())
        self.assertEqual(routed.recovery_frames, {"TR_b": b"b2"})

    def test_releases_stream_ownership_after_a_real_pause(self):
        """A new turn may use the main stream after bounded room silence."""
        router = RoomAudioOverlapRouter(
            maximum_recovery_streams=2,
            primary_release_silence_frames=2,
        )
        router.route(
            mixed_pcm=b"primary",
            speaking_frames={"TR_a": b"a"},
        )
        router.route(
            mixed_pcm=b"overlap",
            speaking_frames={"TR_a": b"a", "TR_b": b"b"},
        )

        first_pause = router.route(mixed_pcm=b"\0" * 4, speaking_frames={})
        released = router.route(mixed_pcm=b"\0" * 4, speaking_frames={})
        next_turn = router.route(
            mixed_pcm=b"next-turn",
            speaking_frames={"TR_b": b"b-next"},
        )

        self.assertEqual(first_pause.recovery_frames, {"TR_b": b"\0" * 4})
        self.assertEqual(released.recovery_frames, {})
        self.assertEqual(next_turn.main_pcm, b"next-turn")
        self.assertEqual(next_turn.main_track_refs, frozenset({"TR_b"}))
        self.assertEqual(next_turn.recovery_frames, {})


if __name__ == "__main__":
    unittest.main()
