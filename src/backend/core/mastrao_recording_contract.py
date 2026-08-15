"""Strict signed contracts for the canonical Mastrao recording boundary."""

# Strict contract validation keeps all binding predicates visible in one place.
# pylint: disable=too-many-boolean-expressions

import hashlib
import json
import re
import time

from django.conf import settings

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from core.mastrao_room_contract import (
    CONTRACT_VERSION,
    DIGEST,
    OPAQUE_REFERENCE,
    REQUEST_ID,
    RoomEffectRefused,
    _base64url_decode,
    _base64url_encode,
    _canonical_json,
    _configured_json,
    _sha256_canonical,
)

MAX_ASSERTION_SECONDS = 30
MAX_ACCESS_SECONDS = 60
PURPOSE = "meeting_recording"
SCOPE = "room_composite_audio_video_screen"
PROVIDER_REFERENCE = re.compile(r"^[A-Za-z0-9_-]{8,160}$")

START_EFFECT_TYPE = "mastrao.core-meeting-recording-start-effect"
START_EFFECT_JOSE_TYPE = "mastrao-meeting-recording-start-effect+jws"
START_RECEIPT_TYPE = "mastrao.meeting-recording-start-receipt"
START_RECEIPT_JOSE_TYPE = "mastrao-meeting-recording-start-receipt+jws"
STOP_EFFECT_TYPE = "mastrao.core-meeting-recording-stop-effect"
STOP_EFFECT_JOSE_TYPE = "mastrao-meeting-recording-stop-effect+jws"
STOP_RECEIPT_TYPE = "mastrao.meeting-recording-stop-receipt"
STOP_RECEIPT_JOSE_TYPE = "mastrao-meeting-recording-stop-receipt+jws"
DECISION_TYPE = "mastrao.meet-recording-decision"
DECISION_JOSE_TYPE = "mastrao-meeting-recording-decision+jws"
ACTIVATION_TYPE = "mastrao.meet-recording-activation"
ACTIVATION_JOSE_TYPE = "mastrao-meeting-recording-activation+jws"
STOP_REQUEST_TYPE = "mastrao.meet-recording-stop-request"
STOP_REQUEST_JOSE_TYPE = "mastrao-meeting-recording-stop-request+jws"
ARTIFACT_RECEIPT_TYPE = "mastrao.meeting-recording-artifact-receipt"
ARTIFACT_RECEIPT_JOSE_TYPE = "mastrao-meeting-recording-artifact-receipt+jws"
FAILURE_RECEIPT_TYPE = "mastrao.meeting-recording-failure-receipt"
FAILURE_RECEIPT_JOSE_TYPE = "mastrao-meeting-recording-failure-receipt+jws"
ACCESS_GRANT_TYPE = "mastrao.core-meeting-recording-access-grant"
ACCESS_GRANT_JOSE_TYPE = "mastrao-meeting-recording-access-grant+jws"

BINDING_FIELDS = {
    "organization_external_id",
    "meeting_ref",
    "room_ref",
    "recording_ref",
    "provider_binding_digest",
}
POLICY_FIELDS = {
    "policy_ref",
    "notice_version",
    "notice_digest",
    "purpose",
    "scope",
    "retention_expires_at",
}
EFFECT_BINDING_FIELDS = {*BINDING_FIELDS, "effect_key", "arguments_digest"}
START_EFFECT_FIELDS = {
    "version",
    "type",
    "issuer",
    "audience",
    "operation",
    "operation_version",
    *EFFECT_BINDING_FIELDS,
    *POLICY_FIELDS,
    "resolve_only",
    "issued_at",
    "expires_at",
    "jti",
}
STOP_EFFECT_FIELDS = {
    "version",
    "type",
    "issuer",
    "audience",
    "operation",
    "operation_version",
    *EFFECT_BINDING_FIELDS,
    "provider_recording_ref",
    "issued_at",
    "expires_at",
    "jti",
}
ACCESS_GRANT_FIELDS = {
    "version",
    "type",
    "issuer",
    "audience",
    "operation",
    "operation_version",
    "organization_external_id",
    "meeting_ref",
    "recording_ref",
    "artifact_ref",
    "subject_external_id",
    "platform_session_digest",
    "issued_at",
    "expires_at",
    "jti",
}


