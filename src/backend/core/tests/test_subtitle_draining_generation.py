"""Exact terminal delivery generation across subtitle intent transitions."""

from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest import mock
from uuid import uuid4

from django.core.exceptions import ValidationError
from django.db import IntegrityError, close_old_connections
from django.utils import timezone

import pytest

from core.factories import RoomFactory
from core.models import RoomSubtitleControl
from core.services.subtitle_control import (
    SubtitleControlConflict,
    SubtitleControlContractError,
    _serialize_control,
    compare_and_set_subtitle_control,
    compare_and_set_subtitle_control_locked,
)


@pytest.fixture(name="control")
def subtitle_control_fixture():
    """Use a real unsaved model while forbidding persistence and notifications."""
    instance = RoomSubtitleControl(
        room_id=uuid4(),
        room_sid="RM_drain_transition",
        provider="openai-live",
        desired_state=RoomSubtitleControl.DesiredState.ON,
        control_generation=7,
        state_version=2,
    )
    with (
        mock.patch.object(instance, "save"),
        mock.patch("core.services.subtitle_reconciliation.queue_subtitle_snapshot"),
    ):
        yield instance


def _transition(control, **changes):
    return compare_and_set_subtitle_control_locked(
        SimpleNamespace(pk=control.room_id),
        control,
        expected_control_generation=control.control_generation,
        expected_state_version=control.state_version,
        **changes,
    )


@pytest.mark.parametrize("generation", [0, 7])
def test_stop_retains_exact_on_generation_before_increment(control, generation):
    """The drain identifies the stopped ON, including a zero generation."""
    control.control_generation = generation

    _transition(control, desired_state="OFF", new_intent=True)

    assert control.draining_generation == generation
    assert control.control_generation == generation + 1
    assert control.state_version == 3
    control.save.assert_called_once_with()


def test_state_change_without_new_intent_still_retains_drain(control):
    """The state transition itself suffices to capture the old generation."""
    _transition(control, desired_state="OFF")

    assert control.draining_generation == 7
    assert control.control_generation == 8


def test_room_finished_after_off_preserves_original_drain(control):
    """A second OFF intent cannot replace the generation which held finals."""
    _transition(control, desired_state="OFF", new_intent=True)
    _transition(
        control,
        desired_state="OFF",
        new_intent=True,
        reason_code=RoomSubtitleControl.ReasonCode.ROOM_FINISHED,
        room_finished_at=timezone.now(),
    )

    assert control.draining_generation == 7
    assert control.control_generation == 9
    assert control.room_finished_at is not None


@pytest.mark.parametrize("new_intent", [False, True])
def test_initial_off_never_creates_a_drain(control, new_intent):
    """Stopping an inactive control does not invent prior delivery authority."""
    control.desired_state = "OFF"
    control.control_generation = 0
    assert control.draining_generation is None

    _transition(
        control,
        desired_state="OFF",
        new_intent=new_intent,
        room_finished_at=timezone.now(),
    )

    assert control.draining_generation is None


@pytest.mark.parametrize("explicit_desired_state", [False, True])
def test_new_on_intent_clears_any_previous_drain(control, explicit_desired_state):
    """Both a restart and a renewed ON intent invalidate old final delivery."""
    _transition(control, desired_state="OFF", new_intent=True)
    if explicit_desired_state:
        changes = {"desired_state": "ON"}
    else:
        # Existing CAS callers can mark an intent without repeating its state.
        control.desired_state = "ON"
        changes = {}

    _transition(control, new_intent=True, **changes)

    assert control.draining_generation is None
    assert control.control_generation == 9


def test_next_stop_records_new_on_generation(control):
    """A later stop must not restore the previous session's drain."""
    _transition(control, desired_state="OFF", new_intent=True)
    _transition(control, desired_state="ON", new_intent=True)
    _transition(control, desired_state="OFF", new_intent=True)

    assert control.draining_generation == 9
    assert control.control_generation == 10


@pytest.mark.parametrize(
    "changes",
    [
        {"public_state": "stopped", "worker_ready": False, "agent_present": False},
        {"public_state": "stopping", "attempts": 1, "next_retry_at": timezone.now()},
        {"reason_code": "provider_unavailable"},
    ],
)
def test_stop_ack_and_error_observations_preserve_drain(control, changes):
    """Cleanup completion and provider errors change no terminal generation."""
    _transition(control, desired_state="OFF", new_intent=True)
    _transition(control, **changes)

    assert control.draining_generation == 7
    assert control.control_generation == 8


