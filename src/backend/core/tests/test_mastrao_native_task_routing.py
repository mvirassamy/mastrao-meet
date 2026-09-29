"""Queue isolation for latency-sensitive native capture admission."""

from importlib import reload

from django.test import override_settings

from core.tasks import native_capture, subtitle

NATIVE_ADMISSION_QUEUE = "mastrao-native-admission"


def _task_queue(celery_task):
    """Return the queue fixed by a Celery task decorator."""

    return celery_task._get_exec_options()["queue"]  # pylint: disable=protected-access


@override_settings(CELERY_ENABLED=True)
def test_native_admission_does_not_wait_behind_asr_work():
    """Keep short admission work off the serial transcription queue."""

    tasks = reload(native_capture)
    process_native_admissions = tasks.process_native_admissions
    process_native_sources = tasks.process_native_sources
    process_native_asr = tasks.process_native_asr
    assert _task_queue(process_native_admissions) == NATIVE_ADMISSION_QUEUE
    assert _task_queue(process_native_sources) == "mastrao-transcription"
    assert _task_queue(process_native_asr) == "mastrao-transcription"


@override_settings(CELERY_ENABLED=True)
def test_subtitle_control_does_not_wait_behind_asr_work():
    """Keep subtitle stop, cleanup and status retries off the serial ASR queue."""

    tasks = reload(subtitle)
    assert _task_queue(tasks.process_subtitle_reconciliation) == "meet-backend"
    assert _task_queue(tasks.process_subtitle_snapshot_publication) == "meet-backend"
