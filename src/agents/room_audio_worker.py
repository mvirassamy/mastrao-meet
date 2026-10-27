"""Run one authorized post-meeting room-audio capture."""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass

from livekit.agents import (
    AutoSubscribe,
    JobContext,
    WorkerOptions,
    WorkerPermissions,
    cli,
)
from minio import Minio

from observability import configure_sentry, set_job_context
from room_audio_authority import RoomAudioAuthority, RoomAudioAuthorityConfig
from room_audio_job import handle_room_audio_job_request, verify_room_audio_job
from room_audio_object_store import RoomAudioObjectStore
from room_audio_ogg import RoomAudioOggEncoder
from room_audio_post_completion import RoomAudioPostCompletion
from room_audio_runtime import create_room_audio_run_for_effect
from room_audio_session import RoomAudioSession
from room_audio_source_publisher import (
    DurableRoomAudioSourcePublisher,
    RoomAudioSourcePublisherConfig,
)
from room_audio_start_authority import room_audio_worker_identity

logger = logging.getLogger("room-audio-worker")

ROOM_AUDIO_AGENT_NAME = "room-audio-worker"
ROOM_AUDIO_WORKER_PERMISSIONS = WorkerPermissions(
    can_publish=False,
    can_subscribe=True,
    can_publish_data=False,
    can_update_metadata=False,
    hidden=True,
)
DISPATCH_REFERENCE = re.compile(r"^[A-Za-z0-9_-]{8,200}$")


@dataclass(frozen=True)
class RoomAudioWorkerConfig:
    """Trusted runtime configuration for one post-audio worker process."""

    effect_issuer: str
    effect_audience: str
    effect_key_id: str
    effect_public_jwk: str
    callback_endpoint: str
    callback_timeout_seconds: float
    livekit_url: str
    livekit_api_key: str
    livekit_api_secret: str
    object_store_endpoint: str
    object_store_access_key: str
    object_store_secret_key: str
    object_store_secure: bool
    object_store_bucket: str

    @classmethod
    def from_environment(cls) -> RoomAudioWorkerConfig:
        """Load every required boundary explicitly and reject partial setup."""
        return cls(
            effect_issuer=_required_environment("ROOM_AUDIO_EFFECT_ISSUER"),
            effect_audience=_required_environment("ROOM_AUDIO_EFFECT_AUDIENCE"),
            effect_key_id=_required_environment("ROOM_AUDIO_EFFECT_KEY_ID"),
            effect_public_jwk=_required_environment("ROOM_AUDIO_EFFECT_PUBLIC_JWK"),
            callback_endpoint=_required_environment(
                "ROOM_AUDIO_SOURCE_CALLBACK_ENDPOINT"
            ),
            callback_timeout_seconds=_positive_float_environment(
                "ROOM_AUDIO_SOURCE_CALLBACK_TIMEOUT_SECONDS"
            ),
            livekit_url=_required_environment("LIVEKIT_URL"),
            livekit_api_key=_required_environment("LIVEKIT_API_KEY"),
            livekit_api_secret=_required_environment("LIVEKIT_API_SECRET"),
            object_store_endpoint=_required_environment("AWS_S3_ENDPOINT_URL"),
            object_store_access_key=_required_environment("AWS_S3_ACCESS_KEY_ID"),
            object_store_secret_key=_required_environment("AWS_S3_SECRET_ACCESS_KEY"),
            object_store_secure=_boolean_environment("AWS_S3_SECURE_ACCESS"),
            object_store_bucket=_required_environment("AWS_STORAGE_BUCKET_NAME"),
        )


def _required_environment(name: str) -> str:
    value = os.getenv(name, "")
    if not value or value.strip() != value:
        raise ValueError(f"missing or invalid {name}")
    return value


def _positive_float_environment(name: str) -> float:
    try:
        value = float(_required_environment(name))
    except ValueError as error:
        raise ValueError(f"invalid {name}") from error
    if value <= 0:
        raise ValueError(f"invalid {name}")
    return value


