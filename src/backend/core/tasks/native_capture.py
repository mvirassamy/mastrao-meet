"""Wake the durable native audio admission consumer using opaque RTC references."""

from core.tasks._task import task


@task(name="core.process_native_admissions", queue="mastrao-transcription")
def process_native_admissions(connection_id):
    from core.mastrao_native_admission import (  # noqa: PLC0415  # Avoid task autodiscovery cycles.
        reconcile_native_admissions,
    )

    return reconcile_native_admissions(connection_id=connection_id)


@task(name="core.process_native_sources", queue="mastrao-transcription")
def process_native_sources():
    from core.mastrao_native_source_transfer import (  # noqa: PLC0415 - avoid task/adapter import cycle
        transfer_next_native_source,
    )

    return transfer_next_native_source()


@task(name="core.process_native_asr", queue="mastrao-transcription")
def process_native_asr():
    from core.mastrao_native_asr_worker import (  # noqa: PLC0415 - avoid task import cycle
        process_next_native_asr,
    )

    return process_next_native_asr()
