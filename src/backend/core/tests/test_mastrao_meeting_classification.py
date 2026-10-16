"""Provider-free HTTP tests for classification and matter commands."""

import json
from unittest.mock import MagicMock, patch

from django.conf import settings as django_settings
from django.middleware.csrf import get_token
from django.test import Client, RequestFactory

import pytest
import requests

from core.mastrao_meeting_classification import (
    MAX_COMMAND_BYTES,
    meeting_classification,
)
from core.mastrao_platform_facade import MAX_PLATFORM_RESPONSE_BYTES

PLATFORM = "https://platform.example.com"
TOKEN = "test-oidc-session-token-123456"
MEETING = "meeting_0123456789abcdef"
MATTER = "matter_0123456789abcdef"
CLASSIFICATION_URL = f"/api/v1.0/meetings/history/{MEETING}/classification/"
MATTERS_URL = "/api/v1.0/matters/"
COMMAND = {
    "version": 1,
    "meeting_ref": MEETING,
    "matter_ref": MATTER,
    "expected_classification_version": 0,
    "command_id": "command_0123456789abcdef",
}
CLASSIFICATION = {
    "version": 1,
    "meeting_ref": MEETING,
    "matter_ref": None,
    "classification_version": 0,
    "can_classify": True,
}
CREATE_MATTER = {
    "version": 1,
    "command_id": "64e818f0-58a1-4bfd-87db-a3bab25d390a",
    "display_label": "Dossier équipe",
}
MATTER_RESULT = {
    "version": 1,
    "matter_ref": MATTER,
    "display_label": "Dossier équipe",
    "metadata_version": 1,
}


@pytest.fixture(autouse=True)
def bridge_settings(settings):
    """Use explicit local settings without database, Redis or secret files."""

    settings.SECRET_KEY = "classification-test-key"
    settings.ALLOWED_HOSTS = ["testserver"]
    settings.SESSION_ENGINE = "django.contrib.sessions.backends.signed_cookies"
    settings.MASTRAO_PLATFORM_API_BASE_URL = PLATFORM


def _client(*, token=TOKEN, csrf=False):
    client = Client(enforce_csrf_checks=csrf)
    session = client.session
    if token is not None:
        session["oidc_access_token"] = token
    # Visio participation must never substitute for the OIDC bearer.
    session["mastrao_guest_grant_ref"] = "guest_0123456789abcdef"
    session.save()
    client.cookies[django_settings.SESSION_COOKIE_NAME] = session.session_key
    return client


def _upstream(status=200, body=None):
    response = MagicMock()
    payload = json.dumps(CLASSIFICATION if body is None else body).encode()
    response.status_code = status
    response.headers = {"content-length": str(len(payload))}
    response.iter_content.return_value = [payload]
    session = MagicMock()
    session.__enter__.return_value = session
    session.request.return_value = response
    return session


def _post(client, url, payload):
    return client.post(url, json.dumps(payload), content_type="application/json")


def test_classification_uses_only_server_oidc_token():
    """Forward the session bearer with the existing bounded facade transport."""

    session = _upstream()
    with patch("core.mastrao_platform_facade.requests.Session", return_value=session):
        response = _client().get(CLASSIFICATION_URL)
    assert response.status_code == 200
    assert response.json() == CLASSIFICATION
    session.request.assert_called_once_with(
        "GET",
        f"{PLATFORM}/api/meet/meetings/history/{MEETING}/classification",
        headers={"authorization": f"Bearer {TOKEN}"},
        timeout=5,
        allow_redirects=False,
        stream=True,
    )
    assert session.trust_env is False
    assert TOKEN not in response.content.decode()
    assert response.headers["Cache-Control"] == "private, no-store"


@pytest.mark.parametrize("url", [CLASSIFICATION_URL, MATTERS_URL])
def test_guest_or_browser_bearer_cannot_replace_oidc_session(url):
    """A visio grant or browser header never supplies business authentication."""

    with patch("core.mastrao_platform_facade.requests.Session") as upstream:
        response = _client(token=None).get(url, HTTP_AUTHORIZATION=f"Bearer {TOKEN}")
    assert response.status_code == 401
    upstream.assert_not_called()


