"""Mediator between the worker service and recording instances in the Django ORM."""

import logging

from core.models import Recording, RecordingStatusChoices
from core.services.room_management import (
    RoomManagement,
    RoomManagementException,
    RoomNotFoundException,
)

from .exceptions import (
    RecordingStartError,
    RecordingStopError,
    WorkerConnectionError,
    WorkerRequestError,
    WorkerResponseError,
)
from .factories import WorkerService

logger = logging.getLogger(__name__)


def _already_terminal_stop_result(error: Exception) -> str | None:
    """Map an already-terminal LiveKit stop error to the local result."""

    message = str(error)
    if "cannot be stopped" not in message:
        return None

    if "EGRESS_COMPLETE" in message or "EGRESS_LIMIT_REACHED" in message:
        return "STOPPED"

    if "EGRESS_ABORTED" in message or "EGRESS_FAILED" in message:
        return "ABORTED"

    return None


class WorkerServiceMediator:
    """Mediate interactions between a worker service and a recording instance.

    A mediator class that decouples the worker from Django ORM, handles recording updates
    based on worker status, and transforms worker errors into user-friendly exceptions.
    Implements Mediator pattern.
    """

    def __init__(self, worker_service: WorkerService):
        """Initialize the WorkerServiceMediator with the provided worker service."""

        self._worker_service = worker_service

    def start(self, recording: Recording):
        """Start the recording process using the worker service.

        If the operation is successful, the recording's status will
        transition from INITIATED to ACTIVE, else to FAILED_TO_START to keep track of errors.

        Args:
            recording (Recording): The recording instance to start.
        Raises:
            RecordingStartError: If there is an error starting the recording.
        """

        if recording.status != RecordingStatusChoices.INITIATED:
            logger.error("Cannot start recording in %s status.", recording.status)
            raise RecordingStartError()

        room_name = str(recording.room.id)
        try:
            worker_id = self._worker_service.start(room_name, recording.id)
        except (WorkerRequestError, WorkerConnectionError, WorkerResponseError) as e:
            logger.exception(
                "Failed to start recording for room %s: %s", recording.room.slug, e
            )
            recording.status = RecordingStatusChoices.FAILED_TO_START
            raise RecordingStartError() from e
        else:
            recording.worker_id = worker_id
            recording.status = RecordingStatusChoices.ACTIVE
        finally:
            recording.save()

        mode = recording.options.get("original_mode", None) or recording.mode

        try:
            RoomManagement().update_metadata(
                room_name, {"recording_mode": mode, "recording_status": "starting"}
            )
        except RoomNotFoundException:
            logger.info(
                "LiveKit room %s no longer exists, skipping metadata update",
                room_name,
            )
        except RoomManagementException as e:
            logger.exception("Failed to update room's metadata: %s", e)

        logger.info(
            "Worker started for room %s (worker ID: %s)",
            recording.room,
            recording.worker_id,
        )

    def stop(self, recording: Recording):
        """Stop the recording process using the worker service.

        If the operation is successful, the recording's status will transition
        from ACTIVE to STOPPED, else to FAILED_TO_STOP to keep track of errors.

        Args:
            recording (Recording): The recording instance to stop.
        Raises:
            RecordingStopError: If there is an error stopping the recording.
        """

        if recording.status != RecordingStatusChoices.ACTIVE:
            logger.error("Cannot stop recording in %s status.", recording.status)
            raise RecordingStopError()

        try:
            response = self._worker_service.stop(worker_id=recording.worker_id)
        except (WorkerConnectionError, WorkerResponseError) as e:
            terminal_result = _already_terminal_stop_result(e)
            if terminal_result is not None:
                logger.info(
                    "Recording egress for room %s was already terminal with result %s.",
                    recording.room.slug,
                    terminal_result,
                )
                recording.status = RecordingStatusChoices[terminal_result]
                recording.save()
                return
            logger.exception(
                "Failed to stop recording for room %s: %s", recording.room.slug, e
            )
            recording.status = RecordingStatusChoices.FAILED_TO_STOP
            raise RecordingStopError() from e
        else:
            recording.status = RecordingStatusChoices[response]
        finally:
            recording.save()

        logger.info("Worker stopped for room %s", recording.room)
