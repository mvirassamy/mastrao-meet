"""Idempotent Django persistence for a verified Mastrao room effect."""

import hashlib

from django.contrib.auth.hashers import make_password
from django.db import connection, transaction

from core.mastrao_room_contract import RoomEffectRefused, _sha256_canonical
from core.models import (
    MastraoRoomBinding,
    ResourceAccess,
    RoleChoices,
    Room,
    RoomAccessLevel,
    User,
)


def _technical_owner_sub(owner_ref):
    return f"mastrao_{hashlib.sha256(owner_ref.encode()).hexdigest()}"


def _provider_binding_digest(room, owner, access):
    return _sha256_canonical(
        {
            "access_id": str(access.id),
            "access_level": RoomAccessLevel.RESTRICTED,
            "owner_id": str(owner.id),
            "role": RoleChoices.OWNER,
            "room_id": str(room.id),
            "slug": room.slug,
        }
    )


def _validate_existing(binding, effect):
    access = ResourceAccess.objects.filter(
        resource=binding.room,
        user=binding.owner,
        role=RoleChoices.OWNER,
    ).first()
    if (
        binding.arguments_digest != effect["arguments_digest"]
        or binding.meeting_ref != effect["meeting_ref"]
        or binding.room_ref != effect["room_ref"]
        or binding.owner_ref != effect["owner_ref"]
        or binding.room.name != effect["room_ref"]
        or binding.room.slug != effect["room_ref"]
        or binding.room.access_level != RoomAccessLevel.RESTRICTED
        or binding.owner.sub != _technical_owner_sub(effect["owner_ref"])
        or not binding.owner.is_device
        or access is None
        or binding.provider_binding_digest
        != _provider_binding_digest(binding.room, binding.owner, access)
    ):
        raise RoomEffectRefused(status=409)
    return binding


@transaction.atomic
def ensure_room(effect):
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
            [effect["effect_key"]],
        )
    existing = (
        MastraoRoomBinding.objects.select_related("room", "owner")
        .filter(effect_key=effect["effect_key"])
        .first()
    )
    if existing:
        return _validate_existing(existing, effect)
    if Room.objects.filter(slug=effect["room_ref"]).exists():
        raise RoomEffectRefused(status=409)

    owner, _created = User.objects.get_or_create(
        sub=_technical_owner_sub(effect["owner_ref"]),
        defaults={"password": make_password(None), "is_device": True},
    )
    if not owner.is_device:
        raise RoomEffectRefused(status=409)
    room = Room.objects.create(
        name=effect["room_ref"],
        access_level=RoomAccessLevel.RESTRICTED,
    )
    access = ResourceAccess.objects.create(
        resource=room,
        user=owner,
        role=RoleChoices.OWNER,
    )
    provider_digest = _provider_binding_digest(room, owner, access)
    return MastraoRoomBinding.objects.create(
        effect_key=effect["effect_key"],
        arguments_digest=effect["arguments_digest"],
        meeting_ref=effect["meeting_ref"],
        room_ref=effect["room_ref"],
        owner_ref=effect["owner_ref"],
        room=room,
        owner=owner,
        provider_binding_digest=provider_digest,
    )
