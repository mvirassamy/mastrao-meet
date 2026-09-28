"""Tests for the durable subtitle lifecycle service."""

from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest import mock

from django.db import close_old_connections
from django.utils import timezone

import pytest
from livekit.api import TwirpError

from core.factories import RoomFactory
from core.services.subtitle import SubtitleException, SubtitleService

pytestmark = pytest.mark.django_db


def _dispatch(dispatch_id="dispatch-1", agent_name="multi-user-transcriber"):
    return SimpleNamespace(id=dispatch_id, agent_name=agent_name)


def _dispatch_response(*dispatches):
    # Real livekit-api 1.2.0 shape: list_dispatch returns list[AgentDispatch].
    return list(dispatches)


@pytest.fixture
def mock_livekit_client():
    with mock.patch("core.utils.create_livekit_client") as mock_create:
        client = mock.AsyncMock()
        client.agent_dispatch.list_dispatch = mock.AsyncMock(
            return_value=_dispatch_response()
        )
        client.agent_dispatch.create_dispatch = mock.AsyncMock(return_value=_dispatch())
        client.agent_dispatch.delete_dispatch = mock.AsyncMock()
        mock_create.return_value = client
        yield client


def test_start_subtitle_persists_dispatch_and_identity(
    mock_livekit_client,
):
    room = RoomFactory(name="my room")

    state = SubtitleService().start_subtitle(room, started_by="participant-1")

    assert state["state"] == "live"
    assert state["provider"] == "livekit"
    assert state["dispatchId"] == "dispatch-1"
    assert state["startedBy"] == "participant-1"
    mock_livekit_client.agent_dispatch.create_dispatch.assert_called_once()


def test_start_subtitle_is_idempotent(mock_livekit_client):
    room = RoomFactory(name="my room")
    service = SubtitleService()

    service.start_subtitle(room)
    state = service.start_subtitle(room)

    assert state["dispatchId"] == "dispatch-1"
    mock_livekit_client.agent_dispatch.create_dispatch.assert_called_once()


@pytest.mark.django_db(transaction=True)
def test_concurrent_start_subtitle_creates_one_dispatch(mock_livekit_client):
    room = RoomFactory(name="my room")

    def start_from_worker(_):
        close_old_connections()
        try:
            return SubtitleService().start_subtitle(room)
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as executor:
        states = list(executor.map(start_from_worker, range(2)))

    assert {state["dispatchId"] for state in states} == {"dispatch-1"}
    mock_livekit_client.agent_dispatch.create_dispatch.assert_called_once()


def test_start_subtitle_adopts_orphan_dispatch(mock_livekit_client):
    room = RoomFactory(name="my room")
    orphan = _dispatch("orphan-1")
    mock_livekit_client.agent_dispatch.list_dispatch.return_value = _dispatch_response(
        orphan
    )

    state = SubtitleService().start_subtitle(room)

    assert state["dispatchId"] == "orphan-1"
    mock_livekit_client.agent_dispatch.create_dispatch.assert_not_called()


def test_start_subtitle_marks_provider_failure_unavailable(mock_livekit_client):
    room = RoomFactory(name="my room")
    mock_livekit_client.agent_dispatch.create_dispatch.side_effect = TwirpError(
        msg="LiveKit unavailable", code="unavailable", status=503
    )

    with pytest.raises(SubtitleException, match="Failed to create subtitle agent"):
        SubtitleService().start_subtitle(room)

    room.refresh_from_db()
    assert room.subtitle_state["state"] == "unavailable"


def test_stop_subtitle_absent_is_idempotent(mock_livekit_client):
    room = RoomFactory(name="my room")

    state = SubtitleService().stop_subtitle(room)

    assert state["state"] == "stopped"
    mock_livekit_client.agent_dispatch.delete_dispatch.assert_not_called()


def test_stop_subtitle_deletes_active_dispatch(mock_livekit_client):
    room = RoomFactory(
        name="my room",
        subtitle_state={
            "state": "live",
            "provider": "livekit",
            "agentName": "multi-user-transcriber",
            "dispatchId": "dispatch-1",
        },
    )
    mock_livekit_client.agent_dispatch.list_dispatch.return_value = _dispatch_response()

    state = SubtitleService().stop_subtitle(room)

    assert state["state"] == "stopped"
    assert state["dispatchId"] is None
    dispatch_id, room_name = (
        mock_livekit_client.agent_dispatch.delete_dispatch.call_args.args
    )
    assert dispatch_id == "dispatch-1"
    assert room_name == str(room.id)


