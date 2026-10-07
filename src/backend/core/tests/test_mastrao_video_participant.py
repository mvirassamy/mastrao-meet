"""Personal capability binding and safe status validation without DB or SFU."""

# Test names state their behavior.
# pylint: disable=missing-function-docstring

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from core.mastrao_recording_contract import RecordingContractRefused
from core.mastrao_recording_session import _validate_video
from core.mastrao_video_participant import bind_video_invitation

MEETING = "meeting_0123456789abcdef"
VIDEO = {
    "consultation_source": "email",
    "decision": "absent",
    "decision_basis": "no_opposition",
    "start_status": "pending",
    "decision_lock": "open",
    "started_at": None,
    "start_requested": False,
    "start_available": True,
}
PARTICIPANT = {
    "kind": "guest",
    "compact": "grant.payload.signature",
    "session_digest": "a" * 64,
}


def _request():
    return SimpleNamespace(
        session={"mastrao_video_choices": {MEETING: "choice.payload.signature"}}
    )


def _guest(state="allowed", confirmed=True):
    return SimpleNamespace(
        admission_state=state, decision_confirmed_at=object() if confirmed else None
    )


def _status(lock="open"):
    return {
        "mode": "recorded",
        "meeting_ref": MEETING,
        "video": {**VIDEO, "decision_lock": lock},
    }


def test_binding_posts_the_real_grant_and_session_without_email_identity():
    request = _request()
    with (
        patch(
            "core.mastrao_video_participant.active_guest_grant", return_value=_guest()
        ),
        patch(
            "core.mastrao_video_participant.post_core_json",
            return_value={
                "version": 1,
                "invitation_ref": "invitation_0123456789abcdef",
            },
        ) as core,
    ):
        assert bind_video_invitation(request, object(), PARTICIPANT, _status())
    assert core.call_args.kwargs["body"] == {
        "participant_grant": PARTICIPANT["compact"],
        "participant_session_digest": PARTICIPANT["session_digest"],
        "choice_token": "choice.payload.signature",
    }
    assert not request.session["mastrao_video_choices"]


@pytest.mark.parametrize(
    "state,confirmed", [("pending", False), ("allowed", False), ("denied", True)]
)
def test_no_binding_before_confirmed_guest_admission(state, confirmed):
    request = _request()
    with (
        patch(
            "core.mastrao_video_participant.active_guest_grant",
            return_value=_guest(state, confirmed),
        ),
        patch("core.mastrao_video_participant.post_core_json") as core,
    ):
        assert not bind_video_invitation(request, object(), PARTICIPANT, _status())
    core.assert_not_called()
    assert MEETING in request.session["mastrao_video_choices"]


@pytest.mark.parametrize("lock", ["start_in_progress", "started", "stopped"])
def test_entry_after_the_canonical_lock_does_not_request_a_new_binding(lock):
    request = _request()
    with patch("core.mastrao_video_participant.post_core_json") as core:
        assert not bind_video_invitation(request, object(), PARTICIPANT, _status(lock))
    core.assert_not_called()
    assert not request.session["mastrao_video_choices"]


@pytest.mark.parametrize("status", [404, 409])
def test_a_rejected_capability_only_requests_an_authoritative_reread(status):
    request = _request()
    with (
        patch(
            "core.mastrao_video_participant.active_guest_grant", return_value=_guest()
        ),
        patch(
            "core.mastrao_video_participant.post_core_json",
            side_effect=RecordingContractRefused(status),
        ),
    ):
        assert bind_video_invitation(request, object(), PARTICIPANT, _status())
    assert not request.session["mastrao_video_choices"]


def test_a_lost_binding_response_keeps_the_capability_for_the_exact_retry():
    request = _request()
    with (
        patch(
            "core.mastrao_video_participant.active_guest_grant", return_value=_guest()
        ),
        patch(
            "core.mastrao_video_participant.post_core_json",
            side_effect=RecordingContractRefused(503),
        ),
    ):
        with pytest.raises(RecordingContractRefused):
            bind_video_invitation(request, object(), PARTICIPANT, _status())
    assert MEETING in request.session["mastrao_video_choices"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("decision", "accepted_by_default"),
        ("decision", []),
        ("start_available", 1),
        ("start_requested", 1),
        ("started_at", True),
        ("decision_lock", "closed"),
    ],
)
def test_malformed_video_projection_is_rejected(field, value):
    with pytest.raises(RecordingContractRefused) as refused:
        _validate_video({**VIDEO, field: value})
    assert refused.value.status == 503


def test_no_opposition_is_preserved_as_absent_not_accepted():
    _validate_video(VIDEO)
    assert VIDEO["decision"] == "absent"
