"""Provider observations and timing for an uncertain video start."""

# LiveKit's generated enum members are not visible to pylint.
# pylint: disable=no-member,missing-function-docstring,redefined-outer-name

import time
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest import mock

import pytest
from livekit import api

from core import models
from core.mastrao_recording_adapter import (
    _apply_start,
    _provider_registration_started_at,
    _record_start_attempt_finished,
    publish_recording_start,
)
from core.mastrao_recording_contract import RecordingContractRefused
from core.mastrao_recording_failure import report_mastrao_recording_failure
from core.mastrao_recording_reconciler import reconcile_mastrao_recording
from core.recording.worker.exceptions import WorkerConnectionError


@pytest.fixture
def start_context():
    recording = SimpleNamespace(
        pk="recording-local",
        room_id="room-local",
        status=models.RecordingStatusChoices.INITIATED,
        worker_id=None,
        save=mock.Mock(),
    )
    binding = SimpleNamespace(
        pk="binding-local",
        recording_id=recording.pk,
        state=models.MastraoRecordingBinding.State.STARTING,
    )
    local_effect = SimpleNamespace(
        pk="effect-local",
        state=models.MastraoRecordingEffect.State.APPLYING,
        receipt_claims={"start_attempted_at": time.time()},
        save=mock.Mock(),
    )
    with (
        mock.patch("core.mastrao_recording_adapter._prepare_start") as prepare,
        mock.patch.object(models.Recording.objects, "select_related") as records,
        mock.patch("core.mastrao_recording_adapter.get_worker_service") as worker,
        mock.patch(
            "core.mastrao_recording_adapter.authorize_video_start",
            return_value={"authorized": True},
        ),
    ):
        records.return_value.get.return_value = recording
        yield recording, binding, local_effect, prepare, worker.return_value


@pytest.mark.parametrize(
    ("provider_status", "should_report_failure"),
    [
        (api.EgressStatus.EGRESS_FAILED, True),
        (api.EgressStatus.EGRESS_ABORTED, True),
        (api.EgressStatus.EGRESS_COMPLETE, False),
    ],
)
def test_replay_never_confirms_a_terminal_egress(
    start_context, provider_status, should_report_failure
):
    recording, binding, local_effect, prepare, worker = start_context
    prepare.return_value = binding, local_effect, False
    provider = SimpleNamespace(status=provider_status, egress_id="EG_terminal")
    with (
        mock.patch(
            "core.mastrao_recording_adapter._exact_provider_egress",
            return_value=provider,
        ),
        mock.patch(
            "core.mastrao_recording_adapter.report_mastrao_recording_failure",
            return_value=True,
        ) as report_failure,
        pytest.raises(RecordingContractRefused) as refusal,
    ):
        _apply_start({"resolve_only": True})

    assert refusal.value.status == 503
    assert recording.status == models.RecordingStatusChoices.INITIATED
    assert recording.worker_id is None
    worker.start.assert_not_called()
    if should_report_failure:
        report_failure.assert_called_once_with(recording, None)
    else:
        report_failure.assert_not_called()


def test_timed_out_start_reports_discovered_failure_without_receipt(start_context):
    recording, binding, local_effect, prepare, worker = start_context
    prepare.return_value = binding, local_effect, True
    worker.start.side_effect = WorkerConnectionError
    provider = SimpleNamespace(status=api.EgressStatus.EGRESS_FAILED)
    with (
        mock.patch(
            "core.mastrao_recording_adapter._exact_provider_egress",
            return_value=provider,
        ),
        mock.patch(
            "core.mastrao_recording_adapter.report_mastrao_recording_failure",
            return_value=True,
        ) as report_failure,
        mock.patch(
            "core.mastrao_recording_adapter.transaction.atomic", side_effect=nullcontext
        ),
        mock.patch.object(
            models.MastraoRecordingEffect.objects, "select_for_update"
        ) as effects,
        pytest.raises(RecordingContractRefused) as refusal,
    ):
        effects.return_value.get.return_value = local_effect
        _apply_start({"resolve_only": False})

    assert refusal.value.status == 503
    worker.start.assert_called_once_with(recording.room_id, recording.pk)
    report_failure.assert_called_once_with(recording, None)
    assert "start_attempt_finished_at" in local_effect.receipt_claims


