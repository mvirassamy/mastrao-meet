"""Persist and deliver exact Tenant Core room-audio track authority."""

# Generated protobuf members and strict effect checks are intentional here.
# pylint: disable=too-many-boolean-expressions,unidiomatic-typecheck

import hashlib
import json
import re
from datetime import UTC, datetime

from django.conf import settings
from django.db import DatabaseError, IntegrityError, transaction
from django.db.models import F, Q
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from asgiref.sync import async_to_sync
from livekit import api
from livekit.api import SendDataRequest  # pylint: disable=E0611

from core import models, utils
from core.mastrao_recording_adapter import _read_effect, _safe_response
from core.mastrao_recording_contract import (
    RecordingContractRefused,
    _validate_ref,
    _validate_time,
    _verify,
    compact_digest,
)

JOSE_TYPE = "mastrao-meeting-room-audio-track-effect+jws"
TOPIC = "mastrao.room-audio.track-authority.v1"
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
    "retention_expires_at",
    "pipeline",
    "authorization_generation",
    "track_ref",
    "media_token_binding_ref",
    "authorization_ref",
    "authorization_digest",
    "issued_at",
    "expires_at",
    "jti",
}


def _semantic_digest(effect):
    """Hash track authority while excluding its renewable JWS envelope."""
    binding = {
        name: value
        for name, value in effect.items()
        if name not in {"issuer", "audience", "issued_at", "expires_at", "jti"}
    }
    return hashlib.sha256(
        json.dumps(binding, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def read_room_audio_track_effect(compact):
    """Verify one Core effect without requiring current freshness."""
    effect = _verify(compact, JOSE_TYPE, FIELDS)
    if (
        type(effect["version"]) is not int
        or effect["version"] != 1
        or effect["type"] != "mastrao.core-meeting-room-audio-track-effect"
        or type(effect["operation_version"]) is not int
        or effect["operation_version"] != 1
        or effect["operation"]
        not in {
            "admit_meeting_room_audio_track",
            "revoke_meeting_room_audio_track",
        }
        or effect["issuer"] != settings.MASTRAO_RECORDING_EFFECT_ISSUER
        or effect["audience"] != settings.MASTRAO_RECORDING_EFFECT_AUDIENCE
        or effect["pipeline"] not in {"post", "live"}
        or type(effect["authorization_generation"]) is not int
        or effect["authorization_generation"] <= 0
        or type(effect["retention_expires_at"]) is not int
        or effect["retention_expires_at"] <= effect["expires_at"]
    ):
        raise RecordingContractRefused()
    for name in (
        "meeting_ref",
        "room_ref",
        "audio_run_ref",
        "policy_ref",
        "media_token_binding_ref",
        "authorization_ref",
        "jti",
    ):
        _validate_ref(effect, name)
    if not re.fullmatch(r"[A-Za-z0-9._:-]{1,160}", effect["organization_external_id"]):
        raise RecordingContractRefused()
    if not re.fullmatch(r"[A-Za-z0-9_-]{8,200}", effect["track_ref"]):
        raise RecordingContractRefused()
    for name in (
        "policy_digest",
        "notice_digest",
        "provider_binding_digest",
        "authorization_digest",
    ):
        if not re.fullmatch(r"[a-f0-9]{64}", effect[name]):
            raise RecordingContractRefused()
    return effect


def verify_room_audio_track_effect(compact):
    """Verify one fresh Core effect before it can enter the outbox."""
    effect = read_room_audio_track_effect(compact)
    _validate_time(effect, maximum=30)
    return effect


def room_audio_worker_identity(room_ref, pipeline):
    """Derive one opaque worker identity for a room and pipeline."""
    if pipeline not in {"post", "live"}:
        raise ValueError("unsupported room audio pipeline")
    digest = hashlib.sha256(room_ref.encode("utf-8")).hexdigest()
    return f"room-audio-worker-{pipeline}-{digest[:32]}"


@transaction.atomic(durable=True)
def persist_room_audio_track_effect(effect, compact):
    """Commit the exact effect before any LiveKit network call."""
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
        raise RecordingContractRefused()
    digest = compact_digest(compact)
    existing = models.MastraoRoomAudioTrackEffect.objects.filter(
        Q(effect_jti=effect["jti"])
        | Q(
            room_binding=binding,
            audio_run_ref=effect["audio_run_ref"],
            pipeline=effect["pipeline"],
            track_ref=effect["track_ref"],
            authorization_generation=effect["authorization_generation"],
        )
    ).first()
    if existing is not None:
        existing_effect = read_room_audio_track_effect(existing.compact_effect)
        if _semantic_digest(existing_effect) != _semantic_digest(effect):
            raise RecordingContractRefused(status=409)
        if existing.state == models.MastraoRoomAudioTrackEffect.State.PENDING:
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
        return models.MastraoRoomAudioTrackEffect.objects.create(
            room_binding=binding,
            audio_run_ref=effect["audio_run_ref"],
            pipeline=effect["pipeline"],
            track_ref=effect["track_ref"],
            authorization_generation=effect["authorization_generation"],
            operation=effect["operation"],
            authorization_digest=effect["authorization_digest"],
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


async def _publish(room_name, compact_effect, destination_identity):
    client = utils.create_livekit_client()
    try:
        await client.room.send_data(
            SendDataRequest(
                room=room_name,
                data=compact_effect.encode("ascii"),
                kind="RELIABLE",
                destination_identities=[destination_identity],
                topic=TOPIC,
            )
        )
    except (api.TwirpError, OSError, TimeoutError) as error:
        raise RecordingContractRefused(status=503) from error
    finally:
        await client.aclose()


def deliver_room_audio_track_effect(row):
    """Attempt one bounded delivery; pending rows remain safe to retry."""
    if row.state == models.MastraoRoomAudioTrackEffect.State.DELIVERED:
        return True
    if row.expires_at <= timezone.now():
        raise RecordingContractRefused(status=409)
    models.MastraoRoomAudioTrackEffect.objects.filter(pk=row.pk).update(
        attempt_count=F("attempt_count") + 1
    )
    room_name = str(row.room_binding.room_id)
    async_to_sync(_publish)(room_name, row.compact_effect, row.destination_identity)
    return bool(
        models.MastraoRoomAudioTrackEffect.objects.filter(
            pk=row.pk,
            state=models.MastraoRoomAudioTrackEffect.State.PENDING,
            expires_at__gt=timezone.now(),
        ).update(
            state=models.MastraoRoomAudioTrackEffect.State.DELIVERED,
            delivered_at=timezone.now(),
        )
    )


@csrf_exempt
@transaction.non_atomic_requests
@require_POST
def dispatch_room_audio_track_effect(request):
    """Accept a Core effect only after durable persistence and exact delivery."""
    try:
        compact = _read_effect(
            request,
            "room_audio_track_effect",
            lambda value: value,
        )
        effect = verify_room_audio_track_effect(compact)
        row = persist_room_audio_track_effect(effect, compact)
        if not deliver_room_audio_track_effect(row):
            raise RecordingContractRefused(status=409)
        return _safe_response(
            {"state": "delivered", "effect_digest": row.effect_digest}
        )
    except RecordingContractRefused as error:
        return _safe_response(
            {"error": "room_audio_effect_refused"}, status=error.status
        )
    except (DatabaseError, UnicodeError, ValueError, TypeError):
        return _safe_response({"error": "room_audio_effect_unavailable"}, status=503)
