"""Durable transcription dispatch, ASR completion and Core notification."""

# Lazy imports keep Celery task registration from loading the HTTP adapter.
# ruff: noqa: PLC0415
# pylint: disable=cyclic-import

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from core import models
from core.mastrao_transcription_artifact import delete_transcript_object
from core.mastrao_transcription_contract import (
    TranscriptionContractRefused,
    TranscriptionPipelineFailed,
)

DispatchState = models.MastraoTranscriptionEffect.DispatchState
EffectState = models.MastraoTranscriptionEffect.State
BindingState = models.MastraoTranscriptionBinding.State
RETRYABLE_STATUSES = {503}
TERMINAL_DELETION_STATUSES = {404}


def transcription_task_id(effect_jti):
    """Return the deterministic Celery task id for one transcription JTI."""
    return f"mastrao-transcribe-{effect_jti}"


def publish_transcription_job(effect_pk):
    """Publish or republish one Celery task. Cache is never a delivery proof."""

    local_effect = models.MastraoTranscriptionEffect.objects.filter(
        pk=effect_pk
    ).first()
    if local_effect is None:
        return False
    if local_effect.dispatch_state == DispatchState.COMPLETED:
        return False
    try:
        # pylint: disable=import-outside-toplevel,broad-exception-caught
        from core.tasks.transcription import (
            process_mastrao_transcription,
        )

        process_mastrao_transcription.apply_async(
            args=[str(effect_pk)],
            task_id=transcription_task_id(local_effect.effect_jti),
            ignore_result=True,
        )
    except Exception:  # pylint: disable=broad-exception-caught  # noqa: BLE001
        models.MastraoTranscriptionEffect.objects.filter(pk=effect_pk).exclude(
            dispatch_state=DispatchState.COMPLETED
        ).update(
            dispatch_state=DispatchState.DISPATCH_PENDING,
            updated_at=timezone.now(),
        )
        return False
    models.MastraoTranscriptionEffect.objects.filter(
        pk=effect_pk, dispatch_state=DispatchState.DISPATCH_PENDING
    ).update(dispatch_state=DispatchState.QUEUED, updated_at=timezone.now())
    return True


def reconcile_transcription_dispatches(limit=20):
    """Republish pending, notifying or crash-expired transcription jobs."""

    pending = list(
        models.MastraoTranscriptionEffect.objects.filter(
            dispatch_state__in=[
                DispatchState.DISPATCH_PENDING,
                DispatchState.QUEUED,
                DispatchState.ARTIFACT_NOTIFICATION_PENDING,
                DispatchState.FAILURE_NOTIFICATION_PENDING,
            ]
        ).order_by("updated_at")[:limit]
    )
    stale_before = timezone.now() - timezone.timedelta(
        seconds=_pipeline_timeout_seconds()
    )
    stale_running = list(
        models.MastraoTranscriptionEffect.objects.filter(
            dispatch_state=DispatchState.RUNNING, updated_at__lte=stale_before
        ).order_by("updated_at")[: max(0, limit - len(pending))]
    )
    republished = 0
    for local_effect in [*pending, *stale_running]:
        if local_effect.dispatch_state == DispatchState.RUNNING:
            models.MastraoTranscriptionEffect.objects.filter(
                pk=local_effect.pk, dispatch_state=DispatchState.RUNNING
            ).update(
                dispatch_state=DispatchState.DISPATCH_PENDING,
                updated_at=timezone.now(),
            )
        if publish_transcription_job(local_effect.pk):
            republished += 1
    return republished


def _pipeline_timeout_seconds():
    # pylint: disable=import-outside-toplevel
    from core.mastrao_transcription_artifact import (
        FFMPEG_TIMEOUT_SECONDS,
    )

    return FFMPEG_TIMEOUT_SECONDS + settings.MASTRAO_TRANSCRIPTION_ASR_TIMEOUT_SECONDS


