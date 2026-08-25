"""Strict host handoff redemption and grant contracts."""

import hashlib
import json
import re
import time
import uuid

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
)

HOST_REDEMPTION_TYPE = "mastrao.meet-host-redemption"
HOST_REDEMPTION_JOSE_TYPE = "mastrao-meeting-host-redemption+jws"
HOST_HANDOFF_TYPE = "mastrao.core-meeting-host-handoff"
HOST_HANDOFF_JOSE_TYPE = "mastrao-meeting-host-handoff+jws"
HOST_GRANT_TYPE = "mastrao.core-meeting-host-grant"
HOST_GRANT_JOSE_TYPE = "mastrao-meeting-host-grant+jws"
MAX_REDEMPTION_SECONDS = 30
MAX_HANDOFF_SECONDS = 120
MAX_GRANT_SECONDS = 14_400
EXTERNAL_ID = re.compile(r"^[A-Za-z0-9._:-]{1,200}$")
HOST_HANDOFF_FIELDS = {
    "version",
    "type",
    "issuer",
    "audience",
    "purpose",
    "handoff_ref",
    "organization_external_id",
    "meeting_ref",
    "room_ref",
    "host_ref",
    "platform_session_ref",
    "provider_binding_digest",
    "issued_at",
    "expires_at",
    "grant_expires_at",
    "nonce",
}
HOST_GRANT_FIELDS = {
    "version",
    "type",
    "issuer",
    "audience",
    "purpose",
    "grant_ref",
    "handoff_ref",
    "organization_external_id",
    "meeting_ref",
    "room_ref",
    "host_ref",
    "platform_session_ref",
    "provider_binding_digest",
    "redemption_id",
    "credential_digest",
    "issued_at",
    "expires_at",
}


def _reject_json_constant(value):
    raise ValueError(f"invalid JSON constant: {value}")


class HostHandoffRefused(Exception):
    """Opaque refusal for host handoff failures."""

    def __init__(self, status=404):
        self.status = status
        super().__init__("host_handoff_refused")


def compact_digest(compact_jws):
    """Return the stable digest used to bind a compact signed credential."""
    if not isinstance(compact_jws, str) or len(compact_jws) > 16_384:
        raise HostHandoffRefused()
    try:
        encoded = compact_jws.encode("ascii")
    except UnicodeEncodeError as error:
        raise HostHandoffRefused() from error
    return hashlib.sha256(encoded).hexdigest()


def _private_signing_key():
    try:
        private_jwk = _configured_json("MASTRAO_ROOM_RECEIPT_PRIVATE_JWK")
    except RoomEffectRefused as error:
        raise HostHandoffRefused(status=503) from error
    if (
        private_jwk.get("kty") != "OKP"
        or private_jwk.get("crv") != "Ed25519"
        or not isinstance(private_jwk.get("d"), str)
    ):
        raise HostHandoffRefused(status=503)
    try:
        return Ed25519PrivateKey.from_private_bytes(_base64url_decode(private_jwk["d"]))
    except (RoomEffectRefused, ValueError, TypeError) as error:
        raise HostHandoffRefused(status=503) from error


def sign_redemption(host_handoff):
    """Mint the short Meet-to-Core redemption proof for one handoff."""
    now = int(time.time())
    redemption_id = f"redemption_{uuid.uuid4().hex}"
    payload = {
        "version": CONTRACT_VERSION,
        "type": HOST_REDEMPTION_TYPE,
        "issuer": settings.MASTRAO_ROOM_RECEIPT_ISSUER,
        "audience": settings.MASTRAO_ROOM_RECEIPT_AUDIENCE,
        "operation": "redeem_host_handoff",
        "operation_version": 1,
        "redemption_id": redemption_id,
        "credential_digest": compact_digest(host_handoff),
        "issued_at": now,
        "expires_at": now + MAX_REDEMPTION_SECONDS,
        "jti": f"redeemjti_{uuid.uuid4().hex}",
    }
    header = {
        "alg": "EdDSA",
        "kid": settings.MASTRAO_ROOM_RECEIPT_KEY_ID,
        "typ": HOST_REDEMPTION_JOSE_TYPE,
    }
    protected = _base64url_encode(_canonical_json(header))
    encoded_payload = _base64url_encode(_canonical_json(payload))
    signature = _private_signing_key().sign(
        f"{protected}.{encoded_payload}".encode("ascii")
    )
    return (
        f"{protected}.{encoded_payload}.{_base64url_encode(signature)}",
        payload,
    )


