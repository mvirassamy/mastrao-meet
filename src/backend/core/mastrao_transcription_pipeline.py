"""Durable transcription dispatch, ASR completion and Core notification."""

import logging

# Lazy imports keep Celery task registration from loading the HTTP adapter.
# ruff: noqa: PLC0415
# pylint: disable=cyclic-import
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from core import models
from core.mastrao_transcription_artifact import (
    delete_transcript_object,
    recover_persisted_transcript,
)
from core.mastrao_transcription_attempt import (
    cleanup_attempt_recovery,
    mark_succeeded,
    mark_terminal,
)
from core.mastrao_transcription_contract import (
    TranscriptionContractRefused,
    TranscriptionPipelineFailed,
)

DispatchState = models.MastraoTranscriptionEffect.DispatchState
EffectState = models.MastraoTranscriptionEffect.State
BindingState = models.MastraoTranscriptionBinding.State
RETRYABLE_OUTCOMES = {"retry"}
CLEANUP_OUTCOMES = {"failed", "deleted", "conflict"}
INCIDENT_OUTCOMES = {"conflict"}
RECONCILE_BACKOFF_SECONDS = 5
RECONCILE_BACKOFF_CAP_SECONDS = 300
PRE_EGRESS_RETRY_LIMIT = 8
logger = logging.getLogger(__name__)


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
            queue=getattr(
                settings, "MASTRAO_TRANSCRIPTION_QUEUE", "mastrao-transcription"
            ),
            ignore_result=True,
        )
    except Exception as error:  # pylint: disable=broad-exception-caught  # noqa: BLE001
        # The exception text can contain broker coordinates. Keep diagnostics
        # useful without leaking connection material into centralized logs.
        logger.warning(
            "Mastrao transcription dispatch failed (%s)",
            type(error).__name__,
        )
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
    """Republish due pending, notifying or crash-expired transcription jobs."""

    now = timezone.now()
    due_states = [
        DispatchState.DISPATCH_PENDING,
        DispatchState.QUEUED,
        DispatchState.CLEANUP_PENDING,
        DispatchState.ARTIFACT_NOTIFICATION_PENDING,
        DispatchState.FAILURE_NOTIFICATION_PENDING,
    ]
    with transaction.atomic():
        due = list(
            models.MastraoTranscriptionEffect.objects.select_for_update(
                skip_locked=True
            )
            .filter(dispatch_state__in=due_states, next_attempt_at__lte=now)
            .order_by("next_attempt_at", "updated_at")[:limit]
        )
        stale_before = now - timezone.timedelta(seconds=_pipeline_timeout_seconds())
        stale_running = list(
            models.MastraoTranscriptionEffect.objects.select_for_update(
                skip_locked=True
            )
            .filter(
                dispatch_state=DispatchState.RUNNING,
                updated_at__lte=stale_before,
                next_attempt_at__lte=now,
            )
            .order_by("next_attempt_at", "updated_at")[: max(0, limit - len(due))]
        )
        claimed_pks = []
        for local_effect in [*due, *stale_running]:
            if _reserve_dispatch_attempt(local_effect, now):
                claimed_pks.append(local_effect.pk)
    republished = 0
    for effect_pk in claimed_pks:
        if publish_transcription_job(effect_pk):
            republished += 1
    return republished


def _backoff_seconds(attempt_count):
    delay = RECONCILE_BACKOFF_SECONDS * (2 ** max(0, attempt_count))
    return min(delay, RECONCILE_BACKOFF_CAP_SECONDS)


def _reserve_dispatch_attempt(local_effect, now):
    """CAS-reserve one due row and rotate next_attempt_at before publish."""

    next_state = (
        DispatchState.DISPATCH_PENDING
        if local_effect.dispatch_state == DispatchState.RUNNING
        else local_effect.dispatch_state
    )
    updated = models.MastraoTranscriptionEffect.objects.filter(
        pk=local_effect.pk,
        dispatch_state=local_effect.dispatch_state,
        next_attempt_at=local_effect.next_attempt_at,
    ).update(
        dispatch_state=next_state,
        attempt_count=local_effect.attempt_count + 1,
        next_attempt_at=now
        + timezone.timedelta(seconds=_backoff_seconds(local_effect.attempt_count)),
        updated_at=now,
    )
    return updated == 1


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
    if action == "wait":
        return
    if action != "produce":
        _finish_completion(action, effect, transcription_binding, local_effect)
        return
    if transcription_binding.checksum_digest:
        artifact = _artifact_from_binding(transcription_binding)
        action = _persist_artifact_pending(
            local_effect.pk, transcription_binding.pk, artifact
        )
    else:
        recovered = _recover_predeclared_artifact(transcription_binding, local_effect)
        if recovered is not None:
            artifact = recovered
            action = _persist_artifact_pending(
                local_effect.pk, transcription_binding.pk, artifact
            )
        else:
            try:
                artifact = _produce_transcript(transcription_binding)
            except (TranscriptionPipelineFailed, TranscriptionContractRefused) as error:
                action = _action_after_produce_error(local_effect.pk, error)
                if action is None:
                    return
                local_effect.refresh_from_db()
                transcription_binding.refresh_from_db()
                effect = _effect_from_local(transcription_binding, local_effect)
                _finish_completion(action, effect, transcription_binding, local_effect)
                if action in {"notify_failure", "completed_failed"}:
                    raise
                return
            action = _persist_artifact_pending(
                local_effect.pk, transcription_binding.pk, artifact
            )
    discarded_artifact = (
        artifact if action in {"notify_failure", "completed_failed"} else None
    )
    local_effect.refresh_from_db()
    transcription_binding.refresh_from_db()
    effect = _effect_from_local(transcription_binding, local_effect)
    _finish_completion(
        action,
        effect,
        transcription_binding,
        local_effect,
        discarded_artifact=discarded_artifact,
    )


