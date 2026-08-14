"""Strict JWS contracts for canonical Mastrao meeting closure."""

import hashlib
import json
import time
import uuid

from django.conf import settings

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from core.mastrao_host_contract import compact_digest
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

MEETING_CLOSE_REQUEST_TYPE = "mastrao.meet-meeting-close-request"
MEETING_CLOSE_REQUEST_JOSE_TYPE = "mastrao-meeting-close-request+jws"
ROOM_CLOSE_EFFECT_TYPE = "mastrao.core-meeting-room-close-effect"
ROOM_CLOSE_EFFECT_JOSE_TYPE = "mastrao-meeting-room-close-effect+jws"
ROOM_CLOSE_RECEIPT_TYPE = "mastrao.meeting-room-close-receipt"
ROOM_CLOSE_RECEIPT_JOSE_TYPE = "mastrao-meeting-room-close-receipt+jws"
MAX_ASSERTION_SECONDS = 30

CLOSE_BINDING_FIELDS = {
    "close_ref",
    "effect_key",
    "arguments_digest",
    "organization_external_id",
    "meeting_ref",
    "room_ref",
    "provider_binding_digest",
}
ROOM_CLOSE_EFFECT_FIELDS = {
    "version",
    "type",
    "issuer",
    "audience",
    "operation",
    "operation_version",
    *CLOSE_BINDING_FIELDS,
    "issued_at",
    "expires_at",
    "jti",
}


class RoomCloseRefused(Exception):
    """Opaque refusal for a close request or room close effect."""

    def __init__(self, status=404):
        self.status = status
        super().__init__("room_close_refused")


def _key(setting_name, *, private):
    try:
        jwk = _configured_json(setting_name)
    except RoomEffectRefused as error:
        raise RoomCloseRefused(status=503) from error
    member = "d" if private else "x"
    if (
        jwk.get("kty") != "OKP"
        or jwk.get("crv") != "Ed25519"
        or not isinstance(jwk.get(member), str)
    ):
        raise RoomCloseRefused(status=503)
    try:
        raw = _base64url_decode(jwk[member])
        return (
            Ed25519PrivateKey.from_private_bytes(raw)
            if private
            else Ed25519PublicKey.from_public_bytes(raw)
        )
    except (RoomEffectRefused, ValueError, TypeError) as error:
        raise RoomCloseRefused(status=503) from error


def _sign(payload, jose_type):
    header = {
        "alg": "EdDSA",
        "kid": settings.MASTRAO_ROOM_RECEIPT_KEY_ID,
        "typ": jose_type,
    }
    protected = _base64url_encode(_canonical_json(header))
    encoded = _base64url_encode(_canonical_json(payload))
    signature = _key("MASTRAO_ROOM_RECEIPT_PRIVATE_JWK", private=True).sign(
        f"{protected}.{encoded}".encode("ascii")
    )
    return f"{protected}.{encoded}.{_base64url_encode(signature)}"


def _verify(compact_jws, jose_type, fields):
    parts = compact_jws.split(".") if isinstance(compact_jws, str) else []
    if len(parts) != 3 or len(compact_jws) > 16_384:
        raise RoomCloseRefused()
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
        raise RoomCloseRefused() from error
    expected_header = {
        "alg": "EdDSA",
        "kid": settings.MASTRAO_ROOM_EFFECT_KEY_ID,
        "typ": jose_type,
    }
    if header != expected_header or not isinstance(payload, dict):
        raise RoomCloseRefused()
    if set(payload) != fields or payload_bytes != _canonical_json(payload):
        raise RoomCloseRefused()
    try:
        _key("MASTRAO_ROOM_EFFECT_PUBLIC_JWK", private=False).verify(
            signature, f"{parts[0]}.{parts[1]}".encode("ascii")
        )
    except (InvalidSignature, ValueError, TypeError) as error:
        raise RoomCloseRefused() from error
    return payload


def _validate_time(payload):
    # pylint: disable=too-many-boolean-expressions
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
        or expires_at - issued_at > MAX_ASSERTION_SECONDS
    ):
        raise RoomCloseRefused()


def _validate_ref(payload, name, *, max_length=160):
    value = payload.get(name)
    if (
        not isinstance(value, str)
        or len(value) > max_length
        or not OPAQUE_REFERENCE.fullmatch(value)
    ):
        raise RoomCloseRefused()


