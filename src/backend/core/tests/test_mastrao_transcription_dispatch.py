"""Transcription dispatch reconciliation, redelivery and callback recovery."""

import threading
from unittest import mock

from django.utils import timezone

import pytest

from core import models
from core.mastrao_transcription_adapter import _apply_transcription
from core.mastrao_transcription_contract import (
    TranscriptionContractRefused,
    TranscriptionPipelineFailed,
)
from core.mastrao_transcription_pipeline import (
    complete_transcription,
    reconcile_transcription_dispatches,
)
from core.tests import test_mastrao_transcription as submission_proofs
from core.tests.test_mastrao_transcription import (
    CORE_FAILED_OUTCOME,
    ENQUEUE,
    _effect,
    _fake_artifact,
    _finalized_recording_binding,
)

pytestmark = pytest.mark.django_db
transcription_settings = submission_proofs.transcription_settings


@pytest.mark.django_db(transaction=True)
def test_failure_callback_before_submit_confirmation_is_retryable():
    """Failure callback before submit confirmation is retryable."""

    binding = _finalized_recording_binding("earlyfail_01234567")
    effect = _effect(binding)
    core_confirmed = threading.Event()
    first_503 = threading.Event()
    notify_attempts = []
    worker = {}

    def notify(*_args, **_kwargs):
        notify_attempts.append(1)
        if not core_confirmed.is_set():
            first_503.set()
            raise TranscriptionContractRefused(status=503, outcome="retry")
        return CORE_FAILED_OUTCOME

    def apply_async(args=None, **_kwargs):
        def run():
            try:
                complete_transcription(args[0])
            except TranscriptionContractRefused as error:
                assert error.status == 503
                first_503.set()
                core_confirmed.wait(timeout=5)
                complete_transcription(args[0])

        thread = threading.Thread(target=run)
        worker["thread"] = thread
        thread.start()
        assert first_503.wait(timeout=5)

    with (
        mock.patch(ENQUEUE, side_effect=apply_async),
        mock.patch(
            "core.mastrao_transcription_adapter.sign_submit_receipt",
            return_value="receipt.payload.signature",
        ),
        mock.patch(
            "core.mastrao_transcription_adapter._produce_transcript",
            side_effect=TranscriptionPipelineFailed("asr_failed"),
        ) as produce,
        mock.patch(
            "core.mastrao_transcription_adapter._notify_core_failure",
            side_effect=notify,
        ),
    ):
        _apply_transcription(effect)
        core_confirmed.set()
        worker["thread"].join(timeout=5)
    assert worker["thread"].is_alive() is False
    produce.assert_called_once()
    assert len(notify_attempts) >= 2
    local_effect = models.MastraoTranscriptionEffect.objects.get()
    assert local_effect.dispatch_state == (
        models.MastraoTranscriptionEffect.DispatchState.COMPLETED
    )
    assert local_effect.state == models.MastraoTranscriptionEffect.State.FAILED


def _dispatch_row(suffix, dispatch_state, next_attempt_at):
    binding = _finalized_recording_binding(suffix)
    transcription = models.MastraoTranscriptionBinding.objects.create(
        recording_binding=binding,
        organization_external_id=binding.organization_external_id,
        meeting_ref=binding.meeting_ref,
        room_ref=binding.room_ref,
        recording_ref=binding.recording_ref,
        transcription_ref=f"transcription_{suffix}"[:32].ljust(32, "0"),
        artifact_ref=binding.artifact_ref,
        provider_binding_digest=binding.provider_binding_digest,
        artifact_checksum_digest=binding.checksum_digest,
        artifact_byte_size=binding.byte_size,
    )
    return models.MastraoTranscriptionEffect.objects.create(
        transcription_binding=transcription,
        effect_key=f"effect_transcribe_{suffix}",
        arguments_digest="e" * 64,
        effect_jti=f"request_transcribe_{suffix}",
        dispatch_state=dispatch_state,
        next_attempt_at=next_attempt_at,
    )


