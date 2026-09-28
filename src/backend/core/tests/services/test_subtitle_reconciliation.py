"""Tests for the durable subtitle agent reconciler."""
# pylint: disable=redefined-outer-name

from unittest import mock

from django.db import transaction

import pytest

from core.factories import RoomFactory
from core.models import RoomSubtitleControl
from core.services.subtitle_control import ensure_subtitle_control
from core.services.subtitle_reconciliation import (
    SUBTITLE_STATUS_TOPIC,
    SubtitleReconciliationAmbiguous,
    observe_subtitle_agent,
    reconcile_subtitle_control,
    request_subtitle_stop,
)

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def enable_subtitles(settings):
    """Keep the reconciler tests on the non-kill-switch path."""
    settings.ROOM_SUBTITLE_ENABLED = True


def _dispatch(dispatch_id, *, agent_name="multi-user-transcriber"):
    return mock.Mock(id=dispatch_id, agent_name=agent_name)


@pytest.fixture
def mock_livekit_client():
    """Provide a fake LiveKit client with explicit async provider methods."""
    with mock.patch("core.utils.create_livekit_client") as create_client:
        client = mock.AsyncMock()
        client.agent_dispatch.list_dispatch = mock.AsyncMock(return_value=[])
        client.agent_dispatch.create_dispatch = mock.AsyncMock(
            return_value=_dispatch("AD_created")
        )
        client.agent_dispatch.delete_dispatch = mock.AsyncMock()
        client.room.send_data = mock.AsyncMock()
        create_client.return_value = client
        yield client


def _turn_on(control):
    control.desired_state = RoomSubtitleControl.DesiredState.ON
    control.public_state = RoomSubtitleControl.PublicState.STARTING
    control.save(update_fields=["desired_state", "public_state", "updated_at"])
    return control


def test_agent_join_and_leave_update_legacy_presence_and_identity():
    """Participant observations update the durable legacy readiness signal."""
    room = RoomFactory()
    control = _turn_on(ensure_subtitle_control(room, room_sid="RM_agent_presence"))

    joined = observe_subtitle_agent(
        control.room_sid,
        participant_identity="agent-identity-a",
        present=True,
    )
    assert joined.agent_present is True
    assert joined.session_id == "agent-identity-a"
    assert joined.public_state == RoomSubtitleControl.PublicState.LIVE

    left = observe_subtitle_agent(
        control.room_sid,
        participant_identity="agent-identity-a",
        present=False,
    )
    assert left.agent_present is False
    assert left.session_id is None
    assert left.public_state == RoomSubtitleControl.PublicState.RECONNECTING


def test_late_leave_from_another_agent_does_not_clear_current_identity():
    """A stale participant-left event cannot clear the current agent session."""
    room = RoomFactory()
    control = _turn_on(ensure_subtitle_control(room, room_sid="RM_identity_guard"))
    observe_subtitle_agent(
        control.room_sid,
        participant_identity="current-agent",
        present=True,
    )

    observed = observe_subtitle_agent(
        control.room_sid,
        participant_identity="old-agent",
        present=False,
    )

    assert observed.agent_present is True
    assert observed.session_id == "current-agent"


def test_reconcile_dispatches_and_persists_observation(mock_livekit_client):
    """A desired ON row creates one dispatch and records its provider ID."""
    room = RoomFactory()
    control = _turn_on(ensure_subtitle_control(room, room_sid="RM_reconcile"))

    result = reconcile_subtitle_control(control.room_sid)

    assert result.observed_dispatch_ids == ["AD_created"]
    assert result.public_state == RoomSubtitleControl.PublicState.STARTING
    mock_livekit_client.agent_dispatch.create_dispatch.assert_awaited_once()


def test_reconcile_uses_list_delete_list_for_duplicate_dispatches(
    mock_livekit_client,
):
    """Cleanup deletes extras, then verifies provider state with a second list."""
    room = RoomFactory()
    control = _turn_on(ensure_subtitle_control(room, room_sid="RM_duplicate"))
    mock_livekit_client.agent_dispatch.list_dispatch.side_effect = [
        [_dispatch("AD_keep"), _dispatch("AD_delete")],
        [_dispatch("AD_keep")],
    ]

    result = reconcile_subtitle_control(control.room_sid)

    assert result.observed_dispatch_ids == ["AD_keep"]
    mock_livekit_client.agent_dispatch.delete_dispatch.assert_awaited_once_with(
        dispatch_id="AD_delete", room_name=str(room.id)
    )
    assert mock_livekit_client.agent_dispatch.list_dispatch.await_count == 2