class RecordingContractRefused(Exception):
    """Opaque refusal for recording credentials and effects."""

    def __init__(self, status=404):
        self.status = status
        super().__init__("recording_contract_refused")


def compact_digest(compact_jws):
    """Hash one bounded compact JWS without retaining the capability."""

    if not isinstance(compact_jws, str) or len(compact_jws) > 16_384:
        raise RecordingContractRefused()
    try:
        return hashlib.sha256(compact_jws.encode("ascii")).hexdigest()
    except UnicodeEncodeError as error:
        raise RecordingContractRefused() from error


def _key(setting_name, *, private):
    try:
        jwk = _configured_json(setting_name)
    except RoomEffectRefused as error:
        raise RecordingContractRefused(status=503) from error
    member = "d" if private else "x"
    if (
        jwk.get("kty") != "OKP"
        or jwk.get("crv") != "Ed25519"
        or not isinstance(jwk.get(member), str)
    ):
        raise RecordingContractRefused(status=503)
    try:
        raw = _base64url_decode(jwk[member])
        return (
            Ed25519PrivateKey.from_private_bytes(raw)
            if private
            else Ed25519PublicKey.from_public_bytes(raw)
        )
    except (RoomEffectRefused, ValueError, TypeError) as error:
        raise RecordingContractRefused(status=503) from error


def _sign(payload, jose_type):
    header = {
        "alg": "EdDSA",
        "kid": settings.MASTRAO_RECORDING_RECEIPT_KEY_ID,
        "typ": jose_type,
    }
    protected = _base64url_encode(_canonical_json(header))
    encoded = _base64url_encode(_canonical_json(payload))
    signature = _key("MASTRAO_RECORDING_RECEIPT_PRIVATE_JWK", private=True).sign(
        f"{protected}.{encoded}".encode("ascii")
    )
    return f"{protected}.{encoded}.{_base64url_encode(signature)}"


def _verify(compact_jws, jose_type, fields):
    parts = compact_jws.split(".") if isinstance(compact_jws, str) else []
    if len(parts) != 3 or len(compact_jws) > 16_384:
        raise RecordingContractRefused()
    try:
        header_bytes = _base64url_decode(parts[0])
        payload_bytes = _base64url_decode(parts[1])
        signature = _base64url_decode(parts[2])
        header = json.loads(header_bytes)
        payload = json.loads(payload_bytes)
    except (
        RoomEffectRefused,
        UnicodeError,
        json.JSONDecodeError,
        ValueError,
        TypeError,
    ) as error:
        raise RecordingContractRefused() from error
    if header != {
        "alg": "EdDSA",
        "kid": settings.MASTRAO_RECORDING_EFFECT_KEY_ID,
        "typ": jose_type,
    }:
        raise RecordingContractRefused()
    if not isinstance(payload, dict) or set(payload) != fields:
        raise RecordingContractRefused()
    if payload_bytes != _canonical_json(payload):
        raise RecordingContractRefused()
    try:
        _key("MASTRAO_RECORDING_EFFECT_PUBLIC_JWK", private=False).verify(
            signature, f"{parts[0]}.{parts[1]}".encode("ascii")
        )
    except (InvalidSignature, ValueError, TypeError) as error:
        raise RecordingContractRefused() from error
    return payload


def _validate_time(payload, maximum=MAX_ASSERTION_SECONDS):
    now = int(time.time())
    issued_at = payload.get("issued_at")
    expires_at = payload.get("expires_at")
    if (
        not isinstance(issued_at, int)
        or isinstance(issued_at, bool)
        or not isinstance(expires_at, int)
        or isinstance(expires_at, bool)
        or issued_at > now
        or expires_at <= now
        or not 1 <= expires_at - issued_at <= maximum
    ):
        raise RecordingContractRefused()


