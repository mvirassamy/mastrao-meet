"""Session-bound recording consent projection for canonical Mastrao rooms."""

# Exact host/guest bindings are intentionally expressed as explicit predicates.
# pylint: disable=too-many-boolean-expressions

import hashlib
import time
from datetime import UTC, datetime
from uuid import uuid4

from django.conf import settings
from django.db import transaction

from core import models
from core.mastrao_core_http import post_core_json
from core.mastrao_guest_contract import verify_guest_bootstrap
from core.mastrao_guest_grant import (
    CANONICAL_ROOM_SLUG,
    active_guest_compact_grant,
    active_guest_grant,
)
from core.mastrao_host_contract import verify_host_grant
from core.mastrao_host_grant import active_host_compact_grant, active_host_grant
from core.mastrao_recording_contract import (
    ACTIVATION_TYPE,
    DECISION_TYPE,
    PURPOSE,
    SCOPE,
    STOP_REQUEST_TYPE,
    RecordingContractRefused,
    compact_digest,
    sign_activation_assertion,
    sign_decision_assertion,
    sign_stop_request_assertion,
)
from core.mastrao_room_contract import DIGEST, OPAQUE_REFERENCE
from core.mastrao_transcription_contract import (
    DECISION_TYPE as TRANSCRIPTION_DECISION_TYPE,
)
from core.mastrao_transcription_contract import (
    PURPOSE as TRANSCRIPTION_PURPOSE,
)
from core.mastrao_transcription_contract import (
    SCOPE as TRANSCRIPTION_SCOPE,
)
from core.mastrao_transcription_contract import (
    sign_transcription_decision_assertion,
)

CAPTURE_STATES = {"collecting", "authorized", "starting", "active"}
NO_CAPTURE_STATES = {"cancelled", "failed", "processing", "available"}
SESSION_STATUS_FIELDS = {
    "version",
    "organization_external_id",
    "meeting_ref",
    "room_ref",
    "mode",
}
RECORDED_STATUS_FIELDS = SESSION_STATUS_FIELDS | {
    "recording_ref",
    "policy_ref",
    "notice_version",
    "notice_digest",
    "purpose",
    "scope",
    "retention_expires_at",
    "recording_state",
    "decision",
    "transcription_mode",
}
TRANSCRIBED_STATUS_FIELDS = RECORDED_STATUS_FIELDS | {
    "transcription_notice_version",
    "transcription_notice_digest",
    "transcription_decision",
}


def _identifier(prefix):
    return f"{prefix}_{uuid4().hex}"


def _participant(request, room):
    host = active_host_grant(request, room)
    if host:
        compact = active_host_compact_grant(request, host)
        if not compact or compact_digest(compact) != host.grant_digest:
            raise RecordingContractRefused()
        claims = verify_host_grant(compact)
        return {
            "kind": "host",
            "ref": host.identity.host_ref,
            "session_digest": host.session_nonce_digest,
            "compact": compact,
            "claims": claims,
        }
    guest = active_guest_grant(request, room)
    if guest:
        compact = active_guest_compact_grant(request, guest)
        if not compact or compact_digest(compact) != guest.grant_digest:
            raise RecordingContractRefused()
        claims = verify_guest_bootstrap(compact)
        return {
            "kind": "guest",
            "ref": guest.guest_ref,
            "session_digest": guest.session_nonce_digest,
            "compact": compact,
            "claims": claims,
        }
    raise RecordingContractRefused()


