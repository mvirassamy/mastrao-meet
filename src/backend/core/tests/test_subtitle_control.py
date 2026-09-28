"""Tests for the room-scoped subtitle control state."""

from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

from django.core.exceptions import ValidationError
from django.db import IntegrityError, close_old_connections, transaction

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

    assert control.public_state == RoomSubtitleControl.PublicState.INACTIVE
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
    """Keep generation stable for observations and advance it for intent."""
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
    assert second.control_generation == first.control_generation
    assert second.session_id == "session-two"

    third = compare_and_set_subtitle_control(
        control.room_sid,
        expected_control_generation=second.control_generation,
        expected_state_version=second.state_version,
        new_intent=True,
        public_state=RoomSubtitleControl.PublicState.LIVE,
    )

    assert third.state_version == 3
    assert third.control_generation == second.control_generation + 1


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
    """Expose an inactive default and only the current public state contract."""
    room = RoomFactory()

    assert get_subtitle_snapshot(room) == {
        "state": "inactive",
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


def test_invalid_values_are_rejected_without_mutating_persisted_state():
    """Reject invalid enum and bounded-array values without a partial write."""
    room = RoomFactory()
    control = ensure_subtitle_control(room, room_sid="RM_invalid")
    before = RoomSubtitleControl.objects.get(pk=control.pk)

    with pytest.raises(SubtitleControlContractError):
        compare_and_set_subtitle_control(
            control.room_sid,
            expected_control_generation=0,
            expected_state_version=0,
            public_state="not-a-public-state",
        )

    with pytest.raises(SubtitleControlContractError):
        compare_and_set_subtitle_control(
            control.room_sid,
            expected_control_generation=0,
            expected_state_version=0,
            desired_state="not-a-desired-state",
        )

    with pytest.raises(SubtitleControlContractError):
        compare_and_set_subtitle_control(
            control.room_sid,
            expected_control_generation=0,
            expected_state_version=0,
            reason_code="not-a-reason-code",
        )

    with pytest.raises(SubtitleControlContractError):
        compare_and_set_subtitle_control(
            control.room_sid,
            expected_control_generation=0,
            expected_state_version=0,
            observed_dispatch_ids=[f"dispatch-{index}" for index in range(33)],
        )

    after = RoomSubtitleControl.objects.get(pk=control.pk)
    assert after.public_state == before.public_state
    assert after.state_version == before.state_version
    assert after.control_generation == before.control_generation


def test_only_the_current_sid_is_returned_after_an_old_sid_is_updated():
    """Keep the newer SID current when an older row receives an observation."""
    room = RoomFactory()
    first = ensure_subtitle_control(room, room_sid="RM_a")
    first = compare_and_set_subtitle_control(
        first.room_sid,
        expected_control_generation=0,
        expected_state_version=0,
        session_id="session-a",
        public_state=RoomSubtitleControl.PublicState.LIVE,
        desired_state=RoomSubtitleControl.DesiredState.ON,
    )
    second = ensure_subtitle_control(room, room_sid="RM_b")
    second = compare_and_set_subtitle_control(
        second.room_sid,
        expected_control_generation=0,
        expected_state_version=0,
        session_id="session-b",
        public_state=RoomSubtitleControl.PublicState.LIVE,
        desired_state=RoomSubtitleControl.DesiredState.ON,
    )

    compare_and_set_subtitle_control(
        first.room_sid,
        expected_control_generation=first.control_generation,
        expected_state_version=first.state_version,
        public_state=RoomSubtitleControl.PublicState.DEGRADED,
    )

    snapshot = get_subtitle_snapshot(room)
    assert snapshot["roomSid"] == second.room_sid
    assert snapshot["sessionId"] == "session-b"
    assert RoomSubtitleControl.objects.get(pk=first.pk).is_current is False
    assert RoomSubtitleControl.objects.get(pk=second.pk).is_current is True


def test_database_allows_only_one_current_control_per_room():
    """Enforce the one-current-row invariant in PostgreSQL."""
    room = RoomFactory()

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            RoomSubtitleControl.objects.bulk_create(
                [
                    RoomSubtitleControl(
                        room=room, room_sid="RM_current_a", is_current=True
                    ),
                    RoomSubtitleControl(
                        room=room, room_sid="RM_current_b", is_current=True
                    ),
                ]
            )


@pytest.mark.django_db(transaction=True)
def test_concurrent_compare_and_set_has_one_winner():
    """Serialize two PostgreSQL CAS attempts so exactly one succeeds."""
    room = RoomFactory()
    control = ensure_subtitle_control(room, room_sid="RM_concurrent")

    def attempt(session_id):
        close_old_connections()
        try:
            compare_and_set_subtitle_control(
                control.room_sid,
                expected_control_generation=0,
                expected_state_version=0,
                session_id=session_id,
                public_state=RoomSubtitleControl.PublicState.LIVE,
            )
            return "success"
        except SubtitleControlConflict:
            return "conflict"
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(attempt, ["session-one", "session-two"]))

    assert sorted(results) == ["conflict", "success"]


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
