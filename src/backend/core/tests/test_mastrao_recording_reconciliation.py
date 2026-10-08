"""Recording start retries, stop delivery and reconciliation proofs."""

from datetime import timedelta
from types import SimpleNamespace
from unittest import mock

from django.utils import timezone

import pytest
from livekit.protocol.egress import EgressStatus

from core import models
from core.factories import RoomFactory, UserFactory
from core.mastrao_recording_adapter import _apply_start, _apply_stop, _prepare_start
from core.mastrao_recording_contract import RecordingContractRefused
from core.mastrao_recording_failure import report_mastrao_recording_failure
from core.mastrao_recording_reconciler import (
    reconcile_mastrao_recording,
    reconcile_mastrao_recordings,
)
from core.models import RoomAccessLevel
from core.recording.worker.exceptions import RecordingStopError
from core.tests import test_mastrao_recording_consent as consent_proofs
from core.tests.test_mastrao_recording_artifacts import _artifact_access

recording_rollout_settings = consent_proofs.recording_rollout_settings


def _provider_egress(recording, status):
    return SimpleNamespace(
        egress_id="EG_oju7PDAhx8k7",
        room_name=str(recording.room_id),
        status=status,
        room_composite=SimpleNamespace(
            file_outputs=[SimpleNamespace(filepath=recording.key)]
        ),
    )


@pytest.mark.usefixtures("db")
def test_start_retry_discovers_exact_egress_without_starting_again(settings):
    """Reuse the exact existing egress on a retried start after rollout closes."""

    settings.MASTRAO_MEETING_RECORDING_ENABLED = True
    owner = UserFactory()
    room = RoomFactory(access_level=RoomAccessLevel.RESTRICTED)
    room_binding = models.MastraoRoomBinding.objects.create(
        effect_key="effect_room_retry_012345",
        arguments_digest="a" * 64,
        meeting_ref="meeting_retry_0123456789",
        room_ref="room_retry_0123456789abcd",
        owner_ref="owner_retry_0123456789ab",
        room=room,
        owner=owner,
        provider_binding_digest="b" * 64,
    )
    effect = {
        "organization_external_id": "organization_0123456789",
        "meeting_ref": room_binding.meeting_ref,
        "room_ref": room_binding.room_ref,
        "recording_ref": "recording_retry_0123456789",
        "provider_binding_digest": room_binding.provider_binding_digest,
        "policy_ref": "policy_retry_0123456789abc",
        "notice_version": "notice_retry_0123456789ab",
        "notice_digest": "c" * 64,
        "purpose": "meeting_recording",
        "scope": "room_composite_audio_video_screen",
        "retention_expires_at": int((timezone.now() + timedelta(days=30)).timestamp()),
        "effect_key": "effect_start_retry_012345",
        "arguments_digest": "d" * 64,
        "resolve_only": False,
        "jti": "request_start_retry_012345",
    }
    binding, _, _ = _prepare_start(effect)
    settings.MASTRAO_MEETING_RECORDING_START_ENABLED = False
    provider = _provider_egress(binding.recording, EgressStatus.EGRESS_ACTIVE)
    with (
        mock.patch(
            "core.mastrao_recording_adapter._exact_provider_egress",
            return_value=provider,
        ),
        mock.patch(
            "core.mastrao_recording_adapter.WorkerServiceMediator.start"
        ) as start,
        mock.patch(
            "core.mastrao_recording_adapter.sign_start_receipt",
            return_value="receipt.payload.signature",
        ),
    ):
        assert _apply_start(effect) == "receipt.payload.signature"
    start.assert_not_called()
    binding.refresh_from_db()
    assert binding.provider_recording_ref == provider.egress_id
    assert models.Recording.objects.filter(room=room).count() == 1


