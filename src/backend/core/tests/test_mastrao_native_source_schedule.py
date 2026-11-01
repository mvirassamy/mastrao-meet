"""The reconciliation limit reaches native source dispatch."""

from unittest.mock import patch

from core.mastrao_recording_reconciler import reconcile_native_recordings


def test_reconcile_forwards_limit_to_source_scheduler():
    """The reconciler passes its existing bound to native source dispatch."""

    with (
        patch(
            "core.mastrao_recording_reconciler.reconcile_native_captures",
            return_value=0,
        ),
        patch(
            "core.mastrao_recording_reconciler.reconcile_native_admissions",
            return_value=0,
        ),
        patch("core.mastrao_recording_reconciler.schedule_native_asr", return_value=0),
        patch(
            "core.mastrao_recording_reconciler.schedule_native_source_transfer",
            return_value=3,
        ) as schedule,
        patch(
            "core.mastrao_recording_reconciler.schedule_room_audio_source_delivery",
            return_value=0,
        ),
        patch(
            "core.mastrao_recording_reconciler.schedule_room_audio_asr",
            return_value=0,
        ),
    ):
        assert reconcile_native_recordings(limit=3) == 3
    schedule.assert_called_once_with(limit=3)


def test_reconcile_forwards_limit_to_room_audio_asr_scheduler():
    """The durable reconciler retries room-mix ASR after a missed dispatch."""

    with (
        patch(
            "core.mastrao_recording_reconciler.reconcile_native_captures",
            return_value=0,
        ),
        patch(
            "core.mastrao_recording_reconciler.reconcile_native_admissions",
            return_value=0,
        ),
        patch(
            "core.mastrao_recording_reconciler.schedule_native_source_transfer",
            return_value=0,
        ),
        patch(
            "core.mastrao_recording_reconciler.schedule_room_audio_source_delivery",
            return_value=0,
        ),
        patch("core.mastrao_recording_reconciler.schedule_native_asr", return_value=0),
        patch(
            "core.mastrao_recording_reconciler.schedule_room_audio_asr",
            return_value=2,
        ) as schedule,
    ):
        assert reconcile_native_recordings(limit=4) == 2
    schedule.assert_called_once_with(limit=4)
