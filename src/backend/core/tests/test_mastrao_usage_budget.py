"""The settings link delegates organization authority to the existing OIDC facade."""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import RequestFactory, override_settings
from django.urls import resolve

import pytest
import requests

PLATFORM = "https://platform.example.test"
TOKEN = "synthetic-oidc-access-token"
DESCRIPTOR = {
    "organization_slug": "example-org",
    "usage_budget_url": f"{PLATFORM}/orgs/example-org/settings/usage-budget",
}


def _request(*, authenticated=True, token=TOKEN, method="get", query=""):
    request = getattr(RequestFactory(), method)(f"/api/v1.0/usage-budget/{query}")
    request.user = SimpleNamespace(is_authenticated=authenticated)
    request.session = {}
    if token is not None:
        request.session["oidc_access_token"] = token
    return request


def _call(request):
    return resolve("/api/v1.0/usage-budget/").func(request)


@pytest.fixture(name="platform_session")
def fixture_platform_session():
    """Exercise the real bounded transport without contacting Platform."""
    response = MagicMock()
    response.status_code = 200
    response.headers = {}
    response.iter_content.return_value = [json.dumps(DESCRIPTOR).encode()]
    session = MagicMock()
    session.__enter__.return_value = session
    session.request.return_value = response
    with (
        override_settings(MASTRAO_PLATFORM_API_BASE_URL=PLATFORM),
        patch("core.mastrao_platform_facade.requests.Session", return_value=session),
    ):
        yield session, response


def test_descriptor_preserves_explicit_organization_and_private_cache_policy(
    platform_session,
):
    """Only the validated link descriptor reaches the browser, never upstream extras."""
    session, upstream = platform_session
    upstream.iter_content.return_value = [
        json.dumps({**DESCRIPTOR, "private_upstream_field": TOKEN}).encode()
    ]
    response = _call(_request())

    assert response.status_code == 200
    assert json.loads(response.content) == DESCRIPTOR
    assert response.headers["Cache-Control"] == "private, no-store"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert TOKEN.encode() not in response.content
    session.request.assert_called_once_with(
        "GET",
        f"{PLATFORM}/api/meet/usage-budget",
        headers={"authorization": f"Bearer {TOKEN}"},
        timeout=5,
        allow_redirects=False,
        stream=True,
    )
    upstream.close.assert_called_once_with()


@pytest.mark.parametrize(
    "authenticated,token", [(False, TOKEN), (True, None), (True, "bad")]
)
def test_guests_and_missing_oidc_credentials_never_call_platform(
    platform_session, authenticated, token
):
    """A local session or room role grants no usage-budget authority."""
    session, _ = platform_session
    response = _call(_request(authenticated=authenticated, token=token))
    assert response.status_code == 401
    assert response.headers["Cache-Control"] == "private, no-store"
    session.request.assert_not_called()


@pytest.mark.parametrize(
    "upstream_status,status",
    [(404, 404), (401, 401), (429, 503), (503, 503), (302, 503)],
)
def test_role_refusal_stays_distinct_from_unavailability(
    platform_session, upstream_status, status
):
    """Platform's concealed member refusal is not presented as an upstream outage."""
    session, upstream = platform_session
    upstream.status_code = upstream_status
    response = _call(_request())
    assert response.status_code == status
    assert response.headers["Cache-Control"] == "private, no-store"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert "usage_budget_url" not in json.loads(response.content)
    session.request.assert_called_once()


@pytest.mark.parametrize(
    "slug,status",
    [
        ("a", 200),
        ("a" * 100, 200),
        ("a" * 101, 503),
        ("Example-org", 503),
        ("example_org", 503),
        ("-example", 503),
        ("example-", 503),
        ("example--org", 503),
    ],
)
def test_slug_matches_platform_contract(platform_session, slug, status):
    """Concordant URLs still require Platform's lowercase, hyphenated, 100-character slug."""
    _, upstream = platform_session
    descriptor = {
        "organization_slug": slug,
        "usage_budget_url": f"{PLATFORM}/orgs/{slug}/settings/usage-budget",
    }
    upstream.iter_content.return_value = [json.dumps(descriptor).encode()]
    response = _call(_request())
    assert response.status_code == status
    if status == 200:
        assert json.loads(response.content) == descriptor


@pytest.mark.parametrize("body", [None, [], "not-an-object", 42, True])
def test_transport_rejects_non_object_json_before_descriptor_access(
    platform_session, body
):
    """The real request_platform boundary rejects non-dicts before the view calls get."""
    _, upstream = platform_session
    upstream.iter_content.return_value = [json.dumps(body).encode()]
    response = _call(_request())
    assert response.status_code == 503
    assert response.headers["Cache-Control"] == "private, no-store"
    assert "usage_budget_url" not in json.loads(response.content)


@pytest.mark.parametrize(
    "changes",
    [
        {"organization_slug": "other-org"},
        {"organization_slug": "../example-org"},
        {"organization_slug": ""},
        {"organization_slug": None},
        {"usage_budget_url": None},
        {
            "usage_budget_url": "https://attacker.test/orgs/example-org/settings/usage-budget"
        },
        {
            "usage_budget_url": "http://platform.example.test"
            "/orgs/example-org/settings/usage-budget"
        },
        {"usage_budget_url": f"{PLATFORM}:444/orgs/example-org/settings/usage-budget"},
        {
            "usage_budget_url": "https://user@platform.example.test"
            "/orgs/example-org/settings/usage-budget"
        },
        {"usage_budget_url": "/orgs/example-org/settings/usage-budget"},
        {
            "usage_budget_url": f"{DESCRIPTOR['usage_budget_url']}?organization=other-org"
        },
        {"usage_budget_url": f"{DESCRIPTOR['usage_budget_url']}#other-org"},
        {"usage_budget_url": f"{PLATFORM}/orgs/example-org/settings/billing"},
    ],
)
def test_rejects_foreign_origins_and_noncanonical_organization_links(
    platform_session, changes
):
    """An upstream success cannot turn the settings link into an arbitrary redirect."""
    _, upstream = platform_session
    upstream.iter_content.return_value = [
        json.dumps({**DESCRIPTOR, **changes}).encode()
    ]
    response = _call(_request())
    assert response.status_code == 503
    assert response.headers["Cache-Control"] == "private, no-store"
    assert "usage_budget_url" not in json.loads(response.content)


def test_transport_timeout_is_unavailable(platform_session):
    """A timeout is an outage, not a hidden role refusal."""
    session, _ = platform_session
    session.request.side_effect = requests.Timeout()
    assert _call(_request()).status_code == 503


def test_no_client_organization_override_or_write(platform_session):
    """The authenticated Platform context chooses the organization and this route only reads."""
    session, _ = platform_session
    assert _call(_request(query="?organization_slug=other-org")).status_code == 422
    assert _call(_request(method="post")).status_code == 405
    session.request.assert_not_called()


@override_settings(MASTRAO_PLATFORM_API_BASE_URL="http://localhost:3000/")
def test_accepts_the_existing_configured_loopback_origin():
    """Local development uses the same canonical descriptor contract."""
    descriptor = {
        "organization_slug": "local-org",
        "usage_budget_url": "http://localhost:3000/orgs/local-org/settings/usage-budget",
    }
    with patch(
        "core.mastrao_usage_budget.request_platform", return_value=(descriptor, 200)
    ):
        assert json.loads(_call(_request()).content) == descriptor
