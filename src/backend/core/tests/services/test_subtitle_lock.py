"""Tests for the bounded PostgreSQL subtitle convergence lock."""

from django.db import transaction

import pytest

from core.models import RoomSubtitleAdvisoryKey
from core.services.subtitle_lock import (
    SubtitleLockUsageError,
    resolve_subtitle_lock_key,
    try_subtitle_convergence_lock,
)

pytestmark = pytest.mark.django_db(transaction=True)


def test_registry_assigns_one_exact_key_per_room_sid():
    """The registry is stable for one SID and distinct for another SID."""

    first = resolve_subtitle_lock_key("RM_lock_registry_a")
    assert resolve_subtitle_lock_key("RM_lock_registry_a") == first
    second = resolve_subtitle_lock_key("RM_lock_registry_b")

    assert second != first
    assert RoomSubtitleAdvisoryKey.objects.count() == 2


def test_lock_refuses_to_run_inside_a_django_transaction():
    """Provider calls cannot accidentally run under a row-lock transaction."""

    with transaction.atomic():
        with pytest.raises(SubtitleLockUsageError):
            with try_subtitle_convergence_lock("RM_lock_atomic"):
                pass


def test_lock_timeout_is_bounded_and_release_is_confirmed(settings):
    """A held room lock reports busy, then becomes reusable after release."""

    settings.ROOM_SUBTITLE_LOCK_TIMEOUT_SECONDS = 0.05
    with try_subtitle_convergence_lock("RM_lock_busy") as first:
        assert first is not None
        with try_subtitle_convergence_lock("RM_lock_busy", timeout=0.02) as second:
            assert second is None

    with try_subtitle_convergence_lock("RM_lock_busy", timeout=0.1) as released:
        assert released is not None


def test_provider_errors_are_not_reclassified_as_lock_contention():
    """The caller's exception crosses the lock context unchanged."""

    marker = RuntimeError("provider failed")
    with pytest.raises(RuntimeError, match="provider failed"):
        with try_subtitle_convergence_lock("RM_lock_error") as lock:
            assert lock is not None
            raise marker

    with try_subtitle_convergence_lock("RM_lock_error", timeout=0.1) as released:
        assert released is not None
