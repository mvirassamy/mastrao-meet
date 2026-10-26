"""After-close Live inventory: bounded extraction, durable delivery and expiry.

Only a verified APPLIED close supplies organization authority. No recording,
media work, open-room collection or audio completeness is inferred here.
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from django.conf import settings
from django.db import DatabaseError, connection, transaction
from django.db.models import Exists, F, OuterRef, Q

from core import models
from core.mastrao_core_http import post_core_json
from core.mastrao_live_observed_domain_contract import (
    CONTEXT_PATH,
    DOMAIN_PATH,
    MAX_ORDINARY_REVISION,
    MAX_SAFE_INTEGER,
    DomainPayloadOverflow,
    LiveDomainRefused,
    context_request,
    ends_epoch,
    publication_request,
    validate_binding,
    validate_snapshot,
    validate_transport_configuration,
    verify_ack,
    verify_context,
)
from core.mastrao_recording_contract import RecordingContractRefused, _key
from core.mastrao_room_close_contract import (
    CLOSE_BINDING_FIELDS,
    RoomCloseRefused,
    persisted_close_receipt_claims_digest,
)
from core.mastrao_room_contract import _sha256_canonical

MAX_BATCH = 20
CLAIM_SECONDS = 45
RETRY_SECONDS = 30
REFRESH_SECONDS = 60
MAX_OBSERVATIONS = 128
MAX_EPOCHS = 32
logger = logging.getLogger(__name__)


# The detached delivery fence must retain all eight immutable claim fields.
@dataclass(frozen=True)
class LiveDomainClaim:  # pylint: disable=too-many-instance-attributes
    """Detached attempt fence; later dirty facts cannot be acknowledged by it."""

    domain_ref: UUID
    token: UUID
    jti: UUID
    generation: int
    revision: int
    digest: str
    binding: dict
    snapshot: dict


def _db_now():
    with connection.cursor() as cursor:
        cursor.execute("SELECT clock_timestamp()")
        return cursor.fetchone()[0]


def _require_postgres():
    if connection.vendor != "postgresql" or connection.in_atomic_block:
        raise LiveDomainRefused(status=503)


def _lock_closure(closure_id, *, skip_locked=False):
    """All writers take room, closure, then cursor; never invert this order."""
    room_id = (
        models.MastraoRoomClosure.objects.filter(pk=closure_id)
        .values_list("room_binding_id", flat=True)
        .first()
    )
    if room_id is None:
        return None
    room = (
        models.MastraoRoomBinding.objects.select_for_update(skip_locked=skip_locked)
        .filter(pk=room_id)
        .first()
    )
    if room is None:
        return None
    closure = (
        models.MastraoRoomClosure.objects.select_for_update(skip_locked=skip_locked)
        .filter(pk=closure_id)
        .first()
    )
    if closure is not None:
        closure.room_binding = room
    return closure


def _lock_domain(domain_id, *, skip_locked=False):
    closure_id = (
        models.MastraoLiveObservedDomain.objects.filter(pk=domain_id)
        .values_list("closure_id", flat=True)
        .first()
    )
    if closure_id is None:
        return None, None
    closure = _lock_closure(closure_id, skip_locked=skip_locked)
    if closure is None:
        return None, None
    cursor = (
        models.MastraoLiveObservedDomain.objects.select_for_update(
            skip_locked=skip_locked
        )
        .filter(pk=domain_id)
        .first()
    )
    return closure, cursor


def _terminal(closure, reason, now):
    closure.live_domain_outcome = "not_applicable"
    closure.live_domain_reason = reason
    closure.live_domain_classified_at = now
    closure.save(
        update_fields=[
            "live_domain_outcome",
            "live_domain_reason",
            "live_domain_classified_at",
            "updated_at",
        ]
    )


def _expire(closure, cursor, now):
    if cursor.retention_expires_at is None or cursor.retention_expires_at > now:
        return False
    _terminal(closure, "retention_expired", now)
    # Django's CASCADE collector physically deletes the bounded fingerprints too.
    cursor.delete()
    return True


def _binding(closure):
    room = closure.room_binding
    if (
        closure.state != models.MastraoRoomClosure.State.APPLIED
        or closure.applied_at is None
        or any(
            getattr(closure, name) != getattr(room, name)
            for name in ("meeting_ref", "room_ref", "provider_binding_digest")
        )
    ):
        raise LiveDomainRefused()
    facts = {name: getattr(closure, name) for name in CLOSE_BINDING_FIELDS}
    facts.update(
        receipt_claims=closure.receipt_claims,
        receipt_digest=closure.receipt_digest,
        provider_observation=closure.provider_observation,
    )
    try:
        digest = persisted_close_receipt_claims_digest(facts)
    except RoomCloseRefused as error:
        raise LiveDomainRefused() from error
    binding = {name: getattr(closure, name) for name in CLOSE_BINDING_FIELDS}
    binding["close_receipt_claims_digest"] = digest
    validate_binding(binding)
    return binding


def _clear_claim(cursor):
    for name in (
        "claim_token",
        "claim_until",
        "claim_generation",
        "claim_revision",
        "claim_digest",
        "claim_jti",
    ):
        setattr(cursor, name, None)


def mark_live_domain_dirty(closure):
    """Called under the room/closure lock, after APPLIED/correlation, no RTC copy."""
    if (
        closure.state != models.MastraoRoomClosure.State.APPLIED
        or closure.live_domain_outcome
    ):
        return
    cursor, created = (
        models.MastraoLiveObservedDomain.objects.select_for_update().get_or_create(
            closure=closure
        )
    )
    now = _db_now()
    if _expire(closure, cursor, now):
        return
    if not created:
        # Saturation cannot wrap or make an old ACK clean newer facts.
        cursor.dirty_generation = min(MAX_SAFE_INTEGER, cursor.dirty_generation + 1)
    cursor.next_snapshot_at = now
    cursor.next_attempt_at = now
    _clear_claim(cursor)
    cursor.save()


def dirty_live_domain_for_room(room_binding):
    """RTC intake already holds the room lock; terminal close markers fence it."""
    closure = (
        models.MastraoRoomClosure.objects.select_for_update()
        .filter(
            room_binding=room_binding, state="applied", live_domain_outcome__isnull=True
        )
        .first()
    )
    if closure is not None:
        mark_live_domain_dirty(closure)


def discover_live_domains(limit=MAX_BATCH):
    """Skip locked rooms before the page limit; discovered rows leave this set."""
    _require_postgres()
    count = 0
    with transaction.atomic():
        rows = list(
            models.MastraoRoomBinding.objects.select_for_update(
                of=("self",), skip_locked=True
            )
            .filter(
                closure__state="applied",
                closure__live_domain_outcome__isnull=True,
                closure__live_observed_domain__isnull=True,
            )
            .order_by("closure__applied_at", "id")
            .values_list("id", "closure__id")[: min(limit, MAX_BATCH)]
        )
        for _, closure_id in rows:
            closure = _lock_closure(closure_id, skip_locked=True)
            if (
                closure is None
                or closure.state != "applied"
                or closure.live_domain_outcome
            ):
                continue
            _, created = models.MastraoLiveObservedDomain.objects.get_or_create(
                closure=closure
            )
            count += created
    return count


def purge_live_observed_domains(limit=MAX_BATCH):
    """Physically remove every new copy, independent of flags, keys or endpoints."""
    _require_postgres()
    with transaction.atomic():
        now = _db_now()
    purged = 0
    with transaction.atomic():
        # SQL SKIP LOCKED applies before LIMIT, so locked old rooms do not hide
        # later expiry work. Lock only rooms first, never their joined cursors.
        candidates = list(
            models.MastraoRoomBinding.objects.select_for_update(
                of=("self",), skip_locked=True
            )
            .filter(closure__live_observed_domain__retention_expires_at__lte=now)
            .order_by("closure__live_observed_domain__retention_expires_at", "id")
            .values_list("id", "closure__live_observed_domain__id")[
                : min(limit, MAX_BATCH)
            ]
        )
        for _, domain_id in candidates:
            closure, cursor = _lock_domain(domain_id, skip_locked=True)
            if cursor is not None and _expire(closure, cursor, _db_now()):
                purged += 1
    return purged


def _policy(cursor):
    return {
        "policy_ref": cursor.policy_ref,
        "retention_policy_ref": cursor.retention_policy_ref,
        "retention_expires_at": int(cursor.retention_expires_at.timestamp()),
    }


def _observation(row):
    has_participant = row["event_type"] != "room_finished"
    has_track = row["event_type"] in {"track_published", "track_unpublished"}
    return {
        "observation_ref": str(row["id"]),
        "event_id": row["event_id"],
        "payload_digest": row["payload_digest"],
        "event_type": row["event_type"],
        "observed_at": row["event_time_seconds"],
        "room_sid": row["room_sid"],
        "participant_sid": row["participant_sid"] if has_participant else None,
        "track_sid": row["track_sid"] if has_track else None,
        "track_type": row["track_type"] if has_track else None,
        "track_source": row["track_source"] if has_track else None,
    }


def _collect_inventory(cursor, room_id):
    """Bounded authenticated inbox facts, never raw bodies or public token hints."""
    baseline = cursor.inventory_baseline
    matching = models.MastraoRtcObservation.objects.filter(
        room_binding_id=room_id,
        room_sid=OuterRef("connection__room_sid"),
        participant_sid=OuterRef("connection__participant_sid"),
        track_sid=OuterRef("track_sid"),
    )
    microphone = matching.filter(
        event_type="track_published", track_type=0, track_source=2
    )
    non_microphone = matching.filter(
        Q(track_type__in=(1, 2)) | Q(track_source__in=(1, 3, 4))
    )
    # Keep unknowns, terminal-only epochs and conflicts for validation. Positive
    # non-microphone facts must not hide a contradiction before the first baseline.
    query = models.MastraoRtcTrackEpoch.objects.filter(
        connection__room_binding_id=room_id
    ).filter(
        Q(pk__in=[r["epoch_ref"] for r in baseline.get("epochs", [])])
        | Q(conflict=True)
        | Q(Exists(microphone))
        | ~Q(Exists(non_microphone))
    )
    raw_epochs = list(
        query.order_by("id").values(
            "id",
            "connection_id",
            "connection__room_sid",
            "connection__participant_sid",
            "connection__correlation",
            "connection__media_token_binding_id",
            "first_publication_id",
            "track_sid",
            "ended",
            "conflict",
        )[: MAX_EPOCHS + 1]
    )
    if len(raw_epochs) > MAX_EPOCHS:
        return {"reason": "epoch_limit_exceeded"}
    related = query.filter(
        connection__room_sid=OuterRef("room_sid"),
        connection__participant_sid=OuterRef("participant_sid"),
    )
    observations_query = models.MastraoRtcObservation.objects.filter(
        room_binding_id=room_id
    ).filter(
        Q(pk__in=[r["observation_ref"] for r in baseline.get("observations", [])])
        | Q(event_type="room_finished")
        | (
            Q(event_type__in=["participant_joined", "participant_left"])
            & Q(Exists(related))
        )
        | (
            Q(event_type__in=["track_published", "track_unpublished"])
            & Q(Exists(related.filter(track_sid=OuterRef("track_sid"))))
        )
        | Q(event_type="track_published", track_type=0, track_source=2)
    )
    observations = [
        _observation(row)
        for row in observations_query.order_by("id").values(
            "id",
            "event_id",
            "payload_digest",
            "event_type",
            "event_time_seconds",
            "room_sid",
            "participant_sid",
            "track_sid",
            "track_type",
            "track_source",
        )[: MAX_OBSERVATIONS + 1]
    ]
    if len(observations) > MAX_OBSERVATIONS:
        return {"reason": "observation_limit_exceeded"}
    epochs = []
    for raw in raw_epochs:
        correlation = raw["connection__correlation"]
        if raw["conflict"] or correlation in {
            "conflicting_join",
            "token_reused",
            "identity_conflict",
        }:
            return {"reason": "correlation_conflict"}
        epoch = {
            "epoch_ref": str(raw["id"]),
            "connection_ref": str(raw["connection_id"]),
            "room_sid": raw["connection__room_sid"],
            "participant_sid": raw["connection__participant_sid"],
            "track_sid": raw["track_sid"],
            "publication_observation_ref": None,
            "end_observation_refs": [],
            "ended": raw["ended"],
            "conflict": raw["conflict"],
            "correlation": correlation,
            "media_token_binding_ref": None,
        }
        if correlation == "correlated":
            if raw["connection__media_token_binding_id"] is None:
                return {"reason": "correlation_conflict"}
            epoch["media_token_binding_ref"] = str(
                raw["connection__media_token_binding_id"]
            )
        if raw["first_publication_id"] is not None:
            epoch["publication_observation_ref"] = str(raw["first_publication_id"])
        epoch["end_observation_refs"] = [
            r["observation_ref"] for r in observations if ends_epoch(r, epoch)
        ]
        epochs.append(epoch)
    rooms = {r["room_sid"] for r in observations} | {e["room_sid"] for e in epochs}
    if len(rooms) > 1 or not _preserves_baseline(baseline, observations, epochs):
        return {"reason": "identity_conflict"}
    return {"observations": observations, "epochs": epochs}


def _preserves_baseline(baseline, observations, epochs):
    by_observation = {row["observation_ref"]: row for row in observations}
    if any(
        by_observation.get(old["observation_ref"]) != old
        for old in baseline.get("observations", [])
    ):
        return False
    by_epoch = {row["epoch_ref"]: row for row in epochs}
    for old in baseline.get("epochs", []):
        new = by_epoch.get(old["epoch_ref"])
        if new is None or any(
            new[k] != old[k]
            for k in ("connection_ref", "room_sid", "participant_sid", "track_sid")
        ):
            return False
        if old["ended"] and not new["ended"]:
            return False
        for name in ("publication_observation_ref", "media_token_binding_ref"):
            if old[name] is not None and old[name] != new[name]:
                return False
        if not set(old["end_observation_refs"]) <= set(new["end_observation_refs"]):
            return False
    return True


def _snapshot(cursor, binding):
    if cursor.producer_revision == MAX_SAFE_INTEGER:
        return None
    common = {
        "version": 1,
        "type": "mastrao.live-observed-domain",
        "scope": "observed_live_domain",
        "binding": binding,
        "policy": _policy(cursor),
        "domain_ref": str(cursor.pk),
    }
    if cursor.inventory_baseline and any(
        cursor.inventory_baseline[key] != value for key, value in common.items()
    ):
        content = {"reason": "identity_conflict"}
    elif cursor.sticky_reason:
        content = {"reason": cursor.sticky_reason}
    else:
        content = _collect_inventory(cursor, cursor.closure.room_binding_id)
    kind = "invalidation" if "reason" in content else "inventory"
    semantic = {**common, "kind": kind, **content}
    facts_digest = _sha256_canonical(semantic)
    if facts_digest == cursor.facts_digest:
        return None
    revision = cursor.producer_revision + 1
    if revision > MAX_ORDINARY_REVISION:
        revision = MAX_SAFE_INTEGER
        semantic = {**common, "kind": "invalidation", "reason": "revision_exhausted"}
        facts_digest = _sha256_canonical(semantic)
    snapshot = {**semantic, "producer_revision": revision}
    try:
        validate_snapshot(snapshot)
        # Measure the actual signed HTTP body, including the largest fixed UUID
        # attempt identity and current configured issuer/audience/key headers.
        publication_request(snapshot, uuid4().hex)
    except (DomainPayloadOverflow, LiveDomainRefused) as error:
        reason = (
            "payload_limit_exceeded"
            if isinstance(error, DomainPayloadOverflow)
            else "identity_conflict"
        )
        semantic = {**common, "kind": "invalidation", "reason": reason}
        snapshot = {**semantic, "producer_revision": revision}
        facts_digest = _sha256_canonical(semantic)
        publication_request(snapshot, uuid4().hex)
    return snapshot, facts_digest


def _prepare_locked(closure, cursor):
    now = _db_now()
    if closure.live_domain_outcome or _expire(closure, cursor, now):
        return None
    if cursor.retention_expires_at is None:
        return None
    cursor.closure = closure
    binding = _binding(closure)
    prepared = _snapshot(cursor, binding)
    # Time is read again after extraction/signing, not just before them. A slow
    # snapshot must not commit any new retained copy past the fixed deadline.
    now = _db_now()
    if _expire(closure, cursor, now):
        return None
    if prepared is not None:
        snapshot, facts_digest = prepared
        cursor.producer_revision = snapshot["producer_revision"]
        cursor.facts_digest = facts_digest
        cursor.snapshot = snapshot
        cursor.snapshot_digest = _sha256_canonical(snapshot)
        if snapshot["kind"] == "inventory":
            cursor.inventory_baseline = snapshot
        else:
            cursor.sticky_reason = snapshot["reason"]
        _clear_claim(cursor)
        cursor.next_attempt_at = now
    cursor.prepared_generation = cursor.dirty_generation
    cursor.next_snapshot_at = now + timedelta(seconds=REFRESH_SECONDS)
    # A new RTC generation with identical semantic facts still has a distinct
    # clean fence. Its ACK must be earned by the new generation's attempt.
    cursor.save()
    if prepared is not None:
        models.MastraoLiveObservedDomainRevision.objects.create(
            domain=cursor,
            producer_revision=cursor.producer_revision,
            snapshot_digest=cursor.snapshot_digest,
            retention_expires_at=cursor.retention_expires_at,
        )
    return cursor


def prepare_live_observed_domain(domain_id):
    """Coherent PostgreSQL snapshot, bounded serialization/deadlock retries."""
    _require_postgres()
    for attempt in range(3):
        try:
            with transaction.atomic(durable=True):
                with connection.cursor() as sql:
                    sql.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
                closure, cursor = _lock_domain(domain_id, skip_locked=True)
                if cursor is None:
                    return None
                return _prepare_locked(closure, cursor)
        except DatabaseError as error:
            if (
                getattr(error.__cause__, "sqlstate", None) not in {"40001", "40P01"}
                or attempt == 2
            ):
                raise
    return None


def claim_live_observed_domain(domain_id):
    """Acquire a leased context/publication attempt with no network under locks."""
    _require_postgres()
    with transaction.atomic():
        closure, cursor = _lock_domain(domain_id, skip_locked=True)
        if cursor is None:
            return None
        now = _db_now()
        if closure.live_domain_outcome or _expire(closure, cursor, now):
            return None
        if cursor.next_attempt_at > now or (
            cursor.claim_until is not None and cursor.claim_until > now
        ):
            return None
        if cursor.retention_expires_at is not None:
            if (
                cursor.producer_revision == 0
                or cursor.prepared_generation != cursor.dirty_generation
            ):
                return None
            if (
                cursor.acked_generation == cursor.prepared_generation
                and cursor.acknowledged_revision == cursor.producer_revision
            ):
                return None
        binding = _binding(closure)
        token, jti = uuid4(), uuid4()
        cursor.claim_token, cursor.claim_jti = token, jti
        cursor.claim_until = now + timedelta(seconds=CLAIM_SECONDS)
        cursor.claim_generation = cursor.prepared_generation
        cursor.claim_revision = cursor.producer_revision
        cursor.claim_digest = cursor.snapshot_digest
        cursor.next_attempt_at = now + timedelta(seconds=RETRY_SECONDS)
        cursor.save()
        return LiveDomainClaim(
            cursor.pk,
            token,
            jti,
            cursor.prepared_generation,
            cursor.producer_revision,
            cursor.snapshot_digest,
            binding,
            cursor.snapshot,
        )


def _matches_claim(cursor, claim, now):
    return (
        cursor.claim_token == claim.token
        and cursor.claim_jti == claim.jti
        and cursor.claim_until is not None
        and cursor.claim_until > now
        and cursor.claim_generation == claim.generation == cursor.prepared_generation
        and cursor.claim_revision == claim.revision == cursor.producer_revision
        and cursor.claim_digest == claim.digest == cursor.snapshot_digest
        and cursor.dirty_generation == claim.generation
    )


def finish_live_observed_domain(claim, *, context=None, acknowledgement=None):
    """Commit only verified responses for the still-current leased generation."""
    with transaction.atomic():
        closure, cursor = _lock_domain(claim.domain_ref)
        if cursor is None:
            return False
        now = _db_now()
        if closure.live_domain_outcome or _expire(closure, cursor, now):
            return False
        # A context claim carries generation zero before any RTC preparation.
        matches = _matches_claim(cursor, claim, now)
        if claim.revision == 0:
            matches = (
                cursor.claim_token == claim.token
                and cursor.claim_jti == claim.jti
                and cursor.claim_until is not None
                and cursor.claim_until > now
                and cursor.producer_revision == 0
                and cursor.retention_expires_at is None
            )
        if not matches or _binding(closure) != claim.binding:
            return False
        _clear_claim(cursor)
        if context is not None:
            # Reverify here after lock wait, including TTL and exact attempt B.
            value = verify_context(context, claim.binding, claim.jti.hex)
            if _apply_verified_context(closure, cursor, value):
                return True
        elif acknowledgement is not None:
            verify_ack(acknowledgement, claim.snapshot, claim.jti.hex)
            if _expire(closure, cursor, _db_now()):
                return False
            cursor.acked_generation = claim.generation
            cursor.acknowledged_revision = claim.revision
            cursor.acknowledged_digest = claim.digest
        cursor.save()
        return context is not None or acknowledgement is not None


def _apply_verified_context(closure, cursor, value):
    """Store Q once, or remove the operational cursor with only a close marker."""
    now = _db_now()
    if value["outcome"] == "not_applicable":
        _terminal(closure, value["reason"], now)
        cursor.delete()
        return True
    policy = value["policy"]
    if policy["retention_expires_at"] <= int(now.timestamp()):
        _terminal(closure, "retention_expired", now)
        cursor.delete()
        return True
    cursor.policy_ref = policy["policy_ref"]
    cursor.retention_policy_ref = policy["retention_policy_ref"]
    cursor.retention_expires_at = datetime.fromtimestamp(
        policy["retention_expires_at"], tz=UTC
    )
    cursor.next_snapshot_at = now
    cursor.next_attempt_at = now
    return False


def _configuration():
    try:
        configured = validate_transport_configuration(settings)
        if configured:
            _key("MASTRAO_RECORDING_EFFECT_PUBLIC_JWK", private=False)
            _key("MASTRAO_RECORDING_RECEIPT_PRIVATE_JWK", private=True)
        return configured
    except RecordingContractRefused as error:
        raise LiveDomainRefused(status=503) from error


def _invalidate_oversized_delivery(claim):
    """A changed signing envelope must invalidate an older positive head too."""
    with transaction.atomic():
        closure, cursor = _lock_domain(claim.domain_ref)
        if cursor is None:
            return False
        now = _db_now()
        if _expire(closure, cursor, now) or not _matches_claim(cursor, claim, now):
            return False
        if cursor.snapshot["kind"] != "inventory":
            return False
        cursor.sticky_reason = "payload_limit_exceeded"
        # No network or RTC read: the irreversible invalidation preserves B/Q,
        # the previous baseline and the exact current claim's generation.
        return _prepare_locked(closure, cursor) is not None


def deliver_live_observed_domain(domain_id):  # noqa: PLR0911 -- phase-specific guarded exits
    """Fresh JOSE envelope around durable semantic bytes; retry after lost reply."""
    # Separate exits preserve the context, publication, fencing and retry phases.
    # pylint: disable=too-many-return-statements
    _require_postgres()
    if not _configuration():
        return False
    claim = claim_live_observed_domain(domain_id)
    if claim is None:
        return False
    try:
        if claim.revision == 0:
            body = context_request(claim.binding, claim.jti.hex)
            response = post_core_json(
                endpoint=settings.MASTRAO_CORE_LIVE_OBSERVED_DOMAIN_CONTEXT_ENDPOINT,
                expected_path=CONTEXT_PATH,
                body=body,
                timeout=settings.MASTRAO_CORE_RECORDING_TIMEOUT_SECONDS,
                refusal=LiveDomainRefused,
                expected_fields={"context"},
                client_error_status=404,
            )
            return finish_live_observed_domain(claim, context=response["context"])
        body = publication_request(claim.snapshot, claim.jti.hex)
        # A new transaction after signing fences expiry and concurrent dirty/ACK
        # before starting HTTP. No lock is retained across the external call.
        with transaction.atomic():
            closure, cursor = _lock_domain(domain_id)
            if cursor is None:
                return False
            now = _db_now()
            if _expire(closure, cursor, now) or not _matches_claim(cursor, claim, now):
                return False
        response = post_core_json(
            endpoint=settings.MASTRAO_CORE_LIVE_OBSERVED_DOMAIN_ENDPOINT,
            expected_path=DOMAIN_PATH,
            body=body,
            timeout=settings.MASTRAO_CORE_RECORDING_TIMEOUT_SECONDS,
            refusal=LiveDomainRefused,
            expected_fields={"acknowledgement"},
            client_error_status=404,
        )
        return finish_live_observed_domain(
            claim, acknowledgement=response["acknowledgement"]
        )
    except DomainPayloadOverflow:
        _invalidate_oversized_delivery(claim)
        return False
    except RecordingContractRefused:
        finish_live_observed_domain(claim)
        return False


def _postpone(domain_id, field):
    with transaction.atomic():
        _, cursor = _lock_domain(domain_id, skip_locked=True)
        if cursor is not None:
            setattr(cursor, field, _db_now() + timedelta(seconds=RETRY_SECONDS))
            cursor.save(update_fields=[field, "updated_at"])


def _due_domain_ids(candidates, field, limit):
    """Bounded room-first SKIP LOCKED selection, committed before HTTP claims."""
    with transaction.atomic():
        rows = (
            models.MastraoRoomBinding.objects.select_for_update(
                of=("self",), skip_locked=True
            )
            .filter(closure__live_observed_domain__id__in=candidates.values("id"))
            .order_by(f"closure__live_observed_domain__{field}", "id")
            .values_list("id", "closure__live_observed_domain__id")[:limit]
        )
        return [domain_id for _, domain_id in rows]


def reconcile_live_observed_domains(limit=MAX_BATCH):
    """Existing full scheduler consumes four independent bounded fair queues."""
    _require_postgres()
    limit = max(1, min(int(limit), MAX_BATCH))
    count = purge_live_observed_domains(limit=limit)
    try:
        configured = _configuration()
    except LiveDomainRefused:
        logger.warning("Live observed-domain configuration refused")
        return count
    if not configured:
        return count
    count += discover_live_domains(limit=limit)
    with transaction.atomic():
        now = _db_now()
    refresh_ids = _due_domain_ids(
        models.MastraoLiveObservedDomain.objects.filter(
            retention_expires_at__gt=now, next_snapshot_at__lte=now
        ),
        "next_snapshot_at",
        limit,
    )
    for domain_id in refresh_ids:
        try:
            if prepare_live_observed_domain(domain_id) is not None:
                count += 1
        except (RecordingContractRefused, DatabaseError):
            _postpone(domain_id, "next_snapshot_at")
            logger.warning("Live observed-domain preparation refused")
    # Contexts and publication deliveries have independent pages. A backlog of
    # unconfirmed closes cannot monopolize already eligible metadata delivery.
    for awaiting_context in (True, False):
        with transaction.atomic():
            now = _db_now()
        candidates = models.MastraoLiveObservedDomain.objects.filter(
            retention_expires_at__isnull=awaiting_context, next_attempt_at__lte=now
        )
        if not awaiting_context:
            candidates = candidates.filter(
                Q(acked_generation__lt=F("prepared_generation"))
                | Q(acknowledged_revision__lt=F("producer_revision")),
                producer_revision__gt=0,
                prepared_generation=F("dirty_generation"),
            )
        ids = _due_domain_ids(candidates, "next_attempt_at", limit)
        for domain_id in ids:
            try:
                count += deliver_live_observed_domain(domain_id)
            except (RecordingContractRefused, DatabaseError):
                _postpone(domain_id, "next_attempt_at")
                logger.warning("Live observed-domain delivery refused")
    return count
