"""Canonical v2 delivery bytes and exact durable receipt validation."""

from __future__ import annotations

import hashlib
import json
import re

from live_send_control import (
    MAX_SAFE_SAMPLE,
    PCM_SAMPLE_RATE,
    ROTATION_SECONDS,
    ControlError,
)

MAX_EVENTS_PER_SOURCE = 100_000
MAX_EVENT_BYTES = 32 * 1024
MAX_TEXT_UNITS = 4000
DELIVERY_VERSION = 2
MAX_LEG_SAMPLES = PCM_SAMPLE_RATE * ROTATION_SECONDS
_REFERENCE = re.compile(r"^[A-Za-z0-9_-]{16,160}$")
_ITEM = re.compile(r"^[A-Za-z0-9_-]{1,160}$")
_LANGUAGE = re.compile(r"^[a-z]{2}(?:-[A-Z]{2})?$")
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


def validated_language(value: object) -> str | None:
    """Keep only a language the contract accepts; never coerce SDK objects."""
    if isinstance(value, str) and _LANGUAGE.fullmatch(value):
        return value
    return None


def _integer(value: object, minimum: int, maximum: int) -> bool:
    return type(value) is int and minimum <= value <= maximum


def _matches(pattern: re.Pattern[str], value: object) -> bool:
    return isinstance(value, str) and pattern.fullmatch(value) is not None


def canonical_timing(value: object) -> dict[str, object]:
    """Order enclosing source PCM evidence without certifying a meeting clock."""
    if value == {"state": "unavailable"}:
        return {"state": "unavailable"}
    fields = ("state", "sample_rate", "start_sample", "end_sample")
    if (
        not isinstance(value, dict)
        or set(value) != set(fields)
        or value["state"] != "source_buffer"
        or type(value["sample_rate"]) is not int
        or value["sample_rate"] != PCM_SAMPLE_RATE
        or not _integer(value["start_sample"], 0, MAX_SAFE_SAMPLE)
        or not _integer(value["end_sample"], 0, MAX_SAFE_SAMPLE)
        or not 0 < value["end_sample"] - value["start_sample"] <= MAX_LEG_SAMPLES
    ):
        raise ControlError("invalid delivery audio timing")
    return {key: value[key] for key in fields}


def _fields(kind: object) -> tuple[str, ...]:
    if kind == "final":
        return ("kind", "item_ref", "revision", "text", "language", "timing")
    if kind == "gap":
        return ("kind", "reason")
    if kind == "end":
        return ("kind", "reason", "final_event_count")
    raise ControlError("invalid delivery event kind")


def event_bytes(event: dict[str, object]) -> bytes:
    """Validate and order the schema without trimming or normalizing text."""
    fields = (
        "version",
        "source_ref",
        "leg_ref",
        "sequence",
        *_fields(event.get("kind")),
    )
    if (
        set(event) != set(fields)
        or type(event["version"]) is not int
        or event["version"] != DELIVERY_VERSION
        or not _matches(_REFERENCE, event["source_ref"])
        or not _matches(_REFERENCE, event["leg_ref"])
        or not _integer(event["sequence"], 1, MAX_EVENTS_PER_SOURCE)
    ):
        raise ControlError("invalid delivery event binding")
    _validate_content(event)
    ordered = {key: event[key] for key in fields}
    if event["kind"] == "final":
        ordered["timing"] = canonical_timing(event["timing"])
    try:
        encoded = json.dumps(
            ordered,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (UnicodeError, ValueError, TypeError) as error:
        raise ControlError("invalid delivery event encoding") from error
    if len(encoded) > MAX_EVENT_BYTES:
        raise ControlError("delivery event byte bound exceeded")
    return encoded


def _validate_content(event: dict[str, object]) -> None:
    kind = event["kind"]
    if kind == "final":
        text = event["text"]
        if (
            not _matches(_ITEM, event["item_ref"])
            or not _integer(event["revision"], 1, MAX_SAFE_SAMPLE)
            or not isinstance(text, str)
            or not text.strip()
            or len(text.encode("utf-16-le", errors="surrogatepass")) // 2
            > MAX_TEXT_UNITS
            or (
                event["language"] is not None
                and not _matches(_LANGUAGE, event["language"])
            )
        ):
            raise ControlError("invalid delivery final")
        return
    if kind == "gap":
        if not isinstance(event["reason"], str) or event["reason"] not in GAP_REASONS:
            raise ControlError("invalid delivery gap")
        return
    if (
        not isinstance(event["reason"], str)
        or event["reason"] not in END_REASONS
        or not _integer(event["final_event_count"], 0, MAX_EVENTS_PER_SOURCE)
        or event["final_event_count"] >= event["sequence"]
    ):
        raise ControlError("invalid delivery end")


def event_digest(event: dict[str, object]) -> str:
    """Hash exact compact UTF-8 schema bytes, including connection identity."""
    return hashlib.sha256(event_bytes(event)).hexdigest()


def validate_receipt(
    receipt: dict[str, object], event: dict[str, object], final_count: int
) -> None:
    """Accept only an exact confirmed Core result; no receipt renews audio."""
    expected_state = "ended" if event["kind"] == "end" else "collecting"
    if (
        not isinstance(receipt, dict)
        or set(receipt) != RECEIPT_FIELDS
        or type(receipt["version"]) is not int
        or receipt["version"] != DELIVERY_VERSION
        or receipt["source_ref"] != event["source_ref"]
        or receipt["leg_ref"] != event["leg_ref"]
        or type(receipt["sequence"]) is not int
        or receipt["sequence"] != event["sequence"]
        or receipt["event_digest"] != event_digest(event)
        or receipt["state"] != expected_state
        or type(receipt["confirmed_sequence"]) is not int
        or receipt["confirmed_sequence"] != event["sequence"]
        or type(receipt["final_event_count"]) is not int
        or receipt["final_event_count"] != final_count
        or receipt["incomplete"] is not True
        or receipt["meeting_coverage"] != "unverified"
    ):
        raise ControlError("invalid durable delivery receipt")