def complete_transcription(effect_pk):
    """Run or resume one reserved effect without duplicating ASR."""

    # Imported lazily so task registration does not load the HTTP adapter.
    # pylint: disable=import-outside-toplevel
    from core.mastrao_transcription_adapter import (
        _artifact_from_binding,
        _effect_from_local,
        _produce_transcript,
    )

    transcription_binding, local_effect, action = _acquire_completion_lease(effect_pk)
    effect = _effect_from_local(transcription_binding, local_effect)
    if action == "completed_available":
        _deliver_artifact(effect, transcription_binding, local_effect)
        return
    if action == "completed_failed":
        _deliver_failure(effect, local_effect, transcription_binding)
        return
    if action == "notify_artifact":
        _deliver_artifact(effect, transcription_binding, local_effect)
        return
    if action == "notify_failure":
        _deliver_failure(effect, local_effect, transcription_binding)
        return
    if action == "retry_later":
        raise TranscriptionContractRefused(status=503)
    if transcription_binding.checksum_digest:
        artifact = _artifact_from_binding(transcription_binding)
    else:
        try:
            artifact = _produce_transcript(transcription_binding)
        except TranscriptionPipelineFailed as failure:
            _persist_failure_pending(local_effect.pk, failure.failure_code)
            local_effect.refresh_from_db()
            _deliver_failure(effect, local_effect, transcription_binding)
            raise
    _persist_artifact_pending(local_effect.pk, transcription_binding.pk, artifact)
    transcription_binding.refresh_from_db()
    _deliver_artifact(effect, transcription_binding, local_effect)


def _acquire_completion_lease(effect_pk):
    with transaction.atomic():
        local_effect = (
            models.MastraoTranscriptionEffect.objects.select_for_update().get(
                pk=effect_pk
            )
        )
        transcription_binding = (
            models.MastraoTranscriptionBinding.objects.select_related(
                "recording_binding"
            )
            .select_for_update(of=("self",))
            .get(pk=local_effect.transcription_binding_id)
        )
        if local_effect.dispatch_state == DispatchState.COMPLETED:
            if local_effect.state == EffectState.FAILED:
                return transcription_binding, local_effect, "completed_failed"
            return transcription_binding, local_effect, "completed_available"
        if local_effect.dispatch_state == DispatchState.ARTIFACT_NOTIFICATION_PENDING:
            return transcription_binding, local_effect, "notify_artifact"
        if local_effect.dispatch_state == DispatchState.FAILURE_NOTIFICATION_PENDING:
            return transcription_binding, local_effect, "notify_failure"
        stale_lease = timezone.now() - local_effect.updated_at >= timezone.timedelta(
            seconds=_pipeline_timeout_seconds()
        )
        if local_effect.dispatch_state == DispatchState.RUNNING and not stale_lease:
            return transcription_binding, local_effect, "retry_later"
        local_effect.dispatch_state = DispatchState.RUNNING
        local_effect.save(update_fields=["dispatch_state", "updated_at"])
        return transcription_binding, local_effect, "produce"


def _persist_artifact_pending(effect_pk, binding_pk, artifact):
    with transaction.atomic():
        locked_effect = (
            models.MastraoTranscriptionEffect.objects.select_for_update().get(
                pk=effect_pk
            )
        )
        if locked_effect.dispatch_state == DispatchState.COMPLETED:
            return
        locked_binding = (
            models.MastraoTranscriptionBinding.objects.select_for_update().get(
                pk=binding_pk
            )
        )
        if locked_binding.checksum_digest is not None and (
            locked_binding.checksum_digest != artifact["checksum_digest"]
            or locked_binding.byte_size != artifact["byte_size"]
        ):
            raise TranscriptionContractRefused(status=409)
        locked_binding.transcript_artifact_ref = artifact["transcript_artifact_ref"]
        locked_binding.object_ref = artifact["object_ref"]
        locked_binding.content_type = "application/json"
        locked_binding.byte_size = artifact["byte_size"]
        locked_binding.checksum_algorithm = "sha256"
        locked_binding.checksum_digest = artifact["checksum_digest"]
        locked_binding.segment_count = artifact["segment_count"]
        locked_binding.engine_ref = artifact["engine_ref"]
        locked_binding.transcript_verified_at = timezone.now()
        locked_binding.state = BindingState.PROCESSING
        locked_binding.save()
        locked_effect.dispatch_state = DispatchState.ARTIFACT_NOTIFICATION_PENDING
        locked_effect.save(update_fields=["dispatch_state", "updated_at"])


