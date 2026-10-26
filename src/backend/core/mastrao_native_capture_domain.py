"""Deliver revisioned snapshots of the known native capture domain, never coverage."""

import json
import logging
import re
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID, uuid4

from django.conf import settings
from django.db import DatabaseError, IntegrityError, connection, transaction
from django.db.models import Case, Exists, F, IntegerField, OuterRef, Q, When
from django.utils import timezone

from core import models
from core.mastrao_core_http import post_core_json
from core.mastrao_native_authorization import native_envelope
from core.mastrao_recording_contract import RecordingContractRefused, _sign
from core.mastrao_room_close_contract import (
    RoomCloseRefused,
    persisted_close_receipt_claims_digest,
)
from core.mastrao_room_contract import _canonical_json, _sha256_canonical

MAX_OBSERVATIONS = 128
MAX_EPOCHS = 32
SNAPSHOT_ATTEMPTS = 3
CLAIM_SECONDS = 45
RETRY_SECONDS = 30
MAX_BATCH = 20
MAX_BODY_BYTES = 65_536
MAX_INVALIDATION_BYTES = 4_096
DOMAIN_PATH = "/internal/v1/meetings/capture/native/domain"
DOMAIN_TYPE = "mastrao.meet-native-capture-domain"
DOMAIN_JOSE = "mastrao-native-capture-domain+jws"
ACK_FIELDS = {
    "version",
    "domain_ref",
    "producer_revision",
    "snapshot_digest",
    "outcome",
    "current_revision",
    "current_snapshot_digest",
}
logger = logging.getLogger(__name__)

OBSERVATION_FIELDS = (
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
)
EPOCH_FIELDS = (
    "id",
    "connection_id",
    "connection__room_sid",
    "connection__participant_sid",
    "connection__correlation",
    "connection__ended",
    "track_sid",
    "first_publication_id",
    "ended",
    "conflict",
    "native_admission_capture_ref",
)
CAPTURE_FIELDS = (
    "id",
    "epoch_id",
    "capture_ref",
    "effect_key",
    "arguments_digest",
    "organization_external_id",
    "provider_job_ref",
    "receipt_claims",
    "stop_requested_at",
    "stop_reason",
    "observed_status",
    "drained_at",
    "source_manifest",
    "source_receipt",
)
CLOSURE_FIELDS = (
    "id",
    "organization_external_id",
    "meeting_ref",
    "room_ref",
    "provider_binding_digest",
    "close_ref",
    "effect_key",
    "arguments_digest",
    "state",
    "requested_at",
    "applied_at",
    "receipt_digest",
    "receipt_claims",
    "provider_observation",
)


class DomainSnapshotRefused(ValueError):
    """No coherent, exactly bound producer snapshot can be prepared."""


class _ConcurrentDomainCreation(Exception):
    """Restart REPEATABLE READ to see a concurrently inserted cursor."""


class _PayloadOverflow(Exception):
    """The signed request or decoded assertion exceeds its wire budget."""


@dataclass(frozen=True)
class NativeCaptureDomainFacts:
    """Bounded raw database facts; datetime/UUID values retain their native types."""

    room_binding: models.MastraoRoomBinding
    recording_binding: models.MastraoRecordingBinding
    observations: tuple[dict, ...]
    epochs: tuple[dict, ...]
    captures: tuple[dict, ...]
    closure: dict | None
    overflow_reason: str | None


@dataclass(frozen=True)
class PreparedDomainRevision:
    """Semantic content and digest supplied by the frozen-contract encoder.

    The content digest includes the candidate revision. The facts digest omits
    it, so unchanged facts keep their persisted revision. Transport envelopes
    are added after commit; retries never rebuild the semantic content.
    """

    snapshot: dict
    digest: str
    facts_digest: str


@dataclass(frozen=True)
class DomainDeliveryClaim:
    """An immutable receipt fence, detached from later model updates."""

    domain_ref: UUID
    producer_revision: int
    snapshot_digest: str
    token: UUID
    snapshot: dict


