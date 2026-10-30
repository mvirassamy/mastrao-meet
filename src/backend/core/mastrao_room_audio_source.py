"""Durably sign and deliver one completed room-audio source to Tenant Core."""

# The contract boundary intentionally keeps every binding predicate visible.
# pylint: disable=cyclic-import,too-many-boolean-expressions,too-many-locals
# pylint: disable=unidiomatic-typecheck

import json
import logging
import math
import re
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from django.conf import settings
from django.db import DatabaseError, IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from livekit import api

from core import models
from core.mastrao_core_http import post_core_json
from core.mastrao_recording_adapter import _safe_response
from core.mastrao_recording_contract import (
    RecordingContractRefused,
    _sign,
    compact_digest,
)
from core.mastrao_room_audio_run import read_room_audio_run_effect
from core.mastrao_room_contract import _sha256_canonical

CALLBACK_PATH = "/internal/mastrao/room-audio/sources/complete/"
CORE_PATH = "/internal/v1/meetings/room-audio/source"
SOURCE_JOSE_TYPE = "mastrao-meeting-room-audio-source+jws"
MAX_BODY_BYTES = 24 * 1024 * 1024
RECEIPT_LIFETIME_SECONDS = 30
MINIMUM_DELIVERY_CLAIM_SECONDS = 45
QUEUE_CLAIM_SECONDS = 300
DELIVERY_CLAIM_MARGIN_SECONDS = 15
MAX_CORE_TIMEOUT_SECONDS = 30
MAX_ATTEMPTS = 20
MAX_GAP_COUNT = 100_000
MAX_AUDIO_DURATION_MS = 14_400_000
MAX_AUDIO_BYTES = 20_000_000_000
PROVIDER_REFERENCE = re.compile(r"^[A-Za-z0-9_-]{8,200}$")
OBJECT_REFERENCE = re.compile(r"^[A-Za-z0-9._/-]{16,1024}$")
DIGEST = re.compile(r"^[a-f0-9]{64}$")
MANIFEST_V1_FIELDS = {
    "version",
    "type",
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
    "provider_session_ref",
    "object_ref",
    "content_type",
    "codec",
    "channel_count",
    "started_at_ms",
    "ended_at_ms",
    "duration_ms",
    "byte_size",
    "checksum_algorithm",
    "checksum_digest",
    "generation_count",
    "gap_count",
    "total_gap_ms",
}
MANIFEST_V2_METRIC_FIELDS = {
    "mixer_output_frame_count",
    "mixer_dropped_frame_count",
    "mixer_underrun_frame_count",
    "mixer_queue_peak_frames",
    "rtc_sample_count",
    "rtc_collection_error_count",
    "rtc_packets_received",
    "rtc_packets_lost",
    "rtc_concealed_samples",
    "rtc_maximum_jitter_ms",
    "rtc_maximum_round_trip_time_ms",
}
MANIFEST_V2_FIELDS = MANIFEST_V1_FIELDS | MANIFEST_V2_METRIC_FIELDS
GAP_FIELDS = {"sequence", "generation", "started_at_ms", "ended_at_ms", "reason"}
BINDING_FIELDS = {
    "organization_external_id",
    "meeting_ref",
    "room_ref",
    "audio_run_ref",
    "policy_ref",
    "policy_digest",
    "notice_digest",
    "provider_binding_digest",
    "retention_expires_at",
}

logger = logging.getLogger(__name__)


def _verify_worker_token(request):
    authorization = request.headers.get("authorization", "")
    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise RecordingContractRefused()
    try:
        claims = api.TokenVerifier(
            settings.LIVEKIT_CONFIGURATION["api_key"],
            settings.LIVEKIT_CONFIGURATION["api_secret"],
        ).verify(parts[1])
    except Exception as error:  # Token library errors are an opaque boundary.
        raise RecordingContractRefused() from error
    video = claims.video
    if (
        not re.fullmatch(
            r"room-audio-worker-post-[a-f0-9]{32}",
            claims.identity or "",
        )
        or video is None
        or not video.room_join
        or not video.room
        or video.can_publish is not False
        or video.can_publish_data is not False
        or video.can_subscribe is not False
    ):
        raise RecordingContractRefused()
    return claims


