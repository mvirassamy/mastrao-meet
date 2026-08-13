"""Strict signed contract shared by the Mastrao room endpoint."""

import base64
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

CONTRACT_VERSION = 1
ROOM_EFFECT_TYPE = "mastrao.core-meeting-room-effect"
ROOM_EFFECT_JOSE_TYPE = "mastrao-meeting-room-effect+jws"
ROOM_RECEIPT_TYPE = "mastrao.meeting-room-receipt"
ROOM_RECEIPT_JOSE_TYPE = "mastrao-meeting-room-receipt+jws"
MAX_EFFECT_SECONDS = 30
MAX_BODY_BYTES = 32_768
OPAQUE_REFERENCE = re.compile(r"^[A-Za-z0-9_-]{16,160}$")
REQUEST_ID = re.compile(r"^[A-Za-z0-9_-]{8,200}$")
DIGEST = re.compile(r"^[a-f0-9]{64}$")
ROOM_EFFECT_FIELDS = {
    "version",
    "type",
    "issuer",
    "audience",
    "operation",
    "operation_version",
    "effect_key",
    "arguments_digest",
    "meeting_ref",
    "room_ref",
    "owner_ref",
    "issued_at",
    "expires_at",
    "jti",
}


class RoomEffectRefused(Exception):
    """Safe refusal for an invalid or conflicting room effect."""

    def __init__(self, status=404):
        self.status = status
        super().__init__("room_effect_refused")


def _base64url_decode(value):
    if not isinstance(value, str) or not value:
        raise RoomEffectRefused()
    try:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except (InvalidSignature, ValueError, TypeError) as error:
        raise RoomEffectRefused() from error


def _base64url_encode(value):
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _canonical_json(value):
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        ).encode("utf-8")
        + b"\n"
    )


def _sha256_canonical(value):
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _configured_json(setting_name):
    raw = getattr(settings, setting_name, "")
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as error:
        raise RoomEffectRefused(status=503) from error
    if not isinstance(value, dict):
        raise RoomEffectRefused(status=503)
    return value


def _validate_configuration():
    if not settings.MASTRAO_ROOM_ADAPTER_ENABLED:
        raise RoomEffectRefused()
    names = (
        "MASTRAO_ROOM_EFFECT_ISSUER",
        "MASTRAO_ROOM_EFFECT_AUDIENCE",
        "MASTRAO_ROOM_EFFECT_KEY_ID",
        "MASTRAO_ROOM_RECEIPT_ISSUER",
        "MASTRAO_ROOM_RECEIPT_AUDIENCE",
        "MASTRAO_ROOM_RECEIPT_KEY_ID",
    )
    if any(not isinstance(getattr(settings, name, None), str) for name in names):
        raise RoomEffectRefused(status=503)
    if any(not getattr(settings, name).strip() for name in names):
        raise RoomEffectRefused(status=503)


def _verify_effect_signature(parts, signature):
    public_jwk = _configured_json("MASTRAO_ROOM_EFFECT_PUBLIC_JWK")
    if (
        public_jwk.get("kty") != "OKP"
        or public_jwk.get("crv") != "Ed25519"
        or not isinstance(public_jwk.get("x"), str)
    ):
        raise RoomEffectRefused(status=503)
    try:
        Ed25519PublicKey.from_public_bytes(_base64url_decode(public_jwk["x"])).verify(
            signature, f"{parts[0]}.{parts[1]}".encode("ascii")
        )
    except (InvalidSignature, ValueError, TypeError) as error:
        raise RoomEffectRefused() from error


def _validate_effect_payload(payload, now):
    invalid_contract = (
        payload.get("version") != CONTRACT_VERSION
        or payload.get("type") != ROOM_EFFECT_TYPE
        or payload.get("issuer") != settings.MASTRAO_ROOM_EFFECT_ISSUER
        or payload.get("audience") != settings.MASTRAO_ROOM_EFFECT_AUDIENCE
        or payload.get("operation") != "ensure_private_room"
        or payload.get("operation_version") != 1
    )
    invalid_time = (
        not isinstance(payload.get("issued_at"), int)
        or not isinstance(payload.get("expires_at"), int)
        or payload["issued_at"] > now
        or payload["expires_at"] <= now
        or payload["expires_at"] - payload["issued_at"] > MAX_EFFECT_SECONDS
    )
    if invalid_contract or invalid_time:
        raise RoomEffectRefused()