def test_cleanup_timeout_is_ambiguous_and_keeps_retry_budget(mock_livekit_client):
    """A cleanup timeout never claims that the provider is clean."""
    room = RoomFactory()
    control = _turn_on(ensure_subtitle_control(room, room_sid="RM_timeout"))
    control = request_subtitle_stop(room, room_sid=control.room_sid)
    mock_livekit_client.agent_dispatch.list_dispatch.side_effect = [
        [_dispatch("AD_timeout")],
    ]

    async def timeout(*args, **kwargs):
        raise TimeoutError("cleanup timed out")

    mock_livekit_client.agent_dispatch.delete_dispatch.side_effect = timeout

    with pytest.raises(SubtitleReconciliationAmbiguous):
        reconcile_subtitle_control(control.room_sid)

    control.refresh_from_db()
    assert control.public_state == RoomSubtitleControl.PublicState.STOPPING
    assert control.attempts == 1
    assert control.next_retry_at is not None


def test_snapshot_packet_is_reliable_and_runs_after_commit(
    mock_livekit_client, django_capture_on_commit_callbacks
):
    """The status packet is emitted only after the committed snapshot exists."""
    room = RoomFactory()
    control = _turn_on(ensure_subtitle_control(room, room_sid="RM_packet"))
    callback_seen = []

    def capture_send(*args, **kwargs):
        callback_seen.append((args, kwargs))

    mock_livekit_client.room.send_data.side_effect = capture_send

    with django_capture_on_commit_callbacks(execute=True):
        with transaction.atomic():
            observe_subtitle_agent(
                control.room_sid,
                participant_identity="agent-packet",
                present=True,
            )
            assert not callback_seen

    assert callback_seen
    request = callback_seen[0][0][0]
    assert request.kind == 0
    assert request.topic == SUBTITLE_STATUS_TOPIC
    assert request.room == str(room.id)
    assert RoomSubtitleControl.objects.get(pk=control.pk).state_version == 1


def test_old_sid_observation_cannot_reactivate_the_current_sid(mock_livekit_client):
    """A late event for an old provider SID cannot change the current row."""
    room = RoomFactory()
    old = ensure_subtitle_control(room, room_sid="RM_old")
    current = _turn_on(ensure_subtitle_control(room, room_sid="RM_current"))

    observe_subtitle_agent(
        old.room_sid,
        participant_identity="stale-agent",
        present=True,
    )

    current.refresh_from_db()
    old.refresh_from_db()
    assert current.is_current is True
    assert current.agent_present is False
    assert current.public_state == RoomSubtitleControl.PublicState.STARTING
    assert old.is_current is False
    assert old.agent_present is False
    mock_livekit_client.agent_dispatch.create_dispatch.assert_not_called()


def test_room_finished_requests_stop_and_preserves_terminal_reason():
    """Room closure projects OFF and a terminal room-finished state."""
    room = RoomFactory()
    control = _turn_on(ensure_subtitle_control(room, room_sid="RM_finished"))

    stopped = request_subtitle_stop(
        room,
        room_sid=control.room_sid,
        reason_code=RoomSubtitleControl.ReasonCode.ROOM_FINISHED,
    )

    assert stopped.desired_state == RoomSubtitleControl.DesiredState.OFF
    assert stopped.public_state == RoomSubtitleControl.PublicState.STOPPED
    assert stopped.reason_code == RoomSubtitleControl.ReasonCode.ROOM_FINISHED


def test_reconcile_retries_are_bounded_without_beat(mock_livekit_client, settings):
    """Provider failures persist a bounded retry and do not require Celery beat."""
    room = RoomFactory()
    control = _turn_on(ensure_subtitle_control(room, room_sid="RM_retries"))
    mock_livekit_client.agent_dispatch.list_dispatch.side_effect = TimeoutError(
        "provider timeout"
    )
    settings.CELERY_ENABLED = False

    with pytest.raises(SubtitleReconciliationAmbiguous):
        reconcile_subtitle_control(control.room_sid)

    control.refresh_from_db()
    assert control.attempts == 1
    assert control.next_retry_at is not None


def test_kill_switch_converges_an_on_intent_to_off(mock_livekit_client, settings):
    """The kill switch disables provider state without creating a dispatch."""
    room = RoomFactory()
    control = _turn_on(ensure_subtitle_control(room, room_sid="RM_kill_switch"))
    settings.ROOM_SUBTITLE_ENABLED = False

    result = reconcile_subtitle_control(control.room_sid)

    assert result.desired_state == RoomSubtitleControl.DesiredState.OFF
    assert result.public_state == RoomSubtitleControl.PublicState.INACTIVE
    assert result.reason_code == RoomSubtitleControl.ReasonCode.PROVIDER_UNAVAILABLE
    mock_livekit_client.agent_dispatch.create_dispatch.assert_not_called()
