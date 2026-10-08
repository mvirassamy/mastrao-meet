"""Metadata dispatch cleanup must preserve the original fencing refusal."""

from types import SimpleNamespace
from unittest import mock

import pytest

from core.recording.services.metadata_collector import (
    MetadataCollectorException,
    MetadataCollectorService,
)


@pytest.mark.parametrize("cleanup_fails", [False, True])
def test_superseded_dispatch_keeps_primary_refusal_and_closes_client(cleanup_fails):
    """Cleanup failures cannot replace the superseded claim or leak its client."""

    refusal = MetadataCollectorException("Dispatch claim was superseded")
    cleanup_error = RuntimeError("Dispatch cleanup failed") if cleanup_fails else None
    client = SimpleNamespace(
        agent_dispatch=SimpleNamespace(
            create_dispatch=mock.AsyncMock(
                return_value=SimpleNamespace(id="dispatch-fixture")
            ),
            delete_dispatch=mock.AsyncMock(side_effect=cleanup_error),
        ),
        aclose=mock.AsyncMock(),
    )
    recording = SimpleNamespace(
        pk="recording-fixture",
        id="recording-fixture",
        room=SimpleNamespace(id="room-fixture"),
    )

    with (
        mock.patch(
            "core.recording.services.metadata_collector.utils.create_livekit_client",
            return_value=client,
        ),
        mock.patch.object(
            MetadataCollectorService, "_store_dispatch_id", side_effect=refusal
        ),
        pytest.raises(MetadataCollectorException) as error,
    ):
        MetadataCollectorService().start(recording)

    assert error.value is refusal
    client.agent_dispatch.delete_dispatch.assert_awaited_once_with(
        dispatch_id="dispatch-fixture", room_name="room-fixture"
    )
    client.aclose.assert_awaited_once()
