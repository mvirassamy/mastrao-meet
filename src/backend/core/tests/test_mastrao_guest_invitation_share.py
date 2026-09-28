"""Focused proofs for canonical-room guest share links."""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.conf import settings
from django.test import Client, override_settings

PLATFORM = "https://app.mastrao.test"
TOKEN = "opaque-access-token-123"
ROOM_REF = "room_0123456789abcdef0123456789abcdef"
MEETING_REF = "meeting_0123456789abcdef"
TEST_SETTINGS = {
    "APPLICATION_BASE_URL": "https://meet.mastrao.test",
    "MASTRAO_PLATFORM_API_BASE_URL": PLATFORM,
    "SESSION_ENGINE": "django.contrib.sessions.backends.signed_cookies",
}


def _binding():
    binding = SimpleNamespace(meeting_ref=MEETING_REF, room_ref=ROOM_REF)
    binding.room = SimpleNamespace(slug=ROOM_REF, mastrao_binding=binding)
    return binding


def _room_lookup(binding):
    queryset = MagicMock()
    queryset.filter.return_value.first.return_value = binding.room
    return patch(
        "core.mastrao_guest_invitation_share.models.Room.objects.select_related",
        return_value=queryset,
    )


def _client_with_token():
    client = Client()
    session = client.session
    session["oidc_access_token"] = TOKEN
    session.save()
    client.cookies[settings.SESSION_COOKIE_NAME] = session.session_key
    return client


def _upstream(body):
    response = MagicMock()
    payload = json.dumps(body).encode()
    response.status_code = 201
    response.headers = {"content-length": str(len(payload))}
    response.iter_content.return_value = [payload]
    session = MagicMock()
    session.__enter__.return_value = session
    session.request.return_value = response
    return session


@override_settings(**TEST_SETTINGS)
def test_active_host_receives_same_origin_guest_url():
    """Return a same-origin guest URL only to the canonical room host."""

    binding = _binding()
    upstream = _upstream(
        {"meeting_ref": MEETING_REF, "guest_invitation": "aaa.bbb.ccc"}
    )
    with (
        _room_lookup(binding),
        patch(
            "core.mastrao_guest_invitation_share.active_host_grant",
            return_value=MagicMock(),
        ),
        patch("core.mastrao_platform_facade.requests.Session", return_value=upstream),
    ):
        response = _client_with_token().post(
            f"/api/v1.0/rooms/{binding.room_ref}/guest-invitation/"
        )

    assert response.status_code == 200
    assert response.json() == {
        "invite_url": "https://meet.mastrao.test/guest#invite=aaa.bbb.ccc"
    }
    upstream.request.assert_called_once_with(
        "POST",
        f"{PLATFORM}/api/meet/meetings/{MEETING_REF}/guest-invitation",
        headers={"authorization": f"Bearer {TOKEN}"},
        timeout=5,
        allow_redirects=False,
        stream=True,
    )
    assert response.headers["Cache-Control"] == "private, no-store"


@override_settings(**TEST_SETTINGS)
def test_non_host_and_ordinary_room_fail_closed_without_upstream_call():
    """Reject unauthorized and non-canonical rooms before calling Platform."""

    binding = _binding()
    with (
        _room_lookup(binding),
        patch("core.mastrao_platform_facade.requests.Session") as upstream,
    ):
        denied = _client_with_token().post(
            f"/api/v1.0/rooms/{binding.room_ref}/guest-invitation/"
        )
        ignored = _client_with_token().post(
            "/api/v1.0/rooms/ordinary/guest-invitation/"
        )

    assert denied.status_code == 404
    assert ignored.status_code == 404
    upstream.assert_not_called()


@override_settings(**TEST_SETTINGS)
def test_malformed_platform_credential_fails_closed():
    """Reject credentials that are not compact JWS values."""

    binding = _binding()
    upstream = _upstream(
        {
            "meeting_ref": MEETING_REF,
            "guest_invitation": "https://attacker.test/invite",
        }
    )
    with (
        _room_lookup(binding),
        patch(
            "core.mastrao_guest_invitation_share.active_host_grant",
            return_value=MagicMock(),
        ),
        patch("core.mastrao_platform_facade.requests.Session", return_value=upstream),
    ):
        response = _client_with_token().post(
            f"/api/v1.0/rooms/{binding.room_ref}/guest-invitation/"
        )

    assert response.status_code == 503
    assert "attacker" not in response.content.decode()


@override_settings(**TEST_SETTINGS)
def test_mismatched_platform_meeting_fails_closed():
    """Reject a Platform response bound to a different meeting."""

    binding = _binding()
    upstream = _upstream(
        {
            "meeting_ref": "meeting_different_0123456789",
            "guest_invitation": "aaa.bbb.ccc",
        }
    )
    with (
        _room_lookup(binding),
        patch(
            "core.mastrao_guest_invitation_share.active_host_grant",
            return_value=MagicMock(),
        ),
        patch("core.mastrao_platform_facade.requests.Session", return_value=upstream),
    ):
        response = _client_with_token().post(
            f"/api/v1.0/rooms/{binding.room_ref}/guest-invitation/"
        )

    assert response.status_code == 503
