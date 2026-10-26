"""Frozen Live observed-domain wire, using the existing recording signing trust.

A metadata acknowledgement never proves audio drain or meeting completeness.
"""

import json
import re
import time
import unicodedata
from uuid import UUID, uuid4

from django.conf import settings

from core.mastrao_core_http import validate_core_endpoint
from core.mastrao_recording_contract import RecordingContractRefused, _sign, _verify
from core.mastrao_room_contract import (
    RoomEffectRefused,
    _canonical_json,
    _sha256_canonical,
)

MAX_SAFE_INTEGER = 9_007_199_254_740_991
MAX_ORDINARY_REVISION = 1024
MAX_BYTES = 65_536
INVALIDATION_BYTES = 4096
CONTEXT_PATH = "/internal/v1/meetings/live/observed-domain/context"
DOMAIN_PATH = "/internal/v1/meetings/live/observed-domain"
CONTEXT_REQUEST_JOSE = "mastrao-live-observed-domain-context-request+jws"
CONTEXT_JOSE = "mastrao-live-observed-domain-context+jws"
PUBLICATION_JOSE = "mastrao-live-observed-domain+jws"
ACK_JOSE = "mastrao-live-observed-domain-ack+jws"
BINDING_FIELDS = {
    "organization_external_id",
    "meeting_ref",
    "room_ref",
    "provider_binding_digest",
    "close_ref",
    "effect_key",
    "arguments_digest",
    "close_receipt_claims_digest",
}
POLICY_FIELDS = {"policy_ref", "retention_policy_ref", "retention_expires_at"}
ENVELOPE_FIELDS = {
    "version",
    "issuer",
    "audience",
    "type",
    "operation",
    "operation_version",
    "issued_at",
    "expires_at",
    "jti",
}
INVALIDATIONS = {
    "observation_limit_exceeded",
    "epoch_limit_exceeded",
    "payload_limit_exceeded",
    "identity_conflict",
    "correlation_conflict",
    "revision_exhausted",
}
TERMINAL_REASONS = {"mode_not_live", "policy_absent", "retention_expired"}
OBSERVATION_FIELDS = {
    "observation_ref",
    "event_id",
    "payload_digest",
    "event_type",
    "observed_at",
    "room_sid",
    "participant_sid",
    "track_sid",
    "track_type",
    "track_source",
}
EPOCH_FIELDS = {
    "epoch_ref",
    "connection_ref",
    "room_sid",
    "participant_sid",
    "track_sid",
    "publication_observation_ref",
    "end_observation_refs",
    "ended",
    "conflict",
    "correlation",
    "media_token_binding_ref",
}
SNAPSHOT_FIELDS = {
    "version",
    "type",
    "scope",
    "binding",
    "policy",
    "domain_ref",
    "producer_revision",
    "kind",
}


class LiveDomainRefused(RecordingContractRefused):
    """Opaque protocol/configuration refusal; no terminal classification implied."""


class DomainPayloadOverflow(ValueError):
    """Replace a positive inventory with a bounded invalidation."""


def canonical_bytes(value, maximum=MAX_BYTES):
    """Match the shared canonicalizer, including entry/depth/UTF-16 limits."""
    entries = 0

    def visit(item, depth):
        nonlocal entries
        entries += 1
        if depth > 32 or entries > 4096:
            raise DomainPayloadOverflow()
        if item is None or type(item) is bool:
            return
        if type(item) is int and abs(item) <= MAX_SAFE_INTEGER:
            return
        if isinstance(item, str):
            if (
                len(item.encode("utf-16-le", errors="surrogatepass")) // 2 > 16_384
                or item != unicodedata.normalize("NFC", item)
                or any(0xD800 <= ord(c) <= 0xDFFF for c in item)
            ):
                raise LiveDomainRefused()
            return
        if isinstance(item, list):
            for child in item:
                visit(child, depth + 1)
            return
        if isinstance(item, dict):
            for key, child in item.items():
                if (
                    not isinstance(key, str)
                    or not 1 <= len(key) <= 128
                    or key != unicodedata.normalize("NFC", key)
                    or any(0xD800 <= ord(c) <= 0xDFFF for c in key)
                ):
                    raise LiveDomainRefused()
                visit(child, depth + 1)
            return
        raise LiveDomainRefused()

    visit(value, 0)
    data = _canonical_json(value)
    if len(data) > maximum:
        raise DomainPayloadOverflow()
    return data


