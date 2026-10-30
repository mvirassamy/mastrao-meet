"""Persist and reconcile one exact Tenant Core room-audio start effect."""

# Generated LiveKit protobuf members and strict effect checks are intentional.
# pylint: disable=too-many-boolean-expressions,unidiomatic-typecheck

import asyncio
import hashlib
import json
import logging
import re
from datetime import UTC, datetime, timedelta

from django.conf import settings
from django.db import DatabaseError, IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from asgiref.sync import async_to_sync
from livekit import api
from livekit.protocol.agent_dispatch import CreateAgentDispatchRequest

from core import models, utils
from core.mastrao_recording_adapter import _read_effect, _safe_response
from core.mastrao_recording_contract import (
    RecordingContractRefused,
    _validate_ref,
    _validate_time,
    _verify,
    compact_digest,
)
from core.mastrao_room_audio_authority import room_audio_worker_identity

JOSE_TYPE = "mastrao-meeting-room-audio-start-effect+jws"
FIELDS = {
    "version",
    "type",
    "issuer",
    "audience",
    "operation",
    "operation_version",
    "organization_external_id",
    "meeting_ref",
    "room_ref",
    "audio_run_ref",
    "policy_ref",
    "policy_digest",
    "notice_digest",
    "provider_binding_digest",
    "provider_room_sid",
    "retention_expires_at",
    "pipeline",
    "source_profile",
    "maximum_duration_seconds",
    "empty_room_grace_seconds",
    "issued_at",
    "expires_at",
    "jti",
}
POST_SOURCE_PROFILE = {
    "format": "authorized_room_mix_ogg_opus_v1",
    "codec": "opus",
    "container": "ogg",
    "channel_count": 1,
}
LIVE_SOURCE_PROFILE = {
    "format": "authorized_room_mix_pcm_s16le_v1",
    "codec": "pcm_s16le",
    "sample_rate_hz": 24_000,
    "channel_count": 1,
}
PROVIDER_TIMEOUT_SECONDS = 3
DELIVERY_LEASE_SECONDS = 5
ROOM_AUDIO_AGENT_NAMES = {
    "post": "room-audio-worker",
    "live": "room-audio-live-worker",
}

logger = logging.getLogger(__name__)


