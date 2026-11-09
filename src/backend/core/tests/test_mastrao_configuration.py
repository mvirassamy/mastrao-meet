"""Fail-closed configuration proofs for canonical meeting closure and ASR."""

import json
import os
import subprocess
import sys
from types import SimpleNamespace

from django.core.exceptions import ImproperlyConfigured

import pytest

from meet import settings as meet_settings
from meet.settings import (
    redis_url_with_database,
    resolve_mastrao_meeting_integration,
    validate_mastrao_meeting_close_configuration,
)


def test_frontend_configuration_projects_the_fixed_platform_origin():
    """Browser cache validation must bind returns to the server configuration."""

    environment = {
        **os.environ,
        "DJANGO_CONFIGURATION": "Development",
        "DJANGO_SETTINGS_MODULE": "meet.settings",
        "MASTRAO_PLATFORM_ORIGIN": "https://platform.mastrao.test",
    }
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from configurations import importer; "
                "importer.install(); "
                "import django; "
                "django.setup(); "
                "from core.api import get_frontend_configuration; "
                "from rest_framework.test import APIRequestFactory; "
                "response = get_frontend_configuration("
                "APIRequestFactory().get('/api/v1.0/config/')); "
                "print(response.data['mastrao_platform_origin'])"
            ),
        ],
        check=True,
        capture_output=True,
        cwd=os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
        env=environment,
        text=True,
    )

    assert result.stdout.strip() == "https://platform.mastrao.test"


def test_development_csrf_origins_follow_the_configured_public_proxy():
    """A non-default local public port must remain able to persist consent."""

    environment = {
        **os.environ,
        "DJANGO_CONFIGURATION": "Development",
        "DJANGO_SETTINGS_MODULE": "meet.settings",
        "CSRF_TRUSTED_ORIGINS": "http://localhost:3020",
    }
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from configurations import importer; "
                "importer.install(); "
                "from django.conf import settings; "
                "import json; "
                "print(json.dumps(settings.CSRF_TRUSTED_ORIGINS))"
            ),
        ],
        check=True,
        capture_output=True,
        cwd=os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
        env=environment,
        text=True,
    )

    assert json.loads(result.stdout.strip()) == ["http://localhost:3020"]


def test_oidc_token_endpoint_can_use_client_secret_basic():
    """The OAuth client authentication method must be configurable by deployment."""

    environment = {
        **os.environ,
        "DJANGO_CONFIGURATION": "Development",
        "DJANGO_SETTINGS_MODULE": "meet.settings",
        "OIDC_TOKEN_USE_BASIC_AUTH": "True",
    }
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from configurations import importer; "
                "importer.install(); "
                "from django.conf import settings; "
                "print(settings.OIDC_TOKEN_USE_BASIC_AUTH)"
            ),
        ],
        check=True,
        capture_output=True,
        cwd=os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
        env=environment,
        text=True,
    )

    assert result.stdout.strip() == "True"


def test_room_adapter_requires_explicit_room_creation():
    """A governed room cannot let stale media tokens recreate its provider room."""

    with pytest.raises(ImproperlyConfigured, match="EXPLICIT_ROOM_CREATION"):
        validate_mastrao_meeting_close_configuration(True, False)


def test_settings_boot_refuses_room_adapter_without_explicit_room_creation():
    """Loading settings must enforce the stale-token invariant on the adapter."""

    environment = {
        **os.environ,
        "DJANGO_CONFIGURATION": "Development",
        "DJANGO_SETTINGS_MODULE": "meet.settings",
        "LIVEKIT_EXPLICIT_ROOM_CREATION": "False",
        **dict.fromkeys(meet_settings.MASTRAO_BASE_CONTRACT_SETTINGS, "configured"),
    }
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from configurations import importer; "
                "importer.install(); "
                "from django.conf import settings; "
                "import sys; "
                "sys.stdout.write(str(settings.MASTRAO_MEETING_INTEGRATION_CONFIGURED))"
            ),
        ],
        check=False,
        capture_output=True,
        cwd=os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
        env=environment,
        text=True,
    )

    assert result.returncode != 0
    assert "LIVEKIT_EXPLICIT_ROOM_CREATION=true" in result.stderr