def _require(condition):
    if not condition:
        raise LiveDomainRefused()


def _fields(value, fields):
    _require(isinstance(value, dict) and set(value) == fields)


def _text(value, pattern):
    _require(isinstance(value, str) and re.fullmatch(pattern, value) is not None)


def _reference(value):
    _text(value, r"[A-Za-z0-9_-]{16,160}")


def _uuid(value):
    _text(value, r"[0-9a-f-]{36}")
    try:
        _require(str(UUID(value)) == value)
    except (ValueError, TypeError, AttributeError) as error:
        raise LiveDomainRefused() from error


def _integer(value, minimum=1, maximum=MAX_SAFE_INTEGER):
    _require(type(value) is int and minimum <= value <= maximum)


def validate_binding(value):
    """Exact trusted close tuple; no authority is inferred from a token hint."""
    _fields(value, BINDING_FIELDS)
    for name, item in value.items():
        if name == "organization_external_id":
            _text(item, r"[A-Za-z0-9._:-]{1,200}")
        elif name.endswith("digest"):
            _text(item, r"[a-f0-9]{64}")
        else:
            _reference(item)


def validate_policy(value):
    """Validate fixed Core retention, without renewing it."""
    _fields(value, POLICY_FIELDS)
    _reference(value["policy_ref"])
    _reference(value["retention_policy_ref"])
    _integer(value["retention_expires_at"])


def _ordered(values):
    _require(isinstance(values, list))
    _require(all(a < b for a, b in zip(values, values[1:], strict=False)))


def _observation(row):
    _fields(row, OBSERVATION_FIELDS)
    _uuid(row["observation_ref"])
    _text(row["event_id"], r"[\x20-\x7e]{1,128}")
    _text(row["payload_digest"], r"[a-f0-9]{64}")
    _integer(row["observed_at"])
    _text(row["room_sid"], r"RM_[A-Za-z0-9_-]{1,120}")
    event = row["event_type"]
    if event == "room_finished":
        _require(row["participant_sid"] is None and row["track_sid"] is None)
    elif event in {"participant_joined", "participant_left"}:
        _text(row["participant_sid"], r"PA_[A-Za-z0-9_-]{1,120}")
        _require(row["track_sid"] is None)
    elif event in {"track_published", "track_unpublished"}:
        _text(row["participant_sid"], r"PA_[A-Za-z0-9_-]{1,120}")
        _text(row["track_sid"], r"TR_[A-Za-z0-9_-]{1,120}")
    else:
        raise LiveDomainRefused()
    for name in ("track_type", "track_source"):
        if row[name] is not None:
            _require(row["track_sid"] is not None)
            _integer(row[name], 0, 65_535)


def ends_epoch(observation, epoch):
    """A server terminal fact binds the physical room and applicable track."""
    if observation["room_sid"] != epoch["room_sid"]:
        return False
    event = observation["event_type"]
    if event == "room_finished":
        return True
    if observation["participant_sid"] != epoch["participant_sid"]:
        return False
    if event == "participant_left":
        return True
    return (
        event == "track_unpublished" and observation["track_sid"] == epoch["track_sid"]
    )


