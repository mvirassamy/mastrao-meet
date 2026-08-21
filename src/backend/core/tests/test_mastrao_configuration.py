"""Fail-closed configuration proofs for canonical meeting closure and ASR."""

from django.core.exceptions import ImproperlyConfigured

import pytest

from meet import settings as meet_settings
from meet.settings import (
    validate_mastrao_meeting_close_configuration,
    validate_mastrao_transcription_configuration,
)


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
        True, "real", "https://asr.internal.mastrao/transcribe", fake_asr_allowed=False
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


def test_parallel_test_cache_is_isolated_per_xdist_worker():
    """pytest -n 2 workers must not share a Redis session cache."""

    caches = meet_settings.Test.CACHES["default"]
    assert caches["BACKEND"] == "django.core.cache.backends.locmem.LocMemCache"
    assert caches["LOCATION"].startswith("meet-test-")
