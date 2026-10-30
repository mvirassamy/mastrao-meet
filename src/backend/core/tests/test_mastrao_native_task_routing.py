"""Queue isolation for latency-sensitive native capture admission."""

from importlib import reload

from django.test import override_settings

from core import tasks as task_package
from core.tasks import native_capture, subtitle

NATIVE_ADMISSION_QUEUE = "mastrao-native-admission"


def test_room_audio_delivery_task_is_exposed_for_celery_autodiscovery():
    """Expose the delivery task when Celery imports the core task package."""

    assert task_package.process_room_audio_sources.__name__ == (
        "process_room_audio_sources"
    )


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
    assert _task_queue(process_native_asr) == "mastrao-native-asr-ready"


@override_settings(CELERY_ENABLED=True)
def test_subtitle_control_does_not_wait_behind_asr_work():
    """Keep subtitle stop, cleanup and status retries off the serial ASR queue."""

    tasks = reload(subtitle)
    assert _task_queue(tasks.process_subtitle_reconciliation) == "meet-backend"
    assert _task_queue(tasks.process_subtitle_snapshot_publication) == "meet-backend"
