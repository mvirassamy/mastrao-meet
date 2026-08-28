"""Meeting metadata collection service."""

from logging import getLogger

from django.conf import settings
from django.db import transaction

from asgiref.sync import async_to_sync, sync_to_async
from livekit.protocol.agent_dispatch import (
    CreateAgentDispatchRequest,
)

from core import utils
from core.models import Recording

logger = getLogger(__name__)


class MetadataCollectorException(Exception):
    """Generic exception in the metadata collector."""


class MetadataCollectorService:
    """Service for dispatching and managing the metadata collector agent."""

    @staticmethod
    def _actual_dispatch_id(value):
        return value if isinstance(value, str) and value != "pending" else None

    @async_to_sync
    async def start(
        self,
        recording: Recording,
        *,
        metadata: str | None = None,
        dispatch_option_key: str = "metadata_collector_dispatch_id",
        expected_pending_claim_id: str | None = None,
    ):
        """Explicitly dispatch the metadata collector agent to a room."""

        lkapi = utils.create_livekit_client()
        room_id = str(recording.room.id)

        try:
            try:
                response = await lkapi.agent_dispatch.create_dispatch(
                    CreateAgentDispatchRequest(
                        agent_name=settings.METADATA_COLLECTOR_AGENT_NAME,
                        room=room_id,
                        metadata=metadata or str(recording.id),
                    )
                )
            except Exception as e:
                logger.exception(
                    "Failed to create metadata collector agent for room %s", room_id
                )
                raise MetadataCollectorException(
                    "Failed to create metadata collector agent"
                ) from e

            dispatch_id = getattr(response, "id", None)

            if not dispatch_id:
                logger.error("LiveKit response missing dispatch ID for room %s", room_id)
                raise MetadataCollectorException(
                    f"LiveKit did not return a dispatch_id for room {room_id}"
                )

            try:
                await sync_to_async(self._store_dispatch_id)(
                    recording.pk,
                    dispatch_option_key,
                    dispatch_id,
                    expected_pending_claim_id,
                )
            except MetadataCollectorException:
                try:
                    await lkapi.agent_dispatch.delete_dispatch(
                        dispatch_id=str(dispatch_id), room_name=room_id
                    )
                except Exception:
                    logger.exception(
                        "Failed to delete superseded metadata collector for room %s",
                        room_id,
                    )
                raise

            return dispatch_id
        finally:
            await lkapi.aclose()

    @staticmethod
    @transaction.atomic
    def _store_dispatch_id(
        recording_id,
        dispatch_option_key: str,
        dispatch_id: str,
        expected_pending_claim_id: str | None,
    ) -> None:
        recording = Recording.objects.select_for_update().get(pk=recording_id)
        if expected_pending_claim_id is not None:
            current = recording.options.get(dispatch_option_key)
            if (
                not isinstance(current, dict)
                or current.get("state") != "pending"
                or current.get("claim_id") != expected_pending_claim_id
            ):
                raise MetadataCollectorException(
                    "Metadata collector dispatch claim was superseded"
                )
        recording.options[dispatch_option_key] = dispatch_id
        recording.save(update_fields=["options"])

    @async_to_sync
    async def stop(
        self,
        recording: Recording,
        *,
        dispatch_option_key: str = "metadata_collector_dispatch_id",
    ):
        """Stop and delete the agent dispatch associated to the room."""

        room_id = str(recording.room.id)
        dispatch_id = self._actual_dispatch_id(recording.options.get(dispatch_option_key))
        lkapi = utils.create_livekit_client()

        try:
            if not dispatch_id:
                logger.warning(
                    "No metadata collector dispatch ID stored for room %s", room_id
                )
                return None

            await lkapi.agent_dispatch.delete_dispatch(
                dispatch_id=str(dispatch_id), room_name=room_id
            )

        except Exception as e:
            logger.exception(
                "Failed to stop metadata collector agent dispatch for room %s",
                room_id,
            )
            raise MetadataCollectorException(
                f"Failed to stop metadata collector agent for room {room_id}"
            ) from e
        finally:
            await lkapi.aclose()