def _validate_status(status, participant, room):
    if not isinstance(status, dict):
        raise RecordingContractRefused(status=503)
    if status.get("mode") == "recorded" and "transcription_mode" not in status:
        # An older Core release does not project the transcription policy
        # yet; default to disabled so recording consent keeps working
        # during a staggered Core/Meet deploy.
        status["transcription_mode"] = "disabled"
    fields = set(status)
    if status.get("mode") != "recorded":
        expected = SESSION_STATUS_FIELDS
    elif status.get("transcription_mode") == "transcribed":
        expected = TRANSCRIBED_STATUS_FIELDS
    else:
        expected = RECORDED_STATUS_FIELDS
    claims = participant["claims"]
    if (
        fields != expected
        or status.get("version") != 1
        or status.get("mode") not in {"unset", "disabled", "recorded"}
        or status.get("organization_external_id") != claims["organization_external_id"]
        or status.get("meeting_ref") != claims["meeting_ref"]
        or status.get("room_ref") != claims["room_ref"]
        or claims["room_ref"] != room.mastrao_binding.room_ref
    ):
        raise RecordingContractRefused(status=503)
    if status["mode"] == "recorded" and (
        status["purpose"] != PURPOSE
        or status["scope"] != SCOPE
        or status["notice_version"] != settings.MASTRAO_RECORDING_NOTICE_VERSION
        or status["notice_digest"] != settings.MASTRAO_RECORDING_NOTICE_DIGEST
        or status["decision"] not in {"absent", "accepted", "refused", "withdrawn"}
        or status["transcription_mode"] not in {"disabled", "transcribed"}
        or status["recording_state"]
        not in CAPTURE_STATES | NO_CAPTURE_STATES | {"stopping"}
        or any(
            not isinstance(status[name], str)
            or not OPAQUE_REFERENCE.fullmatch(status[name])
            for name in (
                "recording_ref",
                "policy_ref",
                "notice_version",
            )
        )
        or not DIGEST.fullmatch(status["notice_digest"])
        or not isinstance(status["retention_expires_at"], int)
        or isinstance(status["retention_expires_at"], bool)
        or status["retention_expires_at"] <= int(time.time())
    ):
        raise RecordingContractRefused(status=503)
    if (
        status["mode"] == "recorded"
        and status["transcription_mode"] == "transcribed"
        and (
            not isinstance(status["transcription_notice_version"], str)
            or not OPAQUE_REFERENCE.fullmatch(status["transcription_notice_version"])
            or not DIGEST.fullmatch(status.get("transcription_notice_digest") or "")
            or status["transcription_decision"]
            not in {"absent", "accepted", "refused", "withdrawn"}
        )
    ):
        raise RecordingContractRefused(status=503)
    return status


def _sync_binding(room, status):
    if status["mode"] != "recorded":
        return None
    local_state = {
        "collecting": models.MastraoRecordingBinding.State.PREPARED,
        "authorized": models.MastraoRecordingBinding.State.PREPARED,
        "starting": models.MastraoRecordingBinding.State.STARTING,
        "active": models.MastraoRecordingBinding.State.ACTIVE,
        "stopping": models.MastraoRecordingBinding.State.STOPPING,
        "processing": models.MastraoRecordingBinding.State.PROCESSING,
        "available": models.MastraoRecordingBinding.State.FINALIZED,
        "cancelled": models.MastraoRecordingBinding.State.CANCELLED,
        "failed": models.MastraoRecordingBinding.State.FAILED,
    }[status["recording_state"]]
    defaults = {
        "organization_external_id": status["organization_external_id"],
        "meeting_ref": status["meeting_ref"],
        "room_ref": status["room_ref"],
        "provider_binding_digest": room.mastrao_binding.provider_binding_digest,
        "policy_ref": status["policy_ref"],
        "notice_version": status["notice_version"],
        "notice_digest": status["notice_digest"],
        "purpose": status["purpose"],
        "scope": status["scope"],
        "retention_expires_at": datetime.fromtimestamp(
            status["retention_expires_at"], tz=UTC
        ),
        "state": local_state,
    }
    binding = models.MastraoRecordingBinding.objects.filter(
        room_binding=room.mastrao_binding
    ).first()
    if binding and binding.recording_ref != status["recording_ref"]:
        raise RecordingContractRefused(status=409)
    if binding:
        changed_fields = []
        for field, value in defaults.items():
            if getattr(binding, field) != value:
                setattr(binding, field, value)
                changed_fields.append(field)
        if changed_fields:
            binding.save(update_fields=[*changed_fields, "updated_at"])
    else:
        binding = models.MastraoRecordingBinding.objects.create(
            room_binding=room.mastrao_binding,
            recording_ref=status["recording_ref"],
            **defaults,
        )
    return binding


