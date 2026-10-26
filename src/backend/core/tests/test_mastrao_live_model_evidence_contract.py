"""Pure gateway contract tests; no database, settings, services or provider API."""

import hashlib
import json
from unittest.mock import patch

import pytest

from core.mastrao_live_delivery_contract import (
    MAX_EVENT_BYTES,
    canonical_delivery_event,
    validate_delivery_receipt,
)
from core.mastrao_recording_contract import RecordingContractRefused

SOURCE = "source_reference_0001"
LEG = "initial_leg_reference_0001"


def record(**changes):
    """Keep exact Unicode and all nullable fields explicit."""
    return {
        "origin": "terminal",
        "model_ref": "model-😀",
        "name": ' e\u0301 é "\\\n ',
        "version": None,
        "architecture": None,
        **changes,
    }


def evidence(**changes):
    """The worker snapshot is a v3 event inside the existing v2 envelope."""
    return {
        "version": 3,
        "source_ref": SOURCE,
        "leg_ref": LEG,
        "sequence": 1,
        "kind": "model_evidence",
        "provider": "deepgram",
        "models": [record()],
        "truncated": False,
        **changes,
    }


def receipt(event, **changes):
    """Construct a bound v2 receipt without presuming a final or incomplete."""
    return {
        "version": 2,
        "source_ref": SOURCE,
        "leg_ref": LEG,
        "sequence": event["sequence"],
        "event_digest": canonical_delivery_event(event, event)[1],
        "state": "collecting",
        "confirmed_sequence": event["sequence"],
        "final_event_count": 0,
        "incomplete": False,
        "meeting_coverage": "unverified",
        **changes,
    }


@pytest.mark.parametrize("provider", ["openai", "mistral", "deepgram"])
def test_evidence_canonical_order_and_neutral_receipt(provider):
    event = evidence(provider=provider)
    expected = (
        '{"version":3,"source_ref":"source_reference_0001",'
        '"leg_ref":"initial_leg_reference_0001","sequence":1,'
        f'"kind":"model_evidence","provider":"{provider}","models":['
        '{"origin":"terminal","model_ref":"model-😀","name":" e\u0301 é '
        '\\"\\\\\\n ","version":null,"architecture":null}],"truncated":false}'
    ).encode()
    event["models"][0] = dict(reversed(list(event["models"][0].items())))
    ordered, digest = canonical_delivery_event(
        dict(reversed(list(event.items()))), event
    )
    assert (
        json.dumps(ordered, ensure_ascii=False, separators=(",", ":")).encode()
        == expected
    )
    assert digest == hashlib.sha256(expected).hexdigest()
    for incomplete in (False, True):
        result = receipt(event, incomplete=incomplete)
        assert validate_delivery_receipt(result, ordered, digest) == result


@pytest.mark.parametrize(
    "changes",
    [
        {"version": 2},
        {"version": True},
        {"provider": "unknown"},
        {"provider": []},
        {"truncated": 1},
        {"models": {}},
        {"models": [record()] * 9},
        {"models": [record()] * 2},
        {"raw": {}},
        {"sequence": 100_001},
        {"sequence": True},
        {"source_ref": "foreign_source_ref_0001"},
        {"leg_ref": "foreign_leg_ref_0001"},
    ],
)
def test_evidence_rejects_invalid_fields_and_binding(changes):
    with pytest.raises(RecordingContractRefused) as error:
        canonical_delivery_event(evidence(**changes), evidence())
    assert error.value.status == 400


@pytest.mark.parametrize(
    "changes",
    [
        {"origin": "request"},
        {"origin": []},
        {"model_ref": None, "name": None},
        {"version": " "},
        {"version": "\ufeff"},
        {"model_ref": False},
        {"name": "😀" * 81},
        {"name": "a" * 161},
        {"name": "\ud800"},
        {"extra": "private"},
    ],
)
def test_evidence_rejects_invalid_records(changes):
    with pytest.raises(RecordingContractRefused) as error:
        canonical_delivery_event(evidence(models=[record(**changes)]), evidence())
    assert error.value.status == 400


def test_empty_and_maximum_evidence_fit_the_unchanged_byte_bound():
    canonical_delivery_event(evidence(models=[]), evidence())
    # Control characters need six JSON bytes each, the worst escaping case.
    models = [
        record(
            model_ref=str(i),
            name="x" + "\x00" * 159,
            version="x" + "\x01" * 159,
            architecture="x" + "\x02" * 159,
        )
        for i in range(8)
    ]
    event, _ = canonical_delivery_event(evidence(models=models), evidence())
    assert (
        len(json.dumps(event, ensure_ascii=False, separators=(",", ":")).encode())
        < MAX_EVENT_BYTES
    )
    canonical_delivery_event(evidence(models=[record(name="😀" * 80)]), evidence())
    with patch("core.mastrao_live_delivery_contract.MAX_EVENT_BYTES", 10):
        with pytest.raises(RecordingContractRefused):
            canonical_delivery_event(evidence(), evidence())


@pytest.mark.parametrize("sequence", [2, 9, 100_000])
@pytest.mark.parametrize("reason", ["track_unpublished", "meeting_ended"])
def test_end_after_only_evidence_is_neutral(sequence, reason):
    event = {
        "version": 2,
        "source_ref": SOURCE,
        "leg_ref": LEG,
        "sequence": sequence,
        "kind": "end",
        "reason": reason,
        "final_event_count": 0,
    }
    result = receipt(event, state="ended")
    assert validate_delivery_receipt(result, event, result["event_digest"]) == result


@pytest.mark.parametrize(
    "reason,count",
    [
        ("meeting_ended", 1),
        ("provider_failed", 0),
        ("capture_incomplete", 0),
    ],
)
def test_neutral_evidence_does_not_clear_known_incomplete(reason, count):
    event = {
        "version": 2,
        "source_ref": SOURCE,
        "leg_ref": LEG,
        "sequence": 3,
        "kind": "end",
        "reason": reason,
        "final_event_count": count,
    }
    result = receipt(event, state="ended", final_event_count=count)
    with pytest.raises(RecordingContractRefused) as error:
        validate_delivery_receipt(result, event, result["event_digest"])
    assert error.value.status == 503


@pytest.mark.parametrize(
    "changes",
    [
        {"version": 3},
        {"final_event_count": 1},
        {"incomplete": 0},
        {"meeting_coverage": "complete"},
        {"state": "ended"},
        {"confirmed_sequence": 0},
        {"event_digest": "0" * 64},
    ],
)
def test_evidence_receipt_keeps_exact_v2_bounds(changes):
    event = evidence()
    result = receipt(event, **changes)
    with pytest.raises(RecordingContractRefused) as error:
        validate_delivery_receipt(result, event, receipt(event)["event_digest"])
    assert error.value.status == 503


@pytest.mark.parametrize("kind", ["final", "gap", "end"])
def test_existing_kinds_cannot_become_v3(kind):
    event = {
        "version": 3,
        "source_ref": SOURCE,
        "leg_ref": LEG,
        "sequence": 2,
        "kind": kind,
    }
    if kind == "final":
        event.update(
            item_ref="item",
            revision=1,
            text="exact",
            language="fr",
            timing={"state": "unavailable"},
        )
    elif kind == "gap":
        event.update(reason="capture_incomplete")
    else:
        event.update(reason="meeting_ended", final_event_count=0)
    with pytest.raises(RecordingContractRefused) as error:
        canonical_delivery_event(event, event)
    assert error.value.status == 400