def _sqlstate(error):
    return getattr(error.__cause__, "sqlstate", None)


def _lock_domain(room_binding_id):
    cursor = (
        models.MastraoNativeCaptureDomain.objects.select_for_update()
        .filter(room_binding_id=room_binding_id)
        .first()
    )
    if cursor is not None:
        return cursor
    try:
        # A fresh row is locked by this transaction until commit as well.
        return models.MastraoNativeCaptureDomain.objects.create(
            room_binding_id=room_binding_id
        )
    except IntegrityError as error:
        if _sqlstate(error) == "23505":
            raise _ConcurrentDomainCreation from error
        raise


def _collect_facts(room_binding_id, baseline=None):
    baseline = baseline or {}
    binding = models.MastraoRoomBinding.objects.get(pk=room_binding_id)
    recording = models.MastraoRecordingBinding.objects.filter(
        room_binding_id=binding.pk,
        meeting_ref=binding.meeting_ref,
        room_ref=binding.room_ref,
        provider_binding_digest=binding.provider_binding_digest,
    ).first()
    if recording is None or not recording.organization_external_id:
        raise DomainSnapshotRefused("Exact recording binding required")

    # Retain every microphone epoch, including terminal-only/unmatched epochs.
    # Positive video/screen provenance never becomes a microphone fact.
    microphone = models.MastraoRtcObservation.objects.filter(
        room_binding_id=binding.pk,
        event_type="track_published",
        track_type=0,
        track_source=2,
        room_sid=OuterRef("connection__room_sid"),
        participant_sid=OuterRef("connection__participant_sid"),
        track_sid=OuterRef("track_sid"),
    )
    non_microphone = models.MastraoRtcObservation.objects.filter(
        room_binding_id=binding.pk,
        room_sid=OuterRef("connection__room_sid"),
        participant_sid=OuterRef("connection__participant_sid"),
        track_sid=OuterRef("track_sid"),
    ).filter(Q(track_type__in=(1, 2)) | Q(track_source__in=(1, 3, 4)))
    native_epochs = models.MastraoRtcTrackEpoch.objects.filter(
        connection__room_binding_id=binding.pk
    ).filter(
        Q(pk__in=[row["epoch_ref"] for row in baseline.get("epochs", [])])
        | Q(Exists(microphone))
        | (Q(first_publication__isnull=True) & ~Q(Exists(non_microphone)))
        | Q(mastraonativecapturestart__isnull=False)
    )
    related_epoch = native_epochs.filter(
        connection__room_sid=OuterRef("room_sid"),
        connection__participant_sid=OuterRef("participant_sid"),
    )
    observations = tuple(
        models.MastraoRtcObservation.objects.filter(room_binding_id=binding.pk)
        .filter(
            Q(
                pk__in=[
                    row["observation_ref"] for row in baseline.get("observations", [])
                ]
            )
            | Q(event_type="track_published", track_type=0, track_source=2)
            | Q(event_type="room_finished")
            | (Q(event_type="participant_left") & Q(Exists(related_epoch)))
            | (
                Q(event_type="track_unpublished")
                & Q(Exists(related_epoch.filter(track_sid=OuterRef("track_sid"))))
            )
        )
        .order_by("pk")
        .values(*OBSERVATION_FIELDS)[: MAX_OBSERVATIONS + 1]
    )
    epochs = tuple(
        native_epochs.annotate(non_microphone=Exists(non_microphone))
        .order_by("pk")
        .values(*EPOCH_FIELDS, "non_microphone")[: MAX_EPOCHS + 1]
    )
    captures = tuple(
        models.MastraoNativeCaptureStart.objects.filter(
            epoch__connection__room_binding_id=binding.pk
        )
        .order_by("pk")
        .values(*CAPTURE_FIELDS)[: MAX_EPOCHS + 1]
    )
    closure = (
        models.MastraoRoomClosure.objects.filter(room_binding_id=binding.pk)
        .values(*CLOSURE_FIELDS)
        .first()
    )
    overflow = None
    if len(observations) > MAX_OBSERVATIONS:
        overflow = "observation_limit_exceeded"
    elif len(epochs) > MAX_EPOCHS or len(captures) > MAX_EPOCHS:
        overflow = "epoch_limit_exceeded"
    # A limit sentinel can never become a partially exported inventory.
    if overflow is not None:
        observations, epochs, captures = (), (), ()
    return NativeCaptureDomainFacts(
        binding, recording, observations, epochs, captures, closure, overflow
    )


