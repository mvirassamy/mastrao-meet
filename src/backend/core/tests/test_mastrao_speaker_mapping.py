"""Tests for mapping diarized transcript speakers to meeting participants."""

import copy

from core.mastrao_transcription_artifact import map_speakers


def test_unique_segment_timeline_wins_when_one_acoustic_cluster_spans_people():
    """Map a reused acoustic cluster from each segment's unique VAD overlap."""
    transcript = {
        "segments": [
            {
                "segment_id": "segment_000000000001",
                "start_ms": 0,
                "end_ms": 2_000,
                "speaker": {"kind": "acoustic", "ref": "speaker_0"},
                "text": "bonjour de matt",
            },
            {
                "segment_id": "segment_000000000002",
                "start_ms": 3_000,
                "end_ms": 4_000,
                "speaker": {"kind": "acoustic", "ref": "speaker_0"},
                "text": "bonjour de vanille",
            },
        ],
        "language": "fr",
    }
    evidence = {
        "timeline_ended_at_ms": 4_000,
        "participants": [
            {
                "participant_ref": "participant_matt",
                "display_name_events": [{"effective_at_ms": 0, "label": "Matt"}],
            },
            {
                "participant_ref": "participant_vanille",
                "display_name_events": [{"effective_at_ms": 0, "label": "Vanille"}],
            },
        ],
        "events": [
            {
                "at_ms": 0,
                "type": "speech_start",
                "participant_ref": "participant_matt",
            },
            {
                "at_ms": 2_000,
                "type": "speech_end",
                "participant_ref": "participant_matt",
            },
            {
                "at_ms": 3_000,
                "type": "speech_start",
                "participant_ref": "participant_vanille",
            },
            {
                "at_ms": 4_000,
                "type": "speech_end",
                "participant_ref": "participant_vanille",
            },
        ],
    }

    mapped = map_speakers(copy.deepcopy(transcript), evidence)

    assert [segment["speaker"] for segment in mapped["segments"]] == [
        {"kind": "participant", "label": "Matt"},
        {"kind": "participant", "label": "Vanille"},
    ]


def test_segment_without_unique_timeline_keeps_global_cluster_fallback():
    """Keep cluster-level attribution when a segment has no direct VAD match."""
    transcript = {
        "segments": [
            {
                "segment_id": "segment_000000000001",
                "start_ms": 0,
                "end_ms": 1_000,
                "speaker": {"kind": "acoustic", "ref": "speaker_0"},
                "text": "segment sans preuve directe",
            },
            {
                "segment_id": "segment_000000000002",
                "start_ms": 2_000,
                "end_ms": 3_000,
                "speaker": {"kind": "acoustic", "ref": "speaker_0"},
                "text": "bonjour de matt",
            },
        ],
        "language": "fr",
    }
    evidence = {
        "timeline_ended_at_ms": 3_000,
        "participants": [
            {
                "participant_ref": "participant_matt",
                "display_name_events": [{"effective_at_ms": 0, "label": "Matt"}],
            }
        ],
        "events": [
            {
                "at_ms": 2_000,
                "type": "speech_start",
                "participant_ref": "participant_matt",
            },
            {
                "at_ms": 3_000,
                "type": "speech_end",
                "participant_ref": "participant_matt",
            },
        ],
    }

    mapped = map_speakers(copy.deepcopy(transcript), evidence)

    assert [segment["speaker"] for segment in mapped["segments"]] == [
        {"kind": "participant", "label": "Matt"},
        {"kind": "participant", "label": "Matt"},
    ]
