"""Room-scoped subtitle control state and its public snapshot."""
# pylint: disable=cyclic-import

from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import IntegrityError, transaction

from ..models import Room, RoomSubtitleControl


class SubtitleControlError(Exception):
    """Base error for the subtitle control contract."""


class SubtitleControlContractError(SubtitleControlError):
    """The caller supplied an invalid or already-bound LiveKit room SID."""


class SubtitleControlConflict(SubtitleControlError):
    """The compare-and-set preconditions no longer match persisted state."""


_validate_room_sid = RegexValidator(
    regex=r"^RM_[A-Za-z0-9_-]{1,124}$",
    message="room_sid must be a LiveKit room SID.",
)
_MUTABLE_FIELDS = {
    "provider",
    "agent_name",
    "session_id",
    "public_state",
    "reason_code",
    "observed_dispatch_ids",
    "agent_present",
    "worker_ready",
    "worker_observed_at",
    "pending_since",
    "attempts",
    "next_retry_at",
    "desired_state",
    "room_finished_at",
}


def _check_room_sid(room_sid):
    """Validate the explicit provider identity boundary."""

    try:
        _validate_room_sid(room_sid)
    except ValidationError as error:
        raise SubtitleControlContractError(str(error)) from error


def _room_id_for_sid(room_sid):
    """Resolve a SID without taking a blocking business-row lock."""

    room_id = (
        RoomSubtitleControl.objects.filter(room_sid=room_sid)
        .values_list("room_id", flat=True)
        .first()
    )
    if room_id is None:
        raise SubtitleControlContractError(
            "The LiveKit room SID has not been acquired."
        )
    return room_id


def lock_room_and_control(room, *, room_sid=None, current_only=True):
    """Lock Room then its subtitle control inside an existing transaction."""

    locked_room = Room.objects.select_for_update().get(pk=room.pk)
    controls = RoomSubtitleControl.objects.select_for_update().filter(room=locked_room)
    if room_sid is not None:
        controls = controls.filter(room_sid=room_sid)
    if current_only:
        controls = controls.filter(is_current=True)
    return locked_room, controls.first()


def _apply_locked_cas(
    locked_room,
    control,
    *,
    expected_control_generation,
    expected_state_version,
    new_intent=False,
    **changes,
):
    """Apply a CAS after the caller acquired Room then Control."""

    if control is None or control.room_id != locked_room.pk:
        raise SubtitleControlContractError(
            "The LiveKit room SID has not been acquired for this room."
        )
    unknown_fields = set(changes) - _MUTABLE_FIELDS
    if unknown_fields:
        raise SubtitleControlContractError(
            f"Unsupported subtitle control fields: {sorted(unknown_fields)}"
        )
    if (
        control.control_generation != expected_control_generation
        or control.state_version != expected_state_version
    ):
        raise SubtitleControlConflict(
            "Subtitle control state changed before this transition was applied."
        )

    desired_state_changed = (
        "desired_state" in changes and changes["desired_state"] != control.desired_state
    )
    for field, value in changes.items():
        setattr(control, field, value)
    control.state_version += 1
    if desired_state_changed or new_intent:
        control.control_generation += 1
    try:
        control.save()
    except ValidationError as error:
        raise SubtitleControlContractError(str(error)) from error
    from .subtitle_reconciliation import (  # noqa: PLC0415  # pylint: disable=import-outside-toplevel
        queue_subtitle_snapshot,
    )

    queue_subtitle_snapshot(control.room_id)
    return control


