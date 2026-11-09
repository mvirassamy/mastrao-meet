"""Tests for Room Audio-owned participant speech evidence."""

import json
import unittest

from room_audio_speaker_evidence import RoomAudioSpeakerTimeline


class RoomAudioSpeakerTimelineTests(unittest.TestCase):
    """Prove overlap and participant labels without per-participant ASR."""

    def test_records_simultaneous_tracks_on_one_room_clock(self):
        """Two overlapping microphones retain independent speech intervals."""
        timeline = RoomAudioSpeakerTimeline()
        timeline.register(
            "TR_A",
            participant_ref="participant_alice",
            participant_label="Alice",
        )
        timeline.register(
            "TR_B",
            participant_ref="participant_bob",
            participant_label="Bob",
        )

        timeline.observe(frozenset({"TR_A"}), at_ms=0)
        timeline.observe(frozenset({"TR_A", "TR_B"}), at_ms=20)
        timeline.observe(frozenset({"TR_B"}), at_ms=40)
        timeline.finish(ended_at_ms=60)

        artifact = timeline.artifact(
            meeting_ref="meeting_fixture_12345678",
            room_ref="room_fixture_12345678",
        )
        payload = json.loads(artifact.content)

        self.assertEqual(
            payload["events"],
            [
                {
                    "type": "speech_start",
                    "participant_ref": "participant_alice",
                    "at_ms": 0,
                },
                {
                    "type": "speech_start",
                    "participant_ref": "participant_bob",
                    "at_ms": 20,
                },
                {
                    "type": "speech_end",
                    "participant_ref": "participant_alice",
                    "at_ms": 40,
                },
                {
                    "type": "speech_end",
                    "participant_ref": "participant_bob",
                    "at_ms": 60,
                },
            ],
        )
        self.assertEqual(
            [
                participant["display_name_events"][0]["label"]
                for participant in payload["participants"]
            ],
            ["Alice", "Bob"],
        )


if __name__ == "__main__":
    unittest.main()