def _inventory(value):
    observations, epochs = value["observations"], value["epochs"]
    _require(isinstance(observations, list) and len(observations) <= 128)
    _require(isinstance(epochs, list) and len(epochs) <= 32)
    for row in observations:
        _observation(row)
    _ordered([row["observation_ref"] for row in observations])
    _require(len({row["event_id"] for row in observations}) == len(observations))
    lookup = {row["observation_ref"]: row for row in observations}
    identities, rooms = set(), {row["room_sid"] for row in observations}
    connections = {}
    for epoch in epochs:
        _fields(epoch, EPOCH_FIELDS)
        for name in ("epoch_ref", "connection_ref"):
            _uuid(epoch[name])
        for name, prefix in (
            ("room_sid", "RM"),
            ("participant_sid", "PA"),
            ("track_sid", "TR"),
        ):
            _text(epoch[name], rf"{prefix}_[A-Za-z0-9_-]{{1,120}}")
        identity = tuple(
            epoch[k]
            for k in ("connection_ref", "room_sid", "participant_sid", "track_sid")
        )
        _require(identity not in identities)
        identities.add(identity)
        connection_identity = (epoch["room_sid"], epoch["participant_sid"])
        previous = connections.setdefault(epoch["connection_ref"], connection_identity)
        _require(previous == connection_identity)
        rooms.add(epoch["room_sid"])
        _require(type(epoch["ended"]) is bool and epoch["conflict"] is False)
        _require(
            epoch["correlation"] in {"missing_join", "unmatched_issuance", "correlated"}
        )
        _require(
            (epoch["correlation"] == "correlated")
            == (epoch["media_token_binding_ref"] is not None)
        )
        if epoch["media_token_binding_ref"] is not None:
            _uuid(epoch["media_token_binding_ref"])
        publication = epoch["publication_observation_ref"]
        if publication is not None:
            _uuid(publication)
            row = lookup.get(publication)
            _require(row is not None and row["event_type"] == "track_published")
            _require(
                all(
                    row[k] == epoch[k]
                    for k in ("room_sid", "participant_sid", "track_sid")
                )
            )
        references = epoch["end_observation_refs"]
        _ordered(references)
        _require(len(references) <= 128 and (epoch["ended"] or not references))
        for reference in references:
            _uuid(reference)
            _require(reference in lookup and ends_epoch(lookup[reference], epoch))
    _ordered([row["epoch_ref"] for row in epochs])
    _require(len(rooms) <= 1)


def validate_snapshot(value):
    """Validate outgoing S against the frozen contract before persistence."""
    _require(isinstance(value, dict))
    kind = value.get("kind")
    if kind == "inventory":
        _fields(value, SNAPSHOT_FIELDS | {"observations", "epochs"})
        _integer(value["producer_revision"], 1, MAX_ORDINARY_REVISION)
        _inventory(value)
    elif kind == "invalidation":
        _fields(value, SNAPSHOT_FIELDS | {"reason"})
        _require(value["reason"] in INVALIDATIONS)
        _require(
            (value["producer_revision"] == MAX_SAFE_INTEGER)
            == (value["reason"] == "revision_exhausted")
        )
        _revision(value["producer_revision"])
    else:
        raise LiveDomainRefused()
    _require(type(value["version"]) is int and value["version"] == 1)
    _require(
        value["type"] == "mastrao.live-observed-domain"
        and value["scope"] == "observed_live_domain"
    )
    validate_binding(value["binding"])
    validate_policy(value["policy"])
    _uuid(value["domain_ref"])
    canonical_bytes(value, INVALIDATION_BYTES if kind == "invalidation" else MAX_BYTES)


def _revision(value):
    _integer(value)
    _require(value <= MAX_ORDINARY_REVISION or value == MAX_SAFE_INTEGER)


def envelope(assertion_type, operation, *, jti=None):
    """Fresh transport identity; semantic snapshot remains unchanged on retry."""
    now = int(time.time())
    return {
        "version": 1,
        "issuer": settings.MASTRAO_RECORDING_RECEIPT_ISSUER,
        "audience": settings.MASTRAO_RECORDING_RECEIPT_AUDIENCE,
        "type": assertion_type,
        "operation": operation,
        "operation_version": 1,
        "issued_at": now,
        "expires_at": now + 30,
        "jti": jti or uuid4().hex,
    }


def _wire(payload, jose, maximum=MAX_BYTES):
    canonical_bytes(payload, maximum)
    body = {"request": _sign(payload, jose)}
    if len(json.dumps(body).encode("utf-8")) > maximum:
        raise DomainPayloadOverflow()
    return body