def _validate_ref(payload, name, *, max_length=160):
    value = payload.get(name)
    if (
        not isinstance(value, str)
        or len(value) > max_length
        or not OPAQUE_REFERENCE.fullmatch(value)
    ):
        raise RecordingContractRefused()


def _validate_provider_ref(payload, name="provider_recording_ref"):
    value = payload.get(name)
    if not isinstance(value, str) or not PROVIDER_REFERENCE.fullmatch(value):
        raise RecordingContractRefused()


def _validate_common(payload):
    if (
        payload.get("version") != CONTRACT_VERSION
        or payload.get("issuer") != settings.MASTRAO_RECORDING_EFFECT_ISSUER
        or payload.get("audience") != settings.MASTRAO_RECORDING_EFFECT_AUDIENCE
        or payload.get("operation_version") != 1
        or not REQUEST_ID.fullmatch(payload.get("jti", ""))
    ):
        raise RecordingContractRefused()
    organization = payload.get("organization_external_id")
    if (
        not isinstance(organization, str)
        or not 1 <= len(organization) <= 200
        or any(
            character
            not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._:-"
            for character in organization
        )
    ):
        raise RecordingContractRefused()
    for name in ("meeting_ref", "recording_ref", "effect_key"):
        _validate_ref(payload, name)
    _validate_ref(payload, "room_ref", max_length=100)
    for name in ("provider_binding_digest", "arguments_digest"):
        if not DIGEST.fullmatch(payload.get(name, "")):
            raise RecordingContractRefused()


def _effect_arguments(effect, operation):
    return {
        "version": CONTRACT_VERSION,
        "operation": operation,
        "recording_ref": effect["recording_ref"],
        "meeting_ref": effect["meeting_ref"],
        "room_ref": effect["room_ref"],
        "provider_binding_digest": effect["provider_binding_digest"],
    }


def verify_recording_start_effect(compact_jws):
    """Verify the exact Core start effect contract."""

    effect = _verify(compact_jws, START_EFFECT_JOSE_TYPE, START_EFFECT_FIELDS)
    _validate_time(effect)
    _validate_common(effect)
    if (
        effect.get("type") != START_EFFECT_TYPE
        or effect.get("operation") != "start_room_composite_recording"
        or effect.get("purpose") != PURPOSE
        or effect.get("scope") != SCOPE
        or not isinstance(effect.get("resolve_only"), bool)
    ):
        raise RecordingContractRefused()
    for name in ("policy_ref", "notice_version"):
        _validate_ref(effect, name)
    if not DIGEST.fullmatch(effect.get("notice_digest", "")):
        raise RecordingContractRefused()
    retention = effect.get("retention_expires_at")
    if (
        not isinstance(retention, int)
        or isinstance(retention, bool)
        or retention <= int(time.time())
    ):
        raise RecordingContractRefused()
    if effect["arguments_digest"] != _sha256_canonical(
        _effect_arguments(effect, "start")
    ):
        raise RecordingContractRefused()
    return effect


def verify_recording_stop_effect(compact_jws):
    """Verify the exact Core stop effect contract."""

    effect = _verify(compact_jws, STOP_EFFECT_JOSE_TYPE, STOP_EFFECT_FIELDS)
    _validate_time(effect)
    _validate_common(effect)
    if (
        effect.get("type") != STOP_EFFECT_TYPE
        or effect.get("operation") != "stop_room_composite_recording"
    ):
        raise RecordingContractRefused()
    _validate_provider_ref(effect)
    if effect["arguments_digest"] != _sha256_canonical(
        _effect_arguments(effect, "stop")
    ):
        raise RecordingContractRefused()
    return effect


