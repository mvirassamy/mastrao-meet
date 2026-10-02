"""Test WorkerServiceMediator class."""

# pylint: disable=redefined-outer-name,unused-argument

from types import SimpleNamespace
from unittest import mock
from unittest.mock import Mock

import pytest
from livekit.api import EgressStatus  # pylint: disable=no-name-in-module

from core.factories import RecordingFactory
from core.models import RecordingStatusChoices
from core.recording.worker.exceptions import (
    RecordingStartError,
    RecordingStopError,
    WorkerConnectionError,
    WorkerRequestError,
    WorkerResponseError,
)
from core.recording.worker.factories import WorkerService
from core.recording.worker.mediator import WorkerServiceMediator
from core.services.livekit_events import LiveKitEventsService

pytestmark = pytest.mark.django_db


@pytest.fixture
def mock_worker_service():
    """Fixture for mock worker service"""
    return Mock(spec=WorkerService)


@pytest.fixture
def mediator(mock_worker_service):
    """Fixture for WorkerServiceMediator"""
    return WorkerServiceMediator(mock_worker_service)


@mock.patch("core.services.room_management.RoomManagement.update_metadata")
def test_start_recording_success(mock_update_metadata, mediator, mock_worker_service):
    """Test successful recording start"""
    # Setup
    worker_id = "test-worker-123"
    mock_worker_service.start.return_value = worker_id

    mock_recording = RecordingFactory(
        status=RecordingStatusChoices.INITIATED,
        worker_id=None,
    )
    mediator.start(mock_recording)

    # Verify worker service call
    expected_room_name = str(mock_recording.room.id)
    mock_worker_service.start.assert_called_once_with(
        expected_room_name, mock_recording.id
    )

    # Verify recording updates
    mock_recording.refresh_from_db()
    assert mock_recording.worker_id == worker_id
    assert mock_recording.status == RecordingStatusChoices.ACTIVE

    mock_update_metadata.assert_called_once_with(
        str(mock_recording.room.id),
        {"recording_mode": mock_recording.mode, "recording_status": "starting"},
    )


@pytest.mark.parametrize(
    "error_class", [WorkerRequestError, WorkerConnectionError, WorkerResponseError]
)
@mock.patch("core.services.room_management.RoomManagement.update_metadata")
def test_mediator_start_recording_worker_errors(
    mock_update_metadata, mediator, mock_worker_service, error_class
):
    """Test handling of various worker errors during start"""
    # Setup
    mock_worker_service.start.side_effect = error_class("Test error")
    mock_recording = RecordingFactory(
        status=RecordingStatusChoices.INITIATED, worker_id=None
    )

    # Execute and verify
    with pytest.raises(RecordingStartError):
        mediator.start(mock_recording)

    # Verify recording updates
    mock_recording.refresh_from_db()
    assert mock_recording.status == RecordingStatusChoices.FAILED_TO_START
    assert mock_recording.worker_id is None

    mock_update_metadata.assert_not_called()


@pytest.mark.parametrize(
    "status",
    [
        RecordingStatusChoices.ACTIVE,
        RecordingStatusChoices.FAILED_TO_START,
        RecordingStatusChoices.FAILED_TO_STOP,
        RecordingStatusChoices.STOPPED,
        RecordingStatusChoices.SAVED,
        RecordingStatusChoices.ABORTED,
    ],
)
@mock.patch("core.services.room_management.RoomManagement.update_metadata")
def test_mediator_start_recording_from_forbidden_status(
    mock_update_metadata, mediator, mock_worker_service, status
):
    """Test handling of various worker errors during start"""
    # Setup
    mock_recording = RecordingFactory(status=status)

    # Execute and verify
    with pytest.raises(RecordingStartError):
        mediator.start(mock_recording)

    # Verify recording was not updated
    mock_recording.refresh_from_db()
    assert mock_recording.status == status

    mock_update_metadata.assert_not_called()


def test_mediator_stop_recording_success(mediator, mock_worker_service):
    """Test successful recording stop"""
    # Setup
    mock_recording = RecordingFactory(
        status=RecordingStatusChoices.ACTIVE, worker_id="test-worker-123"
    )
    mock_worker_service.stop.return_value = "STOPPED"

    # Execute
    mediator.stop(mock_recording)

    # Verify worker service call
    mock_worker_service.stop.assert_called_once_with(worker_id=mock_recording.worker_id)

    # Verify recording updates
    mock_recording.refresh_from_db()
    assert mock_recording.status == RecordingStatusChoices.STOPPED