def _finish_completion(
    action, effect, transcription_binding, local_effect, *, discarded_artifact=None
):
    if action == "retry_later":
        raise TranscriptionContractRefused(status=503)
    if action == "cleanup":
        _finish_cleanup(local_effect.pk)
        return
    if action == "incident":
        return
    if action in {"completed_available", "notify_artifact"}:
        _deliver_artifact(effect, transcription_binding, local_effect)
        return
    if action in {"completed_failed", "notify_failure"}:
        _deliver_failure(
            effect,
            local_effect,
            transcription_binding,
            discarded_artifact=discarded_artifact,
        )


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
        durable = _action_for_dispatch(local_effect)
        if durable is not None:
            return transcription_binding, local_effect, durable
        if (
            local_effect.dispatch_state == DispatchState.DISPATCH_PENDING
            and local_effect.next_attempt_at > timezone.now()
        ):
            return transcription_binding, local_effect, "wait"
        stale_lease = timezone.now() - local_effect.updated_at >= timezone.timedelta(
            seconds=_pipeline_timeout_seconds()
        )
        if local_effect.dispatch_state == DispatchState.RUNNING and not stale_lease:
            return transcription_binding, local_effect, "retry_later"
        local_effect.dispatch_state = DispatchState.RUNNING
        local_effect.save(update_fields=["dispatch_state", "updated_at"])
        return transcription_binding, local_effect, "produce"


def _recover_predeclared_artifact(transcription_binding, local_effect):
    object_ref = transcription_binding.object_ref
    engine_ref = transcription_binding.engine_ref
    if not object_ref:
        attempt = (
            models.MastraoTranscriptionProviderAttempt.objects.filter(
                effect=local_effect, generation=1
            )
            .order_by("created_at")
            .first()
        )
        if attempt is None or not attempt.transcript_object_ref:
            return None
        object_ref = attempt.transcript_object_ref
        engine_ref = attempt.resolved_model_ref or attempt.requested_model_ref
    if not engine_ref:
        engine_ref = "fake-asr-deterministic-v1"
    return recover_persisted_transcript(
        object_ref,
        transcription_binding.transcription_ref,
        engine_ref,
    )


def _action_after_produce_error(effect_pk, error):
    """Notify Core for terminal failures; defer only typed retryable outcomes."""

    if isinstance(error, TranscriptionPipelineFailed):
        return _persist_failure_pending(effect_pk, error.failure_code)
    terminal = error.outcome in {"unknown", "deleted", "conflict"} or error.status in {
        404,
        409,
    }
    retryable = error.outcome in {"failed_pre_egress", "retry"} or (
        error.status == 503 and error.outcome is None
    )
    if not terminal and retryable and _defer_pre_egress_retry(effect_pk, error):
        return None
    return _persist_failure_pending(effect_pk, "asr_failed")


def _action_for_dispatch(locked_effect):
    dispatch = locked_effect.dispatch_state
    if dispatch == DispatchState.COMPLETED:
        if locked_effect.state == EffectState.FAILED:
            return "completed_failed"
        if locked_effect.state == EffectState.APPLIED:
            return "completed_available"
        return "incident"
    return {
        DispatchState.ARTIFACT_NOTIFICATION_PENDING: "notify_artifact",
        DispatchState.FAILURE_NOTIFICATION_PENDING: "notify_failure",
        DispatchState.CLEANUP_PENDING: "cleanup",
    }.get(dispatch)


def _remember_discarded_object(locked_binding, artifact):
    object_ref = artifact.get("object_ref") if artifact else None
    if locked_binding.checksum_digest is not None or not object_ref:
        return
    locked_binding.object_ref = object_ref
    locked_binding.save(update_fields=["object_ref", "updated_at"])


