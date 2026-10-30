"""Wake the durable native audio admission consumer using opaque RTC references."""

from core.tasks._task import task


@task(name="core.process_native_admissions", queue="mastrao-native-admission")
def process_native_admissions(connection_id):
    """Reconcile one durable native admission without waiting behind ASR work."""

    from core.mastrao_native_admission import (  # noqa: PLC0415  # pylint: disable=import-outside-toplevel
        reconcile_native_admissions,
    )

    return reconcile_native_admissions(connection_id=connection_id)


@task(name="core.process_native_sources", queue="mastrao-transcription")
def process_native_sources(source_id=None, queued_claim=None, queued_attempts=None):
    """Transfer the source reserved by the scheduler."""

    if source_id is None and queued_claim is None and queued_attempts is None:
        from core.mastrao_native_source_transfer import (  # noqa: PLC0415  # pylint: disable=import-outside-toplevel
            schedule_native_source_transfer,
        )

        return schedule_native_source_transfer(limit=1)
    if source_id is None or queued_claim is None or queued_attempts is None:
        return False

    from core.mastrao_native_source_transfer import (  # noqa: PLC0415  # pylint: disable=import-outside-toplevel
        transfer_native_source,
    )

    return transfer_native_source(source_id, queued_claim, queued_attempts)


@task(name="core.process_room_audio_sources", queue="mastrao-transcription")
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


@task(name="core.process_room_audio_asr", queue="mastrao-transcription")
def process_room_audio_asr(source_id=None):
    """Transcribe one exact delivered room-audio source."""

    from core.mastrao_room_audio_asr_worker import (  # noqa: PLC0415  # pylint: disable=import-outside-toplevel
        process_room_audio_asr_source,
        schedule_room_audio_asr,
    )

    if source_id is None:
        return schedule_room_audio_asr(limit=1)
    return process_room_audio_asr_source(source_id)


@task(name="core.process_native_asr", queue="mastrao-native-asr-ready")
def process_native_asr(capture_start_id=None, dispatch_ref=None):
    """Process the exact source named by its current dispatch reservation."""

    from core.mastrao_native_asr_worker import (  # noqa: PLC0415  # pylint: disable=import-outside-toplevel
        process_native_asr_source,
    )

    return process_native_asr_source(capture_start_id, dispatch_ref)
