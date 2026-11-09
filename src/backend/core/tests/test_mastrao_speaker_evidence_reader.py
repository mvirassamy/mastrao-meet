"""Bounded reads for room speaker evidence."""

import io
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from core.mastrao_recording_contract import RecordingContractRefused
from core.mastrao_speaker_evidence_reader import read_speaker_evidence


def _source():
    return SimpleNamespace(
        manifest={
            "meeting_ref": "meeting_evidence_fixture",
            "room_ref": "room_evidence_fixture",
        }
    )


def _evidence():
    return {
        "version": 1,
        "meeting_ref": "meeting_evidence_fixture",
        "room_ref": "room_evidence_fixture",
        "participants": [],
        "events": [],
    }


def test_reads_evidence_bound_to_the_source_meeting():
    """The reader returns evidence only for its exact meeting and room."""

    evidence = _evidence()

    with (
        patch(
            "core.mastrao_speaker_evidence_reader._evidence_object_ref",
            return_value="mastrao-speaker-evidence/evidence_fixture_123.json",
        ),
        patch(
            "core.mastrao_speaker_evidence_reader.default_storage.open",
            return_value=io.BytesIO(json.dumps(evidence).encode()),
        ),
    ):
        assert read_speaker_evidence(_source()) == evidence


def test_refuses_evidence_from_another_meeting():
    """Cross-meeting evidence cannot enter the canonical transcript."""

    evidence = {**_evidence(), "meeting_ref": "meeting_other"}

    with (
        patch(
            "core.mastrao_speaker_evidence_reader._evidence_object_ref",
            return_value="mastrao-speaker-evidence/evidence_fixture_123.json",
        ),
        patch(
            "core.mastrao_speaker_evidence_reader.default_storage.open",
            return_value=io.BytesIO(json.dumps(evidence).encode()),
        ),
        pytest.raises(RecordingContractRefused),
    ):
        read_speaker_evidence(_source())
