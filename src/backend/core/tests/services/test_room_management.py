"""Tests for the RoomManagement service."""

from unittest import mock

import pytest
from livekit.api import TwirpError

from core.services.room_management import (
    RoomManagement,
    RoomManagementException,
    RoomNotFoundException,
)


@mock.patch("core.services.room_management.utils.create_livekit_client")
def test_ensure_room_creates_missing_room(mock_create_livekit_client):
    """An absent room is explicitly created."""
    mock_api = mock.MagicMock()
    mock_api.room.list_rooms = mock.AsyncMock(return_value=mock.Mock(rooms=[]))
    mock_api.room.create_room = mock.AsyncMock()
    mock_api.aclose = mock.AsyncMock()
    mock_create_livekit_client.return_value = mock_api

    RoomManagement().ensure_room("room-abc")

    mock_api.room.create_room.assert_awaited_once()
    assert mock_api.room.create_room.await_args.args[0].name == "room-abc"
    mock_api.aclose.assert_awaited_once()


@mock.patch("core.services.room_management.utils.create_livekit_client")
def test_ensure_room_reuses_existing_room(mock_create_livekit_client):
    """An existing room does not trigger a duplicate create."""
    mock_api = mock.MagicMock()
    mock_api.room.list_rooms = mock.AsyncMock(
        return_value=mock.Mock(rooms=[mock.Mock()])
    )
    mock_api.room.create_room = mock.AsyncMock()
    mock_api.aclose = mock.AsyncMock()
    mock_create_livekit_client.return_value = mock_api

    RoomManagement().ensure_room("room-abc")

    mock_api.room.create_room.assert_not_awaited()
    mock_api.aclose.assert_awaited_once()


@mock.patch("core.services.room_management.utils.create_livekit_client")
def test_delete_room_calls_livekit(mock_create_livekit_client):
    """DeleteRoom is forwarded to the LiveKit API."""
    mock_api = mock.MagicMock()
    mock_api.room.delete_room = mock.AsyncMock()
    mock_api.aclose = mock.AsyncMock()
    mock_create_livekit_client.return_value = mock_api

    RoomManagement().delete_room("room-abc")

    mock_api.room.delete_room.assert_awaited_once()
    request = mock_api.room.delete_room.await_args.args[0]
    assert request.room == "room-abc"
    mock_api.aclose.assert_awaited_once()


@mock.patch("core.services.room_management.utils.create_livekit_client")
def test_delete_room_raises_not_found(mock_create_livekit_client):
    """Missing rooms raise RoomNotFoundException."""
    mock_api = mock.MagicMock()
    mock_api.room.delete_room = mock.AsyncMock(
        side_effect=TwirpError("not_found", "room not found", status=404)
    )
    mock_api.aclose = mock.AsyncMock()
    mock_create_livekit_client.return_value = mock_api

    with pytest.raises(RoomNotFoundException):
        RoomManagement().delete_room("missing-room")

    mock_api.aclose.assert_awaited_once()


@mock.patch("core.services.room_management.utils.create_livekit_client")
def test_delete_room_raises_management_exception(mock_create_livekit_client):
    """Unexpected Twirp errors raise RoomManagementException."""
    mock_api = mock.MagicMock()
    mock_api.room.delete_room = mock.AsyncMock(
        side_effect=TwirpError("internal", "boom", status=500)
    )
    mock_api.aclose = mock.AsyncMock()
    mock_create_livekit_client.return_value = mock_api

    with pytest.raises(RoomManagementException):
        RoomManagement().delete_room("room-abc")

    mock_api.aclose.assert_awaited_once()