def recording_session_status(request, room):
    """Fetch the authoritative Core projection for an exact browser grant."""

    # Native Meet rooms must keep the legacy, zero-I/O path when the feature is
    # disabled. Canonical room slugs are reserved for Mastrao room bindings, so
    # only those rooms need the durable-binding lookup required by rollback.
    if (
        not settings.MASTRAO_MEETING_RECORDING_ENABLED
        and not CANONICAL_ROOM_SLUG.fullmatch(room.slug or "")
    ):
        return None
    if not hasattr(room, "mastrao_binding"):
        return None
    if not settings.MASTRAO_MEETING_RECORDING_ENABLED and not (
        models.MastraoRecordingBinding.objects.filter(
            room_binding=room.mastrao_binding
        ).exists()
    ):
        return None
    participant = _participant(request, room)
    status = post_core_json(
        endpoint=settings.MASTRAO_CORE_RECORDING_SESSION_STATUS_ENDPOINT,
        expected_path="/internal/v1/meetings/recording/session-status",
        body={
            "participant_grant": participant["compact"],
            "participant_session_digest": participant["session_digest"],
        },
        timeout=settings.MASTRAO_CORE_RECORDING_TIMEOUT_SECONDS,
        refusal=RecordingContractRefused,
    )
    _validate_status(status, participant, room)
    _sync_binding(room, status)
    return {**status, "participant_kind": participant["kind"]}


def media_allowed(status):
    """Return whether Core permits minting a new LiveKit token."""

    if status is None or status["mode"] == "disabled":
        return True
    if status["mode"] == "unset" or status["recording_state"] == "stopping":
        return False
    if status["recording_state"] in CAPTURE_STATES:
        return status["decision"] == "accepted"
    return status["recording_state"] in NO_CAPTURE_STATES


def public_projection(status):
    """Expose only notice and state data, never compact capabilities."""

    if status is None:
        return None
    if status["mode"] != "recorded":
        return {"mode": status["mode"]}
    projection = {
        name: status[name]
        for name in (
            "mode",
            "recording_ref",
            "notice_version",
            "notice_digest",
            "purpose",
            "scope",
            "retention_expires_at",
            "recording_state",
            "decision",
            "participant_kind",
        )
    }
    projection["transcription_mode"] = status.get("transcription_mode", "disabled")
    if projection["transcription_mode"] == "transcribed":
        projection.update(
            {
                name: status[name]
                for name in (
                    "transcription_notice_version",
                    "transcription_notice_digest",
                    "transcription_decision",
                )
            }
        )
    return projection


def _validate_core_status(result, session_status):
    base = {"version", "matter_ref", "meeting_ref", "room_ref", "state_version"}
    if not isinstance(result, dict):
        raise RecordingContractRefused(status=503)
    mode = result.get("mode")
    if mode == "disabled":
        expected = base | {"mode"}
    elif mode == "recorded":
        expected = base | {
            "mode",
            "recording_ref",
            "state",
            "policy_ref",
            "notice_version",
            "notice_digest",
            "purpose",
            "scope",
            "retention_expires_at",
        }
        artifact_fields = {"artifact_ref", "artifact_state"}
        if set(result) & artifact_fields:
            expected |= artifact_fields
    elif "mode" not in result:
        expected = base
    else:
        raise RecordingContractRefused(status=503)
    if (
        set(result) != expected
        or result.get("version") != 1
        or result.get("meeting_ref") != session_status["meeting_ref"]
        or result.get("room_ref") != session_status["room_ref"]
        or not isinstance(result.get("state_version"), int)
        or isinstance(result.get("state_version"), bool)
        or result["state_version"] < 0
    ):
        raise RecordingContractRefused(status=503)
    if mode == "recorded" and (
        result.get("recording_ref") != session_status["recording_ref"]
        or result.get("state") not in CAPTURE_STATES | NO_CAPTURE_STATES | {"stopping"}
        or result.get("purpose") != PURPOSE
        or result.get("scope") != SCOPE
        or not DIGEST.fullmatch(result.get("notice_digest", ""))
        or any(
            result.get(name) != session_status[name]
            for name in (
                "policy_ref",
                "notice_version",
                "notice_digest",
                "retention_expires_at",
            )
        )
    ):
        raise RecordingContractRefused(status=503)
    return result


def _base_assertion(assertion_type, operation):
    now = int(time.time())
    return {
        "version": 1,
        "type": assertion_type,
        "issuer": settings.MASTRAO_RECORDING_RECEIPT_ISSUER,
        "audience": settings.MASTRAO_RECORDING_RECEIPT_AUDIENCE,
        "operation": operation,
        "operation_version": 1,
        "issued_at": now,
        "expires_at": now + 30,
        "jti": _identifier("req"),
    }


