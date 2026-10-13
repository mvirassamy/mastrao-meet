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
    ):
        assert reconcile_native_recordings(limit=3) == 3
    schedule.assert_called_once_with(limit=3)