def test_resolve_only_start_without_provider_converges_after_grace_period():
    """Report failure after the grace period when resolve-only finds no egress."""

    recording = SimpleNamespace(status=models.RecordingStatusChoices.INITIATED)
    recording_binding = SimpleNamespace(recording_id="recording-id")
    local_effect = SimpleNamespace(
        state=models.MastraoRecordingEffect.State.APPLYING,
        created_at=timezone.now() - timedelta(seconds=31),
    )
    effect = {"resolve_only": True}
    with (
        mock.patch(
            "core.mastrao_recording_adapter._prepare_start",
            return_value=(recording_binding, local_effect, False),
        ),
        mock.patch(
            "core.mastrao_recording_adapter.models.Recording.objects.select_related"
        ) as recordings,
        mock.patch(
            "core.mastrao_recording_adapter._exact_provider_egress",
            return_value=None,
        ),
        mock.patch(
            "core.mastrao_recording_adapter.report_mastrao_recording_failure"
        ) as report_failure,
    ):
        recordings.return_value.get.return_value = recording
        with pytest.raises(RecordingContractRefused) as refusal:
            _apply_start(effect)
    assert refusal.value.status == 503
    report_failure.assert_called_once_with(recording, None)


@pytest.mark.usefixtures("db")
def test_stop_response_loss_reconciles_terminal_exact_egress():
    """Converge a lost stop response from the exact terminal provider egress."""

    access, _ = _artifact_access()
    binding = access.recording_binding
    recording = binding.recording
    recording.status = models.RecordingStatusChoices.ACTIVE
    recording.worker_id = "EG_oju7PDAhx8k7"
    recording.save(update_fields=["status", "worker_id", "updated_at"])
    binding.state = models.MastraoRecordingBinding.State.ACTIVE
    binding.provider_recording_ref = recording.worker_id
    binding.save(update_fields=["state", "provider_recording_ref", "updated_at"])
    effect = {
        "organization_external_id": binding.organization_external_id,
        "meeting_ref": binding.meeting_ref,
        "room_ref": binding.room_ref,
        "recording_ref": binding.recording_ref,
        "provider_binding_digest": binding.provider_binding_digest,
        "provider_recording_ref": recording.worker_id,
        "effect_key": "effect_stop_retry_0123456",
        "arguments_digest": "e" * 64,
        "jti": "request_stop_retry_012345",
    }
    active = _provider_egress(recording, EgressStatus.EGRESS_ACTIVE)
    terminal = _provider_egress(recording, EgressStatus.EGRESS_COMPLETE)
    with (
        mock.patch(
            "core.mastrao_recording_adapter._exact_provider_egress",
            side_effect=[active, terminal],
        ),
        mock.patch(
            "core.mastrao_recording_adapter.WorkerServiceMediator.stop",
            side_effect=RecordingStopError(),
        ),
        mock.patch(
            "core.mastrao_recording_adapter.sign_stop_receipt",
            return_value="receipt.payload.signature",
        ),
    ):
        assert _apply_stop(effect) == "receipt.payload.signature"
    binding.refresh_from_db()
    assert binding.state == models.MastraoRecordingBinding.State.PROCESSING


@pytest.mark.usefixtures("db")
def test_stop_retry_from_applying_reissues_exact_active_egress():
    """Retry the stop for the exact active egress of an applying effect."""

    access, _ = _artifact_access()
    binding = access.recording_binding
    recording = binding.recording
    recording.status = models.RecordingStatusChoices.ACTIVE
    recording.worker_id = "EG_oju7PDAhx8k7"
    recording.save(update_fields=["status", "worker_id", "updated_at"])
    binding.state = models.MastraoRecordingBinding.State.STOPPING
    binding.provider_recording_ref = recording.worker_id
    binding.save(update_fields=["state", "provider_recording_ref", "updated_at"])
    effect = {
        "organization_external_id": binding.organization_external_id,
        "meeting_ref": binding.meeting_ref,
        "room_ref": binding.room_ref,
        "recording_ref": binding.recording_ref,
        "provider_binding_digest": binding.provider_binding_digest,
        "provider_recording_ref": recording.worker_id,
        "effect_key": "effect_stop_applying_012345",
        "arguments_digest": "f" * 64,
        "jti": "request_stop_applying_012345",
    }
    models.MastraoRecordingEffect.objects.create(
        recording_binding=binding,
        effect_key=effect["effect_key"],
        operation=models.MastraoRecordingEffect.Operation.STOP,
        arguments_digest=effect["arguments_digest"],
        effect_jti=effect["jti"],
        state=models.MastraoRecordingEffect.State.APPLYING,
    )
    active = _provider_egress(recording, EgressStatus.EGRESS_ACTIVE)
    with (
        mock.patch(
            "core.mastrao_recording_adapter._exact_provider_egress",
            return_value=active,
        ),
        mock.patch("core.mastrao_recording_adapter.WorkerServiceMediator.stop") as stop,
        mock.patch(
            "core.mastrao_recording_adapter.sign_stop_receipt",
            return_value="receipt.payload.signature",
        ),
    ):
        assert _apply_stop(effect) == "receipt.payload.signature"
    stop.assert_called_once_with(recording)
    binding.refresh_from_db()
    assert binding.state == models.MastraoRecordingBinding.State.PROCESSING