def _read_submission(request):
    declared = request.headers.get("content-length")
    if (
        request.content_type != "application/json"
        or declared is None
        or not declared.isdecimal()
        or int(declared) > MAX_BODY_BYTES
        or len(request.body) > MAX_BODY_BYTES
    ):
        raise RecordingContractRefused()
    try:
        body = json.loads(request.body)
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as error:
        raise RecordingContractRefused() from error
    if not isinstance(body, dict) or set(body) != {
        "room_audio_start_effect",
        "source_manifest",
        "source_gaps",
    }:
        raise RecordingContractRefused()
    compact = body["room_audio_start_effect"]
    manifest = body["source_manifest"]
    gaps = body["source_gaps"]
    if (
        not isinstance(compact, str)
        or not isinstance(manifest, dict)
        or not isinstance(gaps, list)
    ):
        raise RecordingContractRefused()
    return compact, manifest, gaps


def _is_int(value):
    return type(value) is int


def _core_timeout_seconds():
    timeout = settings.MASTRAO_CORE_ROOM_AUDIO_SOURCE_TIMEOUT_SECONDS
    if (
        isinstance(timeout, bool)
        or not isinstance(timeout, (int, float))
        or not 0 < timeout <= MAX_CORE_TIMEOUT_SECONDS
    ):
        raise RecordingContractRefused(status=503)
    return float(timeout)


def _delivery_claim_seconds():
    return max(
        MINIMUM_DELIVERY_CLAIM_SECONDS,
        math.ceil(_core_timeout_seconds()) + DELIVERY_CLAIM_MARGIN_SECONDS,
    )


def _validate_manifest_shape(manifest, effect, dispatch_id):
    version = manifest.get("version")
    expected_fields = MANIFEST_V2_FIELDS if version == 2 else MANIFEST_V1_FIELDS
    if set(manifest) != expected_fields:
        raise RecordingContractRefused()
    if any(manifest[name] != effect[name] for name in BINDING_FIELDS):
        raise RecordingContractRefused(status=409)
    object_ref = manifest["object_ref"]
    duration_ms = manifest["duration_ms"]
    if (
        not _is_int(manifest["version"])
        or version not in {1, 2}
        or manifest["type"] != "mastrao.meeting-room-audio-source"
        or manifest["pipeline"] != "post"
        or effect["pipeline"] != "post"
        or manifest["provider_session_ref"] != dispatch_id
        or not PROVIDER_REFERENCE.fullmatch(manifest["provider_session_ref"])
        or not isinstance(object_ref, str)
        or not OBJECT_REFERENCE.fullmatch(object_ref)
        or any(part in {"", ".", ".."} for part in object_ref.split("/"))
        or manifest["content_type"] != "audio/ogg"
        or manifest["codec"] != "opus"
        or not _is_int(manifest["channel_count"])
        or manifest["channel_count"] != 1
        or not _is_int(manifest["started_at_ms"])
        or manifest["started_at_ms"] <= 0
        or not _is_int(manifest["ended_at_ms"])
        or not _is_int(duration_ms)
        or not 1 <= duration_ms <= MAX_AUDIO_DURATION_MS
        or manifest["ended_at_ms"] - manifest["started_at_ms"] != duration_ms
        or duration_ms > effect["maximum_duration_seconds"] * 1_000
        or not _is_int(manifest["byte_size"])
        or not 1 <= manifest["byte_size"] <= MAX_AUDIO_BYTES
        or manifest["checksum_algorithm"] != "sha256"
        or not isinstance(manifest["checksum_digest"], str)
        or not DIGEST.fullmatch(manifest["checksum_digest"])
        or not _is_int(manifest["generation_count"])
        or not 1 <= manifest["generation_count"] <= 10_000
        or not _is_int(manifest["gap_count"])
        or not 0 <= manifest["gap_count"] <= MAX_GAP_COUNT
        or not _is_int(manifest["total_gap_ms"])
        or not 0 <= manifest["total_gap_ms"] <= duration_ms
    ):
        raise RecordingContractRefused()
    if version == 2 and any(
        not _is_int(manifest[name]) or manifest[name] < 0
        for name in MANIFEST_V2_METRIC_FIELDS
    ):
        raise RecordingContractRefused()
    if version == 2 and manifest["rtc_sample_count"] < 1:
        raise RecordingContractRefused()