def _persist_failure_pending(effect_pk, failure_code):
    with transaction.atomic():
        locked_effect = (
            models.MastraoTranscriptionEffect.objects.select_for_update().get(
                pk=effect_pk
            )
        )
        if locked_effect.dispatch_state == DispatchState.COMPLETED:
            return
        locked_effect.failure_code = failure_code
        locked_effect.dispatch_state = DispatchState.FAILURE_NOTIFICATION_PENDING
        locked_effect.save(
            update_fields=["failure_code", "dispatch_state", "updated_at"]
        )


def _commit_available(effect_pk, binding_pk):
    with transaction.atomic():
        locked_effect = (
            models.MastraoTranscriptionEffect.objects.select_for_update().get(
                pk=effect_pk
            )
        )
        locked_binding = (
            models.MastraoTranscriptionBinding.objects.select_for_update().get(
                pk=binding_pk
            )
        )
        locked_effect.state = EffectState.APPLIED
        locked_effect.dispatch_state = DispatchState.COMPLETED
        locked_effect.applied_at = timezone.now()
        locked_effect.save(
            update_fields=["state", "dispatch_state", "applied_at", "updated_at"]
        )
        locked_binding.state = BindingState.AVAILABLE
        locked_binding.save(update_fields=["state", "updated_at"])


def _commit_failed(effect_pk, binding_pk):
    with transaction.atomic():
        locked_effect = (
            models.MastraoTranscriptionEffect.objects.select_for_update().get(
                pk=effect_pk
            )
        )
        if locked_effect.state == EffectState.APPLIED:
            return
        locked_effect.state = EffectState.FAILED
        locked_effect.dispatch_state = DispatchState.COMPLETED
        locked_effect.save(update_fields=["state", "dispatch_state", "updated_at"])
        locked_binding = (
            models.MastraoTranscriptionBinding.objects.select_for_update().get(
                pk=binding_pk
            )
        )
        locked_binding.state = BindingState.FAILED
        locked_binding.save(update_fields=["state", "updated_at"])


def _deliver_artifact(effect, transcription_binding, local_effect):
    # pylint: disable=import-outside-toplevel
    from core.mastrao_transcription_adapter import (
        _artifact_from_binding,
        _notify_core_artifact,
    )

    artifact = _artifact_from_binding(transcription_binding)

    try:
        _notify_core_artifact(effect, artifact)
    except TranscriptionContractRefused as error:
        if error.status in RETRYABLE_STATUSES:
            raise
        if error.status in TERMINAL_DELETION_STATUSES and artifact.get("object_ref"):
            delete_transcript_object(artifact["object_ref"])
            _commit_failed(local_effect.pk, transcription_binding.pk)
        raise
    _commit_available(local_effect.pk, transcription_binding.pk)


def _deliver_failure(effect, local_effect, transcription_binding):
    # pylint: disable=import-outside-toplevel
    from core.mastrao_transcription_adapter import _notify_core_failure

    try:
        _notify_core_failure(effect, local_effect.failure_code or "asr_failed")
    except TranscriptionContractRefused as error:
        if error.status in RETRYABLE_STATUSES:
            raise
        raise
    _commit_failed(local_effect.pk, transcription_binding.pk)
