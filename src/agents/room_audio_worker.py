"""Run one authorized post or live meeting-room audio pipeline."""

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

from mistral_live_stt import create_mistral_live_stt
from observability import configure_sentry, set_job_context
from room_audio_authority import RoomAudioAuthority, RoomAudioAuthorityConfig
from room_audio_job import handle_room_audio_job_request, verify_room_audio_job
from room_audio_live_output import RoomLiveTranscriptionOutput
from room_audio_object_store import RoomAudioObjectStore
from room_audio_ogg import RoomAudioOggEncoder
from room_audio_post_completion import RoomAudioPostCompletion
from room_audio_runtime import (
    RoomAudioRun,
    RoomLiveOutput,
    create_room_audio_run_for_effect,
)
from room_audio_session import RoomAudioSession, RoomAudioSessionHandlers
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
    can_publish_data=True,
    can_update_metadata=False,
    hidden=True,
)
DISPATCH_REFERENCE = re.compile(r"^[A-Za-z0-9_-]{8,200}$")


@dataclass(frozen=True)
class RoomAudioPostConfig:
    """Secrets and endpoints used only by the durable post pipeline."""

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


@dataclass(frozen=True)
class RoomAudioWorkerConfig:
    """Trusted runtime configuration for one pipeline-specific worker."""

    pipeline: str
    effect_issuer: str
    effect_audience: str
    effect_key_id: str
    effect_public_jwk: str
    post: RoomAudioPostConfig | None

    @classmethod
    def from_environment(cls) -> RoomAudioWorkerConfig:
        """Load every required boundary explicitly and reject partial setup."""
        pipeline = _required_pipeline_environment()
        return cls(
            pipeline=pipeline,
            effect_issuer=_required_environment("ROOM_AUDIO_EFFECT_ISSUER"),
            effect_audience=_required_environment("ROOM_AUDIO_EFFECT_AUDIENCE"),
            effect_key_id=_required_environment("ROOM_AUDIO_EFFECT_KEY_ID"),
            effect_public_jwk=_required_environment("ROOM_AUDIO_EFFECT_PUBLIC_JWK"),
            post=_load_post_config() if pipeline == "post" else None,
        )


def _required_environment(name: str) -> str:
    value = os.getenv(name, "")
    if not value or value.strip() != value:
        raise ValueError(f"missing or invalid {name}")
    return value


def _required_pipeline_environment() -> str:
    pipeline = _required_environment("ROOM_AUDIO_PIPELINE")
    if pipeline not in {"post", "live"}:
        raise ValueError("invalid ROOM_AUDIO_PIPELINE")
    return pipeline


def _load_post_config() -> RoomAudioPostConfig:
    return RoomAudioPostConfig(
        callback_endpoint=_required_environment("ROOM_AUDIO_SOURCE_CALLBACK_ENDPOINT"),
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
    if config.pipeline != "post" or config.post is None:
        raise ValueError("post effect requires post worker configuration")
    post = config.post
    dispatch_id = getattr(ctx.job, "dispatch_id", "")
    if not isinstance(dispatch_id, str) or not DISPATCH_REFERENCE.fullmatch(
        dispatch_id
    ):
        raise ValueError("room-audio dispatch_id is missing or invalid")

    runtime = create_room_audio_run_for_effect(
        effect,
        artifact_sink=RoomAudioOggEncoder(),
    )
    authority = _create_authority(config, effect, runtime)
    object_store = RoomAudioObjectStore(
        Minio(
            endpoint=post.object_store_endpoint,
            access_key=post.object_store_access_key,
            secret_key=post.object_store_secret_key,
            secure=post.object_store_secure,
        ),
        bucket_name=post.object_store_bucket,
    )
    worker_identity = room_audio_worker_identity(
        effect["room_ref"],
        effect["pipeline"],
    )
    publisher = DurableRoomAudioSourcePublisher(
        config=RoomAudioSourcePublisherConfig(
            endpoint=post.callback_endpoint,
            room_name=ctx.room.name,
            worker_identity=worker_identity,
            livekit_api_key=post.livekit_api_key,
            livekit_api_secret=post.livekit_api_secret,
            timeout_seconds=post.callback_timeout_seconds,
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
        handlers=RoomAudioSessionHandlers(complete=completion),
    )


def create_live_room_audio_session(
    ctx: JobContext,
    *,
    effect: dict,
    config: RoomAudioWorkerConfig,
) -> RoomAudioSession:
    """Compose one room-wide Mistral stream with unattributed output."""
    if effect.get("pipeline") != "live":
        raise ValueError("live room-audio session requires live pipeline")
    if config.pipeline != "live" or config.post is not None:
        raise ValueError("live effect requires live worker configuration")
    output = RoomLiveTranscriptionOutput(ctx.room)
    runtime = create_room_audio_run_for_effect(
        effect,
        live_output=RoomLiveOutput(
            provider_factory=create_mistral_live_stt,
            speech_event_handler_factory=output.create_event_handler,
            failure_handler=output.handle_failure,
            generation_invalidator=output.invalidate_generation,
        ),
    )
    return RoomAudioSession(
        room=ctx.room,
        effect=effect,
        runtime=runtime,
        authority=_create_authority(config, effect, runtime),
        handlers=RoomAudioSessionHandlers(
            complete=output.complete,
            publish_gap=output.publish_gap,
        ),
    )


def create_room_audio_session(
    ctx: JobContext,
    *,
    effect: dict,
    compact_effect: str,
    config: RoomAudioWorkerConfig,
) -> RoomAudioSession:
    """Compose exactly the output selected by the verified Core effect."""
    pipeline = effect.get("pipeline")
    if pipeline == "post":
        return create_post_room_audio_session(
            ctx,
            effect=effect,
            compact_effect=compact_effect,
            config=config,
        )
    if pipeline == "live":
        return create_live_room_audio_session(ctx, effect=effect, config=config)
    raise ValueError("unsupported room-audio pipeline")


def _create_authority(
    config: RoomAudioWorkerConfig,
    effect: dict,
    runtime: RoomAudioRun,
) -> RoomAudioAuthority:
    """Bind one runtime to the exact signed Core track authority."""
    return RoomAudioAuthority(
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


async def entrypoint(ctx: JobContext) -> None:
    """Verify, capture, persist and publish one authorized room mix."""
    set_job_context(room=ctx.room.name, job_id=ctx.job.id)
    compact_effect = ctx.job.metadata
    provider_room_sid = ctx.job.room.sid
    effect, _identity = verify_room_audio_job(provider_room_sid, compact_effect)
    config = RoomAudioWorkerConfig.from_environment()
    if effect["pipeline"] != config.pipeline:
        raise ValueError("room-audio effect targets a different worker pipeline")
    session = create_room_audio_session(
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
        logger.exception("Room-audio %s session failed", effect["pipeline"])
        ctx.shutdown(f"room-audio-{effect['pipeline']}-failed")
        raise
    ctx.shutdown(f"room-audio-{effect['pipeline']}-completed")


if __name__ == "__main__":
    configured_agent_name = _required_environment("ROOM_AUDIO_AGENT_NAME")
    configure_sentry(configured_agent_name)
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            request_fnc=handle_room_audio_job_request,
            agent_name=configured_agent_name,
            permissions=ROOM_AUDIO_WORKER_PERMISSIONS,
        )
    )