@transaction.atomic
def record_decision(request, room, decision, decision_request_id):
    """Persist and forward one exact accepted/refused/withdrawn decision."""

    if decision not in {"accepted", "refused", "withdrawn"}:
        raise RecordingContractRefused()
    participant = _participant(request, room)
    status = recording_session_status(request, room)
    if not status or status["mode"] != "recorded":
        raise RecordingContractRefused()
    binding = _sync_binding(room, status)
    payload = {
        **_base_assertion(DECISION_TYPE, "record_meeting_recording_decision"),
        "decision_request_id": decision_request_id,
        "organization_external_id": status["organization_external_id"],
        "meeting_ref": status["meeting_ref"],
        "room_ref": status["room_ref"],
        "recording_ref": status["recording_ref"],
        "provider_binding_digest": room.mastrao_binding.provider_binding_digest,
        "participant_kind": participant["kind"],
        "participant_ref": participant["ref"],
        "participant_session_digest": participant["session_digest"],
        "decision": decision,
        "policy_ref": status["policy_ref"],
        "notice_version": status["notice_version"],
        "notice_digest": status["notice_digest"],
        "purpose": status["purpose"],
        "scope": status["scope"],
        "retention_expires_at": status["retention_expires_at"],
        "participant_grant_digest": compact_digest(participant["compact"]),
    }
    compact = sign_decision_assertion(payload)
    result = post_core_json(
        endpoint=settings.MASTRAO_CORE_RECORDING_DECISION_ENDPOINT,
        expected_path="/internal/v1/meetings/recording/decisions",
        body={
            "participant_grant": participant["compact"],
            "decision_assertion": compact,
        },
        timeout=settings.MASTRAO_CORE_RECORDING_TIMEOUT_SECONDS,
        refusal=RecordingContractRefused,
        expected_fields={
            "version",
            "meeting_ref",
            "recording_ref",
            "decision",
            "recording_state",
            "state_version",
        },
    )
    if (
        result["version"] != 1
        or result["meeting_ref"] != status["meeting_ref"]
        or result["recording_ref"] != status["recording_ref"]
        or result["decision"] != decision
        or result["recording_state"]
        not in CAPTURE_STATES | NO_CAPTURE_STATES | {"stopping"}
        or not isinstance(result["state_version"], int)
        or isinstance(result["state_version"], bool)
        or result["state_version"] < 1
    ):
        raise RecordingContractRefused(status=503)
    semantic = {
        key: value
        for key, value in payload.items()
        if key not in {"issued_at", "expires_at", "jti"}
    }
    models.MastraoRecordingDecision.objects.get_or_create(
        decision_request_id=decision_request_id,
        defaults={
            "recording_binding": binding,
            "participant_kind": participant["kind"],
            "participant_ref": participant["ref"],
            "participant_session_digest": participant["session_digest"],
            "participant_grant_digest": compact_digest(participant["compact"]),
            "decision": decision,
            "assertion_jti": payload["jti"],
            "assertion_digest": compact_digest(compact),
            "semantic_digest": hashlib.sha256(
                repr(sorted(semantic.items())).encode()
            ).hexdigest(),
            "core_state_version": result["state_version"],
        },
    )
    return result


