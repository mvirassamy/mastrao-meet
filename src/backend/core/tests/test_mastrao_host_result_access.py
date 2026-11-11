"""Visitor result page relay: one opaque refusal, no credential retention."""

import json
from unittest import mock

from django.core.cache import cache
from django.test import Client, override_settings
from django.urls import reverse

import pytest

from core.mastrao_host_result_access import HostResultRefused

ORIGIN = "https://meet.mastrao.test"
ENDPOINT = "http://tenant-core:8080/internal/v1/meeting-host-results/read"
ACCESS = "eyJhbGciOiJFZERTQSJ9." + "a" * 64
RESULT = {
    "version": 1,
    "meeting_ref": "meeting_0123456789abcdef",
    "title": "Rendez-vous client",
    "access_expires_at": 1_900_000_000,
    "started_at": 1_899_990_000,
    "ended_at": 1_899_992_520,
    "summary": {"status": "available"},
    "transcript": {"language": "fr", "segments": []},
}

pytestmark = pytest.mark.usefixtures("clear_cache")


@pytest.fixture(name="clear_cache")
def fixture_clear_cache():
    """Isolate the per-link attempt counters between tests."""
    cache.clear()
    yield
    cache.clear()


def _post(body, *, origin=ORIGIN, fetch_site="same-origin"):
    headers = {"HTTP_ORIGIN": origin}
    if fetch_site:
        headers["HTTP_SEC_FETCH_SITE"] = fetch_site
    return Client().post(
        reverse("read_mastrao_host_result"),
        data=json.dumps(body),
        content_type="application/json",
        **headers,
    )


@override_settings(
    APPLICATION_BASE_URL=ORIGIN, MASTRAO_CORE_HOST_RESULT_READ_ENDPOINT=ENDPOINT
)
def test_relays_the_link_once_and_hardens_the_response():
    """A valid link is relayed once and the response is kept out of caches."""
    with mock.patch(
        "core.mastrao_host_result_access.post_core_json", return_value=RESULT
    ) as core:
        response = _post({"access": ACCESS})

    assert response.status_code == 200
    assert response.json() == RESULT
    assert response["Cache-Control"] == "private, no-store"
    assert response["Referrer-Policy"] == "no-referrer"
    assert response["X-Robots-Tag"] == "noindex, nofollow"
    core.assert_called_once()
    assert core.call_args.kwargs["endpoint"] == ENDPOINT
    assert core.call_args.kwargs["body"] == {"credential": ACCESS}
    assert (
        core.call_args.kwargs["expected_path"]
        == "/internal/v1/meeting-host-results/read"
    )


@override_settings(
    APPLICATION_BASE_URL=ORIGIN, MASTRAO_CORE_HOST_RESULT_READ_ENDPOINT=ENDPOINT
)
@pytest.mark.parametrize(
    "body, origin, fetch_site",
    [
        ({"access": ACCESS}, "https://evil.test", "same-origin"),
        ({"access": ACCESS}, ORIGIN, None),
        ({"access": ACCESS, "extra": True}, ORIGIN, "same-origin"),
        ({"access": "short"}, ORIGIN, "same-origin"),
        ({"access": ACCESS + " x"}, ORIGIN, "same-origin"),
        ({"access": ["not", "a", "string"]}, ORIGIN, "same-origin"),
    ],
)
def test_refuses_foreign_or_malformed_requests_before_core(body, origin, fetch_site):
    """Cross-origin or malformed requests never reach Core."""
    with mock.patch("core.mastrao_host_result_access.post_core_json") as core:
        response = _post(body, origin=origin, fetch_site=fetch_site)

    assert response.status_code == 404
    assert response.json() == {"message": "Not found"}
    core.assert_not_called()


@override_settings(
    APPLICATION_BASE_URL=ORIGIN, MASTRAO_CORE_HOST_RESULT_READ_ENDPOINT=ENDPOINT
)
def test_keeps_a_core_refusal_opaque():
    """A Core refusal reaches the browser as the same opaque 404."""
    with mock.patch(
        "core.mastrao_host_result_access.post_core_json",
        side_effect=HostResultRefused(),
    ):
        response = _post({"access": ACCESS})

    assert response.status_code == 404
    assert response.json() == {"message": "Not found"}


@override_settings(
    APPLICATION_BASE_URL=ORIGIN, MASTRAO_CORE_HOST_RESULT_READ_ENDPOINT=ENDPOINT
)
def test_limits_attempts_per_link():
    """Each link is limited to a few reads per minute."""
    with mock.patch(
        "core.mastrao_host_result_access.post_core_json", return_value=RESULT
    ) as core:
        statuses = [_post({"access": ACCESS}).status_code for _ in range(11)]

    assert statuses == [200] * 10 + [429]
    assert core.call_count == 10


@override_settings(
    APPLICATION_BASE_URL=ORIGIN, MASTRAO_CORE_HOST_RESULT_READ_ENDPOINT=""
)
def test_is_unavailable_until_configured():
    """Without a Core endpoint the page is unavailable."""
    with mock.patch("core.mastrao_host_result_access.post_core_json") as core:
        response = _post({"access": ACCESS})

    assert response.status_code == 404
    core.assert_not_called()