def _persist_revision(cursor, prepared):
    if not all(
        re.fullmatch(r"[a-f0-9]{64}", digest)
        for digest in (prepared.digest, prepared.facts_digest)
    ):
        raise DomainSnapshotRefused("A semantic SHA-256 digest is required")
    if not isinstance(prepared.snapshot, dict) or not prepared.snapshot:
        raise DomainSnapshotRefused("Nonempty semantic content is required")
    if cursor.facts_digest == prepared.facts_digest:
        return cursor
    cursor.producer_revision += 1
    cursor.snapshot_digest = prepared.digest
    cursor.facts_digest = prepared.facts_digest
    cursor.snapshot = prepared.snapshot
    if prepared.snapshot.get("kind") == "inventory":
        cursor.inventory_baseline = prepared.snapshot
    cursor.acknowledged_at = None
    cursor.claim_token = None
    cursor.claim_until = None
    cursor.next_attempt_at = timezone.now()
    cursor.save(
        update_fields=[
            "producer_revision",
            "facts_digest",
            "snapshot_digest",
            "snapshot",
            "inventory_baseline",
            "acknowledged_at",
            "claim_token",
            "claim_until",
            "next_attempt_at",
            "updated_at",
        ]
    )
    return cursor


def _text(value, pattern):
    if not isinstance(value, str) or not re.fullmatch(pattern, value):
        raise DomainSnapshotRefused("Persisted fact outside domain contract")
    return value


def _uuid(value):
    try:
        canonical = str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError) as error:
        raise DomainSnapshotRefused("Noncanonical domain reference") from error
    if canonical != str(value):
        raise DomainSnapshotRefused("Noncanonical domain reference")
    return canonical


def _seconds(value):
    if value is None:
        return None
    seconds = int(value.timestamp())
    if not 0 < seconds <= 9_007_199_254_740_991:
        raise DomainSnapshotRefused("Invalid control timestamp")
    return seconds


def _epoch_matches(epoch, observation):
    return (
        epoch["connection__room_sid"] == observation["room_sid"]
        and epoch["connection__participant_sid"] == observation["participant_sid"]
        and epoch["track_sid"] == observation["track_sid"]
    )


def _ends_epoch(epoch, observation):
    if observation["room_sid"] != epoch["connection__room_sid"]:
        return False
    event = observation["event_type"]
    if event == "room_finished":
        return True
    if event == "participant_left":
        return observation["participant_sid"] == epoch["connection__participant_sid"]
    if event == "track_unpublished":
        return _epoch_matches(epoch, observation)
    return False


def _observation_snapshot(observation, epochs):
    event = observation["event_type"]
    participant, track, epoch_ref = None, None, None
    if event != "room_finished":
        participant = _text(observation["participant_sid"], r"PA_[A-Za-z0-9_-]{1,120}")
    if event in {"track_published", "track_unpublished"}:
        track = _text(observation["track_sid"], r"TR_[A-Za-z0-9_-]{1,120}")
        epoch_ref = next(
            (
                _uuid(epoch["id"])
                for epoch in epochs
                if _epoch_matches(epoch, observation)
            ),
            None,
        )
    if not 0 < observation["event_time_seconds"] <= 9_007_199_254_740_991:
        raise DomainSnapshotRefused("Invalid RTC fact time")
    return {
        "observation_ref": _uuid(observation["id"]),
        "event_id": _text(observation["event_id"], r"[A-Za-z0-9_-]{1,128}"),
        "payload_digest": _text(observation["payload_digest"], r"[a-f0-9]{64}"),
        "event_type": event,
        "observed_at": observation["event_time_seconds"],
        "room_sid": _text(observation["room_sid"], r"RM_[A-Za-z0-9_-]{1,120}"),
        "participant_sid": participant,
        "track_sid": track,
        "epoch_ref": epoch_ref,
    }