@transaction.atomic
def ensure_subtitle_control(room: Room, *, room_sid: str) -> RoomSubtitleControl:
    """Acquire a control row for an already-resolved LiveKit room SID.

    This function intentionally does not call LiveKit or dispatch an agent.
    The caller must provide the real provider SID; a room name or application
    token is not a valid substitute.
    """

    _check_room_sid(room_sid)
    existing_room_id = (
        RoomSubtitleControl.objects.filter(room_sid=room_sid)
        .values_list("room_id", flat=True)
        .first()
    )
    if existing_room_id is not None and existing_room_id != room.pk:
        raise SubtitleControlContractError(
            "The LiveKit room SID is already bound to another room."
        )

    locked_room = Room.objects.select_for_update().get(pk=room.pk)
    control = (
        RoomSubtitleControl.objects.select_for_update()
        .filter(room_sid=room_sid)
        .first()
    )
    if control is not None and control.room_id != locked_room.pk:
        raise SubtitleControlContractError(
            "The LiveKit room SID is already bound to another room."
        )

    if control is None:
        control = RoomSubtitleControl(
            room=locked_room,
            room_sid=room_sid,
            is_current=False,
        )
        try:
            with transaction.atomic():
                control.save()
        except IntegrityError as error:
            control = RoomSubtitleControl.objects.select_for_update().get(
                room_sid=room_sid
            )
            if control.room_id != locked_room.pk:
                raise SubtitleControlContractError(
                    "The LiveKit room SID is already bound to another room."
                ) from error
        except ValidationError as error:
            raise SubtitleControlContractError(str(error)) from error

    current_control = (
        RoomSubtitleControl.objects.select_for_update()
        .filter(room=locked_room, is_current=True)
        .exclude(pk=control.pk)
        .first()
    )
    if current_control is not None:
        current_control.is_current = False
        current_control.save(update_fields=["is_current"])

    if not control.is_current:
        control.is_current = True
        try:
            control.save(update_fields=["is_current"])
        except ValidationError as error:
            raise SubtitleControlContractError(str(error)) from error

    return control


@transaction.atomic
def compare_and_set_subtitle_control(
    room_sid: str,
    *,
    expected_control_generation: int,
    expected_state_version: int,
    new_intent: bool = False,
    current_only: bool = False,
    **changes,
) -> RoomSubtitleControl:
    """Apply one state transition using generation and version CAS.

    Every accepted transition increments ``state_version`` while holding the
    row lock. ``control_generation`` changes only when the desired state
    changes or the caller explicitly marks a new intent.
    """

    _check_room_sid(room_sid)
    room_id = _room_id_for_sid(room_sid)
    locked_room = Room.objects.select_for_update().get(pk=room_id)
    controls = RoomSubtitleControl.objects.select_for_update().filter(
        room=locked_room,
        room_sid=room_sid,
    )
    if current_only:
        controls = controls.filter(is_current=True)
    try:
        control = controls.get()
    except RoomSubtitleControl.DoesNotExist as error:
        raise SubtitleControlContractError(
            "The LiveKit room SID has not been acquired."
        ) from error
    return _apply_locked_cas(
        locked_room,
        control,
        expected_control_generation=expected_control_generation,
        expected_state_version=expected_state_version,
        new_intent=new_intent,
        **changes,
    )


def compare_and_set_subtitle_control_locked(
    locked_room,
    control,
    *,
    expected_control_generation,
    expected_state_version,
    new_intent=False,
    **changes,
):
    """Apply a CAS without opening a nested transaction."""

    return _apply_locked_cas(
        locked_room,
        control,
        expected_control_generation=expected_control_generation,
        expected_state_version=expected_state_version,
        new_intent=new_intent,
        **changes,
    )


def _serialize_control(control: RoomSubtitleControl | None) -> dict:
    """Return only the public subtitle state contract."""

    if control is None:
        return {
            "state": RoomSubtitleControl.PublicState.INACTIVE,
            "stateVersion": 0,
            "sessionId": None,
            "updatedAt": None,
            "reason": None,
            "desired": RoomSubtitleControl.DesiredState.OFF,
            "roomSid": None,
        }

    return {
        "state": control.public_state,
        "stateVersion": control.state_version,
        "sessionId": control.session_id,
        "updatedAt": control.updated_at.isoformat() if control.updated_at else None,
        "reason": control.reason_code,
        "desired": control.desired_state,
        "roomSid": control.room_sid,
    }


def get_subtitle_snapshot(room: Room) -> dict:
    """Read the latest room-scoped public subtitle snapshot."""

    control = (
        RoomSubtitleControl.objects.filter(room=room, is_current=True)
        .order_by("-updated_at", "-created_at")
        .first()
    )
    return _serialize_control(control)
