"""Room management service for LiveKit rooms."""

# pylint: disable=no-name-in-module,no-member

import json
import uuid
from logging import getLogger
from typing import Dict, Optional

from django.conf import settings
from django.db import transaction

from asgiref.sync import async_to_sync
from livekit.api import (
    CreateRoomRequest,
    DeleteRoomRequest,
    ListRoomsRequest,
    TwirpError,
    UpdateRoomMetadataRequest,
)

from core import models, utils
from core.mastrao_room_lifecycle import assert_mastrao_room_open

logger = getLogger(__name__)


def ensure_livekit_room(room_name: str):
    """Ensure a room only when explicit room creation is enabled."""

    if not settings.LIVEKIT_EXPLICIT_ROOM_CREATION:
        return

    try:
        room_id = uuid.UUID(room_name)
    except ValueError:
        RoomManagement().ensure_room(room_name)
        return

    # Keep the canonical binding lock through provider creation. A concurrent
    # close either observes the room and deletes it, or wins first and blocks
    # creation through its durable tombstone.
    with transaction.atomic():
        binding = (
            models.MastraoRoomBinding.objects.select_for_update()
            .filter(room_id=room_id)
            .first()
        )
        if binding is not None:
            assert_mastrao_room_open(binding)
        RoomManagement().ensure_room(room_name)


class RoomManagementException(Exception):
    """Exception raised when a room management operation fails."""


class RoomNotFoundException(RoomManagementException):
    """Raised when the target room does not exist in LiveKit."""


class RoomManagement:
    """Service for managing LiveKit rooms."""

    @async_to_sync
    async def ensure_room(self, room_name: str):
        """Create a LiveKit room when it does not already exist."""

        lkapi = utils.create_livekit_client()

        try:
            response = await lkapi.room.list_rooms(ListRoomsRequest(names=[room_name]))
            if response.rooms:
                return
            try:
                await lkapi.room.create_room(CreateRoomRequest(name=room_name))
            except TwirpError as error:
                if error.code != "already_exists":
                    raise
            logger.info("Ensured LiveKit room %s", room_name)
        except TwirpError as error:
            logger.exception("Unexpected error ensuring room %s", room_name)
            raise RoomManagementException("Could not ensure room") from error
        finally:
            await lkapi.aclose()

    @async_to_sync
    async def update_metadata(
        self,
        room_name: str,
        metadata: Optional[Dict] = None,
        remove_keys: Optional[list[str]] = None,
    ):
        """Merge values into a LiveKit room's metadata.

        The `room_name` corresponds to the LiveKit room identifier
        (i.e. the Room model's UUID as a string).

        Raises:
            RoomNotFoundException: the room does not exist in LiveKit.
            RoomManagementException: the metadata update otherwise fails.
        """

        lkapi = utils.create_livekit_client()

        try:
            response = await lkapi.room.list_rooms(ListRoomsRequest(names=[room_name]))

            if not response.rooms:
                logger.warning(
                    "Room %s not found in LiveKit, skipping metadata update",
                    room_name,
                )
                raise RoomNotFoundException("Room does not exist")

            existing_metadata = json.loads(response.rooms[0].metadata or "{}")

            for key in remove_keys or []:
                existing_metadata.pop(key, None)

            updated_metadata = {**existing_metadata, **(metadata or {})}

            await lkapi.room.update_room_metadata(
                UpdateRoomMetadataRequest(
                    room=room_name,
                    metadata=json.dumps(updated_metadata),
                )
            )

        except TwirpError as e:
            if e.code == "not_found":
                logger.warning(
                    "Room %s not found in LiveKit, skipping metadata update",
                    room_name,
                )
                raise RoomNotFoundException("Room does not exist") from e

            logger.exception(
                "Unexpected error updating metadata for room %s",
                room_name,
            )
            raise RoomManagementException("Could not update room metadata") from e

        finally:
            await lkapi.aclose()

    @async_to_sync
    async def delete_room(self, room_name: str):
        """Delete a LiveKit room and disconnect all participants.

        Raises:
            RoomNotFoundException: the room does not exist in LiveKit.
            RoomManagementException: the deletion otherwise fails.
        """

        lkapi = utils.create_livekit_client()

        try:
            await lkapi.room.delete_room(DeleteRoomRequest(room=room_name))
            logger.info("Deleted LiveKit room %s", room_name)
        except TwirpError as e:
            if e.code == "not_found":
                logger.warning(
                    "Room %s not found in LiveKit, skipping deletion",
                    room_name,
                )
                raise RoomNotFoundException("Room does not exist") from e

            logger.exception("Unexpected error deleting room %s", room_name)
            raise RoomManagementException("Could not delete room") from e
        finally:
            await lkapi.aclose()
