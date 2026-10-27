"""Fail-closed admission for one dedicated room-audio worker job."""

from __future__ import annotations

import logging
import os

from livekit import api, rtc
from livekit.agents import JobRequest

from room_audio_start_authority import (
    RoomAudioStartRefused,
    RoomAudioStartVerifier,
    RoomAudioStartVerifierConfig,
    room_audio_worker_identity,
)

logger = logging.getLogger("room-audio-worker")


def _required_environment(name: str) -> str:
    value = os.getenv(name, "")
    if not value:
        raise RoomAudioStartRefused(f"missing {name}")
    return value


def verify_room_audio_job(room_name: str, compact_effect: str) -> tuple[dict, str]:
    """Verify one dispatch metadata value and derive its exact identity."""
    verifier = RoomAudioStartVerifier(
        RoomAudioStartVerifierConfig(
            issuer=_required_environment("ROOM_AUDIO_EFFECT_ISSUER"),
            audience=_required_environment("ROOM_AUDIO_EFFECT_AUDIENCE"),
            key_id=_required_environment("ROOM_AUDIO_EFFECT_KEY_ID"),
            public_jwk=_required_environment("ROOM_AUDIO_EFFECT_PUBLIC_JWK"),
            room_name=room_name,
        )
    )
    effect = verifier.verify(compact_effect)
    return effect, room_audio_worker_identity(effect["audio_run_ref"])


async def handle_room_audio_job_request(job_request: JobRequest) -> None:
    """Accept only a current Core-signed job with no active exact worker."""
    room_name = job_request.room.name
    try:
        effect, identity = verify_room_audio_job(
            room_name,
            job_request.job.metadata,
        )
        if effect["pipeline"] != "post":
            logger.info("Refused unsupported room-audio pipeline for %s", room_name)
            await job_request.reject()
            return
        async with api.LiveKitAPI() as livekit:
            response = await livekit.room.list_participants(
                list=api.ListParticipantsRequest(room=room_name)
            )
        already_present = any(
            participant.kind == rtc.ParticipantKind.PARTICIPANT_KIND_AGENT
            and participant.identity == identity
            for participant in response.participants
        )
        if already_present:
            logger.info("Room-audio worker is already present in room %s", room_name)
            await job_request.reject()
            return
        await job_request.accept(identity=identity, metadata=job_request.job.metadata)
        logger.info(
            "Accepted room-audio %s run for room %s",
            effect["pipeline"],
            room_name,
        )
    except RoomAudioStartRefused:
        logger.warning("Refused unauthorized room-audio job for room %s", room_name)
        await job_request.reject()
    except Exception:  # LiveKit provider boundary
        logger.exception("Refused room-audio job for room %s", room_name)
        await job_request.reject()