def _load_development_setting(name, overrides):
    """Load the real Development configuration and print one setting."""

    return subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from configurations import importer; "
                "importer.install(); "
                "from django.conf import settings; "
                "import sys; "
                f"sys.stdout.write(str(settings.{name}))"
            ),
        ],
        check=False,
        capture_output=True,
        cwd=os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
        env={
            **os.environ,
            "DJANGO_CONFIGURATION": "Development",
            "DJANGO_SETTINGS_MODULE": "meet.settings",
            **overrides,
        },
        text=True,
    )


def test_settings_boot_exposes_a_complete_meeting_integration():
    """A complete contract must reach django.conf.settings, not only the class."""

    result = _load_development_setting(
        "MASTRAO_MEETING_INTEGRATION_CONFIGURED",
        {
            "LIVEKIT_EXPLICIT_ROOM_CREATION": "True",
            **dict.fromkeys(meet_settings.MASTRAO_BASE_CONTRACT_SETTINGS, "configured"),
        },
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "True"


def test_settings_boot_refuses_a_partial_meeting_integration():
    """One configured contract value must stop the process at startup."""

    result = _load_development_setting(
        "MASTRAO_MEETING_INTEGRATION_CONFIGURED",
        {
            **dict.fromkeys(meet_settings.MASTRAO_BASE_CONTRACT_SETTINGS, ""),
            "MASTRAO_ROOM_EFFECT_ISSUER": "https://core.mastrao.test",
        },
    )

    assert result.returncode != 0
    assert "Incomplete Mastrao meeting integration" in result.stderr


def test_meeting_integration_is_absent_when_no_contract_value_is_configured():
    """The upstream fork remains valid without the Mastrao integration."""

    configuration = SimpleNamespace(
        **dict.fromkeys(meet_settings.MASTRAO_BASE_CONTRACT_SETTINGS, "")
    )

    assert resolve_mastrao_meeting_integration(configuration) is False


def test_meeting_integration_refuses_partial_configuration():
    """One configured contract value must never create a partial runtime."""

    configuration = SimpleNamespace(
        **dict.fromkeys(meet_settings.MASTRAO_BASE_CONTRACT_SETTINGS, "")
    )
    configuration.MASTRAO_ROOM_EFFECT_ISSUER = "https://core.mastrao.test"

    with pytest.raises(ImproperlyConfigured, match="Incomplete Mastrao meeting"):
        resolve_mastrao_meeting_integration(configuration)


def test_meeting_integration_requires_host_invitation_exchange():
    """Host invitations cannot start with the rest of the contract configured."""

    values = dict.fromkeys(meet_settings.MASTRAO_BASE_CONTRACT_SETTINGS, "configured")
    values["MASTRAO_CORE_HOST_INVITATION_EXCHANGE_ENDPOINT"] = ""

    with pytest.raises(
        ImproperlyConfigured,
        match="MASTRAO_CORE_HOST_INVITATION_EXCHANGE_ENDPOINT",
    ):
        resolve_mastrao_meeting_integration(SimpleNamespace(**values))


def test_meeting_integration_accepts_complete_base_contract():
    """A complete configuration atomically enables every base contract path."""

    configuration = SimpleNamespace(
        **dict.fromkeys(meet_settings.MASTRAO_BASE_CONTRACT_SETTINGS, "configured")
    )

    assert resolve_mastrao_meeting_integration(configuration) is True


@pytest.mark.parametrize(
    ("room_adapter_enabled", "explicit_creation"),
    [(False, False), (False, True), (True, True)],
)
def test_safe_room_adapter_configurations_are_accepted(
    room_adapter_enabled, explicit_creation
):
    """Absent adapters and explicitly-created governed rooms remain valid."""

    validate_mastrao_meeting_close_configuration(
        room_adapter_enabled, explicit_creation
    )


def test_parallel_test_cache_is_isolated_per_xdist_worker():
    """pytest -n 2 workers must not share a Redis session cache."""

    caches = meet_settings.Test.CACHES["default"]
    assert caches["BACKEND"] == "django_redis.cache.RedisCache"
    assert caches["KEY_PREFIX"].startswith("meet-test-")
    assert caches["LOCATION"].startswith("redis://")


def test_parallel_test_cache_preserves_configured_redis_endpoint():
    """Database isolation must not replace the configured Redis endpoint."""

    redis_url = "redis://user:pass@localhost:6379/1?ssl_cert_reqs=none"

    assert redis_url_with_database(redis_url, 11) == (
        "redis://user:pass@localhost:6379/11?ssl_cert_reqs=none"
    )
