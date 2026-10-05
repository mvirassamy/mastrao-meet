"""Tests for the same-origin Platform meeting facade bridge."""

import json
from unittest.mock import MagicMock, patch

from django.conf import settings
from django.test import Client, RequestFactory, override_settings

import pytest

from core.mastrao_meeting_history import MAX_CREATION_BODY_BYTES, create_meeting
from core.mastrao_platform_facade import MAX_PLATFORM_RESPONSE_BYTES

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
            "/api/v1.0/meetings/",
            data=b"",
            content_type="application/json",
            HTTP_X_IDEMPOTENCY_KEY=IDEMPOTENCY_KEY,
        )

    assert response.status_code == 201
    assert response.json()["room_ref"] == "room_0123456789abcdef"
    assert "host_handoff" not in response.json()
    assert TOKEN not in response.content.decode()
    consume.assert_called_once()
    session.request.assert_called_once_with(
        "POST",
        f"{PLATFORM}/api/meet/meetings",
        headers={
            "authorization": f"Bearer {TOKEN}",
            "x-idempotency-key": IDEMPOTENCY_KEY,
        },
        timeout=(5, 20),
        allow_redirects=False,
        stream=True,
    )
    assert response.headers["Cache-Control"] == "private, no-store"


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"title": "Réunion d'équipe"},
        {
            "title": "Réunion d'équipe",
            "scheduled_start_at": 1_800_000_000,
            "scheduled_end_at": 1_800_003_600,
            "timezone": "Europe/Paris",
        },
    ],
)
@override_settings(**TEST_SETTINGS)
def test_create_meeting_forwards_exact_json_and_retains_schedule_metadata(payload):
    """Keep canonical metadata and consume credentials entirely server-side."""

    metadata = {
        "meeting_ref": "meeting_0123456789abcdef",
        "room_ref": "room_0123456789abcdef",
        "title": "Réunion d'équipe",
        "scheduled_start_at": 1_800_000_000,
        "scheduled_end_at": 1_800_003_600,
        "timezone": "Europe/Paris",
    }
    handoff = "handoff_0123456789abcdef"
    session = _session_returning(_upstream(201, {**metadata, "host_handoff": handoff}))
    binding = MagicMock()
    binding.room.slug = metadata["room_ref"]
    client = _client_with_token()
    with (
        patch("core.mastrao_platform_facade.requests.Session", return_value=session),
        patch(
            "core.mastrao_meeting_history.consume_host_handoff_for_oidc_session",
            return_value=binding,
        ) as consume,
    ):
        response = client.post(
            "/api/v1.0/meetings/",
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_X_IDEMPOTENCY_KEY=IDEMPOTENCY_KEY,
        )

    assert response.status_code == 201
    assert response.json() == metadata
    assert TOKEN not in response.content.decode()
    consume.assert_called_once()
    assert consume.call_args.args[1] == handoff
    session.request.assert_called_once_with(
        "POST",
        f"{PLATFORM}/api/meet/meetings",
        headers={
            "authorization": f"Bearer {TOKEN}",
            "x-idempotency-key": IDEMPOTENCY_KEY,
        },
        timeout=(5, 20),
        allow_redirects=False,
        stream=True,
        json=payload,
    )
    assert response.headers["Cache-Control"] == "private, no-store"


@pytest.mark.parametrize(
    "raw",
    [
        b"{",
        b"\xff",
        b"[]",
        b'"title"',
        b"null",
        b"0",
        b'{"title": NaN}',
        b'{"scheduled_start_at": Infinity}',
        b'{"host_handoff": "client-injected-handoff"}',
        b'{"recording_enabled": true}',
        b"[" * 1500 + b"]" * 1500,
        json.dumps({"title": "x" * MAX_CREATION_BODY_BYTES}).encode(),
    ],
)
@override_settings(**TEST_SETTINGS)
def test_create_meeting_rejects_invalid_envelope_before_platform(raw):
    """Refuse malformed, non-object, unknown or oversized browser payloads."""

    with (
        patch("core.mastrao_platform_facade.requests.Session") as session,
        patch(
            "core.mastrao_meeting_history.consume_host_handoff_for_oidc_session"
        ) as consume,
    ):
        response = _client_with_token().post(
            "/api/v1.0/meetings/",
            data=raw,
            content_type="application/json",
            HTTP_X_IDEMPOTENCY_KEY=IDEMPOTENCY_KEY,
        )

    assert response.status_code == 422
    session.assert_not_called()
    consume.assert_not_called()


