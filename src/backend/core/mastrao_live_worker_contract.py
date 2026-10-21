"""Strict worker wire boundary; Core remains the owner of send authority."""

# Wire integers deliberately exclude bool, and guards keep the whole schema visible.
# pylint: disable=unidiomatic-typecheck,too-many-boolean-expressions

import re
import time

from core.mastrao_recording_contract import RecordingContractRefused
from core.mastrao_room_contract import OPAQUE_REFERENCE

CONTROL_PATH = "/internal/v1/meetings/transcripts/live/control"
PREPARE_PATH = "/internal/v1/meetings/transcripts/live/prepare"
SEND_PATH = "/internal/v1/meetings/transcripts/live/send"
PREPARE_JOSE = "mastrao-live-track-prepare+jws"
SEND_JOSE = "mastrao-live-transcript-send+jws"
PREPARE_TYPE = "mastrao.meet-live-track-prepare"
SEND_TYPE = "mastrao.meet-live-transcript-send"
REQUEST_FIELDS = {
    "version",
    "action",
    "room_sid",
    "participant_sid",
    "track_sid",
    "attempt_ref",
    "worker_instance_ref",
}
OWNER_FIELDS = {"source_ref", "admission_ref", "attempt_ref", "worker_instance_ref"}
SEND_FIELDS = OWNER_FIELDS | {
    "version",
    "action",
    "state",
    "lease_expires_at",
    "start_authorized",
    "send_authorized",
    "replayed",
}
ADMIT_FIELDS = {
    "version",
    "source_ref",
    "state",
    "confirmed_sequence",
    "final_event_count",
    "incomplete",
    "meeting_coverage",
}


def matches(pattern, value):
    """Reject coercion or trimmed identities at the wire boundary."""
    return isinstance(value, str) and pattern.fullmatch(value) is not None


def worker_command(body):
    """Accept exactly one command, never participant authority or credentials."""
    if not isinstance(body, dict):
        raise RecordingContractRefused(status=400)
    action = body.get("action")
    if not isinstance(action, str) or action not in {
        "prepare",
        "claim",
        "renew",
        "stopped",
    }:
        raise RecordingContractRefused(status=400)
    fields = REQUEST_FIELDS
    if action != "prepare":
        fields = fields | {"source_ref", "admission_ref"}
    if (
        set(body) != fields
        or type(body["version"]) is not int
        or body["version"] != 1
        or any(
            not matches(OPAQUE_REFERENCE, body[name]) for name in fields & OWNER_FIELDS
        )
        or any(
            not matches(re.compile(rf"{prefix}_[A-Za-z0-9_-]{{1,96}}"), body[name])
            for name, prefix in (
                ("room_sid", "RM"),
                ("participant_sid", "PA"),
                ("track_sid", "TR"),
            )
        )
    ):
        raise RecordingContractRefused(status=400)
    return body


def validate_admission(result):
    """An admission journals a source; it never authorizes opening a provider."""
    if not isinstance(result, dict) or set(result) != ADMIT_FIELDS:
        raise RecordingContractRefused(status=503)
    if (
        type(result["version"]) is not int
        or result["version"] != 1
        or not matches(OPAQUE_REFERENCE, result["source_ref"])
        or result["state"] not in ("collecting", "ended")
        or type(result["incomplete"]) is not bool
        or result["meeting_coverage"] != "unverified"
        or any(
            type(result[name]) is not int or not 0 <= result[name] <= 100_000
            for name in ("confirmed_sequence", "final_event_count")
        )
        or result["final_event_count"] > result["confirmed_sequence"]
    ):
        raise RecordingContractRefused(status=503)
    return result


def validate_send(result, command):
    """Reject wrong owners, permissive flags, and unusable or excessive leases."""
    if not isinstance(result, dict) or set(result) != SEND_FIELDS:
        raise RecordingContractRefused(status=503)
    if (
        type(result["version"]) is not int
        or result["version"] != 1
        or any(result[name] != command[name] for name in OWNER_FIELDS | {"action"})
        or any(
            type(result[name]) is not bool
            for name in ("start_authorized", "send_authorized", "replayed")
        )
    ):
        raise RecordingContractRefused(status=503)
    if result["state"] == "active":
        lease = result["lease_expires_at"]
        valid = (
            command["action"] in {"claim", "renew"}
            and result["send_authorized"] is True
            and type(lease) is int
            and time.time() < lease <= int(time.time()) + 15
            and (
                result["start_authorized"] is False
                or (command["action"] == "claim" and result["replayed"] is False)
            )
        )
    else:
        valid = (
            result["state"] in ("stop_requested", "stopped")
            and (command["action"] != "stopped" or result["state"] == "stopped")
            and result["lease_expires_at"] is None
            and result["start_authorized"] is False
            and result["send_authorized"] is False
        )
    if not valid:
        raise RecordingContractRefused(status=503)
    return result
