"""SMTP capture and recovery tests without external mail, DB or SFU."""

# Test names state their behavior.
# pylint: disable=missing-function-docstring

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from django.core import mail
from django.test import override_settings

import pytest

from core.mastrao_platform_facade import PlatformFacadeError
from core.mastrao_recording_contract import RecordingContractRefused
from core.mastrao_video_invitations import (
    deliver_meeting_invitations,
    validate_invitations,
)
from core.services.invitation import InvitationError

BINDING = SimpleNamespace(room=object(), meeting_ref="meeting_0123456789abcdef")
INVITATION = {
    "invitation_ref": "invitation_0123456789abcdef",
    "email": "guest@example.com",
    "delivery_state": "pending",
}
CHOICE_TOKEN = "eyJoZWFkZXIiOiJ0ZXN0In0.eyJwYXlsb2FkIjoidGVzdCJ9.c2lnbmF0dXJl"
ROOM_URL = (
    "https://meet.example.com/guest#organization=organization_test"
    "&share=share_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdef"
)
SCHEDULE = {
    "title": "Réunion <équipe>",
    "scheduled_start_at": int(datetime(2027, 1, 15, 9, tzinfo=UTC).timestamp()),
    "scheduled_end_at": int(datetime(2027, 1, 15, 10, tzinfo=UTC).timestamp()),
    "timezone": "Europe/Paris",
}
SETTINGS = {
    "EMAIL_BACKEND": "django.core.mail.backends.locmem.EmailBackend",
    "EMAIL_FROM": "host@example.com",
    "MASTRAO_PLATFORM_API_BASE_URL": "https://app.example.com",
    "MASTRAO_CORE_VIDEO_DELIVERY_ENDPOINT": (
        "http://cabinet-core:3911/internal/v1/meetings/video-invitations/delivery"
    ),
}


def _response(state, sending=False):
    response = {
        **INVITATION,
        "version": 1,
        "delivery_ref": INVITATION["invitation_ref"],
        "delivery_state": state,
        "send_required": sending,
    }
    if sending:
        response["choice_token"] = CHOICE_TOKEN
    return response


def _request():
    return SimpleNamespace(
        user=SimpleNamespace(email="organizer@example.com"), session={}
    )


def _signed(payload, _jose):
    return json.dumps(payload)


@pytest.fixture(autouse=True)
def host_authority():
    """The room binding itself has no organization; use verified host claims."""
    with patch(
        "core.mastrao_video_invitations._participant",
        return_value={
            "kind": "host",
            "claims": {"organization_external_id": "organization_test"},
        },
    ):
        yield


@override_settings(**SETTINGS)
def test_personal_notice_is_sent_once_and_contains_choice_and_guest_links():
    with (
        patch(
            "core.mastrao_video_invitations.guest_invitation_url", return_value=ROOM_URL
        ),
        patch("core.mastrao_video_invitations._sign", side_effect=_signed),
        patch(
            "core.mastrao_video_invitations.post_core_json",
            side_effect=[
                _response("sending", True),
                _response("sent"),
                _response("sent"),
            ],
        ) as core,
    ):
        result = deliver_meeting_invitations(
            _request(), BINDING, [INVITATION], SCHEDULE
        )
        replay = deliver_meeting_invitations(
            _request(), BINDING, [INVITATION], SCHEDULE
        )
    assert result == replay == [{**INVITATION, "delivery_state": "sent"}]
    assert len(mail.outbox) == 1
    message = mail.outbox[0]
    assert message.to == [INVITATION["email"]]
    assert "15/01/2027 10:00" in message.body
    assert "no objection, not an explicit acceptance" in message.body
    assert "until video capture actually starts" in message.body
    assert "Audio capture for transcription has a separate choice" in message.body
    assert "Réunion &lt;équipe&gt;" in message.alternatives[0].content
    urls = [
        line.split(": ", 1)[1]
        for line in message.body.splitlines()
        if ": https://" in line
    ]
    assert parse_qs(urlparse(urls[0]).fragment)["video_choice"] == [CHOICE_TOKEN]
    assert parse_qs(urlparse(urls[1]).query)["token"] == [CHOICE_TOKEN]
    assertions = [
        json.loads(call.kwargs["body"]["delivery_assertion"])
        for call in core.call_args_list
    ]
    assert [a["operation"] for a in assertions] == ["claim", "ack", "claim"]
    assert assertions[1]["outcome"] == "sent"
    assert CHOICE_TOKEN not in json.dumps(result)


