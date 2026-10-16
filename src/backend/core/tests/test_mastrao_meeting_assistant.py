"""Deterministic assistant transport and private-source boundary checks."""

import copy
import json

from django.test import RequestFactory

import pytest

from core.mastrao_meeting_assistant import (
    MAX_COMMAND_BYTES,
    _citation_command,
    _delete_command,
    _send_command,
    _validate_opened,
    _validate_sent,
    _validate_thread,
)
from core.mastrao_platform_facade import PlatformFacadeError

MEETING = "meeting_0123456789abcdef"
COMMAND = {
    "version": 1,
    "meeting_ref": MEETING,
    "command_id": "command_0123456789abcdef",
    "operation": "question",
    "question": "Quel délai a été confirmé ?",
    "expected_thread_version": 0,
}
CITATION = {
    "citation_id": "citation_0123456789abcdef",
    "meeting_ref": MEETING,
    "source_kind": "provider_run",
    "source_ref": "transcription_0123456789abcdef",
    "source_version": 1,
    "source_digest": "a" * 64,
    "segment_id": "segment_1",
    "start_ms": 1000,
    "end_ms": 2000,
    "time_basis": "meeting_relative_ms",
}
COVERAGE = {
    "state": "partial",
    "source_coverage": "partial_sources",
    "selection": "relevant_passages",
    "total_segments": 10,
    "selected_segments": 2,
    "omitted_segments": 8,
}
RESULT = {
    "state": "answered",
    "text": "Le délai confirmé est vendredi.",
    "rationale_category": "supported",
    "citations": [CITATION],
    "actions": [],
    "email_draft": None,
    "publication_state": "private_proposal",
}
TURN = {
    "command_id": COMMAND["command_id"],
    "operation": "question",
    "question": COMMAND["question"],
    "state": "completed",
    "result": RESULT,
    "coverage": COVERAGE,
    "created_at": 1_800_000_000,
}
THREAD = {
    "version": 1,
    "meeting_ref": MEETING,
    "thread_ref": "thread_0123456789abcdef",
    "thread_version": 1,
    "visibility": "private",
    "deleted": False,
    "turns": [TURN],
    "has_more": False,
    "next_cursor": None,
}


def _request(body):
    return RequestFactory().post("/", json.dumps(body), content_type="application/json")


@pytest.mark.parametrize("operation", ["question", "summary", "actions", "email_draft"])
def test_operations_keep_the_exact_command_identity_and_thread_version(operation):
    """Every F07/F08 operation shares Core's durable command envelope."""

    command = {**COMMAND, "operation": operation}
    assert _send_command(_request(command), MEETING) == command


@pytest.mark.parametrize(
    "changes",
    [
        {"actor_ref": "other"},
        {"organization_ref": "other"},
        {"participant_grant": "guest"},
        {"personal_notes": "private"},
        {"meeting_ref": "meeting_other0123456789"},
        {"thread_ref": "thread_other0123456789"},
        {"operation": "send_email"},
        {"operation": []},
        {"expected_thread_version": True},
        {"expected_thread_version": -1},
        {"command_id": "short"},
        {"question": " "},
        {"question": "x" * 1025},
        {"question": "\ud800"},
    ],
)
def test_browser_cannot_choose_identity_scope_or_external_actions(changes):
    """Refuse identity injection, unbound resources and unsupported operations."""

    with pytest.raises(PlatformFacadeError) as refused:
        _send_command(_request({**COMMAND, **changes}), MEETING)
    assert refused.value.status == 422


def test_command_body_remains_bounded_without_content_length():
    """Enforce the streamed JSON budget before transport can begin."""

    request = RequestFactory().post(
        "/", b" " * (MAX_COMMAND_BYTES + 1), content_type="application/json"
    )
    request.META.pop("CONTENT_LENGTH")
    with pytest.raises(PlatformFacadeError) as refused:
        _send_command(request, MEETING)
    assert refused.value.status == 422


def test_duplicate_command_fields_are_rejected():
    """Reject ambiguous command identity rather than keeping the last value."""

    raw = json.dumps(COMMAND).replace('"version": 1', '"version": 1, "version": 1')
    request = RequestFactory().post("/", raw, content_type="application/json")
    with pytest.raises(PlatformFacadeError) as refused:
        _send_command(request, MEETING)
    assert refused.value.status == 422