@pytest.mark.parametrize("attempt_finished", [True, False])
def test_registration_grace_starts_after_attempt_or_recovery_bound(
    attempt_finished,
):
    attempted_at = time.time()
    receipt_claims = {"start_attempted_at": attempted_at}
    expected = datetime.fromtimestamp(attempted_at, tz=UTC) + timedelta(seconds=60)
    if attempt_finished:
        receipt_claims["start_attempt_finished_at"] = attempted_at + 23
        expected = datetime.fromtimestamp(attempted_at + 23, tz=UTC)
    effect = SimpleNamespace(
        created_at=datetime.fromtimestamp(attempted_at, tz=UTC) - timedelta(hours=3),
        applied_at=None,
        receipt_claims=receipt_claims,
    )
    with mock.patch.object(models.MastraoRecordingEffect.objects, "filter") as query:
        query.return_value.order_by.return_value.first.return_value = effect
        assert _provider_registration_started_at(mock.sentinel.binding) == expected


@pytest.mark.parametrize(
    "provider_status", [api.EgressStatus.EGRESS_FAILED, api.EgressStatus.EGRESS_ABORTED]
)
def test_reconciler_reports_terminal_egress_without_provider_ref(provider_status):
    recording = mock.sentinel.recording
    binding = SimpleNamespace(recording=recording, provider_recording_ref=None)
    with (
        mock.patch(
            "core.mastrao_recording_reconciler._exact_provider_egress",
            return_value=SimpleNamespace(status=provider_status),
        ),
        mock.patch(
            "core.mastrao_recording_reconciler.report_mastrao_recording_failure",
            return_value=True,
        ) as report_failure,
    ):
        assert reconcile_mastrao_recording(binding)
    report_failure.assert_called_once_with(recording, None)


def test_reconciler_checks_missing_egress_after_core_projects_active():
    binding = SimpleNamespace(
        recording=mock.sentinel.recording,
        provider_recording_ref=None,
        state=models.MastraoRecordingBinding.State.ACTIVE,
    )
    with (
        mock.patch(
            "core.mastrao_recording_reconciler._exact_provider_egress",
            return_value=None,
        ),
        mock.patch(
            "core.mastrao_recording_reconciler.fail_stale_starting_provider_egress",
            return_value=True,
        ) as fail_stale,
    ):
        assert reconcile_mastrao_recording(binding)
    fail_stale.assert_called_once_with(binding, binding.recording)


def test_attempt_finish_cannot_replace_concurrently_applied_receipt():
    stale = SimpleNamespace(
        pk="effect", receipt_claims={"claim_id": "claim"}, save=mock.Mock()
    )
    applied = SimpleNamespace(
        state=models.MastraoRecordingEffect.State.APPLIED,
        receipt_claims={
            "status": "confirmed",
            "provider_recording_ref": "EG_confirmed",
        },
        save=mock.Mock(),
    )
    with (
        mock.patch(
            "core.mastrao_recording_adapter.transaction.atomic", side_effect=nullcontext
        ),
        mock.patch.object(
            models.MastraoRecordingEffect.objects, "select_for_update"
        ) as effects,
    ):
        effects.return_value.get.return_value = applied
        _record_start_attempt_finished(stale)
    applied.save.assert_not_called()
    stale.save.assert_not_called()
    assert applied.receipt_claims == {
        "status": "confirmed",
        "provider_recording_ref": "EG_confirmed",
    }


