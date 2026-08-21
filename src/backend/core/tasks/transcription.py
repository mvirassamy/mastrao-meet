"""Celery task for one exact Mastrao transcription effect."""

from core.mastrao_transcription_contract import TranscriptionContractRefused
from core.tasks._task import task


class TranscriptionRetryable(TranscriptionContractRefused):
    """Temporary Core or storage failure that Celery may retry."""

    def __init__(self):
        super().__init__(status=503)


@task(
    autoretry_for=(TranscriptionRetryable,),
    retry_kwargs={"max_retries": 8, "countdown": 15},
)
def process_mastrao_transcription(effect_pk):
    """Run audio extraction and ASR outside the Core submit HTTP request.

    The HTTP adapter reserves the effect, persists the signed submitted
    receipt and enqueues this task. A 503 from Core is retryable; a
    definitive refusal after object write deletes the object.
    """

    # Imported lazily so task registration does not load the HTTP adapter.
    # pylint: disable=import-outside-toplevel
    from core.mastrao_transcription_adapter import complete_transcription

    try:
        complete_transcription(effect_pk)
    except TranscriptionContractRefused as error:
        if error.status == 503:
            raise TranscriptionRetryable() from error
        raise