def test_delete_only_carries_the_private_thread_revision_and_command():
    """Deletion uses the authenticated author's current thread, without an author selector."""

    command = {
        field: COMMAND[field]
        for field in ("version", "meeting_ref", "command_id", "expected_thread_version")
    }
    assert _delete_command(_request(command), MEETING) == command


@pytest.mark.parametrize(
    "changes",
    [
        {"meeting_ref": "meeting_other0123456789"},
        {"source_url": "https://private.example"},
        {"source_digest": "wrong"},
        {"end_ms": 999},
        {"source_kind": []},
        {"source_version": True},
    ],
)
def test_citation_opening_cannot_cross_a_meeting_or_inject_a_private_url(changes):
    """Only a complete, version-bound citation can reach the source resolver."""

    command = {
        field: COMMAND[field] for field in ("version", "meeting_ref", "command_id")
    }
    command["citation"] = {**CITATION, **changes}
    with pytest.raises(PlatformFacadeError) as refused:
        _citation_command(_request(command), MEETING)
    assert refused.value.status == 422


def test_thread_preserves_partial_coverage_and_canonical_citation():
    """A response keeps useful coverage limits and the source's exact version."""

    value = copy.deepcopy(THREAD)
    _validate_thread(value, MEETING)
    assert value == THREAD


@pytest.mark.parametrize(
    "state", ["failed", "unknown", "processing", "sources_unavailable"]
)
def test_technical_and_access_states_are_not_factual_abstentions(state):
    """Keep technical turn states distinct from a successful insufficient-evidence answer."""

    value = copy.deepcopy(THREAD)
    value["turns"][0].update(state=state, result=None)
    _validate_thread(value, MEETING)
    assert value["turns"][0]["state"] == state
    assert value["turns"][0]["result"] is None


@pytest.mark.parametrize(
    "changes",
    [
        {"visibility": "shared"},
        {"organization_ref": "other"},
        {"meeting_ref": "meeting_other0123456789"},
        {"thread_version": True},
        {"next_cursor": "short"},
    ],
)
def test_unbound_or_uncontracted_private_history_is_not_forwarded(changes):
    """An upstream mismatch cannot disclose another scope's private thread."""

    with pytest.raises(PlatformFacadeError) as refused:
        _validate_thread({**THREAD, **changes}, MEETING)
    assert refused.value.status == 503


@pytest.mark.parametrize("location", ["result", "citation", "coverage", "turn"])
def test_nested_uncontracted_fields_are_not_forwarded(location):
    """Reject private URLs and unknown fields even when nested in otherwise valid output."""

    value = copy.deepcopy(THREAD)
    turn = value["turns"][0]
    locations = {
        "result": turn["result"],
        "citation": turn["result"]["citations"][0],
        "coverage": turn["coverage"],
        "turn": turn,
    }
    locations[location]["private_url"] = "https://private.example"
    with pytest.raises(PlatformFacadeError) as refused:
        _validate_thread(value, MEETING)
    assert refused.value.status == 503


def test_send_receipt_must_match_the_command_and_operation():
    """An unrelated durable turn cannot be shown as this command's result."""

    value = {
        "version": 1,
        "thread": THREAD,
        "turn": {**TURN, "operation": "email_draft"},
        "idempotent_replay": False,
    }
    with pytest.raises(PlatformFacadeError) as refused:
        _validate_sent(value, MEETING, COMMAND)
    assert refused.value.status == 503


def test_source_response_must_match_the_requested_version_and_passage():
    """Open real text only after the resolver returns the exact authorized citation."""

    value = {
        "version": 1,
        "segment": {
            "citation": CITATION,
            "text": "Le délai est vendredi.",
            "speaker": None,
            "status": "transcript",
        },
    }
    _validate_opened(value, MEETING, CITATION)
    value["segment"]["citation"] = {**CITATION, "source_version": 2}
    with pytest.raises(PlatformFacadeError) as refused:
        _validate_opened(value, MEETING, CITATION)
    assert refused.value.status == 503
