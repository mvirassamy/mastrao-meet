"""Participant mapping for Room Audio post-transcription segments."""

from core.mastrao_speaker_mapping import map_speakers


def _evidence():
    return {
        "version": 1,
        "timeline_ended_at_ms": 2_000,
        "participants": [
            {
                "participant_ref": "alice",
                "display_name_events": [{"effective_at_ms": 0, "label": "Alice"}],
            },
            {
                "participant_ref": "bob",
                "display_name_events": [{"effective_at_ms": 0, "label": "Bob"}],
            },
        ],
        "events": [
            {"type": "speech_start", "participant_ref": "alice", "at_ms": 0},
            {"type": "speech_start", "participant_ref": "bob", "at_ms": 1_000},
            {"type": "speech_end", "participant_ref": "alice", "at_ms": 1_600},
            {"type": "speech_end", "participant_ref": "bob", "at_ms": 2_000},
        ],
    }


def test_maps_distinct_acoustic_speakers_to_participants():
    """Unambiguous portions establish one stable person per acoustic voice."""
    transcript = {
        "segments": [
            {
                "start_ms": 0,
                "end_ms": 900,
                "speaker": {"kind": "acoustic", "ref": "voice_1"},
            },
            {
                "start_ms": 1_700,
                "end_ms": 2_000,
                "speaker": {"kind": "acoustic", "ref": "voice_2"},
            },
        ]
    }

    mapped = map_speakers(transcript, _evidence())

    assert [segment["speaker"] for segment in mapped["segments"]] == [
        {"kind": "participant", "label": "Alice"},
        {"kind": "participant", "label": "Bob"},
    ]


def test_uses_stable_acoustic_identity_during_overlap():
    """A simultaneous interval retains the voice identity proven elsewhere."""
    transcript = {
        "segments": [
            {
                "start_ms": 0,
                "end_ms": 900,
                "speaker": {"kind": "acoustic", "ref": "voice_1"},
            },
            {
                "start_ms": 1_000,
                "end_ms": 1_300,
                "speaker": {"kind": "acoustic", "ref": "voice_1"},
            },
            {
                "start_ms": 1_700,
                "end_ms": 2_000,
                "speaker": {"kind": "acoustic", "ref": "voice_2"},
            },
        ]
    }

    mapped = map_speakers(transcript, _evidence())

    assert mapped["segments"][1]["speaker"] == {
        "kind": "participant",
        "label": "Alice",
    }
