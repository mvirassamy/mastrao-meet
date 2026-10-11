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
def process_native_sources():
    """Transfer the next admitted native audio source to durable storage."""

    from core.mastrao_native_source_transfer import (  # noqa: PLC0415  # pylint: disable=import-outside-toplevel
        transfer_next_native_source,
    )

    return transfer_next_native_source()


@task(name="core.process_native_asr", queue="mastrao-transcription")
def process_native_asr(capture_start_id=None, dispatch_ref=None):
    """Process the exact source named by its current dispatch reservation."""

    from core.mastrao_native_asr_worker import (  # noqa: PLC0415  # pylint: disable=import-outside-toplevel
        process_native_asr_source,
    )

    return process_native_asr_source(capture_start_id, dispatch_ref)