def test_classification_preserves_stable_command_and_platform_replay():
    """Retry the same durable command without generating a new identity."""

    result = {**CLASSIFICATION, "matter_ref": MATTER, "classification_version": 1}
    session = _upstream(body={**result, "idempotent_replay": False})
    client = _client()
    with patch("core.mastrao_platform_facade.requests.Session", return_value=session):
        first = _post(client, CLASSIFICATION_URL, COMMAND)
        session.request.return_value = _upstream(
            body={**result, "idempotent_replay": True}
        ).request.return_value
        replay = _post(client, CLASSIFICATION_URL, COMMAND)
    assert first.status_code == replay.status_code == 200
    assert replay.json()["idempotent_replay"] is True
    assert session.request.call_count == 2
    assert all(
        call.kwargs["json"] == COMMAND for call in session.request.call_args_list
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"organization_external_id": "other"},
        {"author_ref": "other"},
        {"participant_grant": "guest"},
        {"version": True},
        {"meeting_ref": "meeting_other0123456789"},
        {"matter_ref": None},
        {"expected_classification_version": True},
        {"expected_classification_version": -1},
        {"expected_classification_version": 9_007_199_254_740_992},
        {"command_id": "short"},
    ],
)
def test_invalid_or_identity_injected_classification_never_reaches_platform(changes):
    """Reject uncontracted fields and invalid revision or resource bindings."""

    with patch("core.mastrao_platform_facade.requests.Session") as upstream:
        response = _post(_client(), CLASSIFICATION_URL, {**COMMAND, **changes})
    assert response.status_code == 422
    upstream.assert_not_called()


@pytest.mark.parametrize(
    "raw",
    [
        b"{",
        b"\xff",
        b"null",
        b"[]",
        b"[" * 1500 + b"]" * 1500,
        b" " * (MAX_COMMAND_BYTES + 1),
        json.dumps(COMMAND)
        .replace('"version": 1', '"version": 1, "version": 1')
        .encode(),
    ],
)
def test_malformed_or_oversized_commands_are_bounded(raw):
    """Refuse malformed and oversized JSON before any upstream call."""

    with patch("core.mastrao_platform_facade.requests.Session") as upstream:
        response = _client().post(
            CLASSIFICATION_URL, raw, content_type="application/json"
        )
    assert response.status_code == 422
    upstream.assert_not_called()


def test_body_limit_applies_without_declared_length():
    """Enforce the stream budget independently from the length header."""

    request = RequestFactory().post(
        CLASSIFICATION_URL,
        b" " * (MAX_COMMAND_BYTES + 1),
        content_type="application/json",
    )
    request.META.pop("CONTENT_LENGTH")
    request._dont_enforce_csrf_checks = True  # pylint: disable=protected-access
    with patch("core.mastrao_platform_facade.requests.Session") as upstream:
        response = meeting_classification(request, MEETING)
    assert response.status_code == 422
    upstream.assert_not_called()


@pytest.mark.parametrize(
    "url,payload", [(CLASSIFICATION_URL, COMMAND), (MATTERS_URL, CREATE_MATTER)]
)
def test_mutations_require_csrf_and_accept_a_valid_same_origin_token(url, payload):
    """Protect both mutation routes with the real Django CSRF middleware."""

    client = _client(csrf=True)
    with patch("core.mastrao_platform_facade.requests.Session") as upstream:
        assert _post(client, url, payload).status_code == 403
    upstream.assert_not_called()
    token_request = RequestFactory().get("/")
    csrf_token = get_token(token_request)
    client.cookies[django_settings.CSRF_COOKIE_NAME] = token_request.META["CSRF_COOKIE"]
    result = {**CLASSIFICATION, "matter_ref": MATTER, "idempotent_replay": False}
    if url == MATTERS_URL:
        result = {**MATTER_RESULT, "idempotent_replay": False}
    with patch(
        "core.mastrao_platform_facade.requests.Session",
        return_value=_upstream(body=result),
    ):
        response = client.post(
            url,
            json.dumps(payload),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=csrf_token,
        )
    assert response.status_code == 200


@pytest.mark.parametrize("status", [400, 401, 403, 404, 409, 422, 429, 500, 302])
def test_real_error_status_is_preserved_without_upstream_details(status):
    """Keep access and conflict codes while discarding private upstream details."""

    client = _client()
    with patch(
        "core.mastrao_platform_facade.requests.Session",
        return_value=_upstream(
            status, {"private_url": "https://private.example", "token": TOKEN}
        ),
    ):
        response = _post(client, CLASSIFICATION_URL, COMMAND)
    assert response.status_code == (503 if status in {500, 302} else status)
    assert TOKEN not in response.content.decode()
    assert "private.example" not in response.content.decode()
    if status == 401:
        assert "oidc_access_token" not in client.session