def record_transcription_decision(request, room, decision, decision_request_id):
    """Forward one exact transcription decision under its own purpose.

    The Core transcription-consent ledger is the durable record; Meet only
    seals and forwards the assertion, then re-reads the projection. No local
    row is written because the recording-decision table binds one decision
    value per session and would collide with the recording purpose.
    """

    if decision not in {"accepted", "refused", "withdrawn"}:
        raise RecordingContractRefused()
    participant = _participant(request, room)
    status = recording_session_status(request, room)
    if (
        not status
        or status["mode"] != "recorded"
        or status.get("transcription_mode") != "transcribed"
    ):
        raise RecordingContractRefused()
    payload = {
        **_base_assertion(
            TRANSCRIPTION_DECISION_TYPE, "record_meeting_transcription_decision"
        ),
        "decision_request_id": decision_request_id,
        "organization_external_id": status["organization_external_id"],
        "meeting_ref": status["meeting_ref"],
        "room_ref": status["room_ref"],
        "recording_ref": status["recording_ref"],
        "provider_binding_digest": room.mastrao_binding.provider_binding_digest,
        "participant_kind": participant["kind"],
        "participant_ref": participant["ref"],
        "participant_session_digest": participant["session_digest"],
        "decision": decision,
        "policy_ref": status["policy_ref"],
        "notice_version": status["transcription_notice_version"],
        "notice_digest": status["transcription_notice_digest"],
        "purpose": TRANSCRIPTION_PURPOSE,
        "scope": TRANSCRIPTION_SCOPE,
        "retention_expires_at": status["retention_expires_at"],
        "participant_grant_digest": compact_digest(participant["compact"]),
    }
    compact = sign_transcription_decision_assertion(payload)
    result = post_core_json(
        endpoint=settings.MASTRAO_CORE_TRANSCRIPTION_DECISION_ENDPOINT,
        expected_path="/internal/v1/meetings/transcription/decisions",
        body={
            "participant_grant": participant["compact"],
            "decision_assertion": compact,
        },
        timeout=settings.MASTRAO_CORE_RECORDING_TIMEOUT_SECONDS,
        refusal=RecordingContractRefused,
        expected_fields={
            "version",
            "meeting_ref",
            "recording_ref",
            "purpose",
            "decision",
        },
    )
    if (
        result["version"] != 1
        or result["meeting_ref"] != status["meeting_ref"]
        or result["recording_ref"] != status["recording_ref"]
        or result["purpose"] != TRANSCRIPTION_PURPOSE
        or result["decision"] != decision
    ):
        raise RecordingContractRefused(status=503)
    return result


def activate_recording(request, room, activation_request_id):
    """Activate only after the accepted host reports a real LiveKit connection."""

    if not settings.MASTRAO_MEETING_RECORDING_ENABLED:
        raise RecordingContractRefused()
    participant = _participant(request, room)
    status = recording_session_status(request, room)
    if (
        not status
        or participant["kind"] != "host"
        or status.get("decision") != "accepted"
    ):
        raise RecordingContractRefused()
    payload = {
        **_base_assertion(ACTIVATION_TYPE, "activate_meeting_recording"),
        "activation_request_id": activation_request_id,
        "organization_external_id": status["organization_external_id"],
        "meeting_ref": status["meeting_ref"],
        "room_ref": status["room_ref"],
        "recording_ref": status["recording_ref"],
        "provider_binding_digest": room.mastrao_binding.provider_binding_digest,
        "host_ref": participant["ref"],
        "host_session_digest": participant["session_digest"],
        "host_grant_digest": compact_digest(participant["compact"]),
    }
    body = post_core_json(
        endpoint=settings.MASTRAO_CORE_RECORDING_ACTIVATION_ENDPOINT,
        expected_path="/internal/v1/meetings/recording/activate",
        body={
            "host_grant": participant["compact"],
            "activation_assertion": sign_activation_assertion(payload),
        },
        timeout=settings.MASTRAO_CORE_RECORDING_TIMEOUT_SECONDS,
        refusal=RecordingContractRefused,
    )
    return _validate_core_status(body, status)


def request_recording_stop(request, room, source, stop_request_id):
    """Ask Core to converge an active recording to processing."""

    participant = _participant(request, room)
    status = recording_session_status(request, room)
    if not status or status["mode"] != "recorded":
        raise RecordingContractRefused()
    payload = {
        **_base_assertion(STOP_REQUEST_TYPE, "request_meeting_recording_stop"),
        "stop_request_id": stop_request_id,
        "organization_external_id": status["organization_external_id"],
        "meeting_ref": status["meeting_ref"],
        "room_ref": status["room_ref"],
        "recording_ref": status["recording_ref"],
        "provider_binding_digest": room.mastrao_binding.provider_binding_digest,
        "source": source,
        "participant_ref": participant["ref"],
        "participant_grant_digest": compact_digest(participant["compact"]),
    }
    result = post_core_json(
        endpoint=settings.MASTRAO_CORE_RECORDING_STOP_ENDPOINT,
        expected_path="/internal/v1/meetings/recording/stop",
        body={
            "participant_grant": participant["compact"],
            "stop_assertion": sign_stop_request_assertion(payload),
        },
        timeout=settings.MASTRAO_CORE_RECORDING_TIMEOUT_SECONDS,
        refusal=RecordingContractRefused,
    )
    return _validate_core_status(result, status)