@pytest.mark.usefixtures("db")
def test_missing_provider_failure_webhook_converges_via_reconciler(settings):
    """Report provider failure through reconciliation without a webhook."""

    access, _ = _artifact_access()
    binding = access.recording_binding
    recording = binding.recording
    recording.status = models.RecordingStatusChoices.ACTIVE
    recording.worker_id = "EG_oju7PDAhx8k7"
    recording.save(update_fields=["status", "worker_id", "updated_at"])
    binding.state = models.MastraoRecordingBinding.State.ACTIVE
    binding.provider_recording_ref = recording.worker_id
    binding.save(update_fields=["state", "provider_recording_ref", "updated_at"])
    settings.MASTRAO_CORE_RECORDING_FAILURE_ENDPOINT = (
        "http://cabinet-core:3911/internal/v1/meetings/recording/failures"
    )
    failed = _provider_egress(recording, EgressStatus.EGRESS_FAILED)
    with (
        mock.patch(
            "core.mastrao_recording_reconciler._exact_provider_egress",
            return_value=failed,
        ),
        mock.patch(
            "core.mastrao_recording_failure.sign_failure_receipt",
            return_value="failure.payload.signature",
        ),
        mock.patch(
            "core.mastrao_recording_failure.post_core_json",
            return_value={"recordingRef": binding.recording_ref, "state": "failed"},
        ) as post_core_json,
    ):
        assert reconcile_mastrao_recording(binding)
    binding.refresh_from_db()
    assert binding.state == models.MastraoRecordingBinding.State.FAILED
    post_core_json.assert_called_once_with(
        endpoint=settings.MASTRAO_CORE_RECORDING_FAILURE_ENDPOINT,
        expected_path="/internal/v1/meetings/recording/failures",
        body={"recording_failure_receipt": "failure.payload.signature"},
        timeout=settings.MASTRAO_CORE_RECORDING_TIMEOUT_SECONDS,
        refusal=RecordingContractRefused,
        expected_fields={"recordingRef", "state"},
    )


@pytest.mark.usefixtures("db")
def test_recording_failure_local_stale_core_refusal_tombstones_binding(settings):
    """Tombstone a local binding when Core refuses the stale recording."""

    access, _ = _artifact_access()
    binding = access.recording_binding
    binding.state = models.MastraoRecordingBinding.State.PROCESSING
    binding.provider_recording_ref = "EG_oju7PDAhx8k7"
    binding.save(update_fields=["state", "provider_recording_ref", "updated_at"])
    recording = binding.recording
    recording.worker_id = binding.provider_recording_ref
    recording.save(update_fields=["worker_id", "updated_at"])
    settings.DEBUG = True
    settings.MASTRAO_CORE_RECORDING_FAILURE_ENDPOINT = (
        "http://127.0.0.1.nip.io:3911/internal/v1/meetings/recording/failures"
    )

    with (
        mock.patch(
            "core.mastrao_recording_failure.sign_failure_receipt",
            return_value="failure.payload.signature",
        ),
        mock.patch(
            "core.mastrao_recording_failure.post_core_json",
            side_effect=RecordingContractRefused(status=404),
        ),
    ):
        assert report_mastrao_recording_failure(recording, EgressStatus.EGRESS_ABORTED)

    binding.refresh_from_db()
    assert binding.state == models.MastraoRecordingBinding.State.FAILED