@override_settings(**TEST_SETTINGS)
def test_create_meeting_rejects_non_json_body():
    """A nonempty form or text body cannot be silently treated as instant."""

    with patch("core.mastrao_platform_facade.requests.Session") as session:
        response = _client_with_token().post(
            "/api/v1.0/meetings/",
            data='{"title": "Réunion"}',
            content_type="text/plain",
            HTTP_X_IDEMPOTENCY_KEY=IDEMPOTENCY_KEY,
        )

    assert response.status_code == 422
    session.assert_not_called()


@override_settings(**TEST_SETTINGS)
def test_create_meeting_bounds_body_without_declared_length():
    """The read limit still rejects oversized input without Content-Length."""

    request = RequestFactory().post(
        "/api/v1.0/meetings/",
        data=json.dumps({"title": "x" * MAX_CREATION_BODY_BYTES}),
        content_type="application/json",
        HTTP_X_IDEMPOTENCY_KEY=IDEMPOTENCY_KEY,
    )
    request.META.pop("CONTENT_LENGTH")
    with patch("core.mastrao_platform_facade.requests.Session") as session:
        response = create_meeting(request)

    assert response.status_code == 422
    session.assert_not_called()


@pytest.mark.parametrize(
    "payload",
    [
        {"scheduled_start_at": 1_800_000_000},
        {
            "scheduled_start_at": 1_800_003_600,
            "scheduled_end_at": 1_800_000_000,
            "timezone": "Europe/Paris",
        },
    ],
)
@override_settings(**TEST_SETTINGS)
def test_create_meeting_preserves_platform_schedule_validation(payload):
    """Let Platform reject incomplete or reversed schedules at its boundary."""

    session = _session_returning(_upstream(422, {"message": "invalid schedule"}))
    with (
        patch("core.mastrao_platform_facade.requests.Session", return_value=session),
        patch(
            "core.mastrao_meeting_history.consume_host_handoff_for_oidc_session"
        ) as consume,
    ):
        response = _client_with_token().post(
            "/api/v1.0/meetings/",
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_X_IDEMPOTENCY_KEY=IDEMPOTENCY_KEY,
        )

    assert response.status_code == 422
    assert session.request.call_args.kwargs["json"] == payload
    consume.assert_not_called()


@override_settings(**TEST_SETTINGS)
def test_create_meeting_requires_a_bounded_idempotency_key():
    """Reject missing creation idempotency before calling Platform."""

    with patch("core.mastrao_platform_facade.requests.Session") as session:
        response = _client_with_token().post("/api/v1.0/meetings/")

    assert response.status_code == 422
    session.assert_not_called()


@pytest.mark.parametrize("cursor", [None, "opaque_123-456"])
@override_settings(**TEST_SETTINGS)
def test_day_list_forwards_exact_query_and_retains_canonical_metadata(cursor):
    """Return Platform's UTC metadata and states without minting credentials."""

    body = {
        "results": [
            {
                "meeting_ref": f"meeting_0123456789abcdef_{state}",
                "room_ref": f"room_0123456789abcdef_{state}",
                "title": "Réunion d'équipe",
                "created_at": 1_799_900_000,
                "scheduled_start_at": 1_800_000_000,
                "scheduled_end_at": 1_800_003_600,
                "timezone": "Europe/Paris",
                "state": state,
                "ended_at": 1_800_002_000 if state == "ended" else None,
            }
            for state in ("admitted", "ready", "cancelled", "ending", "ended")
        ],
        "next_cursor": "opaque_next-page",
    }
    session = _session_returning(_upstream(200, body))
    query = "day_start=1800000000&day_end=1800086400"
    if cursor is not None:
        query += f"&cursor={cursor}"
    with (
        patch("core.mastrao_platform_facade.requests.Session", return_value=session),
        patch(
            "core.mastrao_meeting_history.consume_host_handoff_for_oidc_session"
        ) as consume,
    ):
        response = _client_with_token().get(f"/api/v1.0/meetings/?{query}")

    assert response.status_code == 200
    assert response.json() == body
    assert TOKEN not in response.content.decode()
    assert "host_handoff" not in response.content.decode()
    assert response.headers["Cache-Control"] == "private, no-store"
    consume.assert_not_called()
    session.request.assert_called_once_with(
        "GET",
        f"{PLATFORM}/api/meet/meetings?{query}",
        headers={"authorization": f"Bearer {TOKEN}"},
        timeout=5,
        allow_redirects=False,
        stream=True,
    )
    assert session.trust_env is False


