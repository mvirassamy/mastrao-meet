"""Celery wakeups for bounded subtitle-agent reconciliation."""

from core.tasks._task import task


@task(name="core.process_subtitle_reconciliation", queue="mastrao-transcription")
def process_subtitle_reconciliation(room_sid):
    """Reconcile one room-scoped subtitle control row."""
    from core.services.subtitle_reconciliation import (  # noqa: PLC0415  # pylint: disable=import-outside-toplevel
        reconcile_subtitle_control,
    )

    return reconcile_subtitle_control(room_sid)