def _epoch_snapshot(epoch, observations, captures):
    capture = next((row for row in captures if row["epoch_id"] == epoch["id"]), None)
    admission, capture_ref = "unadmitted", None
    if capture is not None:
        admission, capture_ref = "capture", _uuid(capture["capture_ref"])
    return {
        "epoch_ref": _uuid(epoch["id"]),
        "room_sid": _text(epoch["connection__room_sid"], r"RM_[A-Za-z0-9_-]{1,120}"),
        "participant_sid": _text(
            epoch["connection__participant_sid"], r"PA_[A-Za-z0-9_-]{1,120}"
        ),
        "track_sid": _text(epoch["track_sid"], r"TR_[A-Za-z0-9_-]{1,120}"),
        "ended": epoch["ended"],
        "conflict": epoch["conflict"]
        or epoch.get("non_microphone", False)
        or epoch["connection__correlation"]
        in {
            "conflicting_join",
            "token_reused",
            "identity_conflict",
        },
        "end_observation_refs": sorted(
            _uuid(row["id"]) for row in observations if _ends_epoch(epoch, row)
        ),
        "admission": admission,
        "refusal_digest": None,
        "capture_ref": capture_ref,
    }


def _capture_snapshot(capture, organization):
    if capture["organization_external_id"] != organization:
        raise DomainSnapshotRefused("Capture organization mismatch")
    job = capture["provider_job_ref"]
    if job is not None:
        _text(job, r"EG_[A-Za-z0-9]{1,96}")
    status = capture["observed_status"]
    # Persisted provider status is an exact integer; bool is not a valid status.
    # pylint: disable-next=unidiomatic-typecheck
    if status is not None and (type(status) is not int or not 0 <= status <= 6):
        raise DomainSnapshotRefused("Invalid persisted provider status")
    stop_reason = capture["stop_reason"] or None
    if stop_reason is not None and stop_reason not in {
        "room_closed",
        "quarantined",
        "source_ended",
        "retention_unknown",
        "retention_expired",
    }:
        raise DomainSnapshotRefused("Invalid persisted stop reason")
    if capture["drained_at"] is not None and (
        job is None or status not in {3, 4, 5, 6}
    ):
        raise DomainSnapshotRefused("Drain requires an exact terminal job")
    manifest_digest, source_ref = None, None
    if capture["source_manifest"] is not None:
        manifest_digest = _sha256_canonical(capture["source_manifest"])
    if capture["source_receipt"] is not None:
        source_ref = _uuid(capture["source_receipt"].get("source_ref"))
    return {
        "epoch_ref": _uuid(capture["epoch_id"]),
        "capture_ref": _uuid(capture["capture_ref"]),
        "effect_key": _text(capture["effect_key"], r"[A-Za-z0-9_-]{16,160}"),
        "arguments_digest": _text(capture["arguments_digest"], r"[a-f0-9]{64}"),
        "provider_job_ref": job,
        "stop_requested_at": _seconds(capture["stop_requested_at"]),
        "stop_reason": stop_reason,
        "observed_status": status,
        "drained_at": _seconds(capture["drained_at"]),
        "manifest_digest": manifest_digest,
        "source_receipt_ref": source_ref,
    }


