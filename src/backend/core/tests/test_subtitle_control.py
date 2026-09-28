"""Tests for the room-scoped subtitle control state."""

from types import SimpleNamespace

from django.core.exceptions import ValidationError

import pytest

from ..api.permissions import can_control_subtitles, can_view_subtitle_state
from ..factories import RoomFactory
from ..models import RoomSubtitleControl
from ..services.subtitle_control import (
    SubtitleControlConflict,
    SubtitleControlContractError,
    compare_and_set_subtitle_control,
    ensure_subtitle_control,
    get_subtitle_snapshot,
)

pytestmark = pytest.mark.django_db


def _request(*, room_id, room_admin=False):
    return SimpleNamespace(
        auth=SimpleNamespace(
            video=SimpleNamespace(room=str(room_id), room_admin=room_admin)
        )
    )


def test_room_sid_is_required_and_control_defaults_are_safe():
    """Reject non-SID identifiers and keep new controls inactive."""
    room = RoomFactory()

    with pytest.raises(SubtitleControlContractError):
        ensure_subtitle_control(room, room_sid="room-name")

    control = ensure_subtitle_control(room, room_sid="RM_backend_a")

    assert control.public_state == RoomSubtitleControl.PublicState.UNKNOWN
    assert control.desired_state == RoomSubtitleControl.DesiredState.OFF
    assert control.state_version == 0
    assert control.control_generation == 0
    assert control.observed_dispatch_ids == []


def test_room_sid_acquisition_is_idempotent_and_scoped_to_room():
    """Acquire one row per room and reject a SID bound to another room."""
    room = RoomFactory()
    other_room = RoomFactory()

    first = ensure_subtitle_control(room, room_sid="RM_same_sid")
    second = ensure_subtitle_control(room, room_sid="RM_same_sid")
    with pytest.raises(SubtitleControlContractError):
        ensure_subtitle_control(other_room, room_sid="RM_same_sid")

    assert first.pk == second.pk
    assert RoomSubtitleControl.objects.filter(room=room).count() == 1


def test_compare_and_set_increments_version_and_keeps_it_across_sessions():
    """Increment versions monotonically when a session changes."""
    room = RoomFactory()
    control = ensure_subtitle_control(room, room_sid="RM_monotone")

    first = compare_and_set_subtitle_control(
        control.room_sid,
        expected_control_generation=0,
        expected_state_version=0,
        session_id="session-one",
        public_state=RoomSubtitleControl.PublicState.LIVE,
        desired_state=RoomSubtitleControl.DesiredState.ON,
    )
    second = compare_and_set_subtitle_control(
        control.room_sid,
        expected_control_generation=first.control_generation,
        expected_state_version=first.state_version,
        session_id="session-two",
        public_state=RoomSubtitleControl.PublicState.RECONNECTING,
    )

    assert first.state_version == 1
    assert second.state_version == 2
    assert second.control_generation == 2
    assert second.session_id == "session-two"


def test_compare_and_set_rejects_stale_generation_or_version():
    """Reject stale compare-and-set preconditions."""
    room = RoomFactory()
    control = ensure_subtitle_control(room, room_sid="RM_cas")
    compare_and_set_subtitle_control(
        control.room_sid,
        expected_control_generation=0,
        expected_state_version=0,
        public_state=RoomSubtitleControl.PublicState.STARTING,
    )

    with pytest.raises(SubtitleControlConflict):
        compare_and_set_subtitle_control(
            control.room_sid,
            expected_control_generation=0,
            expected_state_version=0,
            public_state=RoomSubtitleControl.PublicState.LIVE,
        )


def test_snapshot_is_public_and_unknown_when_no_sid_has_been_acquired():
    """Expose only the public state contract, including the unknown default."""
    room = RoomFactory()

    assert get_subtitle_snapshot(room) == {
        "state": "unknown",
        "stateVersion": 0,
        "sessionId": None,
        "updatedAt": None,
        "reason": None,
        "desired": "OFF",
        "roomSid": None,
    }

    control = ensure_subtitle_control(room, room_sid="RM_snapshot")
    compare_and_set_subtitle_control(
        control.room_sid,
        expected_control_generation=0,
        expected_state_version=0,
        session_id="session-public",
        public_state=RoomSubtitleControl.PublicState.DEGRADED,
        reason_code=RoomSubtitleControl.ReasonCode.WORKER_NOT_READY,
        desired_state=RoomSubtitleControl.DesiredState.ON,
    )

    snapshot = get_subtitle_snapshot(room)
    assert snapshot["state"] == "degraded"
    assert snapshot["stateVersion"] == 1
    assert snapshot["sessionId"] == "session-public"
    assert snapshot["reason"] == "worker_not_ready"
    assert snapshot["desired"] == "ON"
    assert snapshot["roomSid"] == "RM_snapshot"


def test_subtitle_permissions_are_separate_and_room_scoped():
    """Separate read access from administrator-only control access."""
    room = RoomFactory()
    viewer = _request(room_id=room.id)
    controller = _request(room_id=room.id, room_admin=True)
    wrong_room = _request(room_id=RoomFactory().id, room_admin=True)

    assert can_view_subtitle_state(viewer, room)
    assert not can_control_subtitles(viewer, room)
    assert can_control_subtitles(controller, room)
    assert not can_view_subtitle_state(wrong_room, room)
    assert not can_control_subtitles(wrong_room, room)


def test_observed_dispatch_ids_are_bounded():
    """Reject unbounded provider dispatch history."""
    room = RoomFactory()
    control = RoomSubtitleControl(
        room=room,
        room_sid="RM_dispatches",
        observed_dispatch_ids=[f"dispatch-{index}" for index in range(33)],
    )

    with pytest.raises(ValidationError):
        control.full_clean()