def _persist_artifact_pending(effect_pk, binding_pk, artifact):
    with transaction.atomic():
        locked_effect = (
            models.MastraoTranscriptionEffect.objects.select_for_update().get(
                pk=effect_pk
            )
        )
        durable = _action_for_dispatch(locked_effect)
        if durable in {"notify_failure", "completed_failed"}:
            locked_binding = (
                models.MastraoTranscriptionBinding.objects.select_for_update().get(
                    pk=binding_pk
                )
            )
            _remember_discarded_object(locked_binding, artifact)
            return durable
        if durable is not None:
            return durable
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
        return "notify_artifact"


def _persist_failure_pending(effect_pk, failure_code):
    with transaction.atomic():
        locked_effect = (
            models.MastraoTranscriptionEffect.objects.select_for_update().get(
                pk=effect_pk
            )
        )
        durable = _action_for_dispatch(locked_effect)
        if durable is not None:
            return durable
        locked_effect.failure_code = failure_code
        locked_effect.dispatch_state = DispatchState.FAILURE_NOTIFICATION_PENDING
        locked_effect.save(
            update_fields=["failure_code", "dispatch_state", "updated_at"]
        )
        return "notify_failure"


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
        locked_effect.dispatch_state = DispatchState.CLEANUP_PENDING
        locked_effect.applied_at = timezone.now()
        locked_effect.save(
            update_fields=["state", "dispatch_state", "applied_at", "updated_at"]
        )
        locked_binding.state = BindingState.AVAILABLE
        locked_binding.save(update_fields=["state", "updated_at"])
    _mark_effect_succeeded(effect_pk)
    _finish_cleanup(effect_pk)


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
        locked_effect.dispatch_state = DispatchState.CLEANUP_PENDING
        locked_effect.save(update_fields=["state", "dispatch_state", "updated_at"])
        locked_binding = (
            models.MastraoTranscriptionBinding.objects.select_for_update().get(
                pk=binding_pk
            )
        )
        locked_binding.state = BindingState.FAILED
        locked_binding.save(update_fields=["state", "updated_at"])
    _finish_cleanup(effect_pk)


def _commit_incident(effect_pk):
    with transaction.atomic():
        locked_effect = (
            models.MastraoTranscriptionEffect.objects.select_for_update().get(
                pk=effect_pk
            )
        )
        if locked_effect.dispatch_state == DispatchState.COMPLETED:
            return
        locked_effect.dispatch_state = DispatchState.CLEANUP_PENDING
        locked_effect.save(update_fields=["dispatch_state", "updated_at"])
    _finish_cleanup(effect_pk)


def _finish_cleanup(effect_pk):
    _ack_accepted_gateway_result(effect_pk)
    _cleanup_effect_recovery(effect_pk)
    with transaction.atomic():
        locked_effect = (
            models.MastraoTranscriptionEffect.objects.select_for_update().get(
                pk=effect_pk
            )
        )
        if locked_effect.dispatch_state == DispatchState.COMPLETED:
            return
        locked_effect.dispatch_state = DispatchState.COMPLETED
        locked_effect.save(update_fields=["dispatch_state", "updated_at"])


def _ack_accepted_gateway_result(effect_pk):
    """ACK only after Core acceptance; failure leaves cleanup replayable."""

    effect = models.MastraoTranscriptionEffect.objects.filter(pk=effect_pk).first()
    if effect is None or effect.state not in {EffectState.APPLIED, EffectState.FAILED}:
        return
    attempt = (
        models.MastraoTranscriptionProviderAttempt.objects.filter(
            effect_id=effect_pk,
            generation=1,
            provider_ref__in=("mistral", "openai"),
        )
        .order_by("created_at")
        .first()
    )
    if attempt is None or not attempt.result_checksum:
        return
    # pylint: disable=import-outside-toplevel
    from core.mastrao_transcription_worker import ack_gateway_attempt

    ack_gateway_attempt(attempt)


def _callback_outcome(error):
    outcome = getattr(error, "outcome", None)
    if outcome in RETRYABLE_OUTCOMES | CLEANUP_OUTCOMES | INCIDENT_OUTCOMES:
        return outcome
    if error.status == 503:
        return "retry"
    if error.status == 404:
        return "deleted"
    if error.status == 409:
        return "conflict"
    return "retry"


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
        outcome = _callback_outcome(error)
        if outcome in RETRYABLE_OUTCOMES:
            raise
        if outcome == "available":
            _commit_available(local_effect.pk, transcription_binding.pk)
            return
        if effect.get("operation_version") == 3:
            attempt = (
                models.MastraoTranscriptionProviderAttempt.objects.filter(
                    effect_id=local_effect.pk,
                    generation=1,
                    provider_ref__in=("mistral", "openai"),
                )
                .order_by("created_at")
                .first()
            )
            if attempt is not None and attempt.grant_semantic_digest:
                terminal_outcome = "conflict" if outcome == "conflict" else "deleted"
                mark_terminal(attempt, terminal_outcome)
                from core.mastrao_transcription_adapter import _notify_core_failure

                result = _notify_core_failure(effect, "asr_failed")
                if result["state"] == "available" and result["outcome"] == "available":
                    _commit_available(local_effect.pk, transcription_binding.pk)
                    return
        if outcome in CLEANUP_OUTCOMES and artifact.get("object_ref"):
            delete_transcript_object(artifact["object_ref"])
        _commit_failed(local_effect.pk, transcription_binding.pk)
        return
    _commit_available(local_effect.pk, transcription_binding.pk)


