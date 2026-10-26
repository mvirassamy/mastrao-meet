"""Strict worker wire boundary; Core remains the owner of send authority."""

# Wire integers deliberately exclude bool, and guards keep the whole schema visible.
# pylint: disable=unidiomatic-typecheck,too-many-boolean-expressions

import re
import time

from core.mastrao_live_profile import validate_live_profile
from core.mastrao_recording_contract import RecordingContractRefused
from core.mastrao_room_contract import OPAQUE_REFERENCE

CONTROL_PATH = "/internal/v1/meetings/transcripts/live/control"
PREPARE_PATH = "/internal/v1/meetings/transcripts/live/prepare"
SEND_PATH = "/internal/v1/meetings/transcripts/live/send"
PREPARE_JOSE = "mastrao-live-track-prepare+jws"
SEND_JOSE = "mastrao-live-transcript-send+jws"
PREPARE_TYPE = "mastrao.meet-live-track-prepare"
SEND_TYPE = "mastrao.meet-live-transcript-send"
CONTROL_VERSION = 2
PREPARE_VERSION = 2
ADMISSION_VERSION = 3
MAX_SAFE_INTEGER = 2**53 - 1
REQUEST_FIELDS = {
    "version",
    "action",
    "room_sid",
    "participant_sid",
    "track_sid",
    "attempt_ref",
    "worker_instance_ref",
}
OWNER_FIELDS = {
    "source_ref",
    "admission_ref",
    "attempt_ref",
    "worker_instance_ref",
    "leg_ref",
}
SEND_FIELDS = OWNER_FIELDS | {
    "version",
    "action",
    "state",
    "lease_expires_at",
    "start_authorized",
    "send_authorized",
    "replayed",
    "audio_start_sample",
}
ADMIT_FIELDS = {
    "version",
    "live_profile",
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
        "rotate",
        "deliver",
    }:
        raise RecordingContractRefused(status=400)
    fields = REQUEST_FIELDS
    if action != "prepare":
        fields = fields | {"source_ref", "admission_ref", "leg_ref"}
    if action == "rotate":
        fields = fields | {"next_leg_ref", "audio_end_sample"}
    if action == "deliver":
        fields = fields | {"event"}
    if (
        set(body) != fields
        or type(body["version"]) is not int
        or body["version"] != CONTROL_VERSION
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
    if action == "rotate" and (
        not matches(OPAQUE_REFERENCE, body["next_leg_ref"])
        or body["next_leg_ref"] == body["leg_ref"]
        or not _sample_position(body["audio_end_sample"])
    ):
        raise RecordingContractRefused(status=400)
    return body


def _sample_position(value):
    return type(value) is int and 0 <= value <= MAX_SAFE_INTEGER


def validate_admission(result):
    """An admission journals a source; it never authorizes opening a provider."""
    if not isinstance(result, dict) or set(result) != ADMIT_FIELDS:
        raise RecordingContractRefused(status=503)
    if (
        type(result["version"]) is not int
        or result["version"] != ADMISSION_VERSION
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
    validate_live_profile(result["live_profile"])
    return result


def validate_send(result, command):
    """Reject wrong owners, permissive flags, and unusable or excessive leases."""
    if not isinstance(result, dict) or set(result) != SEND_FIELDS:
        raise RecordingContractRefused(status=503)
    if (
        type(result["version"]) is not int
        or result["version"] != CONTROL_VERSION
        or any(
            result[name] != command[name]
            for name in (OWNER_FIELDS - {"leg_ref"}) | {"action"}
        )
        or not _sample_position(result["audio_start_sample"])
        or any(
            type(result[name]) is not bool
            for name in ("start_authorized", "send_authorized", "replayed")
        )
    ):
        raise RecordingContractRefused(status=503)
    expected_leg = command["leg_ref"]
    if command["action"] == "rotate":
        expected_leg = command["next_leg_ref"]
        if result["audio_start_sample"] != command["audio_end_sample"]:
            raise RecordingContractRefused(status=503)
    elif command["action"] == "claim" and result["audio_start_sample"] != 0:
        raise RecordingContractRefused(status=503)
    if result["leg_ref"] != expected_leg:
        raise RecordingContractRefused(status=503)
    if result["state"] == "active":
        lease = result["lease_expires_at"]
        valid = (
            command["action"] in {"claim", "renew", "rotate"}
            and result["send_authorized"] is True
            and type(lease) is int
            and time.time() < lease <= int(time.time()) + 15
            and (
                result["start_authorized"] is False
                or (
                    command["action"] in {"claim", "rotate"}
                    and result["replayed"] is False
                )
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
