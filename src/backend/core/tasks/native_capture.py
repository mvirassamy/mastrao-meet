"""Wake the durable native audio admission consumer using opaque RTC references."""

from core.tasks._task import task


@task(name="core.process_native_admissions", queue="mastrao-transcription")
def process_native_admissions(connection_id):
    """Reconcile one durable native admission after its RTC event."""

    from core.mastrao_native_admission import (  # noqa: PLC0415  # pylint: disable=import-outside-toplevel
        reconcile_native_admissions,
    )

    return reconcile_native_admissions(connection_id=connection_id)


@task(name="core.process_native_sources", queue="mastrao-transcription")
def process_native_sources():
    """Transfer the next admitted native audio source to durable storage."""

    from core.mastrao_native_source_transfer import (  # noqa: PLC0415  # pylint: disable=import-outside-toplevel
        transfer_next_native_source,
    )

    return transfer_next_native_source()


@task(name="core.process_native_asr", queue="mastrao-transcription")
def process_native_asr():
    """Process the next durable native source awaiting ASR delivery."""

    from core.mastrao_native_asr_worker import (  # noqa: PLC0415  # pylint: disable=import-outside-toplevel
        process_next_native_asr,
    )

    return process_next_native_asr()
