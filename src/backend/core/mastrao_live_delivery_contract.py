"""Canonical live-event bytes and exact durable receipts at the worker boundary."""

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
    "model_evidence": ("provider", "models", "truncated"),
    "final": ("item_ref", "revision", "text", "language", "timing"),
    "gap": ("reason",),
    "end": ("reason", "final_event_count"),
}
MODEL_FIELDS = ("origin", "model_ref", "name", "version", "architecture")
# Core validates nonblank using ECMAScript trim, not Python's whitespace set.
MODEL_WHITESPACE = (
    " \t\n\r\v\f\u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006"
    "\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000\ufeff"
)
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


def _canonical_timing(value):
    """Keep committed buffer bounds in their source PCM clock, never meeting time."""
    if not isinstance(value, dict):
        raise ValueError
    if value == {"state": "unavailable"}:
        return {"state": "unavailable"}
    fields = ("state", "sample_rate", "start_sample", "end_sample")
    if (
        set(value) != set(fields)
        or value["state"] != "source_buffer"
        or not _integer(value["sample_rate"], 24000, 24000)
        or not _integer(value["start_sample"], 0, 2**53 - 1)
        or not _integer(value["end_sample"], 1, 2**53 - 1)
        or value["end_sample"] <= value["start_sample"]
        or value["end_sample"] - value["start_sample"] > 540 * 24000
    ):
        raise ValueError
    return {name: value[name] for name in fields}


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
    ):
        return False
    # Zod's string bound counts UTF-16 code units, including surrogate pairs.
    return len(text.encode("utf-16-le")) // 2 <= 4000


def _valid_payload(event):
    kind = event["kind"]
    if kind == "model_evidence":
        return _valid_model_evidence(event)
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


def _valid_model_string(value):
    return (
        isinstance(value, str)
        and bool(value.strip(MODEL_WHITESPACE))
        and len(value.encode("utf-16-le")) // 2 <= 160
    )


def _valid_model_evidence(event):
    models = event["models"]
    if (
        not isinstance(event["provider"], str)
        or event["provider"] not in {"openai", "mistral", "deepgram"}
        or type(event["truncated"]) is not bool
        or not isinstance(models, list)
        or len(models) > 8
    ):
        return False
    previous = []
    for model in models:
        if (
            not isinstance(model, dict)
            or set(model) != set(MODEL_FIELDS)
            or not isinstance(model["origin"], str)
            or model["origin"] not in {"session", "result", "terminal"}
            or not any(model[name] is not None for name in MODEL_FIELDS[1:])
            or any(
                model[name] is not None and not _valid_model_string(model[name])
                for name in MODEL_FIELDS[1:]
            )
            or model in previous
        ):
            return False
        previous.append(model)
    return True


def canonical_delivery_event(value, command):
    """Validate one bound event and serialize in the shared schema field order.

    Buffer bounds describe the input committed on one physical provider leg.
    They do not attest speech timing or a meeting origin. Never normalize text.
    """
    try:
        if not isinstance(value, dict):
            raise ValueError
        kind = value.get("kind")
        if not isinstance(kind, str) or kind not in KIND_FIELDS:
            raise ValueError
        fields = EVENT_FIELDS + KIND_FIELDS[kind]
        version = 3 if kind == "model_evidence" else 2
        if (
            set(value) != set(fields)
            or not _integer(value["version"], version, version)
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
        if kind == "final":
            event["timing"] = _canonical_timing(event["timing"])
        elif kind == "model_evidence":
            event["models"] = [
                {name: model[name] for name in MODEL_FIELDS}
                for model in event["models"]
            ]
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
                event["final_event_count"] > 0
                or event["reason"] in {"provider_failed", "capture_incomplete"}
            )
            and not result["incomplete"]
        )
    ):
        raise RecordingContractRefused(status=503)
    return result
