"""Room-scoped subtitle control state and its public snapshot."""

from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import transaction

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
}


def _check_room_sid(room_sid):
    """Validate the explicit provider identity boundary."""

    try:
        _validate_room_sid(room_sid)
    except ValidationError as error:
        raise SubtitleControlContractError(str(error)) from error


@transaction.atomic
def ensure_subtitle_control(room: Room, *, room_sid: str) -> RoomSubtitleControl:
    """Acquire a control row for an already-resolved LiveKit room SID.

    This function intentionally does not call LiveKit or dispatch an agent.
    The caller must provide the real provider SID; a room name or application
    token is not a valid substitute.
    """

    _check_room_sid(room_sid)
    conflicting_control = (
        RoomSubtitleControl.objects.select_for_update()
        .filter(room_sid=room_sid)
        .exclude(room=room)
        .first()
    )
    if conflicting_control is not None:
        raise SubtitleControlContractError(
            "The LiveKit room SID is already bound to another room."
        )

    control, _ = RoomSubtitleControl.objects.select_for_update().get_or_create(
        room=room,
        room_sid=room_sid,
    )
    return control


@transaction.atomic
def compare_and_set_subtitle_control(
    room_sid: str,
    *,
    expected_control_generation: int,
    expected_state_version: int,
    **changes,
) -> RoomSubtitleControl:
    """Apply one state transition using generation and version CAS.

    Every accepted transition increments ``state_version`` and
    ``control_generation`` while holding the row lock.  Therefore a new
    session cannot reset the public version and stale workers cannot overwrite
    a newer state.
    """

    _check_room_sid(room_sid)
    unknown_fields = set(changes) - _MUTABLE_FIELDS
    if unknown_fields:
        raise SubtitleControlContractError(
            f"Unsupported subtitle control fields: {sorted(unknown_fields)}"
        )

    try:
        control = (
            RoomSubtitleControl.objects.select_for_update()
            .select_related("room")
            .get(room_sid=room_sid)
        )
    except RoomSubtitleControl.DoesNotExist as error:
        raise SubtitleControlContractError(
            "The LiveKit room SID has not been acquired."
        ) from error

    if (
        control.control_generation != expected_control_generation
        or control.state_version != expected_state_version
    ):
        raise SubtitleControlConflict(
            "Subtitle control state changed before this transition was applied."
        )

    for field, value in changes.items():
        setattr(control, field, value)
    control.control_generation += 1
    control.state_version += 1
    control.save()
    return control


def _serialize_control(control: RoomSubtitleControl | None) -> dict:
    """Return only the public subtitle state contract."""

    if control is None:
        return {
            "state": RoomSubtitleControl.PublicState.UNKNOWN,
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
        RoomSubtitleControl.objects.filter(room=room)
        .order_by("-updated_at", "-created_at")
        .first()
    )
    return _serialize_control(control)