def test_mediator_stop_recording_aborted(mediator, mock_worker_service):
    """Test recording stop when worker returns ABORTED"""
    # Setup
    mock_recording = RecordingFactory(
        status=RecordingStatusChoices.ACTIVE, worker_id="test-worker-123"
    )
    mock_worker_service.stop.return_value = "ABORTED"

    # Execute
    mediator.stop(mock_recording)

    # Verify recording updates
    mock_recording.refresh_from_db()
    assert mock_recording.status == RecordingStatusChoices.ABORTED


@pytest.mark.parametrize(
    "provider_status",
    ["EGRESS_ABORTED", "EGRESS_FAILED"],
)
def test_mediator_stop_recording_already_terminal_provider_error_is_terminal(
    mediator, mock_worker_service, provider_status
):
    """LiveKit may reject stop when the egress is already terminal."""

    mock_recording = RecordingFactory(
        status=RecordingStatusChoices.ACTIVE, worker_id="test-worker-123"
    )
    mock_worker_service.stop.side_effect = WorkerConnectionError(
        "LiveKit client connection error, "
        f"egress with status {provider_status} cannot be stopped."
    )

    mediator.stop(mock_recording)

    mock_worker_service.stop.assert_called_once_with(worker_id=mock_recording.worker_id)
    mock_recording.refresh_from_db()
    assert mock_recording.status == RecordingStatusChoices.ABORTED


@pytest.mark.parametrize("error_class", [WorkerConnectionError, WorkerResponseError])
def test_mediator_stop_recording_worker_errors(
    mediator, mock_worker_service, error_class
):
    """Test handling of worker errors during stop"""
    # Setup
    mock_recording = RecordingFactory(
        status=RecordingStatusChoices.ACTIVE, worker_id="test-worker-123"
    )
    mock_worker_service.stop.side_effect = error_class("Test error")

    # Execute and verify
    with pytest.raises(RecordingStopError):
        mediator.stop(mock_recording)

    # Verify recording updates
    mock_recording.refresh_from_db()
    assert mock_recording.status == RecordingStatusChoices.FAILED_TO_STOP


@pytest.mark.parametrize(
    "outcome, expected_status",
    [
        ("success", RecordingStatusChoices.STOPPED),
        ("terminal", RecordingStatusChoices.ABORTED),
        ("connection_error", RecordingStatusChoices.FAILED_TO_STOP),
        ("response_error", RecordingStatusChoices.FAILED_TO_STOP),
    ],
)
def test_stop_preserves_audio_origin_written_by_webhook_during_rpc(
    mediator, mock_worker_service, settings, outcome, expected_status
):
    """A stale stop instance must not erase options persisted during its RPC."""
    settings.RECORDING_STORAGE_EVENT_ENABLE = False
    recording = RecordingFactory(
        status=RecordingStatusChoices.ACTIVE,
        worker_id="interleaved-worker",
        options={"collect_metadata": True},
    )
    webhook = LiveKitEventsService()
    event = SimpleNamespace(
        egress_info=SimpleNamespace(
            egress_id=recording.worker_id,
            status=EgressStatus.EGRESS_COMPLETE,
            file_results=[
                SimpleNamespace(
                    filename=recording.key,
                    started_at=1790950778935944412,
                )
            ],
        )
    )

    def stop_with_webhook(*, worker_id):
        assert worker_id == recording.worker_id
        webhook._handle_egress_ended(event)  # pylint: disable=protected-access
        # The stop caller still holds the pre-webhook options in memory.
        assert "mastrao_audio_started_at_ms" not in recording.options
        if outcome == "terminal":
            raise WorkerConnectionError(
                "egress with status EGRESS_ABORTED cannot be stopped"
            )
        if outcome == "connection_error":
            raise WorkerConnectionError("RPC connection failed")
        if outcome == "response_error":
            raise WorkerResponseError("RPC response failed")
        return "STOPPED"

    mock_worker_service.stop.side_effect = stop_with_webhook
    with (
        mock.patch("core.services.livekit_events.RoomManagement.update_metadata"),
        mock.patch.object(webhook.recording_events, "handle_complete") as complete,
    ):
        if expected_status == RecordingStatusChoices.FAILED_TO_STOP:
            with pytest.raises(RecordingStopError):
                mediator.stop(recording)
        else:
            mediator.stop(recording)
    complete.assert_called_once()
    mock_worker_service.stop.assert_called_once_with(worker_id=recording.worker_id)
    recording.refresh_from_db()
    assert recording.status == expected_status
    assert recording.options == {
        "collect_metadata": True,
        "mastrao_audio_started_at_ms": 1790950778935,
    }