def _boolean_environment(name: str) -> bool:
    value = _required_environment(name).lower()
    if value == "true":
        return True
    if value == "false":
        return False
    raise ValueError(f"invalid {name}")


def create_post_room_audio_session(
    ctx: JobContext,
    *,
    effect: dict,
    compact_effect: str,
    config: RoomAudioWorkerConfig,
) -> RoomAudioSession:
    """Compose one post-only session from explicit trusted collaborators."""
    if effect.get("pipeline") != "post":
        raise ValueError("room-audio worker currently supports only post pipeline")
    dispatch_id = getattr(ctx.job, "dispatch_id", "")
    if not isinstance(dispatch_id, str) or not DISPATCH_REFERENCE.fullmatch(
        dispatch_id
    ):
        raise ValueError("room-audio dispatch_id is missing or invalid")

    runtime = create_room_audio_run_for_effect(
        effect,
        artifact_sink=RoomAudioOggEncoder(),
    )
    authority = RoomAudioAuthority(
        config=RoomAudioAuthorityConfig(
            issuer=config.effect_issuer,
            audience=config.effect_audience,
            key_id=config.effect_key_id,
            public_jwk=config.effect_public_jwk,
            organization_external_id=effect["organization_external_id"],
            meeting_ref=effect["meeting_ref"],
            room_ref=effect["room_ref"],
            audio_run_ref=effect["audio_run_ref"],
            pipeline=effect["pipeline"],
        ),
        runtime=runtime,
    )
    object_store = RoomAudioObjectStore(
        Minio(
            endpoint=config.object_store_endpoint,
            access_key=config.object_store_access_key,
            secret_key=config.object_store_secret_key,
            secure=config.object_store_secure,
        ),
        bucket_name=config.object_store_bucket,
    )
    worker_identity = room_audio_worker_identity(effect["audio_run_ref"])
    publisher = DurableRoomAudioSourcePublisher(
        config=RoomAudioSourcePublisherConfig(
            endpoint=config.callback_endpoint,
            room_name=ctx.room.name,
            worker_identity=worker_identity,
            livekit_api_key=config.livekit_api_key,
            livekit_api_secret=config.livekit_api_secret,
            timeout_seconds=config.callback_timeout_seconds,
        ),
        compact_start_effect=compact_effect,
    )
    completion = RoomAudioPostCompletion(
        object_store=object_store,
        publisher=publisher,
        provider_session_ref=dispatch_id,
    )
    return RoomAudioSession(
        room=ctx.room,
        effect=effect,
        runtime=runtime,
        authority=authority,
        completion_handler=completion,
    )


async def entrypoint(ctx: JobContext) -> None:
    """Verify, capture, persist and publish one authorized room mix."""
    set_job_context(room=ctx.room.name, job_id=ctx.job.id)
    compact_effect = ctx.job.metadata
    effect, _identity = verify_room_audio_job(ctx.room.name, compact_effect)
    config = RoomAudioWorkerConfig.from_environment()
    session = create_post_room_audio_session(
        ctx,
        effect=effect,
        compact_effect=compact_effect,
        config=config,
    )

    async def cleanup(reason: str) -> None:
        await session.aclose(reason)

    ctx.add_shutdown_callback(cleanup)
    await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)
    await session.start()
    try:
        await session.wait_closed()
    except Exception:
        logger.exception("Room-audio post session failed")
        ctx.shutdown("room-audio-post-failed")
        raise
    ctx.shutdown("room-audio-post-completed")


if __name__ == "__main__":
    configure_sentry(ROOM_AUDIO_AGENT_NAME)
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            request_fnc=handle_room_audio_job_request,
            agent_name=ROOM_AUDIO_AGENT_NAME,
            permissions=ROOM_AUDIO_WORKER_PERMISSIONS,
        )
    )
