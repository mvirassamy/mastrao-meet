"""Canonical final-event bytes and exact durable receipts at the worker boundary."""

# JSON wire integers intentionally exclude booleans.
# pylint: disable=unidiomatic-typecheck,too-many-boolean-expressions

import hashlib
import json
import re

from core.mastrao_recording_contract import RecordingContractRefused
from core.mastrao_room_contract import OPAQUE_REFERENCE

DELIVERY_PATH = "/internal/v1/meetings/transcripts/live/append"
DELIVERY_JOSE = "mastrao-live-transcript-delivery+jws"
DELIVERY_TYPE = "mastrao.meet-live-transcript-delivery"
MAX_EVENT_BYTES = 32 * 1024
MAX_EVENTS = 100_000
MAX_DELIVERY_BODY_BYTES = MAX_EVENT_BYTES + 8192
EVENT_FIELDS = ("version", "source_ref", "leg_ref", "sequence", "kind")
KIND_FIELDS = {
    "final": ("item_ref", "revision", "text", "language", "timing"),
    "gap": ("reason",),
    "end": ("reason", "final_event_count"),
}
GAP_REASONS = {
    "connection_lost",
    "provider_failed",
    "delivery_failed",
    "timing_unavailable",
    "capture_incomplete",
}
END_REASONS = {
    "track_unpublished",
    "meeting_ended",
    "provider_failed",
    "capture_incomplete",
}
RECEIPT_FIELDS = {
    "version",
    "source_ref",
    "leg_ref",
    "sequence",
    "event_digest",
    "state",
    "confirmed_sequence",
    "final_event_count",
    "incomplete",
    "meeting_coverage",
}


def _integer(value, minimum, maximum):
    return type(value) is int and minimum <= value <= maximum


def _matches(pattern, value):
    return isinstance(value, str) and pattern.fullmatch(value) is not None


def _valid_final(event):
    text = event["text"]
    language = event["language"]
    if not (
        _matches(re.compile(r"[A-Za-z0-9_-]{1,160}"), event["item_ref"])
        and _integer(event["revision"], 1, 2**53 - 1)
        and isinstance(text, str)
        and text.strip()
        and (
            language is None
            or _matches(re.compile(r"[a-z]{2}(?:-[A-Z]{2})?"), language)
        )
        and event["timing"] == {"state": "unavailable"}
    ):
        return False
    # Zod's string bound counts UTF-16 code units, including surrogate pairs.
    return len(text.encode("utf-16-le")) // 2 <= 4000


def _valid_payload(event):
    kind = event["kind"]
    if kind == "final":
        return _valid_final(event)
    if kind == "gap":
        return isinstance(event["reason"], str) and event["reason"] in GAP_REASONS
    if kind == "end":
        return (
            isinstance(event["reason"], str)
            and event["reason"] in END_REASONS
            and _integer(event["final_event_count"], 0, MAX_EVENTS)
            and event["final_event_count"] < event["sequence"]
        )
    return False


def canonical_delivery_event(value, command):
    """Validate one bound event and serialize in the shared schema field order.

    The pinned SDK does not attest a meeting-relative origin. Active workers
    therefore cannot assert audio timing here. Never normalize provider text.
    """
    try:
        if not isinstance(value, dict):
            raise ValueError
        kind = value.get("kind")
        if not isinstance(kind, str) or kind not in KIND_FIELDS:
            raise ValueError
        fields = EVENT_FIELDS + KIND_FIELDS[kind]
        if (
            set(value) != set(fields)
            or not _integer(value["version"], 2, 2)
            or not _integer(value["sequence"], 1, MAX_EVENTS)
            or any(
                not _matches(OPAQUE_REFERENCE, value[name])
                or value[name] != command[name]
                for name in ("source_ref", "leg_ref")
            )
            or not _valid_payload(value)
        ):
            raise ValueError
        event = {name: value[name] for name in fields}
        raw = json.dumps(event, ensure_ascii=False, separators=(",", ":")).encode()
        if len(raw) > MAX_EVENT_BYTES:
            raise ValueError
        return event, hashlib.sha256(raw).hexdigest()
    except (ValueError, TypeError, UnicodeError, KeyError) as error:
        raise RecordingContractRefused(status=400) from error


def validate_delivery_receipt(result, event, digest):
    """A receipt acknowledges only the exact ordered event, never audio authority."""
    if not isinstance(result, dict) or set(result) != RECEIPT_FIELDS:
        raise RecordingContractRefused(status=503)
    final_limit = event["sequence"]
    if event["kind"] != "final":
        final_limit -= 1
    if (
        not _integer(result["version"], 2, 2)
        or any(result[name] != event[name] for name in ("source_ref", "leg_ref"))
        or not _integer(result["sequence"], event["sequence"], event["sequence"])
        or result["event_digest"] != digest
        or result["state"] != ("ended" if event["kind"] == "end" else "collecting")
        or not _integer(
            result["confirmed_sequence"], event["sequence"], event["sequence"]
        )
        or not _integer(
            result["final_event_count"],
            1 if event["kind"] == "final" else 0,
            final_limit,
        )
        or type(result["incomplete"]) is not bool
        or result["meeting_coverage"] != "unverified"
        or (
            event["kind"] == "end"
            and result["final_event_count"] != event["final_event_count"]
        )
        or (event["kind"] in {"final", "gap"} and not result["incomplete"])
        or (
            event["kind"] == "end"
            and (
                event["sequence"] > 1
                or event["reason"] in {"provider_failed", "capture_incomplete"}
            )
            and not result["incomplete"]
        )
    ):
        raise RecordingContractRefused(status=503)
    return result