def _semantic_digest(effect):
    """Hash the durable run binding, excluding the renewable JWS envelope."""
    binding = {
        name: value
        for name, value in effect.items()
        if name not in {"issuer", "audience", "issued_at", "expires_at", "jti"}
    }
    return hashlib.sha256(
        json.dumps(binding, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def read_room_audio_run_effect(compact):
    """Verify one exact Core start effect without requiring current freshness."""
    effect = _verify(compact, JOSE_TYPE, FIELDS)
    pipeline = effect["pipeline"]
    expected_profile = (
        POST_SOURCE_PROFILE if pipeline == "post" else LIVE_SOURCE_PROFILE
    )
    if (
        type(effect["version"]) is not int
        or effect["version"] != 2
        or effect["type"] != "mastrao.core-meeting-room-audio-start-effect"
        or type(effect["operation_version"]) is not int
        or effect["operation_version"] != 1
        or effect["operation"] != "start_meeting_room_audio_run"
        or effect["issuer"] != settings.MASTRAO_RECORDING_EFFECT_ISSUER
        or effect["audience"] != settings.MASTRAO_RECORDING_EFFECT_AUDIENCE
        or not isinstance(effect["provider_room_sid"], str)
        or not re.fullmatch(r"RM_[A-Za-z0-9]{1,96}", effect["provider_room_sid"])
        or pipeline not in {"post", "live"}
        or effect["source_profile"] != expected_profile
        or type(effect["maximum_duration_seconds"]) is not int
        or not 1 <= effect["maximum_duration_seconds"] <= 14_400
        or type(effect["empty_room_grace_seconds"]) is not int
        or not 0 <= effect["empty_room_grace_seconds"] <= 300
        or type(effect["retention_expires_at"]) is not int
        or effect["retention_expires_at"] <= effect["expires_at"]
    ):
        raise RecordingContractRefused()
    for name in (
        "meeting_ref",
        "room_ref",
        "audio_run_ref",
        "policy_ref",
        "jti",
    ):
        _validate_ref(effect, name)
    if not re.fullmatch(r"[A-Za-z0-9._:-]{1,160}", effect["organization_external_id"]):
        raise RecordingContractRefused()
    for name in ("policy_digest", "notice_digest", "provider_binding_digest"):
        if not re.fullmatch(r"[a-f0-9]{64}", effect[name]):
            raise RecordingContractRefused()
    return effect


def verify_room_audio_run_effect(compact):
    """Verify one fresh Core start effect before it enters the outbox."""
    effect = read_room_audio_run_effect(compact)
    _validate_time(effect, maximum=30)
    return effect


@transaction.atomic(durable=True)
def persist_room_audio_run_effect(effect, compact):
    """Commit the exact start effect before any LiveKit provider call."""
    binding = (
        models.MastraoRoomBinding.objects.select_for_update()
        .filter(
            meeting_ref=effect["meeting_ref"],
            room_ref=effect["room_ref"],
            provider_binding_digest=effect["provider_binding_digest"],
            closing_at__isnull=True,
        )
        .first()
    )
    if binding is None:
        raise RecordingContractRefused(status=404)
    digest = compact_digest(compact)
    existing = models.MastraoRoomAudioRunEffect.objects.filter(
        Q(effect_jti=effect["jti"])
        | Q(room_binding=binding, pipeline=effect["pipeline"])
    ).first()
    if existing is not None:
        existing_effect = read_room_audio_run_effect(existing.compact_effect)
        if _semantic_digest(existing_effect) != _semantic_digest(effect):
            raise RecordingContractRefused(status=409)
        if existing.state == models.MastraoRoomAudioRunEffect.State.PENDING:
            existing.effect_jti = effect["jti"]
            existing.effect_digest = digest
            existing.compact_effect = compact
            existing.expires_at = datetime.fromtimestamp(effect["expires_at"], UTC)
            existing.save(
                update_fields=[
                    "effect_jti",
                    "effect_digest",
                    "compact_effect",
                    "expires_at",
                ]
            )
        return existing
    try:
        return models.MastraoRoomAudioRunEffect.objects.create(
            room_binding=binding,
            audio_run_ref=effect["audio_run_ref"],
            pipeline=effect["pipeline"],
            effect_jti=effect["jti"],
            effect_digest=digest,
            compact_effect=compact,
            destination_identity=room_audio_worker_identity(
                effect["room_ref"], effect["pipeline"]
            ),
            expires_at=datetime.fromtimestamp(effect["expires_at"], UTC),
        )
    except IntegrityError as error:
        raise RecordingContractRefused(status=409) from error


@transaction.atomic(durable=True)
def claim_room_audio_run_effect(row_id):
    """Claim one pending delivery or reclaim its expired provider lease."""
    row = (
        models.MastraoRoomAudioRunEffect.objects.select_for_update()
        .select_related("room_binding")
        .get(pk=row_id)
    )
    if row.state == models.MastraoRoomAudioRunEffect.State.DELIVERED:
        return row, False
    now = timezone.now()
    if row.expires_at <= now:
        raise RecordingContractRefused(status=409)
    lease_cutoff = now - timedelta(seconds=DELIVERY_LEASE_SECONDS)
    if (
        row.state == models.MastraoRoomAudioRunEffect.State.IN_FLIGHT
        and row.attempt_started_at is not None
        and row.attempt_started_at > lease_cutoff
    ):
        raise RecordingContractRefused(status=409)
    row.state = models.MastraoRoomAudioRunEffect.State.IN_FLIGHT
    row.attempt_count += 1
    row.attempt_started_at = now
    row.save(update_fields=["state", "attempt_count", "attempt_started_at"])
    return row, True


async def _ensure_dispatch(room_name, compact_effect, pipeline):
    agent_name = ROOM_AUDIO_AGENT_NAMES[pipeline]
    expected_semantic_digest = _semantic_digest(
        read_room_audio_run_effect(compact_effect)
    )
    client = utils.create_livekit_client()
    try:
        dispatches = list(
            await asyncio.wait_for(
                client.agent_dispatch.list_dispatch(room_name),
                timeout=PROVIDER_TIMEOUT_SECONDS,
            )
        )
        matching = []
        for dispatch in dispatches:
            if getattr(dispatch, "agent_name", None) != agent_name:
                continue
            metadata = getattr(dispatch, "metadata", None)
            if not isinstance(metadata, str):
                continue
            try:
                observed_digest = _semantic_digest(read_room_audio_run_effect(metadata))
            except RecordingContractRefused:
                continue
            if observed_digest == expected_semantic_digest:
                matching.append(dispatch)
        if len(matching) > 1:
            raise RecordingContractRefused(status=409)
        if matching:
            dispatch_id = getattr(matching[0], "id", None)
            if dispatch_id:
                return str(dispatch_id)
            raise RecordingContractRefused(status=503)
        created = await asyncio.wait_for(
            client.agent_dispatch.create_dispatch(
                CreateAgentDispatchRequest(
                    agent_name=agent_name,
                    room=room_name,
                    metadata=compact_effect,
                )
            ),
            timeout=PROVIDER_TIMEOUT_SECONDS,
        )
        dispatch_id = getattr(created, "id", None)
        if not dispatch_id:
            raise RecordingContractRefused(status=503)
        return str(dispatch_id)
    except (api.TwirpError, OSError, asyncio.TimeoutError, TimeoutError) as error:
        raise RecordingContractRefused(status=503) from error
    finally:
        await client.aclose()


@transaction.atomic(durable=True)
def _release_claim(row_id, attempt_started_at):
    return bool(
        models.MastraoRoomAudioRunEffect.objects.filter(
            pk=row_id,
            state=models.MastraoRoomAudioRunEffect.State.IN_FLIGHT,
            attempt_started_at=attempt_started_at,
        ).update(
            state=models.MastraoRoomAudioRunEffect.State.PENDING,
            attempt_started_at=None,
        )
    )


@transaction.atomic(durable=True)
def _mark_delivered(row_id, attempt_started_at, dispatch_id):
    return bool(
        models.MastraoRoomAudioRunEffect.objects.filter(
            pk=row_id,
            state=models.MastraoRoomAudioRunEffect.State.IN_FLIGHT,
            attempt_started_at=attempt_started_at,
        ).update(
            state=models.MastraoRoomAudioRunEffect.State.DELIVERED,
            attempt_started_at=None,
            dispatch_id=dispatch_id,
            delivered_at=timezone.now(),
        )
    )


def deliver_room_audio_run_effect(row):
    """Reconcile one exact LiveKit dispatch under a reclaimable DB lease."""
    claimed, should_deliver = claim_room_audio_run_effect(row.pk)
    if not should_deliver:
        return True
    try:
        dispatch_id = async_to_sync(_ensure_dispatch)(
            str(claimed.room_binding.room_id),
            claimed.compact_effect,
            claimed.pipeline,
        )
    except RecordingContractRefused:
        _release_claim(claimed.pk, claimed.attempt_started_at)
        raise
    if not _mark_delivered(claimed.pk, claimed.attempt_started_at, dispatch_id):
        raise RecordingContractRefused(status=409)
    return True


@csrf_exempt
@transaction.non_atomic_requests
@require_POST
def dispatch_room_audio_run_effect(request):
    """Accept a Core start effect only after persistence and reconciliation."""
    stage = "request"
    try:
        compact = _read_effect(request, "room_audio_start_effect", lambda value: value)
        stage = "verification"
        effect = verify_room_audio_run_effect(compact)
        stage = "persistence"
        row = persist_room_audio_run_effect(effect, compact)
        stage = "provider_delivery"
        deliver_room_audio_run_effect(row)
        row.refresh_from_db()
        return _safe_response(
            {
                "state": "delivered",
                "effect_digest": row.effect_digest,
                "dispatch_id": row.dispatch_id,
            }
        )
    except RecordingContractRefused as error:
        logger.warning("Room audio run effect refused at stage=%s", stage)
        return _safe_response(
            {"error": "room_audio_run_effect_refused"}, status=error.status
        )
    except (DatabaseError, UnicodeError, ValueError, TypeError):
        return _safe_response(
            {"error": "room_audio_run_effect_unavailable"}, status=503
        )