@override_settings(**SETTINGS)
def test_lost_acknowledgement_retries_only_the_same_acknowledgement():
    with (
        patch(
            "core.mastrao_video_invitations.guest_invitation_url", return_value=ROOM_URL
        ),
        patch("core.mastrao_video_invitations._sign", side_effect=_signed),
        patch(
            "core.mastrao_video_invitations.post_core_json",
            side_effect=[
                _response("sending", True),
                RecordingContractRefused(503),
                _response("sent"),
            ],
        ) as core,
    ):
        result = deliver_meeting_invitations(
            _request(), BINDING, [INVITATION], SCHEDULE
        )
    assert result[0]["delivery_state"] == "sent"
    assert len(mail.outbox) == 1
    assert core.call_args_list[1] == core.call_args_list[2]


@override_settings(**SETTINGS)
@pytest.mark.parametrize("state", ["sending", "unknown", "sent"])
def test_a_replayed_claim_never_resends_smtp(state):
    with (
        patch(
            "core.mastrao_video_invitations.guest_invitation_url", return_value=ROOM_URL
        ),
        patch("core.mastrao_video_invitations._sign", side_effect=_signed),
        patch(
            "core.mastrao_video_invitations.post_core_json",
            return_value=_response(state),
        ),
        patch(
            "core.mastrao_video_invitations.InvitationService.invite_to_scheduled_room"
        ) as send,
    ):
        result = deliver_meeting_invitations(
            _request(), BINDING, [INVITATION], SCHEDULE
        )
    send.assert_not_called()
    assert result[0]["delivery_state"] == state


@override_settings(**SETTINGS)
def test_uncertain_smtp_attempt_is_acknowledged_as_unknown():
    with (
        patch(
            "core.mastrao_video_invitations.guest_invitation_url", return_value=ROOM_URL
        ),
        patch("core.mastrao_video_invitations._sign", side_effect=_signed),
        patch(
            "core.mastrao_video_invitations.post_core_json",
            side_effect=[_response("sending", True), _response("unknown")],
        ) as core,
        patch(
            "core.mastrao_video_invitations.InvitationService.invite_to_scheduled_room",
            side_effect=InvitationError("Unknown"),
        ) as send,
    ):
        result = deliver_meeting_invitations(
            _request(), BINDING, [INVITATION], SCHEDULE
        )
    send.assert_called_once()
    assert result[0]["delivery_state"] == "unknown"
    assert (
        json.loads(core.call_args.kwargs["body"]["delivery_assertion"])["outcome"]
        == "unknown"
    )


@override_settings(**SETTINGS)
def test_smtp_acceptance_without_a_confirmed_ack_remains_unknown():
    with (
        patch(
            "core.mastrao_video_invitations.guest_invitation_url", return_value=ROOM_URL
        ),
        patch("core.mastrao_video_invitations._sign", side_effect=_signed),
        patch(
            "core.mastrao_video_invitations.post_core_json",
            side_effect=[
                _response("sending", True),
                RecordingContractRefused(503),
                RecordingContractRefused(503),
            ],
        ),
    ):
        result = deliver_meeting_invitations(
            _request(), BINDING, [INVITATION], SCHEDULE
        )
    assert len(mail.outbox) == 1
    assert result[0]["delivery_state"] == "unknown"


@pytest.mark.parametrize(
    "invitations",
    [
        None,
        [{**INVITATION, "choice_token": CHOICE_TOKEN}],
        [{**INVITATION, "email": "invalid"}],
        [INVITATION, INVITATION],
    ],
)
def test_creator_metadata_rejects_capabilities_and_invalid_recipients(invitations):
    with pytest.raises(PlatformFacadeError):
        validate_invitations(invitations)


def test_empty_invitees_do_not_call_the_delivery_runtime():
    with patch("core.mastrao_video_invitations.guest_invitation_url") as share:
        assert not deliver_meeting_invitations(_request(), BINDING, [], SCHEDULE)
    share.assert_not_called()
