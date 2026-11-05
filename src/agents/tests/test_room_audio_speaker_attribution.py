"""Tests for source-derived speaker attribution on one mixed room stream."""

import unittest

from room_audio_speaker_attribution import RoomAudioSpeakerAttribution


class RoomAudioSpeakerAttributionTests(unittest.TestCase):
    """Attribute text only when one known microphone clearly dominates."""

    def setUp(self):
        """Bind two microphones to stable participant identities."""
        self.attribution = RoomAudioSpeakerAttribution(
            provider_delay_ms=480,
            initial_window_ms=2_000,
            minimum_speech_frames=2,
        )
        self.attribution.register_track("TR_matthias", "matthias")
        self.attribution.register_track("TR_martin", "martin")

    def test_follows_alternating_speakers_without_opening_more_asr_streams(self):
        """Sequential microphone windows resolve to their actual owners."""
        for observed_at_ms in (1_000, 1_020, 1_040):
            self.attribution.observe_frame(
                {"TR_matthias"}, observed_at_ms=observed_at_ms
            )

        self.assertEqual(
            self.attribution.resolve(observed_at_ms=1_540),
            "matthias",
        )

        for observed_at_ms in (1_080, 1_100, 1_120):
            self.attribution.observe_frame({"TR_martin"}, observed_at_ms=observed_at_ms)

        self.assertEqual(
            self.attribution.resolve(observed_at_ms=1_620),
            "martin",
        )

    def test_keeps_room_identity_when_speakers_overlap(self):
        """Overlapping microphones never produce a fabricated human label."""
        for observed_at_ms in (2_000, 2_020, 2_040):
            self.attribution.observe_frame(
                {"TR_matthias", "TR_martin"},
                observed_at_ms=observed_at_ms,
            )

        self.assertIsNone(self.attribution.resolve(observed_at_ms=2_540))

    def test_unknown_contributor_prevents_false_human_attribution(self):
        """An unmapped active track makes the entire text window ambiguous."""
        self.attribution.observe_frame(
            {"TR_matthias", "TR_unmapped"},
            observed_at_ms=3_000,
        )
        self.attribution.observe_frame(
            {"TR_matthias"},
            observed_at_ms=3_020,
        )

        self.assertIsNone(self.attribution.resolve(observed_at_ms=3_520))


if __name__ == "__main__":
    unittest.main()
