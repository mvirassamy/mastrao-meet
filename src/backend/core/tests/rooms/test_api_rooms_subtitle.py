"""
Test rooms API endpoints in the Meet core app: start subtitle.
"""
# pylint: disable=W0621

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest import mock

from django.conf import settings
from django.db import close_old_connections

import pytest
from livekit.api import AccessToken, TwirpError, VideoGrants
from rest_framework.test import APIClient

from ...factories import RoomFactory, UserFactory
from ...models import RoomSubtitleControl
from ...services.subtitle_control import (
    compare_and_set_subtitle_control,
    ensure_subtitle_control,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def mock_room_id() -> str:
    """Mock room's id."""
    return "d2aeb774-1ecd-4d73-a3ac-3d3530cad7ff"


@pytest.fixture
def mock_livekit_token(mock_room_id):
    """Mock LiveKit JWT token."""

    video_grants = VideoGrants(
        room=mock_room_id,
        room_join=True,
        room_admin=True,
        can_update_own_metadata=True,
        can_publish_sources=[
            "camera",
            "microphone",
            "screen_share",
            "screen_share_audio",
        ],
    )

    token = (
        AccessToken(
            api_key=settings.LIVEKIT_CONFIGURATION["api_key"],
            api_secret=settings.LIVEKIT_CONFIGURATION["api_secret"],
        )
        .with_grants(video_grants)
        .with_identity(str(uuid.uuid4()))
    )

    return token.to_jwt()


@pytest.fixture
def mock_livekit_participant_token(mock_room_id):
    """Mock a non-administrator LiveKit token for the room."""

    video_grants = VideoGrants(room=mock_room_id, room_join=True)
    token = (
        AccessToken(
            api_key=settings.LIVEKIT_CONFIGURATION["api_key"],
            api_secret=settings.LIVEKIT_CONFIGURATION["api_secret"],
        )
        .with_grants(video_grants)
        .with_identity(str(uuid.uuid4()))
    )
    return token.to_jwt()


@pytest.fixture
def mock_livekit_client():
    """Mock LiveKit API client."""
    with mock.patch("core.utils.create_livekit_client") as mock_create:
        mock_client = mock.AsyncMock()
        mock_client.room.list_rooms = mock.AsyncMock(
            return_value=SimpleNamespace(rooms=[SimpleNamespace(sid="RM_api")])
        )
        mock_client.agent_dispatch.list_dispatch = mock.AsyncMock(return_value=[])

        async def create_dispatch(request):
            # LiveKit lists a created dispatch with the exact metadata it got.
            created = SimpleNamespace(
                id="AD_api",
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


def test_start_subtitle_missing_token_anonymous(settings):
    """Test that anonymous users cannot start subtitles without a valid LiveKit token."""

    settings.ROOM_SUBTITLE_ENABLED = True

    room = RoomFactory()
    client = APIClient()

    response = client.post(
        f"/api/v1.0/rooms/{room.id}/start-subtitle/",
    )

    assert response.status_code == 403
    assert response.json() == {
        "detail": "Authentication credentials were not provided."
    }


def test_start_subtitle_missing_token_authenticated(settings):
    """Test that authenticated users still need a valid LiveKit token to start subtitles."""

    settings.ROOM_SUBTITLE_ENABLED = True

    room = RoomFactory()
    user = UserFactory()
    client = APIClient()
    client.force_login(user)

    response = client.post(
        f"/api/v1.0/rooms/{room.id}/start-subtitle/",
    )

    assert response.status_code == 403
    assert response.json() == {
        "detail": "Authentication credentials were not provided."
    }


def test_start_subtitle_invalid_token():
    """Test that malformed or invalid LiveKit tokens are rejected."""

    room = RoomFactory()
    user = UserFactory()
    client = APIClient()
    client.force_login(user)

    response = client.post(
        f"/api/v1.0/rooms/{room.id}/start-subtitle/",
        {},
        HTTP_AUTHORIZATION="Bearer invalid-token",
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "Invalid LiveKit token: Not enough segments"}


def test_start_subtitle_disabled_by_default(mock_livekit_token):
    """Test that subtitle functionality is disabled when feature flag is off."""

    room = RoomFactory()
    user = UserFactory()
    client = APIClient()
    client.force_login(user)

    response = client.post(
        f"/api/v1.0/rooms/{room.id}/start-subtitle/",
        {},
        HTTP_AUTHORIZATION=f"Bearer {mock_livekit_token}",
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "Not found."}


# Provider convergence runs outside Django transactions by design.
@pytest.mark.django_db(transaction=True)
def test_start_subtitle_valid_token(
    settings, mock_livekit_client, mock_livekit_token, mock_room_id
):
    """Test successful subtitle initiation with valid token and enabled feature."""

    settings.ROOM_SUBTITLE_ENABLED = True

    room = RoomFactory(id=mock_room_id)
    client = APIClient()

    response = client.post(
        f"/api/v1.0/rooms/{room.id}/start-subtitle/",
        {},
        HTTP_AUTHORIZATION=f"Bearer {mock_livekit_token}",
    )

    assert response.status_code == 200
    assert response.json() == {"status": "success"}

    mock_livekit_client.agent_dispatch.create_dispatch.assert_called_once()

    call_args = mock_livekit_client.agent_dispatch.create_dispatch.call_args[0][0]
    assert call_args.agent_name == "multi-user-transcriber"
    assert call_args.room == "d2aeb774-1ecd-4d73-a3ac-3d3530cad7ff"
    assert (
        RoomSubtitleControl.objects.get(room=room, is_current=True).provider == "legacy"
    )


@pytest.mark.django_db(transaction=True)
def test_start_subtitle_is_idempotent_when_provider_is_already_active(
    settings, mock_livekit_client, mock_livekit_token, mock_room_id
):
    """Do not create a second subtitle provider dispatch."""

    settings.ROOM_SUBTITLE_ENABLED = True
    room = RoomFactory(id=mock_room_id)
    mock_livekit_client.agent_dispatch.list_dispatch.return_value = [
        SimpleNamespace(id="AD_existing", agent_name="multi-user-transcriber")
    ]
    client = APIClient()

    response = client.post(
        f"/api/v1.0/rooms/{room.id}/start-subtitle/",
        {},
        HTTP_AUTHORIZATION=f"Bearer {mock_livekit_token}",
    )

    assert response.status_code == 200
    assert response.json() == {"status": "success"}
    mock_livekit_client.agent_dispatch.create_dispatch.assert_not_called()


@pytest.mark.django_db(transaction=True)
def test_two_concurrent_starts_same_provider_are_both_idempotent(
    settings,
    mock_livekit_client,
    mock_livekit_token,
    mock_room_id,
):
    """Two PostgreSQL transactions start one legacy provider dispatch."""

    settings.ROOM_SUBTITLE_ENABLED = True
    settings.CELERY_ENABLED = False
    room = RoomFactory(id=mock_room_id)
    ensure_subtitle_control(room, room_sid="RM_api")
    dispatches = []
    dispatch_lock = threading.Lock()

    async def list_dispatches(*args, **kwargs):
        with dispatch_lock:
            return list(dispatches)

    async def create_dispatch(request):
        with dispatch_lock:
            dispatch = SimpleNamespace(
                id=f"AD_concurrent_{len(dispatches)}"
                if dispatches
                else "AD_concurrent",
                agent_name=request.agent_name,
                metadata=request.metadata,
            )
            dispatches.append(dispatch)
            return dispatch

    mock_livekit_client.agent_dispatch.list_dispatch.side_effect = list_dispatches
    mock_livekit_client.agent_dispatch.create_dispatch.side_effect = create_dispatch

    def start():
        close_old_connections()
        try:
            client = APIClient()
            return client.post(
                f"/api/v1.0/rooms/{room.id}/start-subtitle/",
                {},
                HTTP_AUTHORIZATION=f"Bearer {mock_livekit_token}",
            )
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(lambda _: start(), (1, 2)))

    assert [response.status_code for response in responses] == [200, 200], [
        response.data for response in responses
    ]
    assert len(dispatches) == 1
    control = RoomSubtitleControl.objects.get(room=room, is_current=True)
    assert control.provider == "legacy"


def test_start_subtitle_rejects_a_different_active_provider(
    settings, mock_livekit_client, mock_livekit_token, mock_room_id
):
    """A requested provider cannot replace a different active provider."""

    settings.ROOM_SUBTITLE_ENABLED = True
    room = RoomFactory(id=mock_room_id)
    mock_livekit_client.agent_dispatch.list_dispatch.return_value = [
        SimpleNamespace(
            id="AD_other",
            agent_name="multi-user-transcriber",
            metadata='{"provider":"mistral","roomSid":"RM_api"}',
        )
    ]
    client = APIClient()

    response = client.post(
        f"/api/v1.0/rooms/{room.id}/start-subtitle/",
        {},
        HTTP_AUTHORIZATION=f"Bearer {mock_livekit_token}",
    )

    assert response.status_code == 409
    assert response.json() == {
        "error": "A different subtitle provider is already active for this room."
    }
    mock_livekit_client.agent_dispatch.create_dispatch.assert_not_called()


def test_start_subtitle_twirp_error(
    settings, mock_livekit_client, mock_livekit_token, mock_room_id
):
    """Test handling of LiveKit service errors during subtitle initiation."""

    settings.ROOM_SUBTITLE_ENABLED = True

    room = RoomFactory(id=mock_room_id)
    client = APIClient()

    mock_livekit_client.agent_dispatch.create_dispatch.side_effect = TwirpError(
        msg="Internal server error", code="unknown", status=500
    )

    response = client.post(
        f"/api/v1.0/rooms/{room.id}/start-subtitle/",
        {},
        HTTP_AUTHORIZATION=f"Bearer {mock_livekit_token}",
    )

    assert response.status_code == 500
    assert response.json() == {
        "error": f"Subtitles failed to start for room {room.slug}"
    }


@pytest.mark.django_db(transaction=True)
def test_start_subtitle_allows_a_room_participant(
    settings, mock_livekit_client, mock_livekit_participant_token, mock_room_id
):
    """Keep the existing room-access contract independent from control rights."""

    settings.ROOM_SUBTITLE_ENABLED = True
    room = RoomFactory(id=mock_room_id)
    client = APIClient()

    response = client.post(
        f"/api/v1.0/rooms/{room.id}/start-subtitle/",
        {},
        HTTP_AUTHORIZATION=f"Bearer {mock_livekit_participant_token}",
    )

    assert response.status_code == 200
    mock_livekit_client.agent_dispatch.create_dispatch.assert_called_once()


def test_start_subtitle_wrong_room(settings, mock_livekit_token):
    """Test that tokens are validated against the correct room ID."""

    settings.ROOM_SUBTITLE_ENABLED = True

    room = RoomFactory()
    client = APIClient()

    response = client.post(
        f"/api/v1.0/rooms/{room.id}/start-subtitle/",
        {},
        HTTP_AUTHORIZATION=f"Bearer {mock_livekit_token}",
    )

    assert response.status_code == 403
    assert response.json() == {
        "detail": "You do not have permission to perform this action."
    }


def test_start_subtitle_wrong_signature(settings, mock_livekit_token):
    """Test that tokens signed with incorrect signature are rejected."""

    settings.ROOM_SUBTITLE_ENABLED = True
    settings.LIVEKIT_CONFIGURATION["api_secret"] = "wrong-secret-padded-to-32-bytes!!"

    room = RoomFactory()
    client = APIClient()

    response = client.post(
        f"/api/v1.0/rooms/{room.id}/start-subtitle/",
        {},
        HTTP_AUTHORIZATION=f"Bearer {mock_livekit_token}",
    )

    assert response.status_code == 403
    assert response.json() == {
        "detail": "Invalid LiveKit token: Signature verification failed"
    }


def test_subtitle_state_requires_a_room_scoped_livekit_token():
    """Require a room-scoped token for the subtitle snapshot."""
    room = RoomFactory()
    client = APIClient()

    response = client.get(f"/api/v1.0/rooms/{room.id}/subtitle-state/")

    assert response.status_code == 403


def test_subtitle_state_returns_public_snapshot(mock_livekit_token, mock_room_id):
    """Return the persisted public snapshot through the room API."""
    room = RoomFactory(id=mock_room_id)
    control = ensure_subtitle_control(room, room_sid="RM_api_snapshot")
    compare_and_set_subtitle_control(
        control.room_sid,
        expected_control_generation=0,
        expected_state_version=0,
        session_id="session-api",
        public_state=RoomSubtitleControl.PublicState.LIVE,
        desired_state=RoomSubtitleControl.DesiredState.ON,
    )
    client = APIClient()

    response = client.get(
        f"/api/v1.0/rooms/{room.id}/subtitle-state/",
        HTTP_AUTHORIZATION=f"Bearer {mock_livekit_token}",
    )

    assert response.status_code == 200
    assert response.json()["subtitle"]["state"] == "live"
    assert response.json()["subtitle"]["stateVersion"] == 1
    assert response.json()["subtitle"]["sessionId"] == "session-api"


@pytest.mark.django_db(transaction=True)
def test_stop_subtitle_persists_off_and_cleans_provider(
    settings, mock_livekit_client, mock_livekit_token, mock_room_id
):
    """Stopping through the API persists OFF before provider cleanup."""

    settings.ROOM_SUBTITLE_ENABLED = True
    room = RoomFactory(id=mock_room_id)
    control = ensure_subtitle_control(room, room_sid="RM_api")
    compare_and_set_subtitle_control(
        control.room_sid,
        expected_control_generation=0,
        expected_state_version=0,
        desired_state=RoomSubtitleControl.DesiredState.ON,
        public_state=RoomSubtitleControl.PublicState.LIVE,
        session_id="session-api",
        agent_present=True,
        worker_ready=True,
    )
    mock_livekit_client.agent_dispatch.list_dispatch.side_effect = [
        [
            SimpleNamespace(
                id="AD_api",
                agent_name="multi-user-transcriber",
                metadata=None,
            )
        ],
        [],
    ]
    client = APIClient()

    response = client.post(
        f"/api/v1.0/rooms/{room.id}/stop-subtitle/",
        {},
        HTTP_AUTHORIZATION=f"Bearer {mock_livekit_token}",
    )

    assert response.status_code == 200
    control.refresh_from_db()
    assert control.desired_state == RoomSubtitleControl.DesiredState.OFF
    assert control.public_state == RoomSubtitleControl.PublicState.STOPPED
    mock_livekit_client.agent_dispatch.delete_dispatch.assert_awaited_once_with(
        dispatch_id="AD_api", room_name=str(room.id)
    )


def test_stop_subtitle_requires_a_room_administrator(
    settings, mock_livekit_participant_token, mock_room_id
):
    """A regular room participant cannot change the subtitle control intent."""

    settings.ROOM_SUBTITLE_ENABLED = True
    room = RoomFactory(id=mock_room_id)
    client = APIClient()

    response = client.post(
        f"/api/v1.0/rooms/{room.id}/stop-subtitle/",
        {},
        HTTP_AUTHORIZATION=f"Bearer {mock_livekit_participant_token}",
    )

    assert response.status_code == 403


def test_subtitle_state_rejects_a_token_for_another_room(mock_livekit_token):
    """Reject a valid token scoped to another room."""
    room = RoomFactory()
    client = APIClient()

    response = client.get(
        f"/api/v1.0/rooms/{room.id}/subtitle-state/",
        HTTP_AUTHORIZATION=f"Bearer {mock_livekit_token}",
    )

    assert response.status_code == 403
    assert response.json() == {
        "detail": "You do not have permission to perform this action."
    }