def _closure_snapshot(facts):
    closure = facts.closure
    if closure is None or closure["state"] != "applied":
        return None
    recording = facts.recording_binding
    if any(
        closure[field] != getattr(recording, field)
        for field in (
            "organization_external_id",
            "meeting_ref",
            "room_ref",
            "provider_binding_digest",
        )
    ):
        raise DomainSnapshotRefused("Closure binding mismatch")
    try:
        receipt_digest = persisted_close_receipt_claims_digest(closure)
    except RoomCloseRefused as error:
        raise DomainSnapshotRefused("Invalid persisted close receipt") from error
    return {
        "close_ref": _text(closure["close_ref"], r"[A-Za-z0-9_-]{16,160}"),
        "effect_key": _text(closure["effect_key"], r"[A-Za-z0-9_-]{16,160}"),
        "arguments_digest": _text(closure["arguments_digest"], r"[a-f0-9]{64}"),
        "applied_at": _seconds(closure["applied_at"]),
        "receipt_digest": receipt_digest,
    }


def _semantic_snapshot(facts, domain_ref):
    recording = facts.recording_binding
    snapshot = {
        "version": 1,
        "type": DOMAIN_TYPE,
        "organization_external_id": _text(
            recording.organization_external_id, r"[A-Za-z0-9._:-]{1,160}"
        ),
        "meeting_ref": _text(recording.meeting_ref, r"[A-Za-z0-9_-]{16,160}"),
        "room_ref": _text(recording.room_ref, r"[A-Za-z0-9_-]{16,160}"),
        "provider_binding_digest": _text(
            recording.provider_binding_digest, r"[a-f0-9]{64}"
        ),
        "scope": "observed_native_capture_domain",
        "domain_ref": _uuid(domain_ref),
    }
    if facts.overflow_reason is not None:
        return {**snapshot, "kind": "invalidation", "reason": facts.overflow_reason}
    observations = sorted(
        (_observation_snapshot(row, facts.epochs) for row in facts.observations),
        key=lambda row: row["observation_ref"],
    )
    epochs = sorted(
        (
            _epoch_snapshot(row, facts.observations, facts.captures)
            for row in facts.epochs
        ),
        key=lambda row: row["epoch_ref"],
    )
    captures = sorted(
        (
            _capture_snapshot(row, recording.organization_external_id)
            for row in facts.captures
        ),
        key=lambda row: row["capture_ref"],
    )
    requested_at = facts.room_binding.closing_at
    if facts.closure is not None:
        requested_at = facts.closure["requested_at"]
    return {
        **snapshot,
        "kind": "inventory",
        "inventory_complete": True,
        "observations": observations,
        "epochs": epochs,
        "captures": captures,
        "counts": {
            "observations": len(observations),
            "epochs": len(epochs),
            "captures": len(captures),
        },
        "close_requested_at": _seconds(requested_at),
        "closure": _closure_snapshot(facts),
    }


def _wire_body(snapshot):
    assertion = {**native_envelope(DOMAIN_TYPE), **snapshot}
    body = {"domain": _sign(assertion, DOMAIN_JOSE)}
    budget = MAX_BODY_BYTES
    if snapshot["kind"] == "invalidation":
        budget = MAX_INVALIDATION_BYTES
    # post_core_json uses requests' default JSON encoding (including its spaces).
    if (
        len(_canonical_json(assertion)) > budget
        or len(json.dumps(body).encode("utf-8")) > budget
    ):
        raise _PayloadOverflow
    return body


def _invalidation(snapshot, reason):
    return {
        key: snapshot[key]
        for key in (
            "version",
            "type",
            "organization_external_id",
            "meeting_ref",
            "room_ref",
            "provider_binding_digest",
            "scope",
            "domain_ref",
        )
    } | {"kind": "invalidation", "reason": reason}


def _revision_content(semantic, cursor):
    fingerprint = _sha256_canonical(semantic)
    revision = cursor.producer_revision + 1
    if cursor.facts_digest == fingerprint:
        revision = cursor.producer_revision
    if not 0 < revision <= 9_007_199_254_740_991:
        raise DomainSnapshotRefused("Producer revision exhausted")
    content = {**semantic, "producer_revision": revision}
    digest = _sha256_canonical(content)
    return PreparedDomainRevision(
        {**content, "snapshot_digest": digest}, digest, fingerprint
    )


