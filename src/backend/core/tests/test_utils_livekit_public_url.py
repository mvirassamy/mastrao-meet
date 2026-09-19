"""Public signaling configuration must not change private server API routing."""

from django.contrib.auth.models import AnonymousUser

import pytest
from rest_framework.test import APIRequestFactory

from core.api import get_frontend_configuration
from core.utils import generate_livekit_config


@pytest.mark.parametrize("public_url", ["ws://127.0.0.1:27880", ""])
def test_frontend_configuration_uses_the_browser_signaling_origin(settings, public_url):
    """Conference consumes /config/, not the URL in the room token payload."""
    private_url = "http://livekit:7880"
    settings.LIVEKIT_CONFIGURATION = {"url": private_url}
    settings.LIVEKIT_PUBLIC_URL = public_url

    response = get_frontend_configuration(APIRequestFactory().get("/api/v1.0/config/"))

    assert response.status_code == 200
    assert response.data["livekit"]["url"] == (public_url or private_url)
    assert settings.LIVEKIT_CONFIGURATION["url"] == private_url


def test_public_signaling_origin_is_distinct(settings):
    """A browser receives the published endpoint, not the Docker hostname."""
    settings.LIVEKIT_CONFIGURATION = {
        "url": "http://livekit:7880",
        "api_key": "synthetic-local-key",
        "api_secret": "synthetic-local-secret-never-a-provider-key",
    }
    settings.LIVEKIT_PUBLIC_URL = "ws://127.0.0.1:27880"
    result = generate_livekit_config(
        "synthetic-room", AnonymousUser(), "Guest", participant_id="guest-test"
    )
    assert result["url"] == "ws://127.0.0.1:27880"
    assert settings.LIVEKIT_CONFIGURATION["url"] == "http://livekit:7880"
    assert result["token"]


def test_unset_public_origin_preserves_existing_configuration(settings):
    """Existing deployments retain their current signaling origin by default."""
    settings.LIVEKIT_CONFIGURATION = {
        "url": "http://127.0.0.1.nip.io:7880",
        "api_key": "synthetic-local-key",
        "api_secret": "synthetic-local-secret-never-a-provider-key",
    }
    settings.LIVEKIT_PUBLIC_URL = ""
    result = generate_livekit_config(
        "synthetic-room", AnonymousUser(), "Guest", participant_id="guest-test"
    )
    assert result["url"] == settings.LIVEKIT_CONFIGURATION["url"]