def test_stop_subtitle_already_stopped_does_not_delete(mock_livekit_client):
    room = RoomFactory(
        name="my room",
        subtitle_state={"state": "stopped", "dispatchId": None},
    )

    state = SubtitleService().stop_subtitle(room)

    assert state["state"] == "stopped"
    mock_livekit_client.agent_dispatch.delete_dispatch.assert_not_called()


def test_stop_subtitle_drain_timeout_preserves_dispatch(mock_livekit_client, settings):
    settings.ROOM_SUBTITLE_DRAIN_TIMEOUT_SECONDS = 0.01
    room = RoomFactory(
        name="my room",
        subtitle_state={
            "state": "live",
            "agentName": "multi-user-transcriber",
            "dispatchId": "dispatch-1",
        },
    )
    mock_livekit_client.agent_dispatch.list_dispatch.return_value = _dispatch_response(
        _dispatch()
    )

    with pytest.raises(SubtitleException, match="Timed out waiting"):
        SubtitleService().stop_subtitle(room)

    room.refresh_from_db()
    assert room.subtitle_state["state"] == "degraded"
    assert room.subtitle_state["dispatchId"] == "dispatch-1"


def test_interrupted_stop_does_not_block_the_room(mock_livekit_client, settings):
    """A stale stopping claim is reclaimed by stop and does not block start."""
    settings.ROOM_SUBTITLE_DRAIN_TIMEOUT_SECONDS = 1
    stale = "2026-01-01T00:00:00+00:00"
    room = RoomFactory(
        name="my room",
        subtitle_state={
            "state": "stopping",
            "agentName": "multi-user-transcriber",
            "dispatchId": "dispatch-1",
            "stoppingAt": stale,
        },
    )

    state = SubtitleService().stop_subtitle(room)

    assert state["state"] == "stopped"
    mock_livekit_client.agent_dispatch.delete_dispatch.assert_called_once()

    room.subtitle_state = {
        "state": "stopping",
        "agentName": "multi-user-transcriber",
        "dispatchId": "dispatch-1",
        "stoppingAt": stale,
    }
    room.save()
    assert SubtitleService().start_subtitle(room)["state"] == "live"


def test_fresh_stop_claim_is_not_duplicated(mock_livekit_client):
    """A stop already in progress is not started twice."""
    room = RoomFactory(
        name="my room",
        subtitle_state={
            "state": "stopping",
            "agentName": "multi-user-transcriber",
            "dispatchId": "dispatch-1",
            "stoppingAt": timezone.now().isoformat(),
        },
    )

    assert SubtitleService().stop_subtitle(room)["state"] == "stopping"
    mock_livekit_client.agent_dispatch.delete_dispatch.assert_not_called()


def test_openai_agent_requires_allowlisted_room(mock_livekit_client, settings):
    settings.LIVE_STT_OPENAI_ENABLED = True
    settings.LIVE_STT_OPENAI_ROOM_ALLOWLIST = "another-room"
    room = RoomFactory(name="my room")

    with pytest.raises(SubtitleException, match="not enabled for room"):
        SubtitleService().start_subtitle(room)

    mock_livekit_client.agent_dispatch.create_dispatch.assert_not_called()


def test_openai_agent_uses_configured_name_for_allowlisted_room(
    mock_livekit_client, settings
):
    room = RoomFactory(name="my room")
    settings.LIVE_STT_OPENAI_ENABLED = True
    settings.LIVE_STT_OPENAI_ROOM_ALLOWLIST = str(room.id)
    settings.LIVE_STT_OPENAI_AGENT_NAME = "configured-live-transcribe"

    state = SubtitleService().start_subtitle(room)

    request = mock_livekit_client.agent_dispatch.create_dispatch.call_args.args[0]
    assert request.agent_name == "configured-live-transcribe"
    assert state["provider"] == "openai"


def test_openai_kill_switch_keeps_existing_agent_path(mock_livekit_client, settings):
    settings.LIVE_STT_OPENAI_ENABLED = False
    settings.ROOM_SUBTITLE_AGENT_NAME = "existing-agent"
    room = RoomFactory(name="my room")

    state = SubtitleService().start_subtitle(room)

    request = mock_livekit_client.agent_dispatch.create_dispatch.call_args.args[0]
    assert request.agent_name == "existing-agent"
    assert state["provider"] == "livekit"