def verify_host_handoff(  # noqa: PLR0912  # pylint: disable=too-many-branches
    compact_jws,
):
    """Verify one Cabinet Core host handoff before charging redemption capacity."""
    parts = compact_jws.split(".") if isinstance(compact_jws, str) else []
    if len(parts) != 3 or len(compact_jws) > 16_384:
        raise HostHandoffRefused()
    try:
        header_bytes = _base64url_decode(parts[0])
        payload_bytes = _base64url_decode(parts[1])
        signature = _base64url_decode(parts[2])
        header = json.loads(header_bytes, parse_constant=_reject_json_constant)
        payload = json.loads(payload_bytes, parse_constant=_reject_json_constant)
        canonical_payload = _canonical_json(payload)
    except (
        RoomEffectRefused,
        UnicodeError,
        json.JSONDecodeError,
        ValueError,
        TypeError,
    ) as error:
        raise HostHandoffRefused() from error
    expected_header = {
        "alg": "EdDSA",
        "kid": settings.MASTRAO_ROOM_EFFECT_KEY_ID,
        "typ": HOST_HANDOFF_JOSE_TYPE,
    }
    if header != expected_header or not isinstance(payload, dict):
        raise HostHandoffRefused()
    if set(payload) != HOST_HANDOFF_FIELDS or payload_bytes != canonical_payload:
        raise HostHandoffRefused()
    try:
        public_jwk = _configured_json("MASTRAO_ROOM_EFFECT_PUBLIC_JWK")
    except RoomEffectRefused as error:
        raise HostHandoffRefused(status=503) from error
    if (
        public_jwk.get("kty") != "OKP"
        or public_jwk.get("crv") != "Ed25519"
        or not isinstance(public_jwk.get("x"), str)
    ):
        raise HostHandoffRefused(status=503)
    try:
        public_key = Ed25519PublicKey.from_public_bytes(
            _base64url_decode(public_jwk["x"])
        )
    except (RoomEffectRefused, ValueError, TypeError) as error:
        raise HostHandoffRefused(status=503) from error
    try:
        public_key.verify(signature, f"{parts[0]}.{parts[1]}".encode("ascii"))
    except (InvalidSignature, ValueError, TypeError) as error:
        raise HostHandoffRefused() from error
    now = int(time.time())
    if (  # pylint: disable=too-many-boolean-expressions
        payload.get("version") != CONTRACT_VERSION
        or payload.get("type") != HOST_HANDOFF_TYPE
        or payload.get("issuer") != settings.MASTRAO_ROOM_EFFECT_ISSUER
        or payload.get("audience") != settings.MASTRAO_ROOM_EFFECT_AUDIENCE
        or payload.get("purpose") != "join_as_host"
        or not isinstance(payload.get("issued_at"), int)
        or isinstance(payload.get("issued_at"), bool)
        or not isinstance(payload.get("expires_at"), int)
        or isinstance(payload.get("expires_at"), bool)
        or not isinstance(payload.get("grant_expires_at"), int)
        or isinstance(payload.get("grant_expires_at"), bool)
        or payload["issued_at"] > now
        or payload["expires_at"] <= now
        or payload["expires_at"] - payload["issued_at"] > MAX_HANDOFF_SECONDS
        or payload["grant_expires_at"] <= payload["issued_at"]
        or payload["grant_expires_at"] - payload["issued_at"] > MAX_GRANT_SECONDS
    ):
        raise HostHandoffRefused()
    for name in (
        "handoff_ref",
        "meeting_ref",
        "room_ref",
        "host_ref",
        "platform_session_ref",
    ):
        if not isinstance(payload.get(name), str) or not OPAQUE_REFERENCE.fullmatch(
            payload[name]
        ):
            raise HostHandoffRefused()
    if not isinstance(payload.get("organization_external_id"), str) or not (
        EXTERNAL_ID.fullmatch(payload["organization_external_id"])
    ):
        raise HostHandoffRefused()
    if not isinstance(payload.get("provider_binding_digest"), str) or not (
        DIGEST.fullmatch(payload["provider_binding_digest"])
    ):
        raise HostHandoffRefused()
    if not isinstance(payload.get("nonce"), str) or not REQUEST_ID.fullmatch(
        payload["nonce"]
    ):
        raise HostHandoffRefused()
    return payload


