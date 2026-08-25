"""Fail-closed configuration proofs for canonical meeting closure and ASR."""

import json
import os
import subprocess
import sys

from django.core.exceptions import ImproperlyConfigured

import pytest

from meet import settings as meet_settings
from meet.settings import (
    validate_mastrao_meeting_close_configuration,
    validate_mastrao_transcription_configuration,
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


def test_close_rollout_requires_explicit_room_creation():
    """A stale-token-safe close cannot run with implicit room creation."""

    with pytest.raises(ImproperlyConfigured, match="EXPLICIT_ROOM_CREATION"):
        validate_mastrao_meeting_close_configuration(True, False)


@pytest.mark.parametrize(
    ("close_enabled", "explicit_creation"),
    [(False, False), (False, True), (True, True)],
)
def test_safe_close_rollout_configurations_are_accepted(
    close_enabled, explicit_creation
):
    """Disabled close and explicitly-created rooms remain valid configurations."""

    validate_mastrao_meeting_close_configuration(close_enabled, explicit_creation)


def test_transcription_disabled_needs_no_asr_configuration():
    """An untouched deployment stays valid with no ASR endpoint at all."""

    validate_mastrao_transcription_configuration(
        False, "real", "", fake_asr_allowed=False
    )
    validate_mastrao_transcription_configuration(
        False, "fake", "", fake_asr_allowed=False
    )


def test_non_deployable_configurations_may_select_the_deterministic_fake():
    """Development and Test keep the deterministic engine for qualification.

    Test runs with DEBUG=False, so the permission must be an explicit flag;
    deriving it from DEBUG would break provider-free qualification.
    """

    validate_mastrao_transcription_configuration(
        True, "fake", "", fake_asr_allowed=True
    )


def test_production_transcription_refuses_the_fake_engine():
    """The fixture engine must never answer as a real transcription."""

    with pytest.raises(ImproperlyConfigured, match="ASR_MODE=real"):
        validate_mastrao_transcription_configuration(
            True, "fake", "", fake_asr_allowed=False
        )


def test_production_transcription_requires_a_real_endpoint():
    """Real mode without a private endpoint cannot start."""

    with pytest.raises(ImproperlyConfigured, match="ASR_ENDPOINT"):
        validate_mastrao_transcription_configuration(
            True, "real", "", fake_asr_allowed=False
        )


@pytest.mark.parametrize(
    "endpoint",
    [
        "/relative/path",
        "ftp://asr.internal/transcribe",
        "https://user:secret@asr.internal/transcribe",
        "https://asr.internal/transcribe?token=secret",
        "https://asr.internal/transcribe#fragment",
    ],
)
def test_production_transcription_refuses_unqualified_endpoints(endpoint):
    """Relative, non-HTTP and credential-bearing endpoints are refused."""

    with pytest.raises(ImproperlyConfigured):
        validate_mastrao_transcription_configuration(
            True, "real", endpoint, fake_asr_allowed=False
        )


def test_production_transcription_accepts_a_qualified_private_endpoint():
    """A separately qualified private sovereign endpoint remains allowed."""

    validate_mastrao_transcription_configuration(
        True,
        "real",
        "https://asr.internal.mastrao/transcribe",
        fake_asr_allowed=False,
        asr_provider="mistral",
        asr_model="voxtral-mini-2602",
        asr_gateway_token="workload-token",
        asr_qualification_mode=True,
    )


def test_real_mode_refuses_implicit_provider_defaults():
    """Paid ASR cannot start from an implicit Mistral model or missing token."""

    with pytest.raises(ImproperlyConfigured, match="TRANSCRIPTION_PROVIDER"):
        validate_mastrao_transcription_configuration(
            True,
            "real",
            "https://asr.internal.mastrao/transcribe",
            fake_asr_allowed=False,
        )
    with pytest.raises(ImproperlyConfigured, match="ASR_GATEWAY_AUTH_TOKEN"):
        validate_mastrao_transcription_configuration(
            True,
            "real",
            "https://asr.internal.mastrao/transcribe",
            fake_asr_allowed=False,
            asr_provider="mistral",
            asr_model="voxtral-mini-2602",
        )
    with pytest.raises(ImproperlyConfigured, match="QUALIFICATION_MODE"):
        validate_mastrao_transcription_configuration(
            True,
            "real",
            "https://asr.internal.mastrao/transcribe",
            fake_asr_allowed=False,
            asr_provider="openai",
            asr_model="gpt-transcribe",
            asr_gateway_token="workload-token",
        )


def test_unknown_asr_mode_is_refused_even_when_transcription_is_disabled():
    """A typo must fail at startup rather than at the first transcription."""

    with pytest.raises(ImproperlyConfigured, match="ASR_MODE"):
        validate_mastrao_transcription_configuration(
            False, "typo", "", fake_asr_allowed=True
        )


# Referenced by name so importing the settings class called "Test" does not
# make pytest try to collect it as a test class.
@pytest.mark.parametrize("configuration_name", ["Development", "Test"])
def test_only_non_deployable_configurations_allow_the_fake_engine(configuration_name):
    """Development and Test opt in explicitly so qualification stays possible."""

    configuration = getattr(meet_settings, configuration_name)
    assert configuration.MASTRAO_TRANSCRIPTION_FAKE_ASR_ALLOWED is True


@pytest.mark.parametrize("configuration_name", ["Production", "Staging", "Demo"])
def test_deployable_configurations_never_allow_the_fake_engine(configuration_name):
    """A deployed environment can never inherit the deterministic fixture."""

    configuration = getattr(meet_settings, configuration_name)
    assert configuration.MASTRAO_TRANSCRIPTION_FAKE_ASR_ALLOWED is False


@pytest.mark.parametrize("configuration_name", ["Production", "Staging", "Demo"])
def test_deployable_transcription_requires_celery(configuration_name):
    """A deployed transcription runtime cannot fall back to synchronous ASR."""

    configuration = getattr(meet_settings, configuration_name)
    assert configuration.MASTRAO_TRANSCRIPTION_CELERY_REQUIRED is True
    with pytest.raises(ImproperlyConfigured, match="CELERY_ENABLED"):
        validate_mastrao_transcription_configuration(
            True,
            "real",
            "https://asr.internal.mastrao/transcribe",
            fake_asr_allowed=False,
            celery_enabled=False,
            celery_required=True,
        )


@pytest.mark.parametrize("configuration_name", ["Development", "Test"])
def test_local_transcription_may_start_without_celery(configuration_name):
    """Local qualification may retain its synchronous development fallback."""

    configuration = getattr(meet_settings, configuration_name)
    assert configuration.MASTRAO_TRANSCRIPTION_CELERY_REQUIRED is False


def test_parallel_test_cache_is_isolated_per_xdist_worker():
    """pytest -n 2 workers must not share a Redis session cache."""

    caches = meet_settings.Test.CACHES["default"]
    assert caches["BACKEND"] == "django_redis.cache.RedisCache"
    assert caches["KEY_PREFIX"].startswith("meet-test-")
    assert caches["LOCATION"].startswith("redis://")
