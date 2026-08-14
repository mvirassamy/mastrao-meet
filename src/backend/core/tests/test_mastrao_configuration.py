"""Fail-closed configuration proofs for canonical meeting closure."""

from django.core.exceptions import ImproperlyConfigured

import pytest

from meet.settings import validate_mastrao_meeting_close_configuration


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