def verify_host_grant(  # noqa: PLR0912  # pylint: disable=too-many-branches
    compact_jws,
):
    """Verify and decode an exact Cabinet Core media-host grant."""
    parts = compact_jws.split(".") if isinstance(compact_jws, str) else []
    if len(parts) != 3 or len(compact_jws) > 16_384:
        raise HostHandoffRefused()
    try:
        header_bytes = _base64url_decode(parts[0])
        payload_bytes = _base64url_decode(parts[1])
        signature = _base64url_decode(parts[2])
        header = json.loads(header_bytes)
        payload = json.loads(payload_bytes)
    except (
        RoomEffectRefused,
        UnicodeDecodeError,
        json.JSONDecodeError,
        ValueError,
        TypeError,
    ) as error:
        raise HostHandoffRefused() from error
    expected_header = {
        "alg": "EdDSA",
        "kid": settings.MASTRAO_ROOM_EFFECT_KEY_ID,
        "typ": HOST_GRANT_JOSE_TYPE,
    }
    if header != expected_header or not isinstance(payload, dict):
        raise HostHandoffRefused()
    if set(payload) != HOST_GRANT_FIELDS or payload_bytes != _canonical_json(payload):
        raise HostHandoffRefused()
    try:
        public_jwk = _configured_json("MASTRAO_ROOM_EFFECT_PUBLIC_JWK")
    except RoomEffectRefused as error:
        raise HostHandoffRefused(status=503) from error
    if (
        public_jwk.get("kty") != "OKP"
        or public_jwk.get("crv") != "Ed25519"
        or not isinstance(public_jwk.get("x"), str)
    ):
        raise HostHandoffRefused(status=503)
    try:
        Ed25519PublicKey.from_public_bytes(_base64url_decode(public_jwk["x"])).verify(
            signature, f"{parts[0]}.{parts[1]}".encode("ascii")
        )
    except (RoomEffectRefused, InvalidSignature, ValueError, TypeError) as error:
        raise HostHandoffRefused() from error
    now = int(time.time())
    if (  # pylint: disable=too-many-boolean-expressions
        payload.get("version") != CONTRACT_VERSION
        or payload.get("type") != HOST_GRANT_TYPE
        or payload.get("issuer") != settings.MASTRAO_ROOM_EFFECT_ISSUER
        or payload.get("audience") != settings.MASTRAO_ROOM_EFFECT_AUDIENCE
        or payload.get("purpose") != "media_host"
        or not isinstance(payload.get("issued_at"), int)
        or isinstance(payload.get("issued_at"), bool)
        or not isinstance(payload.get("expires_at"), int)
        or isinstance(payload.get("expires_at"), bool)
        or payload["issued_at"] > now
        or payload["expires_at"] <= now
        or payload["expires_at"] - payload["issued_at"] > MAX_GRANT_SECONDS
    ):
        raise HostHandoffRefused()
    for name in (
        "grant_ref",
        "handoff_ref",
        "meeting_ref",
        "room_ref",
        "host_ref",
        "platform_session_ref",
        "redemption_id",
    ):
        if not isinstance(payload.get(name), str) or not OPAQUE_REFERENCE.fullmatch(
            payload[name]
        ):
            raise HostHandoffRefused()
    if not isinstance(payload.get("organization_external_id"), str) or not (
        EXTERNAL_ID.fullmatch(payload["organization_external_id"])
    ):
        raise HostHandoffRefused()
    for name in ("provider_binding_digest", "credential_digest"):
        if not DIGEST.fullmatch(payload.get(name, "")):
            raise HostHandoffRefused()
    if not REQUEST_ID.fullmatch(payload["redemption_id"]):
        raise HostHandoffRefused()
    return payload