def test_reconcile_skips_rows_that_are_not_due():
    """Reconcile skips rows that are not due."""

    now = timezone.now()
    due = _dispatch_row(
        "due000000000000001",
        models.MastraoTranscriptionEffect.DispatchState.DISPATCH_PENDING,
        now,
    )
    _dispatch_row(
        "later0000000000001",
        models.MastraoTranscriptionEffect.DispatchState.DISPATCH_PENDING,
        now + timezone.timedelta(minutes=5),
    )
    published = []

    def fake_publish(effect_pk):
        published.append(effect_pk)
        return True

    with mock.patch(
        "core.mastrao_transcription_pipeline.publish_transcription_job",
        side_effect=fake_publish,
    ):
        assert reconcile_transcription_dispatches(limit=10) == 1
    assert published == [due.pk]
    due.refresh_from_db()
    assert due.next_attempt_at > now
    assert due.attempt_count == 1


def test_reconcile_poison_rows_do_not_starve_healthy_rows():
    """Reconcile poison rows do not starve healthy rows."""

    now = timezone.now()
    poison = [
        _dispatch_row(
            f"poison{index:012d}xx",
            models.MastraoTranscriptionEffect.DispatchState.QUEUED,
            now,
        )
        for index in range(3)
    ]
    healthy = [
        _dispatch_row(
            f"healthy{index:011d}x",
            models.MastraoTranscriptionEffect.DispatchState.QUEUED,
            now,
        )
        for index in range(3)
    ]
    published = []

    def fake_publish(effect_pk):
        local_effect = models.MastraoTranscriptionEffect.objects.get(pk=effect_pk)
        if local_effect.effect_key.startswith("effect_transcribe_poison"):
            return False
        published.append(local_effect.pk)
        return True

    with mock.patch(
        "core.mastrao_transcription_pipeline.publish_transcription_job",
        side_effect=fake_publish,
    ):
        for _ in range(6):
            models.MastraoTranscriptionEffect.objects.filter(
                pk__in=[row.pk for row in poison + healthy],
                next_attempt_at__gt=timezone.now(),
            ).update(next_attempt_at=timezone.now())
            reconcile_transcription_dispatches(limit=2)
    assert set(published) == {row.pk for row in healthy}


def test_broker_failure_keeps_the_effect_dispatchable():
    """Broker failure keeps the effect dispatchable."""

    binding = _finalized_recording_binding("brokerfail_01234567")
    effect = _effect(binding)
    with (
        mock.patch(ENQUEUE, side_effect=ConnectionError("broker")) as enqueue,
        mock.patch(
            "core.mastrao_transcription_adapter.sign_submit_receipt",
            return_value="receipt.payload.signature",
        ),
    ):
        _apply_transcription(effect)
        local_effect = models.MastraoTranscriptionEffect.objects.get()
        assert (
            local_effect.dispatch_state
            == models.MastraoTranscriptionEffect.DispatchState.DISPATCH_PENDING
        )
    enqueue.side_effect = None
    with (
        mock.patch(ENQUEUE) as replay,
        mock.patch(
            "core.mastrao_transcription_adapter.sign_submit_receipt",
            return_value="receipt.payload.signature",
        ),
        mock.patch(
            "core.mastrao_transcription_adapter._produce_transcript",
            return_value=_fake_artifact(),
        ) as produce,
        mock.patch("core.mastrao_transcription_adapter._notify_core_artifact"),
    ):
        _apply_transcription(effect)
        complete_transcription(local_effect.pk)
    replay.assert_called_once()
    produce.assert_called_once()
    local_effect.refresh_from_db()
    assert local_effect.dispatch_state == (
        models.MastraoTranscriptionEffect.DispatchState.COMPLETED
    )
    assert models.MastraoTranscriptionBinding.objects.get().state == (
        models.MastraoTranscriptionBinding.State.AVAILABLE
    )


