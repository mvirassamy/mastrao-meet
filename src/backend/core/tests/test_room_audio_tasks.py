"""Focused scheduling proofs for Room Audio Celery tasks."""

# pylint: disable=missing-function-docstring

from unittest.mock import patch

from core.tasks.room_audio import process_room_audio_admissions


def test_transient_room_audio_admission_schedules_bounded_retry():
    with (
        patch(
            "core.tasks.room_audio.reconcile_room_audio_admissions",
            return_value=0,
        ),
        patch("core.tasks.room_audio.room_audio_admission_due", return_value=True),
        patch(
            "core.tasks.room_audio.room_audio_admission_retry_pending",
            return_value=True,
        ),
        patch.object(process_room_audio_admissions, "apply_async") as apply_async,
    ):
        assert process_room_audio_admissions("connection-fixture", retry_count=2) == 0

    apply_async.assert_called_once_with(
        args=("connection-fixture", 3),
        countdown=30,
    )


def test_room_audio_admission_stops_retrying_at_bound():
    with (
        patch(
            "core.tasks.room_audio.reconcile_room_audio_admissions",
            return_value=0,
        ),
        patch("core.tasks.room_audio.room_audio_admission_due", return_value=True),
        patch(
            "core.tasks.room_audio.room_audio_admission_retry_pending",
            return_value=True,
        ),
        patch.object(process_room_audio_admissions, "apply_async") as apply_async,
    ):
        assert process_room_audio_admissions("connection-fixture", retry_count=10) == 0

    apply_async.assert_not_called()


def test_room_audio_admission_does_not_duplicate_scheduled_retry():
    with (
        patch(
            "core.tasks.room_audio.reconcile_room_audio_admissions",
            return_value=0,
        ),
        patch("core.tasks.room_audio.room_audio_admission_due", return_value=False),
        patch(
            "core.tasks.room_audio.room_audio_admission_retry_pending",
            return_value=True,
        ),
        patch.object(process_room_audio_admissions, "apply_async") as apply_async,
    ):
        assert process_room_audio_admissions("connection-fixture", retry_count=2) == 0

    apply_async.assert_not_called()
