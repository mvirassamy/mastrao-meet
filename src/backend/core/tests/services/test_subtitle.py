"""Tests for the durable subtitle lifecycle service."""
# pylint: disable=redefined-outer-name,protected-access

import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from types import SimpleNamespace
from unittest import mock

from django.db import close_old_connections
from django.utils import timezone

import pytest
from asgiref.sync import async_to_sync
from livekit.api import TwirpError

from core.factories import RoomFactory
from core.services.subtitle import SubtitleConflict, SubtitleException, SubtitleService

pytestmark = pytest.mark.django_db


def _dispatch(dispatch_id="dispatch-1", agent_name="multi-user-transcriber"):
    return SimpleNamespace(id=dispatch_id, agent_name=agent_name)


def _dispatch_response(*dispatches):
    # Real livekit-api 1.2.0 shape: list_dispatch returns list[AgentDispatch].
    return list(dispatches)


@pytest.fixture
def mock_livekit_client():
    """Mock the LiveKit API client used by the subtitle service."""
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
    """Start persists the dispatch id and who started it."""
    room = RoomFactory(name="my room")

    state = SubtitleService().start_subtitle(room, started_by="participant-1")

    assert state["state"] == "live"
    assert state["provider"] == "livekit"
    assert state["dispatchId"] == "dispatch-1"
    assert state["startedBy"] == "participant-1"
    mock_livekit_client.agent_dispatch.create_dispatch.assert_called_once()


def test_empty_persisted_state_is_inactive():
    """Missing optional JSON keys keep the documented inactive default."""
    room = RoomFactory(name="my room", subtitle_state={})

    state = SubtitleService().get_status(room)

    assert state["state"] == SubtitleService.INACTIVE
    assert state["stopRequested"] is False


def test_start_subtitle_is_idempotent(mock_livekit_client):
    """A second start reuses the live dispatch."""
    room = RoomFactory(name="my room")
    service = SubtitleService()

    service.start_subtitle(room)
    state = service.start_subtitle(room)

    assert state["dispatchId"] == "dispatch-1"
    mock_livekit_client.agent_dispatch.create_dispatch.assert_called_once()


@pytest.mark.django_db(transaction=True)
def test_concurrent_start_subtitle_creates_one_dispatch(mock_livekit_client):
    """Concurrent starts create a single dispatch."""
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
    """Start adopts a dispatch left by a previous attempt."""
    room = RoomFactory(name="my room")
    orphan = _dispatch("orphan-1")
    mock_livekit_client.agent_dispatch.list_dispatch.return_value = _dispatch_response(
        orphan
    )

    state = SubtitleService().start_subtitle(room)

    assert state["dispatchId"] == "orphan-1"
    mock_livekit_client.agent_dispatch.create_dispatch.assert_not_called()


def test_start_subtitle_marks_provider_failure_unavailable(mock_livekit_client):
    """A provider failure marks the room unavailable."""
    room = RoomFactory(name="my room")
    mock_livekit_client.agent_dispatch.create_dispatch.side_effect = TwirpError(
        msg="LiveKit unavailable", code="unavailable", status=503
    )

    with pytest.raises(SubtitleException, match="Failed to create subtitle agent"):
        SubtitleService().start_subtitle(room)

    room.refresh_from_db()
    assert room.subtitle_state["state"] == "unavailable"


def test_stop_subtitle_absent_is_idempotent(mock_livekit_client):
    """Stopping without a dispatch is a no-op."""
    room = RoomFactory(name="my room")

    state = SubtitleService().stop_subtitle(room)

    assert state["state"] == "stopped"
    mock_livekit_client.agent_dispatch.delete_dispatch.assert_not_called()


def test_stop_subtitle_deletes_active_dispatch(mock_livekit_client):
    """Stop deletes the active dispatch and confirms the drain."""
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
    """Stopping a stopped room deletes nothing."""
    room = RoomFactory(
        name="my room",
        subtitle_state={"state": "stopped", "dispatchId": None},
    )

    state = SubtitleService().stop_subtitle(room)

    assert state["state"] == "stopped"
    mock_livekit_client.agent_dispatch.delete_dispatch.assert_not_called()