@pytest.mark.parametrize(
    "terminal_state",
    [
        models.MastraoRecordingBinding.State.CANCELLED,
        models.MastraoRecordingBinding.State.FAILED,
        models.MastraoRecordingBinding.State.FINALIZED,
    ],
)
def test_provider_success_cannot_publish_after_binding_terminal(terminal_state):
    binding = SimpleNamespace(state=terminal_state)
    with (
        mock.patch(
            "core.mastrao_recording_adapter.transaction.atomic", side_effect=nullcontext
        ),
        mock.patch.object(
            models.MastraoRecordingBinding.objects, "select_for_update"
        ) as bindings,
        mock.patch.object(
            models.MastraoRecordingEffect.objects, "select_for_update"
        ) as effects,
        mock.patch.object(models.Recording.objects, "select_for_update") as recordings,
        pytest.raises(RecordingContractRefused) as refusal,
    ):
        bindings.return_value.get.return_value = binding
        publish_recording_start("binding", "EG_confirmed", "started")
    assert refusal.value.status == 409
    effects.assert_not_called()
    recordings.assert_not_called()


@pytest.fixture
def failure_context():
    binding = SimpleNamespace(
        pk="binding-local",
        recording_id="recording-local",
        recording_ref="recording-ref",
        organization_external_id="organization",
        meeting_ref="meeting",
        room_ref="room",
        provider_binding_digest="digest",
        provider_recording_ref=None,
        state=models.MastraoRecordingBinding.State.STARTING,
        State=models.MastraoRecordingBinding.State,
        save=mock.Mock(),
    )
    recording = SimpleNamespace(
        pk=binding.recording_id,
        mastrao_binding=binding,
        status=models.RecordingStatusChoices.INITIATED,
        save=mock.Mock(),
    )
    with (
        mock.patch(
            "core.mastrao_recording_failure.transaction.atomic", side_effect=nullcontext
        ),
        mock.patch.object(
            models.MastraoRecordingBinding.objects, "select_for_update"
        ) as bindings,
        mock.patch.object(models.Recording.objects, "select_for_update") as recordings,
        mock.patch(
            "core.mastrao_recording_failure.sign_failure_receipt",
            return_value="signed.failure.receipt",
        ),
    ):
        bindings.return_value.get.return_value = binding
        recordings.return_value.get.return_value = recording
        yield binding, recording


def test_failure_is_reserved_before_core_can_race_a_start(failure_context):
    binding, recording = failure_context

    def core_call(**_kwargs):
        assert binding.state == models.MastraoRecordingBinding.State.FAILED
        with pytest.raises(RecordingContractRefused) as refused:
            publish_recording_start(binding.pk, "EG_concurrent", "started")
        assert refused.value.status == 409
        return {"recordingRef": binding.recording_ref, "state": "failed"}

    with mock.patch(
        "core.mastrao_recording_failure.post_core_json", side_effect=core_call
    ) as post_core:
        assert report_mastrao_recording_failure(recording, None)
    post_core.assert_called_once()


@pytest.mark.parametrize(
    ("provider_status", "expected_status"),
    [
        (None, models.RecordingStatusChoices.FAILED_TO_START),
        (api.EgressStatus.EGRESS_FAILED, models.RecordingStatusChoices.ABORTED),
    ],
)
def test_confirmed_failure_releases_recording_slot(
    failure_context, provider_status, expected_status
):
    binding, recording = failure_context
    if provider_status is not None:
        binding.provider_recording_ref = "EG_registered"
        recording.status = models.RecordingStatusChoices.ACTIVE
    with mock.patch(
        "core.mastrao_recording_failure.post_core_json",
        return_value={"recordingRef": binding.recording_ref, "state": "failed"},
    ):
        assert report_mastrao_recording_failure(recording, provider_status)
    assert binding.state == models.MastraoRecordingBinding.State.FAILED
    assert recording.status == expected_status
    recording.save.assert_called_once_with(update_fields=["status", "updated_at"])