def _retain_inventory_baseline(semantic, baseline):
    """Keep exact immutable members/links and never clear an exported conflict."""
    if semantic["kind"] != "inventory" or not baseline:
        return semantic
    observations = {row["observation_ref"]: row for row in semantic["observations"]}
    for previous in baseline["observations"]:
        current = observations.get(previous["observation_ref"], previous)
        # A previously uncorrelated observation may acquire its first epoch link.
        retained = {**previous}
        if retained["epoch_ref"] is None:
            retained["epoch_ref"] = current["epoch_ref"]
        observations[previous["observation_ref"]] = retained
    epochs = {row["epoch_ref"]: row for row in semantic["epochs"]}
    for previous in baseline["epochs"]:
        current = epochs.get(previous["epoch_ref"], previous)
        epochs[previous["epoch_ref"]] = {
            **current,
            "conflict": current["conflict"] or previous["conflict"],
            "end_observation_refs": sorted(
                set(current["end_observation_refs"])
                | set(previous["end_observation_refs"])
            ),
        }
    semantic["observations"] = sorted(
        observations.values(), key=lambda row: row["observation_ref"]
    )
    semantic["epochs"] = sorted(epochs.values(), key=lambda row: row["epoch_ref"])
    semantic["counts"]["observations"] = len(observations)
    semantic["counts"]["epochs"] = len(epochs)
    if len(observations) > MAX_OBSERVATIONS:
        return _invalidation(semantic, "observation_limit_exceeded")
    if len(epochs) > MAX_EPOCHS:
        return _invalidation(semantic, "epoch_limit_exceeded")
    return semantic


def _encode_revision(facts, cursor):
    semantic = _semantic_snapshot(facts, cursor.pk)
    semantic = _retain_inventory_baseline(semantic, cursor.inventory_baseline)
    prepared = _revision_content(semantic, cursor)
    try:
        _wire_body(prepared.snapshot)
    except _PayloadOverflow:
        if semantic["kind"] == "invalidation":
            raise DomainSnapshotRefused(
                "Invalidation envelope exceeds budget"
            ) from None
        semantic = _invalidation(semantic, "payload_limit_exceeded")
        prepared = _revision_content(semantic, cursor)
        _wire_body(prepared.snapshot)
    return prepared


def prepare_native_capture_domain(room_binding_id):
    """Collect, encode and persist under one cursor lock and PostgreSQL snapshot.

    Encoding/signing is local: no network or grants in this transaction.
    A top-level transaction is required so isolation never depends on a caller.
    """
    if connection.vendor != "postgresql":
        raise DomainSnapshotRefused("PostgreSQL REPEATABLE READ is required")
    if connection.in_atomic_block or not connection.get_autocommit():
        raise DomainSnapshotRefused("A dedicated top-level transaction is required")
    for attempt in range(SNAPSHOT_ATTEMPTS):
        try:
            with transaction.atomic(durable=True):
                with connection.cursor() as sql:
                    sql.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
                cursor = _lock_domain(room_binding_id)
                facts = _collect_facts(room_binding_id, cursor.inventory_baseline)
                prepared = _encode_revision(facts, cursor)
                return _persist_revision(cursor, prepared)
        except _ConcurrentDomainCreation:
            if attempt == SNAPSHOT_ATTEMPTS - 1:
                raise DomainSnapshotRefused(
                    "Concurrent domain creation exhausted"
                ) from None
        except DatabaseError as error:
            if _sqlstate(error) not in {"40001", "40P01"}:
                raise
            if attempt == SNAPSHOT_ATTEMPTS - 1:
                raise
    raise AssertionError("Unreachable snapshot retry state")