def _discarded_object_ref(transcription_binding, discarded_artifact):
    if transcription_binding.checksum_digest:
        return None
    if discarded_artifact and discarded_artifact.get("object_ref"):
        return discarded_artifact["object_ref"]
    return transcription_binding.object_ref


def _cleanup_discarded_transcript(transcription_binding, discarded_artifact):
    object_ref = _discarded_object_ref(transcription_binding, discarded_artifact)
    if object_ref:
        delete_transcript_object(object_ref)


def _defer_pre_egress_retry(effect_pk, error=None):
    """Keep a proven pre-egress failure local until retries are exhausted."""

    now = timezone.now()
    retry_after = getattr(error, "retry_after_seconds", None)
    with transaction.atomic():
        locked = models.MastraoTranscriptionEffect.objects.select_for_update().get(
            pk=effect_pk
        )
        if locked.dispatch_state == DispatchState.COMPLETED:
            return False
        if retry_after is not None:
            due_at = now + timezone.timedelta(seconds=max(1, int(retry_after)))
            locked.dispatch_state = DispatchState.DISPATCH_PENDING
            if locked.next_attempt_at > now:
                locked.next_attempt_at = max(locked.next_attempt_at, due_at)
                locked.save(
                    update_fields=["dispatch_state", "next_attempt_at", "updated_at"]
                )
                return True
            if locked.attempt_count >= PRE_EGRESS_RETRY_LIMIT:
                return False
            locked.attempt_count = locked.attempt_count + 1
            locked.next_attempt_at = due_at
            locked.save(
                update_fields=[
                    "dispatch_state",
                    "attempt_count",
                    "next_attempt_at",
                    "updated_at",
                ]
            )
            return True
        if locked.attempt_count >= PRE_EGRESS_RETRY_LIMIT:
            return False
        locked.dispatch_state = DispatchState.DISPATCH_PENDING
        locked.attempt_count = locked.attempt_count + 1
        locked.next_attempt_at = now + timezone.timedelta(
            seconds=_backoff_seconds(locked.attempt_count)
        )
        locked.save(
            update_fields=[
                "dispatch_state",
                "attempt_count",
                "next_attempt_at",
                "updated_at",
            ]
        )
        return True


def _mark_effect_succeeded(effect_pk):
    """Mark the provider attempt succeeded only after Core accepts the artifact."""
    attempt = (
        models.MastraoTranscriptionProviderAttempt.objects.filter(
            effect_id=effect_pk, generation=1
        )
        .order_by("created_at")
        .first()
    )
    if attempt is None:
        return
    mark_succeeded(attempt)


def _cleanup_effect_recovery(effect_pk):
    attempt = (
        models.MastraoTranscriptionProviderAttempt.objects.filter(
            effect_id=effect_pk, generation=1
        )
        .order_by("created_at")
        .first()
    )
    if attempt is None:
        return
    cleanup_attempt_recovery(attempt)


def _deliver_failure(
    effect, local_effect, transcription_binding, *, discarded_artifact=None
):
    # pylint: disable=import-outside-toplevel
    from core.mastrao_transcription_adapter import _notify_core_failure

    try:
        result = _notify_core_failure(effect, local_effect.failure_code or "asr_failed")
    except TranscriptionContractRefused as error:
        outcome = _callback_outcome(error)
        if outcome in RETRYABLE_OUTCOMES:
            raise
        if outcome == "available":
            _commit_available(local_effect.pk, transcription_binding.pk)
            return
        if outcome in INCIDENT_OUTCOMES:
            _commit_incident(local_effect.pk)
            return
        _cleanup_discarded_transcript(transcription_binding, discarded_artifact)
        _commit_failed(local_effect.pk, transcription_binding.pk)
        return
    if result["outcome"] == "available":
        _commit_available(local_effect.pk, transcription_binding.pk)
        return
    _cleanup_discarded_transcript(transcription_binding, discarded_artifact)
    _commit_failed(local_effect.pk, transcription_binding.pk)