def test_late_no_provider_failure_does_not_override_published_start(failure_context):
    _, recording = failure_context
    published = SimpleNamespace(
        provider_recording_ref="EG_registered",
        state=models.MastraoRecordingBinding.State.ACTIVE,
    )
    with (
        mock.patch.object(
            models.MastraoRecordingBinding.objects, "select_for_update"
        ) as bindings,
        mock.patch("core.mastrao_recording_failure.post_core_json") as post_core,
    ):
        bindings.return_value.get.return_value = published
        assert not report_mastrao_recording_failure(recording, None)
    post_core.assert_not_called()
    assert published.state == models.MastraoRecordingBinding.State.ACTIVE


@pytest.mark.parametrize(
    "terminal_state",
    [
        models.MastraoRecordingBinding.State.CANCELLED,
        models.MastraoRecordingBinding.State.FINALIZED,
    ],
)
def test_late_provider_failure_does_not_override_terminal_binding(
    failure_context, terminal_state
):
    binding, recording = failure_context
    binding.state = terminal_state
    binding.provider_recording_ref = "EG_terminal"
    original_recording_status = recording.status

    with mock.patch("core.mastrao_recording_failure.post_core_json") as post_core:
        assert not report_mastrao_recording_failure(
            recording, api.EgressStatus.EGRESS_FAILED
        )

    assert binding.state == terminal_state
    assert recording.status == original_recording_status
    binding.save.assert_not_called()
    recording.save.assert_not_called()
    post_core.assert_not_called()


@pytest.mark.parametrize(
    "status", [api.EgressStatus.EGRESS_STARTING, api.EgressStatus.EGRESS_ACTIVE]
)
def test_present_egress_is_never_reported_not_started_after_grace(status):
    binding = SimpleNamespace(
        recording=mock.sentinel.recording,
        provider_recording_ref="EG_present",
    )
    with (
        mock.patch(
            "core.mastrao_recording_reconciler._exact_provider_egress",
            return_value=SimpleNamespace(status=status, egress_id="EG_present"),
        ),
        mock.patch(
            "core.mastrao_recording_reconciler.fail_stale_starting_provider_egress"
        ) as fail_stale,
    ):
        reconcile_mastrao_recording(binding)
    fail_stale.assert_not_called()


@pytest.mark.parametrize(
    "status", [api.EgressStatus.EGRESS_ACTIVE, api.EgressStatus.EGRESS_COMPLETE]
)
def test_reconciler_publishes_start_before_processing_exact_egress(status):
    recording = SimpleNamespace(pk="recording-local")
    binding = SimpleNamespace(
        pk="binding",
        recording_id=recording.pk,
        recording=recording,
        provider_recording_ref=None,
        state=models.MastraoRecordingBinding.State.STARTING,
    )
    egress = SimpleNamespace(status=status, egress_id="EG_exact")
    with (
        mock.patch(
            "core.mastrao_recording_reconciler._exact_provider_egress",
            return_value=egress,
        ),
        mock.patch(
            "core.mastrao_recording_reconciler.publish_recording_start"
        ) as publish,
        mock.patch(
            "core.mastrao_recording_reconciler.finalize_mastrao_artifact"
        ) as finalize,
        mock.patch(
            "core.mastrao_recording_reconciler.transaction.atomic",
            side_effect=nullcontext,
        ),
        mock.patch.object(models.MastraoRecordingBinding.objects, "filter") as bindings,
        mock.patch.object(models.Recording.objects, "filter") as recordings,
    ):
        bindings.return_value.update.return_value = 1
        assert reconcile_mastrao_recording(binding)
    publish.assert_called_once()
    if status == api.EgressStatus.EGRESS_COMPLETE:
        finalize.assert_called_once_with(binding.recording)
        recordings.assert_called_once()
        assert recordings.return_value.update.call_args.kwargs["status"] == (
            models.RecordingStatusChoices.STOPPED
        )
    else:
        finalize.assert_not_called()
        recordings.assert_not_called()