@pytest.mark.parametrize("stale_field", ["generation", "version"])
def test_stale_cas_cannot_restore_drain_after_restart(control, stale_field):
    """A losing stop cannot overwrite the newer ON after the locks serialize it."""
    _transition(control, desired_state="OFF", new_intent=True)
    _transition(control, desired_state="ON", new_intent=True)
    expected_generation = control.control_generation
    expected_version = control.state_version
    if stale_field == "generation":
        expected_generation -= 1
    else:
        expected_version -= 1
    control.save.reset_mock()

    with pytest.raises(SubtitleControlConflict):
        compare_and_set_subtitle_control_locked(
            SimpleNamespace(pk=control.room_id),
            control,
            expected_control_generation=expected_generation,
            expected_state_version=expected_version,
            desired_state="OFF",
        )

    assert control.desired_state == "ON"
    assert control.draining_generation is None
    assert control.control_generation == 9
    assert control.state_version == 4
    control.save.assert_not_called()


def test_caller_cannot_supply_a_draining_generation(control):
    """Only the CAS may derive the private terminal-delivery generation."""
    with pytest.raises(SubtitleControlContractError):
        _transition(control, desired_state="OFF", draining_generation=6)

    assert control.draining_generation is None
    assert control.desired_state == "ON"
    control.save.assert_not_called()


@pytest.mark.parametrize(
    "error", [ValidationError("invalid"), IntegrityError("failed")]
)
def test_failed_save_emits_no_success_snapshot(control, error):
    """A failed transition propagates before any terminal state notification."""
    control.save.side_effect = error
    expected_error = type(error)
    if isinstance(error, ValidationError):
        expected_error = SubtitleControlContractError

    with mock.patch(
        "core.services.subtitle_reconciliation.queue_subtitle_snapshot"
    ) as queue:
        with pytest.raises(expected_error):
            _transition(control, desired_state="OFF", new_intent=True)
        queue.assert_not_called()


def test_drain_is_absent_from_public_snapshot(control):
    """Terminal delivery metadata is never part of the subtitle UI contract."""
    _transition(control, desired_state="OFF", new_intent=True)

    assert set(_serialize_control(control)) == {
        "state",
        "stateVersion",
        "sessionId",
        "updatedAt",
        "reason",
        "desired",
        "roomSid",
    }


@pytest.mark.django_db
def test_failed_transition_persists_neither_off_nor_drain():
    """Model validation failure must leave the stored ON intent untouched."""
    control = RoomSubtitleControl.objects.create(
        room=RoomFactory(),
        room_sid="RM_drain_failed_save",
        desired_state="ON",
        control_generation=7,
    )

    with pytest.raises(SubtitleControlContractError):
        compare_and_set_subtitle_control(
            control.room_sid,
            expected_control_generation=7,
            expected_state_version=0,
            desired_state="OFF",
            public_state="invalid",
        )

    control.refresh_from_db()
    assert control.desired_state == "ON"
    assert control.control_generation == 7
    assert control.state_version == 0
    assert control.draining_generation is None


@pytest.mark.django_db(transaction=True)
def test_concurrent_stop_and_new_on_intent_have_one_winner():
    """Room/Control locks and CAS must commit one internally consistent intent."""
    control = RoomSubtitleControl.objects.create(
        room=RoomFactory(),
        room_sid="RM_drain_concurrent",
        desired_state="ON",
        control_generation=7,
    )

    def attempt(desired_state):
        close_old_connections()
        try:
            compare_and_set_subtitle_control(
                control.room_sid,
                expected_control_generation=7,
                expected_state_version=0,
                desired_state=desired_state,
                new_intent=True,
            )
            return desired_state
        except SubtitleControlConflict:
            return "conflict"
        finally:
            close_old_connections()

    with mock.patch("core.services.subtitle_reconciliation.queue_subtitle_snapshot"):
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(attempt, ["OFF", "ON"]))

    assert results.count("conflict") == 1
    control.refresh_from_db()
    assert control.control_generation == 8
    assert control.state_version == 1
    assert control.desired_state in results
    if control.desired_state == "OFF":
        assert control.draining_generation == 7
    else:
        assert control.draining_generation is None
