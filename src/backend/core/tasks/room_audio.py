"""Run durable room-scoped audio delivery and transcription work."""

from core.mastrao_room_audio_admission import (
    reconcile_room_audio_admissions,
    room_audio_admission_due,
    room_audio_admission_retry_pending,
)
from core.room_audio_queue import ROOM_AUDIO_PROCESSING_QUEUE
from core.tasks._task import task

ADMISSION_RETRY_COUNTDOWN_SECONDS = 30
ADMISSION_MAX_RETRIES = 10


@task(name="core.process_room_audio_admissions", queue=ROOM_AUDIO_PROCESSING_QUEUE)
def process_room_audio_admissions(connection_id, retry_count=0):
    """Reconcile one admission and reschedule bounded transient failures."""
    attempt_due = room_audio_admission_due(connection_id)
    admitted = reconcile_room_audio_admissions(connection_id=connection_id)
    retry_pending = room_audio_admission_retry_pending(connection_id)
    if attempt_due and retry_pending and retry_count < ADMISSION_MAX_RETRIES:
        process_room_audio_admissions.apply_async(
            args=(connection_id, retry_count + 1),
            countdown=ADMISSION_RETRY_COUNTDOWN_SECONDS,
        )
    return admitted


@task(name="core.process_room_audio_sources", queue=ROOM_AUDIO_PROCESSING_QUEUE)
def process_room_audio_sources(source_id, queued_claim, queued_attempts):
    """Deliver the room-audio source named by an exact scheduler reservation."""

    from core import models  # noqa: PLC0415  # pylint: disable=import-outside-toplevel
    from core.mastrao_room_audio_source import (  # noqa: PLC0415  # pylint: disable=import-outside-toplevel
        deliver_room_audio_source,
    )

    row = models.MastraoRoomAudioSourceOutbox.objects.filter(pk=source_id).first()
    if row is None:
        return False
    return deliver_room_audio_source(row, queued_claim, queued_attempts)


@task(name="core.process_room_audio_asr", queue=ROOM_AUDIO_PROCESSING_QUEUE)
def process_room_audio_asr(source_id=None):
    """Transcribe one exact delivered room-audio source."""

    from core.mastrao_room_audio_asr_worker import (  # noqa: PLC0415  # pylint: disable=import-outside-toplevel
        process_room_audio_asr_source,
        schedule_room_audio_asr,
    )

    if source_id is None:
        return schedule_room_audio_asr(limit=1)
    return process_room_audio_asr_source(source_id)
