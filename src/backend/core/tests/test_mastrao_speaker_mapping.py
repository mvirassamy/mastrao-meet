"""Tests for mapping diarized transcript speakers to meeting participants."""

import copy

import pytest

from core.mastrao_transcription_artifact import _speech_intervals, map_speakers


@pytest.mark.parametrize("file_offset_ms", [2587, 0, -800])
def test_file_origin_aligns_turn_boundary_without_changing_transcript_times(
    file_offset_ms,
):
    """A first Martin phrase must not inherit the preceding acoustic cluster."""
    origin = 1_790_950_776_348
    transcript = {
        "segments": [
            {
                "segment_id": "segment_anchor",
                "start_ms": 60_000,
                "end_ms": 80_000,
                "speaker": {"kind": "acoustic", "ref": "speaker_0"},
                "text": "Earlier turn",
            },
            {
                "segment_id": "segment_boundary",
                "start_ms": 89_212,
                "end_ms": 91_112,
                "speaker": {"kind": "acoustic", "ref": "speaker_0"},
                "text": "First Martin phrase",
            },
        ]
    }
    evidence = {
        "recording_started_at_ms": origin,
        "timeline_ended_at_ms": 100_000 + file_offset_ms,
        "participants": [
            {"participant_ref": "matt", "display_name_events": [{"label": "Matthias"}]},
            {"participant_ref": "martin", "display_name_events": [{"label": "Martin"}]},
        ],
        "events": [
            {"type": kind, "participant_ref": ref, "at_ms": time + file_offset_ms}
            for kind, ref, time in [
                ("speech_start", "matt", 60_000),
                ("speech_end", "matt", 80_000),
                ("speech_start", "martin", 88_680),
                ("speech_end", "martin", 90_031),
                ("speech_start", "martin", 90_130),
                ("speech_end", "martin", 91_130),
            ]
        ],
    }
    original_evidence = copy.deepcopy(evidence)
    mapped = map_speakers(
        copy.deepcopy(transcript), evidence, audio_started_at_ms=origin + file_offset_ms
    )
    assert [s["speaker"]["label"] for s in mapped["segments"]] == ["Matthias", "Martin"]
    assert [
        {k: v for k, v in s.items() if k != "speaker"} for s in mapped["segments"]
    ] == [
        {k: v for k, v in s.items() if k != "speaker"} for s in transcript["segments"]
    ]
    assert evidence == original_evidence


def test_file_origin_aligns_open_vad_interval_end():
    """The final open interval ends in the same audio coordinate system."""
    evidence = {
        "recording_started_at_ms": 10_000,
        "timeline_ended_at_ms": 8_000,
        "events": [
            {"type": "speech_start", "participant_ref": "martin", "at_ms": 5_000}
        ],
    }
    assert _speech_intervals(evidence, 12_000) == {"martin": [(3_000, 6_000)]}


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


def test_dominant_segment_overlap_wins_over_short_cross_talk():
    """Use dominant VAD overlap when short cross-talk makes the cluster ambiguous."""
    transcript = {
        "segments": [
            {
                "segment_id": "segment_000000000001",
                "start_ms": 0,
                "end_ms": 1_000,
                "speaker": {"kind": "acoustic", "ref": "shared_cluster"},
                "text": "bonjour de matt",
            },
            {
                "segment_id": "segment_000000000002",
                "start_ms": 2_000,
                "end_ms": 3_000,
                "speaker": {"kind": "acoustic", "ref": "shared_cluster"},
                "text": "bonjour de vanille",
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
                "at_ms": 1_000,
                "type": "speech_end",
                "participant_ref": "participant_matt",
            },
            {
                "at_ms": 900,
                "type": "speech_start",
                "participant_ref": "participant_vanille",
            },
            {
                "at_ms": 1_000,
                "type": "speech_end",
                "participant_ref": "participant_vanille",
            },
            {
                "at_ms": 2_000,
                "type": "speech_start",
                "participant_ref": "participant_matt",
            },
            {
                "at_ms": 2_100,
                "type": "speech_end",
                "participant_ref": "participant_matt",
            },
            {
                "at_ms": 2_000,
                "type": "speech_start",
                "participant_ref": "participant_vanille",
            },
            {
                "at_ms": 3_000,
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


def test_ambiguous_segment_stays_anonymous_without_unambiguous_cluster_fallback():
    """Keep a turn anonymous when its VAD overlap has two participants."""
    transcript = {
        "segments": [
            {
                "segment_id": "segment_000000000001",
                "start_ms": 1_000,
                "end_ms": 2_000,
                "speaker": {"kind": "acoustic", "ref": "speaker_0"},
                "text": "chevauchement ambigu",
            }
        ],
        "language": "fr",
    }
    evidence = {
        "timeline_ended_at_ms": 3_000,
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
                "at_ms": 1_000,
                "type": "speech_start",
                "participant_ref": "participant_vanille",
            },
            {
                "at_ms": 3_000,
                "type": "speech_end",
                "participant_ref": "participant_vanille",
            },
        ],
    }

    mapped = map_speakers(copy.deepcopy(transcript), evidence)

    assert mapped["segments"][0]["speaker"] == {
        "kind": "anonymous",
        "index": 1,
    }


def test_tied_segment_stays_anonymous_when_cluster_has_a_dominant_mapping():
    """Do not let a cluster fallback override a segment-level overlap tie."""
    transcript = {
        "segments": [
            {
                "segment_id": "segment_000000000001",
                "start_ms": 0,
                "end_ms": 1_000,
                "speaker": {"kind": "acoustic", "ref": "shared_cluster"},
                "text": "chevauchement égalitaire",
            },
            {
                "segment_id": "segment_000000000002",
                "start_ms": 2_000,
                "end_ms": 3_000,
                "speaker": {"kind": "acoustic", "ref": "shared_cluster"},
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
                "at_ms": 1_000,
                "type": "speech_end",
                "participant_ref": "participant_matt",
            },
            {
                "at_ms": 0,
                "type": "speech_start",
                "participant_ref": "participant_vanille",
            },
            {
                "at_ms": 1_000,
                "type": "speech_end",
                "participant_ref": "participant_vanille",
            },
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
        {"kind": "anonymous", "index": 1},
        {"kind": "participant", "label": "Matt"},
    ]