def _validate_effect_identifiers(payload):
    for name in ("effect_key", "meeting_ref", "room_ref", "owner_ref"):
        if not isinstance(payload.get(name), str) or not OPAQUE_REFERENCE.fullmatch(
            payload[name]
        ):
            raise RoomEffectRefused()
    if not isinstance(payload.get("jti"), str) or not REQUEST_ID.fullmatch(
        payload["jti"]
    ):
        raise RoomEffectRefused()
    if len(payload["room_ref"]) > 100 or not DIGEST.fullmatch(
        payload.get("arguments_digest", "")
    ):
        raise RoomEffectRefused()


def verify_effect(compact_jws):
    """Verify and return one canonical private-room effect."""

    _validate_configuration()
    parts = compact_jws.split(".") if isinstance(compact_jws, str) else []
    if len(parts) != 3:
        raise RoomEffectRefused()
    header_bytes = _base64url_decode(parts[0])
    payload_bytes = _base64url_decode(parts[1])
    signature = _base64url_decode(parts[2])
    try:
        header = json.loads(header_bytes)
        payload = json.loads(payload_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RoomEffectRefused() from error
    if not isinstance(header, dict) or set(header) != {"alg", "kid", "typ"}:
        raise RoomEffectRefused()
    if header != {
        "alg": "EdDSA",
        "kid": settings.MASTRAO_ROOM_EFFECT_KEY_ID,
        "typ": ROOM_EFFECT_JOSE_TYPE,
    }:
        raise RoomEffectRefused()
    if not isinstance(payload, dict) or set(payload) != ROOM_EFFECT_FIELDS:
        raise RoomEffectRefused()
    if payload_bytes != _canonical_json(payload):
        raise RoomEffectRefused()
    _verify_effect_signature(parts, signature)
    _validate_effect_payload(payload, int(time.time()))
    _validate_effect_identifiers(payload)
    arguments = {
        "version": CONTRACT_VERSION,
        "operation": "ensure_private_room",
        "operation_version": 1,
        "meeting_ref": payload["meeting_ref"],
        "room_ref": payload["room_ref"],
        "owner_ref": payload["owner_ref"],
    }
    if payload["arguments_digest"] != _sha256_canonical(arguments):
        raise RoomEffectRefused()
    return payload


def sign_receipt(effect, binding):
    """Sign the receipt proving the exact canonical room binding."""

    now = int(time.time())
    receipt = {
        "version": CONTRACT_VERSION,
        "type": ROOM_RECEIPT_TYPE,
        "issuer": settings.MASTRAO_ROOM_RECEIPT_ISSUER,
        "audience": settings.MASTRAO_ROOM_RECEIPT_AUDIENCE,
        "operation": "confirm_private_room",
        "operation_version": 1,
        "effect_key": effect["effect_key"],
        "arguments_digest": effect["arguments_digest"],
        "meeting_ref": effect["meeting_ref"],
        "room_ref": effect["room_ref"],
        "owner_ref": effect["owner_ref"],
        "status": "confirmed",
        "access_level": "restricted",
        "owner_bound": True,
        "provider_binding_digest": binding.provider_binding_digest,
        "issued_at": now,
        "expires_at": now + MAX_EFFECT_SECONDS,
        "jti": effect["jti"],
    }
    header = {
        "alg": "EdDSA",
        "kid": settings.MASTRAO_ROOM_RECEIPT_KEY_ID,
        "typ": ROOM_RECEIPT_JOSE_TYPE,
    }
    protected = _base64url_encode(_canonical_json(header))
    payload = _base64url_encode(_canonical_json(receipt))
    private_jwk = _configured_json("MASTRAO_ROOM_RECEIPT_PRIVATE_JWK")
    if (
        private_jwk.get("kty") != "OKP"
        or private_jwk.get("crv") != "Ed25519"
        or not isinstance(private_jwk.get("d"), str)
    ):
        raise RoomEffectRefused(status=503)
    try:
        key = Ed25519PrivateKey.from_private_bytes(_base64url_decode(private_jwk["d"]))
        signature = key.sign(f"{protected}.{payload}".encode("ascii"))
    except (ValueError, TypeError) as error:
        raise RoomEffectRefused(status=503) from error
    return f"{protected}.{payload}.{_base64url_encode(signature)}"