@pytest.mark.parametrize(
    "query",
    [
        "",
        "day_start=1800000000",
        "day_end=1800086400",
        "day_start=&day_end=1800086400",
        "day_start=1800000000&day_end=",
        "day_start=-1&day_end=1800086400",
        "day_start=1.5&day_end=1800086400",
        "day_start=1e9&day_end=1800086400",
        "day_start=NaN&day_end=1800086400",
        "day_start=1800000000&day_end=Infinity",
        "day_start=1800000000&day_end=9007199254740992",
        "day_start=1800000000&day_end=" + "9" * 5000,
        "day_start=1800000000&day_end=1800000000",
        "day_start=1800086400&day_end=1800000000",
        "day_start=1&day_start=2&day_end=1800086400",
        "day_start=1&day_end=2&day_end=3",
        "day_start=1&day_end=2&cursor=one&cursor=two",
        "day_start=1&day_end=2&timezone=Europe%2FParis",
        "day_start=1&day_end=2&cursor=",
        "day_start=1&day_end=2&cursor=bad%2Fcursor",
        "day_start=1&day_end=2&cursor=" + "x" * 513,
    ],
)
@override_settings(**TEST_SETTINGS)
def test_day_list_rejects_invalid_query_before_platform(query):
    """Refuse missing, malformed, duplicate, unknown or oversized parameters."""

    with patch("core.mastrao_platform_facade.requests.Session") as session:
        response = _client_with_token().get(f"/api/v1.0/meetings/?{query}")

    assert response.status_code == 422
    assert response.headers["Cache-Control"] == "private, no-store"
    session.assert_not_called()


@override_settings(**TEST_SETTINGS)
def test_day_list_requires_access_token_and_clears_platform_refusal():
    """Use the same authentication boundary as existing meeting history."""

    target = "/api/v1.0/meetings/?day_start=1800000000&day_end=1800086400"
    with patch("core.mastrao_platform_facade.requests.Session") as session:
        response = Client().get(target)
    assert response.status_code == 401
    assert response.headers["Cache-Control"] == "private, no-store"
    session.assert_not_called()

    client = _client_with_token()
    session = _session_returning(_upstream(401, {"message": "unauthorized"}))
    with patch("core.mastrao_platform_facade.requests.Session", return_value=session):
        response = client.get(target)

    assert response.status_code == 401
    assert "oidc_access_token" not in client.session
    assert TOKEN not in response.content.decode()


@pytest.mark.parametrize("declared", [True, False])
@override_settings(**TEST_SETTINGS)
def test_day_list_keeps_existing_platform_response_size_limit(declared):
    """Reject excessive declared or streamed response bytes and close the peer."""

    upstream = _upstream(200, {"results": [], "next_cursor": None})
    if declared:
        upstream.headers["content-length"] = str(MAX_PLATFORM_RESPONSE_BYTES + 1)
    else:
        upstream.headers = {}
        upstream.iter_content.return_value = [b" " * MAX_PLATFORM_RESPONSE_BYTES, b" "]
    session = _session_returning(upstream)
    with patch("core.mastrao_platform_facade.requests.Session", return_value=session):
        response = _client_with_token().get(
            "/api/v1.0/meetings/?day_start=1800000000&day_end=1800086400"
        )

    assert response.status_code == 503
    assert TOKEN not in response.content.decode()
    upstream.close.assert_called_once()


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
