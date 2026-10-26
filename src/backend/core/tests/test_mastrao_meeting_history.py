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
        {"invitee_emails": []},
        {"title": "Réunion d'équipe"},
        {
            "title": "Réunion d'équipe",
            "scheduled_start_at": 1_800_000_000,
            "scheduled_end_at": 1_800_003_600,
            "timezone": "Europe/Paris",
        },
        {
            "scheduled_start_at": 1_800_000_000,
            "scheduled_end_at": 1_800_003_600,
            "timezone": "Europe/Paris",
            "invitee_emails": ["alice@example.com", "bob@example.com"],
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


@pytest.mark.parametrize(
    "schedule",
    [
        {},
        {"title": "Invited meeting"},
        {"scheduled_start_at": 1_800_000_000},
        {"scheduled_start_at": 1_800_000_000, "scheduled_end_at": 1_800_003_600},
        {"scheduled_start_at": 1_800_000_000, "timezone": "Europe/Paris"},
        {"scheduled_end_at": 1_800_003_600, "timezone": "Europe/Paris"},
        {
            "scheduled_start_at": 1_800_000_000,
            "scheduled_end_at": 1_800_003_600,
            "timezone": None,
        },
    ],
)
@override_settings(**TEST_SETTINGS)
def test_invitees_require_complete_schedule_before_any_external_side_effect(schedule):
    """Invalid invitation requests cannot create a meeting or consume a delivery claim."""
    with (
        patch("core.mastrao_meeting_history.request_platform") as platform,
        patch(
            "core.mastrao_meeting_history.consume_host_handoff_for_oidc_session"
        ) as consume,
        patch("core.mastrao_meeting_history.deliver_meeting_invitations") as deliver,
    ):
        platform.return_value = (
            {
                "meeting_ref": "meeting_0123456789abcdef",
                "room_ref": "room_0123456789abcdef",
                "host_handoff": "handoff_0123456789abcdef",
            },
            201,
        )
        consume.return_value.room.slug = "room_0123456789abcdef"
        response = _client_with_token().post(
            "/api/v1.0/meetings/",
            data=json.dumps({**schedule, "invitee_emails": ["guest@example.com"]}),
            content_type="application/json",
            HTTP_X_IDEMPOTENCY_KEY=IDEMPOTENCY_KEY,
        )
    assert response.status_code == 422
    platform.assert_not_called()
    consume.assert_not_called()
    deliver.assert_not_called()


NATIVE_MEETING_REF = "meeting_native0123456789"
NATIVE_DIGEST = "a" * 64
NATIVE_TARGET = (
    f"/api/v1.0/meetings/history/{NATIVE_MEETING_REF}/native-transcript/"
)


@override_settings(**TEST_SETTINGS)
def test_native_transcript_forwards_exact_snapshot_query_and_retains_text():
    """Use one authenticated GET, with no cache, ASR call or text rewriting."""
    epoch = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    body = {
        "version": 1,
        "meeting_ref": NATIVE_MEETING_REF,
        "snapshot_digest": NATIVE_DIGEST,
        "coverage": "partial_sources",
        "ordering": "independent_epoch_groups",
        "native_coverage": {
            "version": 1,
            "meeting_ref": NATIVE_MEETING_REF,
            "snapshot_digest": NATIVE_DIGEST,
            "epochs": [{"epoch_ref": epoch, "state": "available"}],
        },
        "segments": [
            {
                "id": f"{epoch}_s{index}",
                "start_ms": index,
                "speaker": "Camille",
                "text": "  De\u0301cision : ne pas payer 250 €.\n",
                "source_epoch": {
                    "epoch_ref": epoch,
                    "time_basis": "source_relative_ms",
                },
            }
            for index in range(1001)
        ],
        "retention_expires_at": 2_000_000_000,
        "truncated": False,
    }
    upstream = _upstream(200, body)
    session = _session_returning(upstream)
    with patch("core.mastrao_platform_facade.requests.Session", return_value=session):
        response = _client_with_token().get(
            f"{NATIVE_TARGET}?snapshot_digest={NATIVE_DIGEST}"
        )
    assert response.status_code == 200
    assert response.json() == body
    assert response.headers["Cache-Control"] == "private, no-store"
    assert TOKEN not in response.content.decode()
    session.request.assert_called_once_with(
        "GET",
        f"{PLATFORM}/api/meet/meetings/history/{NATIVE_MEETING_REF}/native-transcript/"
        f"?snapshot_digest={NATIVE_DIGEST}",
        headers={"authorization": f"Bearer {TOKEN}"},
        timeout=5,
        allow_redirects=False,
        stream=True,
    )
    assert session.trust_env is False
    upstream.close.assert_called_once()


@pytest.mark.parametrize(
    "query",
    [
        "",
        "snapshot_digest=",
        "snapshot_digest=" + "a" * 63,
        "snapshot_digest=" + "a" * 65,
        "snapshot_digest=" + "A" * 64,
        "snapshot_digest=" + "g" * 64,
        "snapshot_digest=" + "a" * 64 + "%0A",
        "snapshot_digest=" + "a" * 64 + "%20",
        f"snapshot_digest={NATIVE_DIGEST}&snapshot_digest={NATIVE_DIGEST}",
        f"snapshot_digest={NATIVE_DIGEST}&snapshot_digest=" + "b" * 64,
        f"snapshot_digest={NATIVE_DIGEST}&cursor=next",
        f"snapshot_digest={NATIVE_DIGEST}&refresh=true",
    ],
)
@override_settings(**TEST_SETTINGS)
def test_native_transcript_rejects_ambiguous_or_invalid_query_before_network(query):
    """Accept exactly one lowercase SHA-256 and no extra parameters."""
    with patch("core.mastrao_platform_facade.requests.Session") as session:
        response = _client_with_token().get(f"{NATIVE_TARGET}?{query}")
    assert response.status_code == 422
    assert response.headers["Cache-Control"] == "private, no-store"
    session.assert_not_called()


@override_settings(**TEST_SETTINGS)
def test_native_transcript_rejects_missing_auth_and_invalid_meeting_before_network():
    """Keep the existing session and canonical path checks ahead of transport."""
    with patch("core.mastrao_platform_facade.requests.Session") as session:
        missing = Client().get(f"{NATIVE_TARGET}?snapshot_digest={NATIVE_DIGEST}")
        invalid = _client_with_token().get(
            "/api/v1.0/meetings/history/not-valid!/native-transcript/"
            f"?snapshot_digest={NATIVE_DIGEST}"
        )
    assert missing.status_code == 401
    assert invalid.status_code == 404
    session.assert_not_called()


@pytest.mark.parametrize("status", [401, 403, 404, 409, 410, 422])
@override_settings(**TEST_SETTINGS)
def test_native_transcript_preserves_access_and_snapshot_refusals_without_body_read(status):
    """No-store refusals remain opaque and never forward an upstream error body."""
    upstream = _upstream(status, {"message": "private upstream error"})
    session = _session_returning(upstream)
    client = _client_with_token()
    with patch("core.mastrao_platform_facade.requests.Session", return_value=session):
        response = client.get(f"{NATIVE_TARGET}?snapshot_digest={NATIVE_DIGEST}")
    assert response.status_code == status
    assert response.headers["Cache-Control"] == "private, no-store"
    assert TOKEN not in response.content.decode()
    assert "private upstream error" not in response.content.decode()
    if status == 401:
        assert "oidc_access_token" not in client.session
    upstream.iter_content.assert_not_called()
    upstream.close.assert_called_once()


@pytest.mark.parametrize("declared", [True, False])
@override_settings(**TEST_SETTINGS)
def test_native_transcript_retains_existing_two_mib_response_limit(declared):
    """Refuse declared and streamed overflow without increasing shared limits."""
    assert MAX_PLATFORM_RESPONSE_BYTES == 2 * 1024 * 1024
    upstream = _upstream(200, {})
    if declared:
        upstream.headers["content-length"] = str(MAX_PLATFORM_RESPONSE_BYTES + 1)
    else:
        upstream.headers = {}
        upstream.iter_content.return_value = [b" " * MAX_PLATFORM_RESPONSE_BYTES, b" "]
    session = _session_returning(upstream)
    with patch("core.mastrao_platform_facade.requests.Session", return_value=session):
        response = _client_with_token().get(
            f"{NATIVE_TARGET}?snapshot_digest={NATIVE_DIGEST}"
        )
    assert response.status_code == 503
    assert response.headers["Cache-Control"] == "private, no-store"
    upstream.close.assert_called_once()


@override_settings(**TEST_SETTINGS)
def test_native_transcript_accepts_exact_existing_transport_limit():
    """The transport's exact 2 MiB boundary remains inclusive."""
    body = {"transport_fixture": ""}
    overhead = len(json.dumps(body).encode())
    body["transport_fixture"] = "x" * (MAX_PLATFORM_RESPONSE_BYTES - overhead)
    upstream = _upstream(200, body)
    session = _session_returning(upstream)
    with patch("core.mastrao_platform_facade.requests.Session", return_value=session):
        response = _client_with_token().get(
            f"{NATIVE_TARGET}?snapshot_digest={NATIVE_DIGEST}"
        )
    assert response.status_code == 200
    assert response.json() == body


@override_settings(**TEST_SETTINGS)
def test_native_transcript_rejects_mutating_methods():
    """Consultation is GET-only, even with a well-formed digest."""
    with patch("core.mastrao_platform_facade.requests.Session") as session:
        response = _client_with_token().post(
            f"{NATIVE_TARGET}?snapshot_digest={NATIVE_DIGEST}"
        )
    assert response.status_code == 405
    session.assert_not_called()


LIVE_TARGET = f"/api/v1.0/meetings/history/{NATIVE_MEETING_REF}/live-transcript/"
LIVE_QUERY = f"source_heads_digest={NATIVE_DIGEST}"


@override_settings(**TEST_SETTINGS)
def test_live_transcript_forwards_exact_heads_and_unicode_without_reinterpreting_content():
    """Transport all retained finals and markers once, using the session token."""
    source = "source_live0123456789"
    leg = "leg_live0123456789"
    events = [
        {
            "version": 2,
            "kind": "final",
            "source_ref": source,
            "leg_ref": leg,
            "sequence": index + 1,
            "item_ref": f"item-{index}",
            "revision": 2,
            "text": "  De\u0301cision : ne pas payer 250 €.\n",
            "language": None,
            "timing": {"state": "unavailable"},
        }
        for index in range(1001)
    ]
    events.append(
        {
            "version": 2,
            "kind": "gap",
            "source_ref": source,
            "leg_ref": leg,
            "sequence": 1002,
            "reason": "connection_lost",
        }
    )
    body = {
        "version": 1,
        "meeting_ref": NATIVE_MEETING_REF,
        "matter_ref": "matter_live0123456789",
        "source_heads_digest": NATIVE_DIGEST,
        "snapshot_digest": "b" * 64,
        "coverage": "partial_sources",
        "meeting_coverage": "unverified",
        "ordering": "independent_source_groups",
        "retention_expires_at": 2_000_000_000,
        "truncated": False,
        "sources": [
            {
                "source_ref": source,
                "participant_kind": "host",
                "participant_ref": "participant_live0123456789",
                "state": "collecting",
                "incomplete": True,
                "confirmed_sequence": 1002,
                "events": events,
            }
        ],
    }
    upstream = _upstream(200, body)
    session = _session_returning(upstream)
    with patch("core.mastrao_platform_facade.requests.Session", return_value=session):
        response = _client_with_token().get(f"{LIVE_TARGET}?{LIVE_QUERY}")
    assert response.status_code == 200
    assert response.json() == body
    assert response.headers["Cache-Control"] == "private, no-store"
    session.request.assert_called_once_with(
        "GET",
        f"{PLATFORM}/api/meet/meetings/history/{NATIVE_MEETING_REF}/live-transcript/"
        f"?{LIVE_QUERY}",
        headers={"authorization": f"Bearer {TOKEN}"},
        timeout=(5, 45),
        allow_redirects=False,
        stream=True,
    )
    assert session.trust_env is False
    upstream.close.assert_called_once()


@pytest.mark.parametrize(
    "query",
    [
        "",
        "source_heads_digest=",
        "source_heads_digest=" + "a" * 63,
        "source_heads_digest=" + "a" * 65,
        "source_heads_digest=" + "A" * 64,
        "source_heads_digest=" + "g" * 64,
        f"{LIVE_QUERY}%0A",
        f"{LIVE_QUERY}%20",
        f"{LIVE_QUERY}&{LIVE_QUERY}",
        f"{LIVE_QUERY}&source_heads_digest=" + "b" * 64,
        f"{LIVE_QUERY}&cursor=next",
        f"{LIVE_QUERY}&snapshot_digest={NATIVE_DIGEST}",
        f"{LIVE_QUERY}&participant_ref=forged",
        f"{LIVE_QUERY}&refresh=true",
    ],
)
@override_settings(**TEST_SETTINGS)
def test_live_transcript_rejects_ambiguous_query_before_transport(query):
    """Participant input supplies only the expected heads digest."""
    with patch("core.mastrao_platform_facade.requests.Session") as session:
        response = _client_with_token().get(f"{LIVE_TARGET}?{query}")
    assert response.status_code == 422
    assert response.headers["Cache-Control"] == "private, no-store"
    session.assert_not_called()


@override_settings(**TEST_SETTINGS)
def test_live_transcript_rejects_missing_session_invalid_meeting_and_mutations():
    """Existing auth, canonical path and GET restrictions precede network access."""
    with patch("core.mastrao_platform_facade.requests.Session") as session:
        missing = Client().get(f"{LIVE_TARGET}?{LIVE_QUERY}")
        invalid = _client_with_token().get(
            f"/api/v1.0/meetings/history/not-valid!/live-transcript/?{LIVE_QUERY}"
        )
        mutation = _client_with_token().post(f"{LIVE_TARGET}?{LIVE_QUERY}")
    assert missing.status_code == 401
    assert invalid.status_code == 404
    assert mutation.status_code == 405
    session.assert_not_called()


@pytest.mark.parametrize("status", [401, 403, 404, 409, 410, 422, 503])
@override_settings(**TEST_SETTINGS)
def test_live_transcript_preserves_opaque_refusals_without_reading_private_body(status):
    """Never read or expose upstream text on access or snapshot refusal."""
    upstream = _upstream(status, {"message": "private live text"})
    session = _session_returning(upstream)
    with patch("core.mastrao_platform_facade.requests.Session", return_value=session):
        response = _client_with_token().get(f"{LIVE_TARGET}?{LIVE_QUERY}")
    assert response.status_code == status
    assert response.headers["Cache-Control"] == "private, no-store"
    assert "private live text" not in response.content.decode()
    assert TOKEN not in response.content.decode()
    upstream.iter_content.assert_not_called()
    upstream.close.assert_called_once()


@pytest.mark.parametrize("declared", [True, False])
@override_settings(**TEST_SETTINGS)
def test_live_transcript_rejects_declared_and_streamed_two_mib_overflow(declared):
    """Keep the shared transport bound unchanged for live consultation."""
    upstream = _upstream(200, {})
    if declared:
        upstream.headers["content-length"] = str(MAX_PLATFORM_RESPONSE_BYTES + 1)
    else:
        upstream.headers = {}
        upstream.iter_content.return_value = [b" " * MAX_PLATFORM_RESPONSE_BYTES, b" "]
    session = _session_returning(upstream)
    with patch("core.mastrao_platform_facade.requests.Session", return_value=session):
        response = _client_with_token().get(f"{LIVE_TARGET}?{LIVE_QUERY}")
    assert response.status_code == 503
    assert response.headers["Cache-Control"] == "private, no-store"
    upstream.close.assert_called_once()
