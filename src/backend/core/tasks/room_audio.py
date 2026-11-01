"""Run durable room-scoped audio delivery and transcription work."""

from core.room_audio_queue import ROOM_AUDIO_PROCESSING_QUEUE
from core.tasks._task import task


@task(name="core.process_room_audio_admissions", queue=ROOM_AUDIO_PROCESSING_QUEUE)
def process_room_audio_admissions(connection_id):
    """Reconcile one durable Room Audio admission without blocking ASR work."""

    from core.mastrao_room_audio_admission import (  # noqa: PLC0415  # pylint: disable=import-outside-toplevel
        reconcile_room_audio_admissions,
    )

    return reconcile_room_audio_admissions(connection_id=connection_id)


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