def close_arguments(effect):
    """Return the semantic close arguments protected by arguments_digest."""

    return {
        "version": CONTRACT_VERSION,
        "operation": "close_private_room",
        "operation_version": 1,
        "close_ref": effect["close_ref"],
        "organization_external_id": effect["organization_external_id"],
        "meeting_ref": effect["meeting_ref"],
        "room_ref": effect["room_ref"],
        "provider_binding_digest": effect["provider_binding_digest"],
    }


def verify_room_close_effect(compact_jws):
    """Verify one exact Core-to-Meet room close effect."""

    # pylint: disable=too-many-boolean-expressions

    effect = _verify(compact_jws, ROOM_CLOSE_EFFECT_JOSE_TYPE, ROOM_CLOSE_EFFECT_FIELDS)
    _validate_time(effect)
    if (
        effect.get("version") != CONTRACT_VERSION
        or effect.get("type") != ROOM_CLOSE_EFFECT_TYPE
        or effect.get("issuer") != settings.MASTRAO_ROOM_EFFECT_ISSUER
        or effect.get("audience") != settings.MASTRAO_ROOM_EFFECT_AUDIENCE
        or effect.get("operation") != "close_private_room"
        or effect.get("operation_version") != 1
    ):
        raise RoomCloseRefused()
    for name in ("close_ref", "effect_key", "meeting_ref"):
        _validate_ref(effect, name)
    _validate_ref(effect, "room_ref", max_length=100)
    organization = effect.get("organization_external_id")
    if (
        not isinstance(organization, str)
        or not 1 <= len(organization) <= 200
        or any(
            character
            not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._:-"
            for character in organization
        )
    ):
        raise RoomCloseRefused()
    if not REQUEST_ID.fullmatch(effect.get("jti", "")):
        raise RoomCloseRefused()
    for name in ("arguments_digest", "provider_binding_digest"):
        if not DIGEST.fullmatch(effect.get(name, "")):
            raise RoomCloseRefused()
    if effect["arguments_digest"] != _sha256_canonical(close_arguments(effect)):
        raise RoomCloseRefused()
    return effect


def build_room_close_receipt_claims(effect, provider_observation):
    """Build stable receipt claims for durable replay."""

    now = int(time.time())
    return {
        "version": CONTRACT_VERSION,
        "type": ROOM_CLOSE_RECEIPT_TYPE,
        "issuer": settings.MASTRAO_ROOM_RECEIPT_ISSUER,
        "audience": settings.MASTRAO_ROOM_RECEIPT_AUDIENCE,
        "operation": "confirm_private_room_closed",
        "operation_version": 1,
        **{name: effect[name] for name in CLOSE_BINDING_FIELDS},
        "status": "confirmed",
        "provider_observation": provider_observation,
        "issued_at": now,
        "expires_at": now + MAX_ASSERTION_SECONDS,
        "jti": effect["jti"],
    }


def sign_room_close_receipt(claims):
    """Sign persisted exact receipt claims deterministically."""

    return _sign(claims, ROOM_CLOSE_RECEIPT_JOSE_TYPE)


def sign_meeting_close_request(grant, compact_host_grant, close_request_id):
    """Sign the exact temporary host close intent sent to Cabinet Core."""

    _validate_ref({"close_request_id": close_request_id}, "close_request_id")
    now = int(time.time())
    payload = {
        "version": CONTRACT_VERSION,
        "type": MEETING_CLOSE_REQUEST_TYPE,
        "issuer": settings.MASTRAO_ROOM_RECEIPT_ISSUER,
        "audience": settings.MASTRAO_ROOM_RECEIPT_AUDIENCE,
        "operation": "request_meeting_close",
        "operation_version": 1,
        "close_request_id": close_request_id,
        "meeting_ref": grant.meeting_ref,
        "room_ref": grant.room_ref,
        "host_grant_digest": compact_digest(compact_host_grant),
        "issued_at": now,
        "expires_at": now + MAX_ASSERTION_SECONDS,
        "jti": f"closejti_{uuid.uuid4().hex}",
    }
    return _sign(payload, MEETING_CLOSE_REQUEST_JOSE_TYPE), payload


def compact_receipt_digest(compact_jws):
    """Hash a bounded receipt without retaining its compact representation."""

    if not isinstance(compact_jws, str) or len(compact_jws) > 16_384:
        raise RoomCloseRefused()
    try:
        return hashlib.sha256(compact_jws.encode("ascii")).hexdigest()
    except UnicodeEncodeError as error:
        raise RoomCloseRefused() from error
