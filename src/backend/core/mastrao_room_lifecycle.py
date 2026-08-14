"""Canonical Mastrao room lifecycle gates."""

# pylint: disable=no-member

from core import models


class MastraoRoomClosed(Exception):
    """Raised when an operation targets a tombstoned canonical room."""


def room_binding(room):
    """Return the canonical binding for a Room, if one exists."""

    return (
        models.MastraoRoomBinding.objects.filter(room=room)
        .select_related("room")
        .first()
    )


def is_mastrao_room_closed(value) -> bool:
    """Return whether a binding or Room has a durable closure tombstone."""

    binding_id = getattr(value, "room_binding_id", None)
    if binding_id is None:
        if isinstance(value, models.MastraoRoomBinding):
            binding_id = value.pk
        else:
            binding_id = (
                models.MastraoRoomBinding.objects.filter(room=value)
                .values_list("pk", flat=True)
                .first()
            )
    if binding_id is None:
        return False
    return models.MastraoRoomClosure.objects.filter(room_binding_id=binding_id).exists()


def assert_mastrao_room_open(value):
    """Fail closed when the exact canonical binding is tombstoned."""

    if is_mastrao_room_closed(value):
        raise MastraoRoomClosed("canonical room is closed")