def test_crash_after_publish_before_local_confirmation_redelivers():
    """Crash after publish before local confirmation redelivers."""

    binding = _finalized_recording_binding("crashpub_012345678")
    effect = _effect(binding)
    with (
        mock.patch(ENQUEUE),
        mock.patch(
            "core.mastrao_transcription_adapter.sign_submit_receipt",
            return_value="receipt.payload.signature",
        ),
        mock.patch(
            "core.mastrao_transcription_adapter._produce_transcript",
            return_value=_fake_artifact(),
        ) as produce,
        mock.patch("core.mastrao_transcription_adapter._notify_core_artifact"),
    ):
        _apply_transcription(effect)
        local_effect = models.MastraoTranscriptionEffect.objects.get()
        local_effect.dispatch_state = (
            models.MastraoTranscriptionEffect.DispatchState.DISPATCH_PENDING
        )
        local_effect.save(update_fields=["dispatch_state", "updated_at"])
        _apply_transcription(effect)
        complete_transcription(local_effect.pk)
        complete_transcription(local_effect.pk)
    produce.assert_called_once()
    local_effect.refresh_from_db()
    assert local_effect.dispatch_state == (
        models.MastraoTranscriptionEffect.DispatchState.COMPLETED
    )


def test_artifact_callback_503_then_success_does_not_rerun_asr():
    """Artifact callback 503 then success does not rerun asr."""

    binding = _finalized_recording_binding("retry503_012345678")
    effect = _effect(binding)
    notify_attempts = []

    def notify(*_args, **_kwargs):
        notify_attempts.append(1)
        if len(notify_attempts) == 1:
            raise TranscriptionContractRefused(status=503)

    with (
        mock.patch(ENQUEUE),
        mock.patch(
            "core.mastrao_transcription_adapter.sign_submit_receipt",
            return_value="receipt.payload.signature",
        ),
        mock.patch(
            "core.mastrao_transcription_adapter._produce_transcript",
            return_value=_fake_artifact(),
        ) as produce,
        mock.patch(
            "core.mastrao_transcription_adapter._notify_core_artifact",
            side_effect=notify,
        ),
    ):
        _apply_transcription(effect)
        local_effect = models.MastraoTranscriptionEffect.objects.get()
        with pytest.raises(TranscriptionContractRefused) as refusal:
            complete_transcription(local_effect.pk)
        assert refusal.value.status == 503
        local_effect.refresh_from_db()
        assert local_effect.dispatch_state == (
            models.MastraoTranscriptionEffect.DispatchState.ARTIFACT_NOTIFICATION_PENDING
        )
        complete_transcription(local_effect.pk)
    produce.assert_called_once()
    assert len(notify_attempts) == 2
    assert models.MastraoTranscriptionBinding.objects.get().state == (
        models.MastraoTranscriptionBinding.State.AVAILABLE
    )


def test_failure_callback_503_then_success_marks_failed():
    """Failure callback 503 then success marks failed."""

    binding = _finalized_recording_binding("fail503_0123456789")
    effect = _effect(binding)
    notify_attempts = []

    def notify(*_args, **_kwargs):
        notify_attempts.append(1)
        if len(notify_attempts) == 1:
            raise TranscriptionContractRefused(status=503)
        return CORE_FAILED_OUTCOME

    with (
        mock.patch(ENQUEUE),
        mock.patch(
            "core.mastrao_transcription_adapter.sign_submit_receipt",
            return_value="receipt.payload.signature",
        ),
        mock.patch(
            "core.mastrao_transcription_adapter._produce_transcript",
            side_effect=TranscriptionPipelineFailed("asr_failed"),
        ) as produce,
        mock.patch(
            "core.mastrao_transcription_adapter._notify_core_failure",
            side_effect=notify,
        ),
    ):
        _apply_transcription(effect)
        local_effect = models.MastraoTranscriptionEffect.objects.get()
        with pytest.raises(TranscriptionContractRefused) as refusal:
            complete_transcription(local_effect.pk)
        assert refusal.value.status == 503
        local_effect.refresh_from_db()
        assert local_effect.dispatch_state == (
            models.MastraoTranscriptionEffect.DispatchState.FAILURE_NOTIFICATION_PENDING
        )
        complete_transcription(local_effect.pk)
    produce.assert_called_once()
    assert len(notify_attempts) == 2
    local_effect.refresh_from_db()
    assert local_effect.state == models.MastraoTranscriptionEffect.State.FAILED
    assert models.MastraoTranscriptionBinding.objects.get().state == (
        models.MastraoTranscriptionBinding.State.FAILED
    )