def _validate_gaps(gaps, manifest):
    if len(gaps) != manifest["gap_count"]:
        raise RecordingContractRefused()
    total_gap_ms = 0
    previous_end = manifest["started_at_ms"]
    previous_generation = 0
    for sequence, gap in enumerate(gaps):
        if not isinstance(gap, dict) or set(gap) != GAP_FIELDS:
            raise RecordingContractRefused()
        if (
            not _is_int(gap["sequence"])
            or gap["sequence"] != sequence
            or not _is_int(gap["generation"])
            or not 1 <= gap["generation"] <= manifest["generation_count"]
            or gap["generation"] < previous_generation
            or not _is_int(gap["started_at_ms"])
            or not _is_int(gap["ended_at_ms"])
            or gap["started_at_ms"] < previous_end
            or gap["ended_at_ms"] <= gap["started_at_ms"]
            or gap["ended_at_ms"] > manifest["ended_at_ms"]
            or gap["reason"] != "source_reconnect"
        ):
            raise RecordingContractRefused()
        total_gap_ms += gap["ended_at_ms"] - gap["started_at_ms"]
        previous_end = gap["ended_at_ms"]
        previous_generation = gap["generation"]
    if total_gap_ms != manifest["total_gap_ms"]:
        raise RecordingContractRefused()


def _build_receipt(manifest):
    now = int(timezone.now().timestamp())
    expires_at = now + RECEIPT_LIFETIME_SECONDS
    receipt = {
        "issuer": settings.MASTRAO_RECORDING_RECEIPT_ISSUER,
        "audience": settings.MASTRAO_RECORDING_RECEIPT_AUDIENCE,
        "operation": "confirm_meeting_room_audio_source",
        "operation_version": 1,
        "manifest": manifest,
        "issued_at": now,
        "expires_at": expires_at,
        "jti": f"room_audio_source_{uuid4().hex}",
    }
    compact = _sign(receipt, SOURCE_JOSE_TYPE)
    return receipt, compact, datetime.fromtimestamp(expires_at, UTC)


@transaction.atomic(durable=True)
def persist_room_audio_source(compact_effect, manifest, gaps, worker_claims):
    """Persist one exact signed receipt before any Tenant Core network call."""
    effect_digest = compact_digest(compact_effect)
    run_effect = (
        models.MastraoRoomAudioRunEffect.objects.select_for_update()
        .filter(effect_digest=effect_digest, compact_effect=compact_effect)
        .first()
    )
    if (
        run_effect is None
        or run_effect.state != models.MastraoRoomAudioRunEffect.State.DELIVERED
        or run_effect.dispatch_id is None
        or worker_claims.identity != run_effect.destination_identity
        or worker_claims.video.room != str(run_effect.room_binding.room_id)
    ):
        raise RecordingContractRefused(status=404)
    effect = read_room_audio_run_effect(compact_effect)
    _validate_manifest_shape(manifest, effect, run_effect.dispatch_id)
    _validate_gaps(gaps, manifest)
    manifest_digest = _sha256_canonical(manifest)
    existing = models.MastraoRoomAudioSourceOutbox.objects.filter(
        run_effect=run_effect
    ).first()
    if existing is not None:
        if (
            existing.manifest_digest != manifest_digest
            or existing.manifest != manifest
            or existing.gaps != gaps
        ):
            raise RecordingContractRefused(status=409)
        return existing
    receipt, compact_receipt, expires_at = _build_receipt(manifest)
    try:
        return models.MastraoRoomAudioSourceOutbox.objects.create(
            run_effect=run_effect,
            manifest=manifest,
            gaps=gaps,
            manifest_digest=manifest_digest,
            receipt_jti=receipt["jti"],
            compact_receipt=compact_receipt,
            receipt_expires_at=expires_at,
        )
    except IntegrityError as error:
        raise RecordingContractRefused(status=409) from error


def _claimable_room_audio_sources(now):
    return models.MastraoRoomAudioSourceOutbox.objects.filter(
        state=models.MastraoRoomAudioSourceOutbox.State.PENDING,
        next_attempt_at__lte=now,
        attempt_count__lt=MAX_ATTEMPTS,
    ).filter(Q(claim_until__isnull=True) | Q(claim_until__lte=now))


def _exhaust_stale_room_audio_sources(now):
    exhausted = (
        models.MastraoRoomAudioSourceOutbox.objects.filter(
            state=models.MastraoRoomAudioSourceOutbox.State.PENDING,
            attempt_count__gte=MAX_ATTEMPTS,
        )
        .filter(Q(claim_until__isnull=True) | Q(claim_until__lte=now))
        .update(
            state=models.MastraoRoomAudioSourceOutbox.State.EXHAUSTED,
            claim_id=None,
            claim_until=None,
            updated_at=now,
        )
    )
    if exhausted:
        logger.error("Room audio source delivery exhausted its retry budget")
    return exhausted