def test_stop_subtitle_drain_timeout_preserves_dispatch(mock_livekit_client, settings):
    """A drain timeout keeps the dispatch and degrades the room."""
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


def test_openai_agent_falls_back_outside_allowlist(mock_livekit_client, settings):
    """The existing provider remains active outside the OpenAI canary."""
    settings.LIVE_STT_OPENAI_ENABLED = True
    settings.LIVE_STT_OPENAI_ROOM_ALLOWLIST = "another-room"
    room = RoomFactory(name="my room")

    state = SubtitleService().start_subtitle(room)

    request = mock_livekit_client.agent_dispatch.create_dispatch.call_args.args[0]
    assert request.agent_name == settings.ROOM_SUBTITLE_AGENT_NAME
    assert state["provider"] == "livekit"


def test_stop_requested_during_start_cleans_up_without_live_state():
    """A stop claim fences a delayed start before it can publish live."""
    room = RoomFactory(name="my room")
    room_id = str(room.id)
    attempt = SubtitleService._claim_start(
        room_id, "multi-user-transcriber", "livekit", "participant-1"
    )

    stop = SubtitleService._claim_stop(room_id)
    assert stop["state"]["state"] == SubtitleService.STOPPING
    assert stop["state"]["stopRequested"] is True

    result = SubtitleService._finalize_start(
        room_id,
        attempt["attemptId"],
        "dispatch-1",
        "multi-user-transcriber",
        "livekit",
    )
    assert result["cleanup"] is True
    stopped = SubtitleService._mark_stopped_after_start(room_id, attempt["attemptId"])
    assert stopped["state"] == SubtitleService.STOPPED


def _superseded_start_attempts(room):
    room_id = str(room.id)
    first = SubtitleService._claim_start(
        room_id, "multi-user-transcriber", "livekit", "participant-1"
    )
    room.refresh_from_db()
    state = room.subtitle_state
    state["startedAt"] = (timezone.now() - timedelta(seconds=60)).isoformat()
    room.subtitle_state = state
    room.save(update_fields=["subtitle_state"])

    second = SubtitleService._claim_start(
        room_id, "multi-user-transcriber", "livekit", "participant-2"
    )
    SubtitleService._finalize_start(
        room_id,
        second["attemptId"],
        "dispatch-2",
        "multi-user-transcriber",
        "livekit",
    )
    return first, second


def test_superseded_start_only_deletes_its_dispatch(mock_livekit_client):
    """A stale finalizer cannot delete the dispatch adopted by a newer start."""
    room = RoomFactory(name="my room")
    first, second = _superseded_start_attempts(room)
    room_id = str(room.id)
    mock_livekit_client.agent_dispatch.list_dispatch.return_value = _dispatch_response(
        _dispatch("dispatch-1"), _dispatch("dispatch-2")
    )

    result = SubtitleService._finalize_start(
        room_id,
        first["attemptId"],
        "dispatch-1",
        "multi-user-transcriber",
        "livekit",
    )

    assert result["cleanup"] is True
    assert result["superseded"] is True
    assert result["dispatchId"] == "dispatch-2"
    async_to_sync(SubtitleService()._cleanup_start_result)(
        mock_livekit_client,
        room_id,
        "dispatch-1",
        "multi-user-transcriber",
        result,
        time.monotonic() + 5,
    )

    assert [
        call.args[0]
        for call in mock_livekit_client.agent_dispatch.delete_dispatch.call_args_list
    ] == ["dispatch-1"]
    room.refresh_from_db()
    assert room.subtitle_state["state"] == SubtitleService.LIVE
    assert room.subtitle_state["dispatchId"] == "dispatch-2"
    assert room.subtitle_state["attemptId"] == second["attemptId"]