@transaction.atomic
def claim_native_capture_domain(domain_ref):
    """Lease a persisted revision without holding a transaction during delivery."""
    now = timezone.now()
    cursor = (
        models.MastraoNativeCaptureDomain.objects.select_for_update(skip_locked=True)
        .filter(
            pk=domain_ref,
            producer_revision__gt=0,
            acknowledged_at__isnull=True,
            next_attempt_at__lte=now,
        )
        .filter(Q(claim_until__isnull=True) | Q(claim_until__lte=now))
        .first()
    )
    if cursor is None:
        return None
    cursor.claim_token = uuid4()
    cursor.claim_until = now + timedelta(seconds=CLAIM_SECONDS)
    cursor.save(update_fields=["claim_token", "claim_until", "updated_at"])
    return DomainDeliveryClaim(
        cursor.pk,
        cursor.producer_revision,
        cursor.snapshot_digest,
        cursor.claim_token,
        cursor.snapshot,
    )


def _claim_is_current(cursor, claim, now):
    return (
        cursor is not None
        and cursor.pk == claim.domain_ref
        and cursor.producer_revision == claim.producer_revision
        and cursor.snapshot_digest == claim.snapshot_digest
        and cursor.claim_token == claim.token
        and cursor.claim_until is not None
        and cursor.claim_until > now
        and cursor.acknowledged_at is None
    )


@transaction.atomic
def finish_native_capture_domain(claim, *, acknowledged):
    """An expired/replaced claim cannot acknowledge or delay a newer revision."""
    cursor = (
        models.MastraoNativeCaptureDomain.objects.select_for_update()
        .filter(pk=claim.domain_ref)
        .first()
    )
    # Waiting for the row lock consumes the lease. Check a fresh clock under lock.
    now = timezone.now()
    if not _claim_is_current(cursor, claim, now):
        return 0
    acknowledged_at = None
    if acknowledged:
        acknowledged_at = now
    return models.MastraoNativeCaptureDomain.objects.filter(
        pk=claim.domain_ref,
    ).update(
        acknowledged_at=acknowledged_at,
        claim_token=None,
        claim_until=None,
        next_attempt_at=now + timedelta(seconds=RETRY_SECONDS),
        updated_at=now,
    )


def _send(claim):
    # Keep the exact ACK schema together; integer versions/revisions reject bool.
    # pylint: disable=too-many-boolean-expressions,unidiomatic-typecheck
    if connection.in_atomic_block or not connection.get_autocommit():
        raise DomainSnapshotRefused("Domain network delivery requires autocommit")
    result = post_core_json(
        endpoint=settings.MASTRAO_CORE_NATIVE_CAPTURE_DOMAIN_ENDPOINT,
        expected_path=DOMAIN_PATH,
        body=_wire_body(claim.snapshot),
        timeout=min(settings.MASTRAO_CORE_RECORDING_TIMEOUT_SECONDS, 10),
        refusal=RecordingContractRefused,
        expected_fields=ACK_FIELDS,
    )
    current = result["current_revision"]
    if (
        type(result["version"]) is not int
        or result["version"] != 1
        or result["domain_ref"] != str(claim.domain_ref)
        or type(result["producer_revision"]) is not int
        or result["producer_revision"] != claim.producer_revision
        or result["snapshot_digest"] != claim.snapshot_digest
        or result["outcome"] not in ("recorded", "replayed", "superseded")
        or type(current) is not int
        or not claim.producer_revision <= current <= 9_007_199_254_740_991
    ):
        raise RecordingContractRefused(status=503)
    _text(result["current_snapshot_digest"], r"[a-f0-9]{64}")
    if current == claim.producer_revision and (
        result["current_snapshot_digest"] != claim.snapshot_digest
        or result["outcome"] == "superseded"
    ):
        raise RecordingContractRefused(status=503)