@transaction.atomic
def _reserve_room_audio_sources(limit):
    """Reserve each queued row so later scheduler ticks cannot enqueue it again."""
    now = timezone.now()
    _exhaust_stale_room_audio_sources(now)
    rows = list(
        _claimable_room_audio_sources(now)
        .select_for_update(skip_locked=True)
        .order_by("next_attempt_at", "pk")[:limit]
    )
    for row in rows:
        row.claim_id = uuid4()
        row.claim_until = now + timedelta(seconds=QUEUE_CLAIM_SECONDS)
        row.save(update_fields=["claim_id", "claim_until", "updated_at"])
    return [(row.pk, row.claim_id, row.attempt_count) for row in rows]


@transaction.atomic
def claim_room_audio_source(row_id, queued_claim=None, queued_attempts=None):
    """Claim one direct delivery or consume one exact scheduler reservation."""
    row = models.MastraoRoomAudioSourceOutbox.objects.select_for_update().get(pk=row_id)
    if row.state == models.MastraoRoomAudioSourceOutbox.State.DELIVERED:
        return row, False
    now = timezone.now()
    if row.next_attempt_at > now:
        return row, False
    if row.attempt_count >= MAX_ATTEMPTS:
        if row.claim_until is None or row.claim_until <= now:
            row.state = models.MastraoRoomAudioSourceOutbox.State.EXHAUSTED
            row.claim_id = None
            row.claim_until = None
            row.save(update_fields=["state", "claim_id", "claim_until", "updated_at"])
            logger.error("Room audio source delivery exhausted its retry budget")
        return row, False
    queued = queued_claim is not None or queued_attempts is not None
    if queued:
        if (
            queued_claim is None
            or queued_attempts is None
            or str(row.claim_id) != str(queued_claim)
            or row.attempt_count != queued_attempts
            or row.claim_until is None
            or row.claim_until <= now
        ):
            return row, False
    elif row.claim_until is not None and row.claim_until > now:
        return row, False
    row.claim_id = uuid4()
    row.claim_until = now + timedelta(seconds=_delivery_claim_seconds())
    row.attempt_count += 1
    row.save(update_fields=["claim_id", "claim_until", "attempt_count", "updated_at"])
    return row, True


def _send_source(row):
    result = post_core_json(
        endpoint=settings.MASTRAO_CORE_ROOM_AUDIO_SOURCE_ENDPOINT,
        expected_path=CORE_PATH,
        body={"source_receipt": row.compact_receipt},
        timeout=_core_timeout_seconds(),
        refusal=RecordingContractRefused,
        expected_fields={"sourceId", "replayed"},
        client_error_status=None,
    )
    try:
        source_id = UUID(result["sourceId"])
    except (AttributeError, TypeError, ValueError) as error:
        raise RecordingContractRefused(status=503) from error
    if type(result["replayed"]) is not bool:
        raise RecordingContractRefused(status=503)
    return source_id, result["replayed"]


def _is_expired_receipt_refusal(error):
    return getattr(
        error, "core_error_code", None
    ) == "invalid_assertion" and "meeting_room_audio_source_receipt_expired" in getattr(
        error, "core_reason_codes", ()
    )


@transaction.atomic
def _renew_expired_receipt(row_id, claim_id, previous_compact):
    row = (
        models.MastraoRoomAudioSourceOutbox.objects.select_for_update()
        .filter(
            pk=row_id,
            state=models.MastraoRoomAudioSourceOutbox.State.PENDING,
            claim_id=claim_id,
            compact_receipt=previous_compact,
            claim_until__gt=timezone.now(),
        )
        .first()
    )
    if row is None:
        raise RecordingContractRefused(status=409)
    receipt, compact, expires_at = _build_receipt(row.manifest)
    row.receipt_jti = receipt["jti"]
    row.compact_receipt = compact
    row.receipt_expires_at = expires_at
    row.save(
        update_fields=[
            "receipt_jti",
            "compact_receipt",
            "receipt_expires_at",
            "updated_at",
        ]
    )
    return row


@transaction.atomic
def _release_claim(row_id, claim_id):
    row = (
        models.MastraoRoomAudioSourceOutbox.objects.select_for_update()
        .filter(
            pk=row_id,
            state=models.MastraoRoomAudioSourceOutbox.State.PENDING,
            claim_id=claim_id,
        )
        .first()
    )
    if row is None:
        return False
    exhausted = row.attempt_count >= MAX_ATTEMPTS
    if exhausted:
        row.state = models.MastraoRoomAudioSourceOutbox.State.EXHAUSTED
    delay = min(300, 2 ** min(row.attempt_count, 8))
    row.claim_id = None
    row.claim_until = None
    row.next_attempt_at = timezone.now() + timedelta(seconds=delay)
    row.save(
        update_fields=[
            "state",
            "claim_id",
            "claim_until",
            "next_attempt_at",
            "updated_at",
        ]
    )
    if exhausted:
        logger.error("Room audio source delivery exhausted its retry budget")
    return True


