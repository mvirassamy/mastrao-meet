"""Celery wakeups for bounded subtitle-agent reconciliation."""

from core.tasks._task import task


@task(name="core.process_subtitle_reconciliation", queue="mastrao-transcription")
def process_subtitle_reconciliation(room_sid):
    """Reconcile one room-scoped subtitle control row."""
    from core.services.subtitle_reconciliation import (  # noqa: PLC0415  # pylint: disable=import-outside-toplevel
        reconcile_subtitle_control,
    )

    return reconcile_subtitle_control(room_sid)


@task(name="core.process_subtitle_snapshot_publication", queue="mastrao-transcription")
def process_subtitle_snapshot_publication(room_id, attempt=1):
    """Retry publication of the latest committed subtitle snapshot."""
    from core.services.subtitle_reconciliation import (  # noqa: PLC0415  # pylint: disable=import-outside-toplevel
        publish_subtitle_snapshot,
    )

    return publish_subtitle_snapshot(room_id, attempt=attempt)
