"""
Test subtitle service.
"""

# pylint: disable=W0621
from types import SimpleNamespace
from unittest import mock

import pytest

from core.factories import RoomFactory
from core.services.subtitle import SubtitleService

pytestmark = pytest.mark.django_db


@pytest.fixture
def mock_livekit_client():
    """Mock LiveKit API client."""
    with mock.patch("core.utils.create_livekit_client") as mock_create:
        mock_client = mock.AsyncMock()
        mock_client.room.list_rooms = mock.AsyncMock(
            return_value=SimpleNamespace(rooms=[SimpleNamespace(sid="RM_service")])
        )
        mock_client.agent_dispatch.list_dispatch = mock.AsyncMock(return_value=[])

        async def create_dispatch(request):
            # LiveKit lists a created dispatch with the exact metadata it got.
            created = SimpleNamespace(
                id="AD_service",
                agent_name=request.agent_name,
                metadata=request.metadata,
            )
            mock_client.agent_dispatch.list_dispatch.return_value = [
                *mock_client.agent_dispatch.list_dispatch.return_value,
                created,
            ]
            return created

        mock_client.agent_dispatch.create_dispatch = mock.AsyncMock(
            side_effect=create_dispatch
        )
        mock_create.return_value = mock_client
        yield mock_client


# Provider convergence runs outside Django transactions by design.
@pytest.mark.django_db(transaction=True)
def test_start_subtitle_settings(mock_livekit_client, settings):
    """Test that start_subtitle uses the configured agent name from Django settings."""

    settings.ROOM_SUBTITLE_AGENT_NAME = "fake-subtitle-agent-name"
    settings.ROOM_SUBTITLE_ENABLED = True

    room = RoomFactory(name="my room")
    SubtitleService().start_subtitle(room)

    mock_livekit_client.agent_dispatch.create_dispatch.assert_called_once()

    call_args = mock_livekit_client.agent_dispatch.create_dispatch.call_args[0][0]
    assert call_args.agent_name == "fake-subtitle-agent-name"
    assert call_args.room == str(room.id)


def test_stop_subtitle_without_control_is_idempotent():
    """Stopping a room without an acquired control row is a no-op."""

    room = RoomFactory(name="my room")

    assert SubtitleService().stop_subtitle(room) is None


def test_celery_start_returns_after_persisting_intent(settings):
    """Celery mode responds after durable intent and schedules convergence."""

    settings.CELERY_ENABLED = True
    room = RoomFactory()
    control = mock.Mock(room_sid="RM_celery_start")
    with (
        mock.patch(
            "core.services.subtitle._resolve_subtitle_room",
            new=mock.AsyncMock(return_value=(control.room_sid, [])),
        ),
        mock.patch(
            "core.services.subtitle._persist_start_intent", return_value=control
        ),
        mock.patch(
            "core.services.subtitle.schedule_subtitle_reconciliation"
        ) as schedule,
        mock.patch("core.services.subtitle.reconcile_subtitle_control") as reconcile,
    ):
        assert SubtitleService().start_subtitle(room) is control

    schedule.assert_called_once_with(control.room_sid)
    reconcile.assert_not_called()


def test_synchronous_start_uses_the_explicit_convergence_budget(settings):
    """The no-Celery path invokes bounded convergence before returning."""

    settings.CELERY_ENABLED = False
    room = RoomFactory()
    control = mock.Mock(room_sid="RM_sync_start")
    with (
        mock.patch(
            "core.services.subtitle._resolve_subtitle_room",
            new=mock.AsyncMock(return_value=(control.room_sid, [])),
        ),
        mock.patch(
            "core.services.subtitle._persist_start_intent", return_value=control
        ),
        mock.patch(
            "core.services.subtitle.schedule_subtitle_reconciliation"
        ) as schedule,
        mock.patch(
            "core.services.subtitle.reconcile_subtitle_control",
            return_value=control,
        ) as reconcile,
    ):
        assert SubtitleService().start_subtitle(room) is control

    schedule.assert_called_once_with(control.room_sid)
    reconcile.assert_called_once()
    assert reconcile.call_args.kwargs["deadline"] > 0