def context_request(binding, jti):
    """Sign one exact B request with no recording reference."""
    validate_binding(binding)
    payload = {
        **envelope(
            "mastrao.meet-live-observed-domain-context-request",
            "resolve_live_observed_domain_context",
            jti=jti,
        ),
        "binding": binding,
    }
    return _wire(payload, CONTEXT_REQUEST_JOSE)


def publication_request(snapshot, jti):
    """Sign durable S; do not apply the old 16 KiB compact-string cap."""
    validate_snapshot(snapshot)
    payload = {
        **envelope(
            "mastrao.meet-live-observed-domain", "publish_live_observed_domain", jti=jti
        ),
        "snapshot": snapshot,
        "snapshot_digest": _sha256_canonical(snapshot),
    }
    maximum = INVALIDATION_BYTES if snapshot["kind"] == "invalidation" else MAX_BYTES
    return _wire(payload, PUBLICATION_JOSE, maximum)


def _response(compact, jose, fields, assertion_type, operation):
    # Context and ACK are small exact schemas. Reuse established signature crypto.
    try:
        payload = _verify(compact, jose, ENVELOPE_FIELDS | fields)
        canonical_bytes(payload)
        _require(payload["issuer"] == settings.MASTRAO_RECORDING_EFFECT_ISSUER)
        _require(payload["audience"] == settings.MASTRAO_RECORDING_EFFECT_AUDIENCE)
        _require(type(payload["version"]) is int and payload["version"] == 1)
        _require(
            type(payload["operation_version"]) is int
            and payload["operation_version"] == 1
        )
        _require(
            payload["type"] == assertion_type and payload["operation"] == operation
        )
        _reference(payload["jti"])
        _integer(payload["issued_at"])
        _integer(payload["expires_at"])
        now = int(time.time())
        _require(payload["issued_at"] <= now < payload["expires_at"])
        _require(1 <= payload["expires_at"] - payload["issued_at"] <= 30)
        validate_binding(payload["binding"])
        _reference(payload["request_jti"])
        return payload
    except (
        RecordingContractRefused,
        DomainPayloadOverflow,
        UnicodeError,
        ValueError,
        TypeError,
    ) as error:
        raise LiveDomainRefused(status=getattr(error, "status", 404)) from error


def verify_context(compact, binding, jti):
    """Verify signed terminal classification or fixed initial Live policy."""
    # Discriminated fields are identified only to select the exact signed schema.
    from core.mastrao_room_contract import _base64url_decode  # noqa: PLC0415

    try:
        _require(isinstance(compact, str) and len(compact) <= 16384)
        raw = json.loads(_base64url_decode(compact.split(".")[1]))
        fields = {"binding", "request_jti", "outcome", "policy"}
        if raw.get("outcome") == "not_applicable":
            fields.add("reason")
        value = _response(
            compact,
            CONTEXT_JOSE,
            fields,
            "mastrao.core-live-observed-domain-context",
            "confirm_live_observed_domain_context",
        )
        _require(value["binding"] == binding and value["request_jti"] == jti)
        if value["outcome"] == "eligible":
            validate_policy(value["policy"])
            _require(value["policy"]["retention_expires_at"] > int(time.time()))
        else:
            _require(
                value["outcome"] == "not_applicable"
                and value["reason"] in TERMINAL_REASONS
            )
            if value["reason"] == "retention_expired":
                validate_policy(value["policy"])
                _require(value["policy"]["retention_expires_at"] <= int(time.time()))
            else:
                _require(value["policy"] is None)
        return value
    except (
        IndexError,
        AttributeError,
        TypeError,
        ValueError,
        RoomEffectRefused,
    ) as error:
        raise LiveDomainRefused() from error