def test_redelivery_after_object_write_skips_asr():
    """Redelivery after object write skips asr."""

    binding = _finalized_recording_binding("redeliver_01234567")
    effect = _effect(binding)
    notify_attempts = []

    def notify(*_args, **_kwargs):
        notify_attempts.append(1)
        if len(notify_attempts) == 1:
            raise TranscriptionContractRefused(status=503)

    with (
        mock.patch(ENQUEUE),
        mock.patch(
            "core.mastrao_transcription_adapter.sign_submit_receipt",
            return_value="receipt.payload.signature",
        ),
        mock.patch(
            "core.mastrao_transcription_adapter._produce_transcript",
            return_value=_fake_artifact(),
        ) as produce,
        mock.patch(
            "core.mastrao_transcription_adapter._notify_core_artifact",
            side_effect=notify,
        ),
    ):
        _apply_transcription(effect)
        local_effect = models.MastraoTranscriptionEffect.objects.get()
        with pytest.raises(TranscriptionContractRefused):
            complete_transcription(local_effect.pk)
        assert models.MastraoTranscriptionBinding.objects.get().checksum_digest == (
            "9" * 64
        )
        complete_transcription(local_effect.pk)
    produce.assert_called_once()
    assert len(notify_attempts) == 2


@pytest.mark.django_db(transaction=True)
def test_worker_callback_before_submit_confirmation_is_retryable():
    """Worker callback before submit confirmation is retryable."""

    binding = _finalized_recording_binding("earlycb_0123456789")
    effect = _effect(binding)
    core_confirmed = threading.Event()
    first_503 = threading.Event()
    notify_attempts = []
    worker = {}

    def notify(*_args, **_kwargs):
        notify_attempts.append(1)
        if not core_confirmed.is_set():
            first_503.set()
            raise TranscriptionContractRefused(status=503)

    def apply_async(args=None, **_kwargs):
        def run():
            try:
                complete_transcription(args[0])
            except TranscriptionContractRefused as error:
                assert error.status == 503
                first_503.set()
                core_confirmed.wait(timeout=5)
                complete_transcription(args[0])

        thread = threading.Thread(target=run)
        worker["thread"] = thread
        thread.start()
        assert first_503.wait(timeout=5)

    with (
        mock.patch(ENQUEUE, side_effect=apply_async),
        mock.patch(
            "core.mastrao_transcription_adapter.sign_submit_receipt",
            return_value="receipt.payload.signature",
        ),
        mock.patch(
            "core.mastrao_transcription_adapter._produce_transcript",
            return_value=_fake_artifact(),
        ) as produce,
        mock.patch(
            "core.mastrao_transcription_adapter._notify_core_artifact",
            side_effect=notify,
        ),
        mock.patch(
            "core.mastrao_transcription_pipeline.delete_transcript_object"
        ) as delete_object,
    ):
        _apply_transcription(effect)
        core_confirmed.set()
        worker["thread"].join(timeout=5)
    assert worker["thread"].is_alive() is False
    produce.assert_called_once()
    assert len(notify_attempts) >= 2
    delete_object.assert_not_called()
    assert models.MastraoTranscriptionBinding.objects.get().state == (
        models.MastraoTranscriptionBinding.State.AVAILABLE
    )
