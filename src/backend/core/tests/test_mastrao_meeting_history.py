"""Tests for the same-origin Platform meeting facade bridge."""

import json
from unittest.mock import MagicMock, patch

from django.conf import settings
from django.test import Client, override_settings

PLATFORM = "https://app.mastrao-staging.com"
TOKEN = "opaque-access-token-123"
IDEMPOTENCY_KEY = "meeting-request-123456789"
TEST_SETTINGS = {
    "MASTRAO_PLATFORM_API_BASE_URL": PLATFORM,
    "SESSION_ENGINE": "django.contrib.sessions.backends.signed_cookies",
}


def _client_with_token():
    client = Client()
    session = client.session
    session["oidc_access_token"] = TOKEN
    session.save()
    client.cookies[settings.SESSION_COOKIE_NAME] = session.session_key
    return client


def _upstream(status, body):
    response = MagicMock()
    payload = json.dumps(body).encode()
    response.status_code = status
    response.headers = {"content-length": str(len(payload))}
    response.iter_content.return_value = [payload]
    return response


def _session_returning(response):
    session = MagicMock()
    session.__enter__.return_value = session
    session.request.return_value = response
    return session


@override_settings(**TEST_SETTINGS)
def test_create_meeting_forwards_server_side_token_without_redirect():
    """Consume the host handoff server-side without exposing it to the client."""

    upstream = _upstream(
        201,
        {
            "meeting_ref": "meeting_0123456789abcdef",
            "room_ref": "room_0123456789abcdef",
            "host_handoff": "handoff_0123456789abcdef",
        },
    )
    session = _session_returning(upstream)
    binding = MagicMock()
    binding.room.slug = "room_0123456789abcdef"
    with (
        patch("core.mastrao_platform_facade.requests.Session", return_value=session),
        patch(
            "core.mastrao_meeting_history.consume_host_handoff_for_oidc_session",
            return_value=binding,
        ) as consume,
    ):
        response = _client_with_token().post(
            "/api/v1.0/meetings/", HTTP_X_IDEMPOTENCY_KEY=IDEMPOTENCY_KEY
        )

    assert response.status_code == 201
    assert response.json()["room_ref"] == "room_0123456789abcdef"
    assert "host_handoff" not in response.json()
    consume.assert_called_once()
    session.request.assert_called_once_with(
        "POST",
        f"{PLATFORM}/api/meet/meetings",
        headers={
            "authorization": f"Bearer {TOKEN}",
            "x-idempotency-key": IDEMPOTENCY_KEY,
        },
        timeout=5,
        allow_redirects=False,
        stream=True,
    )
    assert response.headers["Cache-Control"] == "private, no-store"


@override_settings(**TEST_SETTINGS)
def test_create_meeting_requires_a_bounded_idempotency_key():
    """Reject missing creation idempotency before calling Platform."""

    with patch("core.mastrao_platform_facade.requests.Session") as session:
        response = _client_with_token().post("/api/v1.0/meetings/")

    assert response.status_code == 422
    session.assert_not_called()


@override_settings(**TEST_SETTINGS)
def test_history_forwards_only_one_valid_cursor():
    """Forward one cursor and reject ambiguous pagination requests."""

    upstream = _upstream(200, {"results": [], "next_cursor": None})
    session = _session_returning(upstream)
    with patch("core.mastrao_platform_facade.requests.Session", return_value=session):
        response = _client_with_token().get(
            "/api/v1.0/meetings/history/?cursor=cursor_123"
        )

    assert response.status_code == 200
    assert response.json() == {"results": [], "next_cursor": None}
    assert session.request.call_args.args[1] == (
        f"{PLATFORM}/api/meet/meetings/history/?cursor=cursor_123"
    )

    invalid = _client_with_token().get(
        "/api/v1.0/meetings/history/?cursor=one&cursor=two"
    )
    assert invalid.status_code == 422


@override_settings(**TEST_SETTINGS)
def test_missing_or_refused_access_token_fails_closed_and_clears_session():
    """Fail closed and discard a token rejected by Platform."""

    assert Client().get("/api/v1.0/meetings/history/").status_code == 401

    upstream = _upstream(401, {"message": "unauthorized"})
    session = _session_returning(upstream)
    client = _client_with_token()
    with patch("core.mastrao_platform_facade.requests.Session", return_value=session):
        response = client.get("/api/v1.0/meetings/history/")

    assert response.status_code == 401
    assert "oidc_access_token" not in client.session
    assert TOKEN not in response.content.decode()


@override_settings(**TEST_SETTINGS)
def test_detail_rejects_noncanonical_meeting_reference_before_network():
    """Reject malformed meeting references without contacting Platform."""

    with patch("core.mastrao_platform_facade.requests.Session") as session:
        response = _client_with_token().get("/api/v1.0/meetings/history/not-valid!/")

    assert response.status_code == 404
    session.assert_not_called()