def verify_ack(compact, snapshot, jti):
    """ACK binds this attempt and exact requested/current immutable heads."""
    fields = {
        "request_jti",
        "binding",
        "policy",
        "domain_ref",
        "requested_revision",
        "requested_digest",
        "current_revision",
        "current_digest",
        "outcome",
    }
    value = _response(
        compact,
        ACK_JOSE,
        fields,
        "mastrao.core-live-observed-domain-ack",
        "acknowledge_live_observed_domain",
    )
    validate_policy(value["policy"])
    _uuid(value["domain_ref"])
    _require(value["request_jti"] == jti)
    _require(all(value[k] == snapshot[k] for k in ("binding", "policy", "domain_ref")))
    for name in ("requested_revision", "current_revision"):
        _revision(value[name])
    for name in ("requested_digest", "current_digest"):
        _text(value[name], r"[a-f0-9]{64}")
    _require(value["requested_revision"] == snapshot["producer_revision"])
    _require(value["requested_digest"] == _sha256_canonical(snapshot))
    if value["outcome"] == "superseded":
        _require(value["current_revision"] > value["requested_revision"])
    else:
        _require(value["outcome"] in {"recorded", "replayed"})
        _require(
            value["current_revision"] == value["requested_revision"]
            and value["current_digest"] == value["requested_digest"]
        )
    _require(value["policy"]["retention_expires_at"] > int(time.time()))
    return value


MASTRAO_LIVE_OBSERVED_DOMAIN_ENDPOINTS = (
    "MASTRAO_CORE_LIVE_OBSERVED_DOMAIN_CONTEXT_ENDPOINT",
    "MASTRAO_CORE_LIVE_OBSERVED_DOMAIN_ENDPOINT",
)
MASTRAO_LIVE_OBSERVED_DOMAIN_TRUST = (
    "MASTRAO_RECORDING_EFFECT_ISSUER",
    "MASTRAO_RECORDING_EFFECT_AUDIENCE",
    "MASTRAO_RECORDING_EFFECT_PUBLIC_JWK",
    "MASTRAO_RECORDING_EFFECT_KEY_ID",
    "MASTRAO_RECORDING_RECEIPT_ISSUER",
    "MASTRAO_RECORDING_RECEIPT_AUDIENCE",
    "MASTRAO_RECORDING_RECEIPT_PRIVATE_JWK",
    "MASTRAO_RECORDING_RECEIPT_KEY_ID",
)


def validate_transport_configuration(configuration):
    """Atomic opt-in, evaluated by transport after unconditional metadata purge.

    Existing recording trust alone does not enable this independent producer.
    Incomplete configuration refuses delivery without preventing scheduler boot.
    """
    endpoints = [
        getattr(configuration, name, "")
        for name in MASTRAO_LIVE_OBSERVED_DOMAIN_ENDPOINTS
    ]
    if not any(endpoints):
        return False
    names = (
        *MASTRAO_LIVE_OBSERVED_DOMAIN_ENDPOINTS,
        *MASTRAO_LIVE_OBSERVED_DOMAIN_TRUST,
    )
    if any(
        not isinstance(getattr(configuration, name, None), str)
        or not getattr(configuration, name).strip()
        for name in names
    ):
        raise LiveDomainRefused(status=503)
    # Validate actual endpoint, key and identifier values at the transport edge;
    # none of these failures should stop expiry cleanup from starting.
    try:
        validate_core_endpoint(endpoints[0], CONTEXT_PATH, LiveDomainRefused)
        validate_core_endpoint(endpoints[1], DOMAIN_PATH, LiveDomainRefused)
        for prefix in ("MASTRAO_RECORDING_EFFECT", "MASTRAO_RECORDING_RECEIPT"):
            for suffix in ("ISSUER", "AUDIENCE"):
                value = getattr(configuration, f"{prefix}_{suffix}")
                if not 1 <= len(value) <= 200:
                    raise LiveDomainRefused()
                canonical_bytes(value)
            if not re.fullmatch(
                r"[A-Za-z0-9._:-]{1,200}", getattr(configuration, f"{prefix}_KEY_ID")
            ):
                raise LiveDomainRefused()
    except (ValueError, LiveDomainRefused) as error:
        raise LiveDomainRefused(status=503) from error
    return True