@transaction.atomic
def _mark_delivered(row_id, claim_id, source_id, replayed):
    return (
        models.MastraoRoomAudioSourceOutbox.objects.filter(
            pk=row_id,
            state=models.MastraoRoomAudioSourceOutbox.State.PENDING,
            claim_id=claim_id,
            claim_until__gt=timezone.now(),
        ).update(
            state=models.MastraoRoomAudioSourceOutbox.State.DELIVERED,
            claim_id=None,
            claim_until=None,
            source_id=source_id,
            replayed=replayed,
            delivered_at=timezone.now(),
            updated_at=timezone.now(),
        )
        == 1
    )


def deliver_room_audio_source(row, queued_claim=None, queued_attempts=None):
    """Deliver one exact JWS; remint only after an explicit expiry refusal."""
    if not settings.ROOM_AUDIO_SOURCE_TRANSFER_ENABLED:
        return False
    claimed, should_deliver = claim_room_audio_source(
        row.pk,
        queued_claim=queued_claim,
        queued_attempts=queued_attempts,
    )
    if not should_deliver:
        return claimed.state == models.MastraoRoomAudioSourceOutbox.State.DELIVERED
    claim_id = claimed.claim_id
    if claimed.receipt_expires_at <= timezone.now():
        claimed = _renew_expired_receipt(
            claimed.pk,
            claim_id,
            claimed.compact_receipt,
        )
    try:
        try:
            source_id, replayed = _send_source(claimed)
        except RecordingContractRefused as error:
            if not _is_expired_receipt_refusal(error):
                raise
            previous = claimed.compact_receipt
            claimed = _renew_expired_receipt(claimed.pk, claim_id, previous)
            source_id, replayed = _send_source(claimed)
    except RecordingContractRefused:
        _release_claim(claimed.pk, claim_id)
        raise
    if not _mark_delivered(claimed.pk, claim_id, source_id, replayed):
        raise RecordingContractRefused(status=409)
    if settings.ROOM_AUDIO_ASR_ENABLED:
        from core.mastrao_room_audio_asr_worker import (  # noqa: PLC0415  # pylint: disable=import-outside-toplevel
            schedule_room_audio_asr,
        )

        transaction.on_commit(lambda: schedule_room_audio_asr(limit=1))
    return True


def schedule_room_audio_source_delivery(limit=20):
    """Queue a bounded set of pending source receipts for durable delivery."""
    if not settings.ROOM_AUDIO_SOURCE_TRANSFER_ENABLED or not settings.CELERY_ENABLED:
        return 0
    from core.tasks.native_capture import (  # noqa: PLC0415  # pylint: disable=import-outside-toplevel
        process_room_audio_sources,
    )

    dispatched = 0
    for row_id, queued_claim, queued_attempts in _reserve_room_audio_sources(limit):
        try:
            process_room_audio_sources.apply_async(
                args=[str(row_id), str(queued_claim), queued_attempts],
                queue="mastrao-transcription",
            )
            dispatched += 1
        except Exception:  # noqa: BLE001  # pylint: disable=broad-exception-caught
            models.MastraoRoomAudioSourceOutbox.objects.filter(
                pk=row_id,
                state=models.MastraoRoomAudioSourceOutbox.State.PENDING,
                claim_id=queued_claim,
            ).update(claim_id=None, claim_until=None)
            logger.warning(
                "Room audio source dispatch unavailable; pending row retained"
            )
    return dispatched


@csrf_exempt
@transaction.non_atomic_requests
@require_POST
def complete_room_audio_source(request):
    """Accept worker completion only after durable object persistence."""
    try:
        worker_claims = _verify_worker_token(request)
        compact, manifest, gaps = _read_submission(request)
        row = persist_room_audio_source(compact, manifest, gaps, worker_claims)
        if row.state != models.MastraoRoomAudioSourceOutbox.State.DELIVERED:
            deliver_room_audio_source(row)
            row.refresh_from_db()
        if row.state != models.MastraoRoomAudioSourceOutbox.State.DELIVERED:
            raise RecordingContractRefused(status=503)
        return _safe_response(
            {
                "state": "delivered",
                "manifest_digest": row.manifest_digest,
                "source_id": str(row.source_id),
                "replayed": row.replayed,
            }
        )
    except RecordingContractRefused as error:
        return _safe_response(
            {"error": "room_audio_source_refused"}, status=error.status
        )
    except (DatabaseError, UnicodeError, ValueError, TypeError):
        return _safe_response({"error": "room_audio_source_unavailable"}, status=503)