@transaction.atomic
def _persist_payload_invalidation(claim):
    """Configuration growth between preparation and send must invalidate durably."""
    cursor = (
        models.MastraoNativeCaptureDomain.objects.select_for_update()
        .filter(
            pk=claim.domain_ref,
        )
        .first()
    )
    if not _claim_is_current(cursor, claim, timezone.now()):
        return
    if cursor.snapshot["kind"] == "invalidation":
        raise DomainSnapshotRefused("Invalidation envelope exceeds budget")
    semantic = _invalidation(cursor.snapshot, "payload_limit_exceeded")
    prepared = _revision_content(semantic, cursor)
    _wire_body(prepared.snapshot)
    if not _claim_is_current(cursor, claim, timezone.now()):
        return
    _persist_revision(cursor, prepared)


def deliver_native_capture_domain(domain_ref):
    """Send the persisted revision; a size change can replace it once, atomically."""
    for _ in range(2):
        claim = claim_native_capture_domain(domain_ref)
        if claim is None:
            return 0
        try:
            _send(claim)
        except _PayloadOverflow:
            _persist_payload_invalidation(claim)
            continue
        except (
            RecordingContractRefused,
            DomainSnapshotRefused,
            DatabaseError,
            ValueError,
            TypeError,
        ):
            finish_native_capture_domain(claim, acknowledged=False)
            logger.warning("Native capture domain delivery pending retry")
            return 0
        return finish_native_capture_domain(claim, acknowledged=True)
    return 0


def reconcile_native_capture_domains(limit=MAX_BATCH):
    """Refresh known rooms fairly, then prioritize pending invalidations for delivery."""
    if not (
        settings.MASTRAO_MEETING_INTEGRATION_CONFIGURED
        and getattr(settings, "MASTRAO_CORE_NATIVE_CAPTURE_DOMAIN_ENDPOINT", "")
    ):
        return 0
    limit = min(MAX_BATCH, max(0, limit))
    if limit == 0:
        return 0
    now = timezone.now()
    bindings = list(
        models.MastraoRoomBinding.objects.filter(
            recording_binding__meeting_ref=F("meeting_ref"),
            recording_binding__room_ref=F("room_ref"),
            recording_binding__provider_binding_digest=F("provider_binding_digest"),
        )
        .exclude(recording_binding__organization_external_id="")
        .filter(
            Q(native_capture_domain__isnull=True)
            | Q(native_capture_domain__next_snapshot_at__lte=now)
        )
        .order_by(
            F("native_capture_domain__next_snapshot_at").asc(nulls_first=True), "pk"
        )
        .values_list("pk", flat=True)[:limit]
    )
    for binding_id in bindings:
        try:
            # Initial scheduling cursor survives failed preparation. No facts/revision
            # are persisted outside the dedicated REPEATABLE READ transaction.
            models.MastraoNativeCaptureDomain.objects.get_or_create(
                room_binding_id=binding_id
            )
            prepare_native_capture_domain(binding_id)
        except Exception:  # pylint: disable=broad-exception-caught
            logger.exception("Native capture domain snapshot pending retry")
        finally:
            models.MastraoNativeCaptureDomain.objects.filter(
                room_binding_id=binding_id
            ).update(next_snapshot_at=now + timedelta(seconds=RETRY_SECONDS))
    pending = list(
        models.MastraoNativeCaptureDomain.objects.filter(
            producer_revision__gt=0,
            acknowledged_at__isnull=True,
            next_attempt_at__lte=timezone.now(),
        )
        .filter(Q(claim_until__isnull=True) | Q(claim_until__lte=now))
        .order_by(
            Case(
                When(snapshot__kind="invalidation", then=0),
                default=1,
                output_field=IntegerField(),
            ),
            "next_attempt_at",
            "pk",
        )
        .values_list("pk", flat=True)[:limit]
    )
    delivered = 0
    for domain_ref in pending:
        try:
            delivered += deliver_native_capture_domain(domain_ref)
        except Exception:  # pylint: disable=broad-exception-caught
            logger.exception("Native capture domain delivery pending retry")
    return delivered