@pytest.mark.usefixtures("db")
def test_recording_failure_core_unavailable_stays_retryable(settings):
    """Preserve the retryable binding when Core is unavailable."""

    access, _ = _artifact_access()
    binding = access.recording_binding
    binding.state = models.MastraoRecordingBinding.State.PROCESSING
    binding.provider_recording_ref = "EG_oju7PDAhx8k7"
    binding.save(update_fields=["state", "provider_recording_ref", "updated_at"])
    recording = binding.recording
    recording.worker_id = binding.provider_recording_ref
    recording.save(update_fields=["worker_id", "updated_at"])
    settings.DEBUG = True
    settings.MASTRAO_CORE_RECORDING_FAILURE_ENDPOINT = (
        "http://127.0.0.1.nip.io:3911/internal/v1/meetings/recording/failures"
    )

    with (
        mock.patch(
            "core.mastrao_recording_failure.sign_failure_receipt",
            return_value="failure.payload.signature",
        ),
        mock.patch(
            "core.mastrao_recording_failure.post_core_json",
            side_effect=RecordingContractRefused(status=503),
        ),
    ):
        with pytest.raises(RecordingContractRefused):
            report_mastrao_recording_failure(recording, EgressStatus.EGRESS_ABORTED)

    binding.refresh_from_db()
    assert binding.state == models.MastraoRecordingBinding.State.PROCESSING


@pytest.mark.usefixtures("db")
def test_reconciler_isolates_one_bad_item_while_rollout_controls_are_off(settings):
    """Continue reconciliation after one item fails with rollout closed."""

    first_access, _ = _artifact_access()
    second_access, _ = _artifact_access("second")
    bindings = [first_access.recording_binding, second_access.recording_binding]
    for index, binding in enumerate(bindings):
        binding.provider_recording_ref = f"EG_rollout_{index}"
        binding.state = models.MastraoRecordingBinding.State.ACTIVE
        binding.save(update_fields=["provider_recording_ref", "state", "updated_at"])
    failed_before = bindings[0].updated_at
    settings.MASTRAO_MEETING_RECORDING_START_ENABLED = False
    settings.MASTRAO_MEETING_RECORDING_ARTIFACT_ACCESS_ENABLED = False

    with (
        mock.patch(
            "core.mastrao_recording_reconciler.reconcile_mastrao_recording",
            side_effect=[RuntimeError("provider unavailable"), True],
        ) as reconcile,
        mock.patch("core.mastrao_recording_reconciler.logger.exception") as logged,
    ):
        assert reconcile_mastrao_recordings(limit=20) == 1

    assert reconcile.call_count == 2
    logged.assert_called_once_with("Mastrao recording reconciliation item failed")
    bindings[0].refresh_from_db()
    assert bindings[0].updated_at > failed_before


@pytest.mark.usefixtures("db")
def test_reconciler_rotates_observed_active_items_before_the_next_batch():
    """Rotate observed items so subsequent batches reach pending recordings."""

    accesses = [_artifact_access(suffix) for suffix in ("first", "second", "third")]
    bindings = [access.recording_binding for access, _retry in accesses]
    for index, binding in enumerate(bindings):
        binding.provider_recording_ref = f"EG_fair_{index}"
        binding.state = models.MastraoRecordingBinding.State.ACTIVE
        binding.save(update_fields=["provider_recording_ref", "state", "updated_at"])

    with mock.patch(
        "core.mastrao_recording_reconciler.reconcile_mastrao_recording",
        side_effect=[False, False, True, False],
    ) as reconcile:
        assert reconcile_mastrao_recordings(limit=2) == 0
        assert reconcile_mastrao_recordings(limit=2) == 1

    attempted = [call.args[0].pk for call in reconcile.call_args_list]
    assert bindings[2].pk not in attempted[:2]
    assert bindings[2].pk in attempted[2:]