def build_start_receipt_claims(effect, provider_recording_ref, observation):
    """Build strict persisted start receipt claims."""

    _validate_provider_ref({"provider_recording_ref": provider_recording_ref})
    if observation not in {"started", "already_active"}:
        raise RecordingContractRefused()
    now = int(time.time())
    return {
        "version": CONTRACT_VERSION,
        "type": START_RECEIPT_TYPE,
        "issuer": settings.MASTRAO_RECORDING_RECEIPT_ISSUER,
        "audience": settings.MASTRAO_RECORDING_RECEIPT_AUDIENCE,
        "operation": "confirm_room_composite_recording_started",
        "operation_version": 1,
        **{name: effect[name] for name in EFFECT_BINDING_FIELDS},
        "status": "confirmed",
        "provider_recording_ref": provider_recording_ref,
        "provider_observation": observation,
        "issued_at": now,
        "expires_at": now + MAX_ASSERTION_SECONDS,
        "jti": effect["jti"],
    }


def build_stop_receipt_claims(effect, observation):
    """Build strict persisted stop receipt claims."""

    if observation not in {"stopped", "already_stopped", "room_ended"}:
        raise RecordingContractRefused()
    now = int(time.time())
    return {
        "version": CONTRACT_VERSION,
        "type": STOP_RECEIPT_TYPE,
        "issuer": settings.MASTRAO_RECORDING_RECEIPT_ISSUER,
        "audience": settings.MASTRAO_RECORDING_RECEIPT_AUDIENCE,
        "operation": "confirm_room_composite_recording_stopped",
        "operation_version": 1,
        **{name: effect[name] for name in EFFECT_BINDING_FIELDS},
        "provider_recording_ref": effect["provider_recording_ref"],
        "status": "confirmed",
        "provider_observation": observation,
        "issued_at": now,
        "expires_at": now + MAX_ASSERTION_SECONDS,
        "jti": effect["jti"],
    }


def sign_start_receipt(claims):
    """Sign one exact start receipt."""
    return _sign(claims, START_RECEIPT_JOSE_TYPE)


def sign_stop_receipt(claims):
    """Sign one exact stop receipt."""
    return _sign(claims, STOP_RECEIPT_JOSE_TYPE)


def sign_decision_assertion(payload):
    """Sign one participant recording decision."""
    return _sign(payload, DECISION_JOSE_TYPE)


def sign_activation_assertion(payload):
    """Sign one host activation assertion."""
    return _sign(payload, ACTIVATION_JOSE_TYPE)


def sign_stop_request_assertion(payload):
    """Sign one host stop assertion."""
    return _sign(payload, STOP_REQUEST_JOSE_TYPE)


def sign_artifact_receipt(payload):
    """Sign one verified artifact receipt."""
    return _sign(payload, ARTIFACT_RECEIPT_JOSE_TYPE)


def sign_failure_receipt(payload):
    """Sign one provider terminal failure receipt."""
    return _sign(payload, FAILURE_RECEIPT_JOSE_TYPE)


def verify_recording_access_grant(compact_jws):
    """Verify one exact short-lived Core artifact access grant."""

    grant = _verify(compact_jws, ACCESS_GRANT_JOSE_TYPE, ACCESS_GRANT_FIELDS)
    _validate_time(grant, MAX_ACCESS_SECONDS)
    if (
        grant.get("version") != CONTRACT_VERSION
        or grant.get("type") != ACCESS_GRANT_TYPE
        or grant.get("issuer") != settings.MASTRAO_RECORDING_EFFECT_ISSUER
        or grant.get("audience") != settings.MASTRAO_RECORDING_EFFECT_AUDIENCE
        or grant.get("operation") != "access_meeting_recording_artifact"
        or grant.get("operation_version") != 1
        or not REQUEST_ID.fullmatch(grant.get("jti", ""))
    ):
        raise RecordingContractRefused()
    for name in ("meeting_ref", "recording_ref", "artifact_ref"):
        _validate_ref(grant, name)
    if not DIGEST.fullmatch(grant.get("platform_session_digest", "")):
        raise RecordingContractRefused()
    return grant