def test_platform_timeout_has_no_automatic_retry():
    """A transport failure cannot silently issue a second command."""

    session = _upstream()
    session.request.side_effect = requests.Timeout()
    with patch("core.mastrao_platform_facade.requests.Session", return_value=session):
        response = _post(_client(), CLASSIFICATION_URL, COMMAND)
    assert response.status_code == 503
    session.request.assert_called_once()


@pytest.mark.parametrize("declared", [True, False])
def test_response_budget_still_closes_excessive_upstream_responses(declared):
    """Reuse both the declared and streamed response budgets."""

    session = _upstream()
    upstream = session.request.return_value
    upstream.headers = {}
    if declared:
        upstream.headers["content-length"] = str(MAX_PLATFORM_RESPONSE_BYTES + 1)
    else:
        upstream.iter_content.return_value = [b" " * MAX_PLATFORM_RESPONSE_BYTES, b" "]
    with patch("core.mastrao_platform_facade.requests.Session", return_value=session):
        response = _client().get(CLASSIFICATION_URL)
    assert response.status_code == 503
    upstream.close.assert_called_once()


@pytest.mark.parametrize(
    "changes",
    [
        {"meeting_ref": "meeting_other0123456789"},
        {"private_url": "hidden"},
        {"can_classify": 1},
    ],
)
def test_invalid_classification_response_is_never_forwarded(changes):
    """Refuse unbound or uncontracted upstream classification responses."""

    with patch(
        "core.mastrao_platform_facade.requests.Session",
        return_value=_upstream(body={**CLASSIFICATION, **changes}),
    ):
        response = _client().get(CLASSIFICATION_URL)
    assert response.status_code == 503
    assert "hidden" not in response.content.decode()


def test_matter_list_forwards_bounded_search_and_permissions_filter():
    """Use the exact Platform matter query with the authenticated actor."""

    body = {"version": 1, "items": [MATTER_RESULT], "next_cursor": None}
    session = _upstream(body=body)
    with patch("core.mastrao_platform_facade.requests.Session", return_value=session):
        response = _client().get(
            MATTERS_URL, {"query": "équipe", "limit": "20", "writable_only": "true"}
        )
    assert response.status_code == 200
    assert response.json() == body
    assert (
        session.request.call_args.args[1]
        == f"{PLATFORM}/api/meet/matters?query=%C3%A9quipe&limit=20&writable_only=true"
    )


@pytest.mark.parametrize(
    "query",
    [
        "organization=other",
        "limit=51",
        "limit=-1",
        "query=" + "x" * 101,
        "cursor=bad%2Fcursor",
        "query=a&query=b",
        "writable_only=1",
    ],
)
def test_matter_list_refuses_uncontracted_or_ambiguous_query(query):
    """Reject unknown and ambiguous search fields at the browser boundary."""

    with patch("core.mastrao_platform_facade.requests.Session") as upstream:
        response = _client().get(f"{MATTERS_URL}?{query}")
    assert response.status_code == 422
    upstream.assert_not_called()


@pytest.mark.parametrize("replay,status", [(False, 201), (True, 200)])
def test_create_matter_retains_command_and_replay_status(replay, status):
    """Preserve the durable creation command separately from classification."""

    body = {**MATTER_RESULT, "idempotent_replay": replay}
    session = _upstream(status, body)
    with patch("core.mastrao_platform_facade.requests.Session", return_value=session):
        response = _post(_client(), MATTERS_URL, CREATE_MATTER)
    assert response.status_code == status
    assert response.json() == body
    assert session.request.call_args.kwargs["json"] == CREATE_MATTER
    assert session.request.call_args.args[1] == f"{PLATFORM}/api/meet/matters"


@pytest.mark.parametrize(
    "changes",
    [
        {"command_id": "not-uuid"},
        {"display_label": " Label "},
        {"display_label": "bad\nlabel"},
        {"display_label": "\ud800"},
        {"organization_ref": MATTER},
    ],
)
def test_create_matter_refuses_invalid_or_identity_injected_command(changes):
    """Reject malformed labels, command identities and tenant injection."""

    with patch("core.mastrao_platform_facade.requests.Session") as upstream:
        response = _post(_client(), MATTERS_URL, {**CREATE_MATTER, **changes})
    assert response.status_code == 422
    upstream.assert_not_called()