def test_superseded_start_exception_cleanup_only_deletes_its_dispatch(
    mock_livekit_client,
):
    """Exception cleanup rereads the fence and preserves the newer dispatch."""
    room = RoomFactory(name="my room")
    first, second = _superseded_start_attempts(room)
    room_id = str(room.id)
    mock_livekit_client.agent_dispatch.list_dispatch.return_value = _dispatch_response(
        _dispatch("dispatch-1"), _dispatch("dispatch-2")
    )

    async_to_sync(SubtitleService()._best_effort_cleanup_start)(
        mock_livekit_client,
        room_id,
        first["attemptId"],
        "multi-user-transcriber",
        "dispatch-1",
        time.monotonic() + 5,
    )

    assert [
        call.args[0]
        for call in mock_livekit_client.agent_dispatch.delete_dispatch.call_args_list
    ] == ["dispatch-1"]
    room.refresh_from_db()
    assert room.subtitle_state["state"] == SubtitleService.LIVE
    assert room.subtitle_state["dispatchId"] == "dispatch-2"
    assert room.subtitle_state["attemptId"] == second["attemptId"]


def test_provider_change_requires_explicit_stop(mock_livekit_client, settings):
    """Changing from OpenAI to the legacy provider never starts a second agent."""
    settings.LIVE_STT_OPENAI_ENABLED = False
    room = RoomFactory(
        name="my room",
        subtitle_state={
            "state": "live",
            "provider": "openai",
            "agentName": settings.LIVE_STT_OPENAI_AGENT_NAME,
            "dispatchId": "openai-1",
        },
    )

    with pytest.raises(SubtitleConflict, match="explicit stop"):
        SubtitleService().start_subtitle(room)

    mock_livekit_client.agent_dispatch.create_dispatch.assert_not_called()


def test_stop_deletes_livekit_and_openai_dispatches(mock_livekit_client, settings):
    """Rollback cleanup removes both known providers from the room."""
    settings.ROOM_SUBTITLE_AGENT_NAME = "legacy-agent"
    settings.LIVE_STT_OPENAI_AGENT_NAME = "gpt-live-transcribe"
    room = RoomFactory(
        name="my room",
        subtitle_state={
            "state": "live",
            "provider": "openai",
            "agentName": "gpt-live-transcribe",
            "dispatchId": "openai-1",
        },
    )
    mock_livekit_client.agent_dispatch.list_dispatch.side_effect = [
        _dispatch_response(
            _dispatch("openai-1", "gpt-live-transcribe"),
            _dispatch("legacy-1", "legacy-agent"),
        ),
        _dispatch_response(),
    ]

    state = SubtitleService().stop_subtitle(room)

    assert state["state"] == SubtitleService.STOPPED
    assert {
        call.args[0]
        for call in mock_livekit_client.agent_dispatch.delete_dispatch.call_args_list
    } == {"openai-1", "legacy-1"}


def test_start_failure_does_not_persist_provider_error_details(
    mock_livekit_client,
):
    """Provider URLs/details stay in logs, not in the participant-visible state."""
    room = RoomFactory(name="my room")
    mock_livekit_client.agent_dispatch.create_dispatch.side_effect = RuntimeError(
        "https://internal.example/livekit?token=secret"
    )

    with pytest.raises(SubtitleException, match="Failed to create subtitle agent"):
        SubtitleService().start_subtitle(room)

    room.refresh_from_db()
    assert room.subtitle_state["lastError"] == "Failed to create subtitle agent"


def test_openai_agent_uses_configured_name_for_allowlisted_room(
    mock_livekit_client, settings
):
    """Allowlisted rooms use the OpenAI agent name."""
    room = RoomFactory(name="my room")
    settings.LIVE_STT_OPENAI_ENABLED = True
    settings.LIVE_STT_OPENAI_ROOM_ALLOWLIST = str(room.id)
    settings.LIVE_STT_OPENAI_AGENT_NAME = "configured-live-transcribe"

    state = SubtitleService().start_subtitle(room)

    request = mock_livekit_client.agent_dispatch.create_dispatch.call_args.args[0]
    assert request.agent_name == "configured-live-transcribe"
    assert state["provider"] == "openai"


def test_openai_kill_switch_keeps_existing_agent_path(mock_livekit_client, settings):
    """With the OpenAI flag off the existing agent is used."""
    settings.LIVE_STT_OPENAI_ENABLED = False
    settings.ROOM_SUBTITLE_AGENT_NAME = "existing-agent"
    room = RoomFactory(name="my room")

    state = SubtitleService().start_subtitle(room)

    request = mock_livekit_client.agent_dispatch.create_dispatch.call_args.args[0]
    assert request.agent_name == "existing-agent"
    assert state["provider"] == "livekit"
