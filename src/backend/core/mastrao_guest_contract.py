"""Strict signed contracts for the Mastrao guest invitation boundary."""

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

from core.mastrao_host_contract import EXTERNAL_ID
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

GUEST_INVITATION_TYPE = "mastrao.core-meeting-guest-invitation"
GUEST_INVITATION_JOSE_TYPE = "mastrao-meeting-guest-invitation+jws"
GUEST_REDEMPTION_TYPE = "mastrao.meet-guest-redemption"
GUEST_REDEMPTION_JOSE_TYPE = "mastrao-meeting-guest-redemption+jws"
GUEST_BOOTSTRAP_TYPE = "mastrao.core-meeting-guest-grant"
GUEST_BOOTSTRAP_JOSE_TYPE = "mastrao-meeting-guest-grant+jws"
GUEST_DECISION_TYPE = "mastrao.meet-guest-admission-decision"
GUEST_DECISION_JOSE_TYPE = "mastrao-meeting-guest-admission-decision+jws"
GUEST_DECISION_GRANT_TYPE = "mastrao.core-meeting-guest-admission-grant"
GUEST_DECISION_GRANT_JOSE_TYPE = "mastrao-meeting-guest-admission-grant+jws"
GUEST_DECISION_RECEIPT_TYPE = "mastrao.meet-guest-admission-receipt"
GUEST_DECISION_RECEIPT_JOSE_TYPE = "mastrao-meeting-guest-admission-receipt+jws"
GUEST_MEDIA_REQUEST_TYPE = "mastrao.meet-guest-media-request"
GUEST_MEDIA_REQUEST_JOSE_TYPE = "mastrao-meeting-guest-media-request+jws"
GUEST_MEDIA_GRANT_TYPE = "mastrao.core-meeting-guest-media-grant"
GUEST_MEDIA_GRANT_JOSE_TYPE = "mastrao-meeting-guest-media-grant+jws"

MAX_ASSERTION_SECONDS = 30
MAX_INVITATION_SECONDS = 14_400
MAX_BOOTSTRAP_SECONDS = 14_400
MAX_MEDIA_SECONDS = 300

INVITATION_FIELDS = {
    "version",
    "type",
    "issuer",
    "audience",
    "purpose",
    "invitation_ref",
    "organization_external_id",
    "key_id",
    "issued_at",
    "expires_at",
    "nonce",
}
BOOTSTRAP_FIELDS = {
    "version",
    "type",
    "issuer",
    "audience",
    "purpose",
    "grant_ref",
    "invitation_ref",
    "organization_external_id",
    "redemption_id",
    "guest_ref",
    "meeting_ref",
    "room_ref",
    "provider_binding_digest",
    "credential_digest",
    "issued_at",
    "expires_at",
}
DECISION_GRANT_FIELDS = {
    "version",
    "type",
    "issuer",
    "audience",
    "purpose",
    "decision_grant_ref",
    "decision_id",
    "invitation_ref",
    "redemption_id",
    "guest_ref",
    "organization_external_id",
    "meeting_ref",
    "room_ref",
    "provider_binding_digest",
    "credential_digest",
    "decision",
    "issued_at",
    "expires_at",
}
MEDIA_GRANT_FIELDS = {
    "version",
    "type",
    "issuer",
    "audience",
    "purpose",
    "media_grant_ref",
    "media_request_id",
    "invitation_ref",
    "redemption_id",
    "guest_ref",
    "organization_external_id",
    "meeting_ref",
    "room_ref",
    "provider_binding_digest",
    "credential_digest",
    "issued_at",
    "expires_at",
}


class GuestHandoffRefused(Exception):
    """Opaque refusal for all guest capability failures."""

    def __init__(self, status=404):
        self.status = status
        super().__init__("guest_handoff_refused")


def compact_digest(compact_jws):
    """Digest one bounded ASCII compact JWS without retaining it."""

    if not isinstance(compact_jws, str) or len(compact_jws) > 16_384:
        raise GuestHandoffRefused()
    try:
        value = compact_jws.encode("ascii")
    except UnicodeEncodeError as error:
        raise GuestHandoffRefused() from error
    return hashlib.sha256(value).hexdigest()


def _key(setting_name, *, private):
    try:
        jwk = _configured_json(setting_name)
    except RoomEffectRefused as error:
        raise GuestHandoffRefused(status=503) from error
    member = "d" if private else "x"
    if (
        jwk.get("kty") != "OKP"
        or jwk.get("crv") != "Ed25519"
        or not isinstance(jwk.get(member), str)
    ):
        raise GuestHandoffRefused(status=503)
    try:
        raw = _base64url_decode(jwk[member])
        return (
            Ed25519PrivateKey.from_private_bytes(raw)
            if private
            else Ed25519PublicKey.from_public_bytes(raw)
        )
    except (RoomEffectRefused, ValueError, TypeError) as error:
        raise GuestHandoffRefused(status=503) from error


def _sign(payload, jose_type):
    header = {
        "alg": "EdDSA",
        "kid": settings.MASTRAO_ROOM_RECEIPT_KEY_ID,
        "typ": jose_type,
    }
    protected = _base64url_encode(_canonical_json(header))
    encoded_payload = _base64url_encode(_canonical_json(payload))
    signature = _key("MASTRAO_ROOM_RECEIPT_PRIVATE_JWK", private=True).sign(
        f"{protected}.{encoded_payload}".encode("ascii")
    )
    return f"{protected}.{encoded_payload}.{_base64url_encode(signature)}"


def _verify(compact_jws, jose_type, fields):
    parts = compact_jws.split(".") if isinstance(compact_jws, str) else []
    if len(parts) != 3 or len(compact_jws) > 16_384:
        raise GuestHandoffRefused()
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
        raise GuestHandoffRefused() from error
    expected_header = {
        "alg": "EdDSA",
        "kid": settings.MASTRAO_ROOM_EFFECT_KEY_ID,
        "typ": jose_type,
    }
    if header != expected_header or not isinstance(payload, dict):
        raise GuestHandoffRefused()
    if set(payload) != fields or payload_bytes != _canonical_json(payload):
        raise GuestHandoffRefused()
    try:
        _key("MASTRAO_ROOM_EFFECT_PUBLIC_JWK", private=False).verify(
            signature, f"{parts[0]}.{parts[1]}".encode("ascii")
        )
    except (InvalidSignature, ValueError, TypeError) as error:
        raise GuestHandoffRefused() from error
    return payload


def _validate_times(payload, maximum_seconds):
    now = int(time.time())
    issued_at = payload.get("issued_at")
    expires_at = payload.get("expires_at")
    if (
        not isinstance(issued_at, int)
        or isinstance(issued_at, bool)
        or not isinstance(expires_at, int)
        or isinstance(expires_at, bool)
    ):
        raise GuestHandoffRefused()
    if issued_at > now or expires_at <= now or expires_at - issued_at > maximum_seconds:
        raise GuestHandoffRefused()


def _validate_refs(payload, names):
    for name in names:
        if not isinstance(payload.get(name), str) or not OPAQUE_REFERENCE.fullmatch(
            payload[name]
        ):
            raise GuestHandoffRefused()


def _base_assertion(assertion_type, operation):
    now = int(time.time())
    return {
        "version": CONTRACT_VERSION,
        "type": assertion_type,
        "issuer": settings.MASTRAO_ROOM_RECEIPT_ISSUER,
        "audience": settings.MASTRAO_ROOM_RECEIPT_AUDIENCE,
        "operation": operation,
        "operation_version": 1,
        "issued_at": now,
        "expires_at": now + MAX_ASSERTION_SECONDS,
        "jti": f"guestjti_{uuid.uuid4().hex}",
    }


def verify_guest_invitation(compact_jws):
    """Verify the routing-only invitation before an online Core redemption."""

    payload = _verify(compact_jws, GUEST_INVITATION_JOSE_TYPE, INVITATION_FIELDS)
    _validate_times(payload, MAX_INVITATION_SECONDS)
    if (
        payload.get("version") != CONTRACT_VERSION
        or payload.get("type") != GUEST_INVITATION_TYPE
        or payload.get("issuer") != settings.MASTRAO_ROOM_EFFECT_ISSUER
        or payload.get("audience") != settings.MASTRAO_ROOM_EFFECT_AUDIENCE
        or payload.get("purpose") != "request_guest_lobby"
    ):
        raise GuestHandoffRefused()
    _validate_refs(payload, ("invitation_ref",))
    if not EXTERNAL_ID.fullmatch(payload.get("organization_external_id", "")):
        raise GuestHandoffRefused()
    if payload.get("key_id") != settings.MASTRAO_ROOM_EFFECT_KEY_ID:
        raise GuestHandoffRefused()
    if not REQUEST_ID.fullmatch(payload.get("nonce", "")):
        raise GuestHandoffRefused()
    return payload


def sign_guest_redemption(guest_invitation, redemption_id):
    """Sign one exact online invitation redemption attempt."""

    invitation = verify_guest_invitation(guest_invitation)
    payload = {
        **_base_assertion(GUEST_REDEMPTION_TYPE, "redeem_guest_invitation"),
        "invitation_ref": invitation["invitation_ref"],
        "redemption_id": redemption_id,
        "credential_digest": compact_digest(guest_invitation),
    }
    return _sign(payload, GUEST_REDEMPTION_JOSE_TYPE), payload


def verify_guest_bootstrap(compact_jws):
    """Verify the Core grant that establishes one anonymous guest session."""

    payload = _verify(compact_jws, GUEST_BOOTSTRAP_JOSE_TYPE, BOOTSTRAP_FIELDS)
    _validate_times(payload, MAX_BOOTSTRAP_SECONDS)
    if (
        payload.get("version") != CONTRACT_VERSION
        or payload.get("type") != GUEST_BOOTSTRAP_TYPE
        or payload.get("issuer") != settings.MASTRAO_ROOM_EFFECT_ISSUER
        or payload.get("audience") != settings.MASTRAO_ROOM_EFFECT_AUDIENCE
        or payload.get("purpose") != "guest_lobby"
    ):
        raise GuestHandoffRefused()
    _validate_refs(
        payload,
        (
            "grant_ref",
            "invitation_ref",
            "redemption_id",
            "guest_ref",
            "meeting_ref",
            "room_ref",
        ),
    )
    if not EXTERNAL_ID.fullmatch(payload.get("organization_external_id", "")):
        raise GuestHandoffRefused()
    for name in ("provider_binding_digest", "credential_digest"):
        if not DIGEST.fullmatch(payload.get(name, "")):
            raise GuestHandoffRefused()
    return payload


def sign_guest_decision(
    grant, compact_host_grant, compact_guest_grant, decision_id, allow_entry
):
    """Sign the host's exact guest admission intent for Core."""

    payload = {
        **_base_assertion(GUEST_DECISION_TYPE, "decide_guest_admission"),
        "decision_id": decision_id,
        "invitation_ref": grant.invitation_ref,
        "redemption_id": grant.redemption_id,
        "guest_ref": grant.guest_ref,
        "meeting_ref": grant.meeting_ref,
        "room_ref": grant.room_ref,
        "host_grant_digest": compact_digest(compact_host_grant),
        "guest_grant_digest": compact_digest(compact_guest_grant),
        "decision": "allow" if allow_entry else "deny",
    }
    return _sign(payload, GUEST_DECISION_JOSE_TYPE), payload


def verify_guest_decision_grant(compact_jws):
    """Verify Core authorization for one allow/deny projection."""

    payload = _verify(
        compact_jws, GUEST_DECISION_GRANT_JOSE_TYPE, DECISION_GRANT_FIELDS
    )
    _validate_times(payload, MAX_ASSERTION_SECONDS)
    if (
        payload.get("version") != CONTRACT_VERSION
        or payload.get("type") != GUEST_DECISION_GRANT_TYPE
        or payload.get("issuer") != settings.MASTRAO_ROOM_EFFECT_ISSUER
        or payload.get("audience") != settings.MASTRAO_ROOM_EFFECT_AUDIENCE
        or payload.get("purpose") != "apply_guest_admission"
    ):
        raise GuestHandoffRefused()
    if payload.get("decision") not in {"allow", "deny"}:
        raise GuestHandoffRefused()
    _validate_refs(
        payload,
        (
            "decision_id",
            "decision_grant_ref",
            "invitation_ref",
            "redemption_id",
            "guest_ref",
            "meeting_ref",
            "room_ref",
        ),
    )
    if not EXTERNAL_ID.fullmatch(payload.get("organization_external_id", "")):
        raise GuestHandoffRefused()
    for name in ("provider_binding_digest", "credential_digest"):
        if not DIGEST.fullmatch(payload.get(name, "")):
            raise GuestHandoffRefused()
    return payload


def sign_guest_decision_receipt(grant, compact_decision_grant):
    """Sign the exact local admission postcondition applied by Meet."""

    payload = {
        **_base_assertion(GUEST_DECISION_RECEIPT_TYPE, "confirm_guest_admission"),
        "decision_id": grant.decision_ref,
        "invitation_ref": grant.invitation_ref,
        "redemption_id": grant.redemption_id,
        "guest_ref": grant.guest_ref,
        "meeting_ref": grant.meeting_ref,
        "room_ref": grant.room_ref,
        "decision_grant_digest": compact_digest(compact_decision_grant),
        "applied_state": grant.admission_state,
        "status": "confirmed",
    }
    return _sign(payload, GUEST_DECISION_RECEIPT_JOSE_TYPE), payload


def sign_guest_media_request(grant, compact_guest_grant):
    """Sign a fresh request for permission to mint one guest media token."""

    payload = {
        **_base_assertion(GUEST_MEDIA_REQUEST_TYPE, "authorize_guest_media"),
        "media_request_id": f"mediarequest_{uuid.uuid4().hex}",
        "invitation_ref": grant.invitation_ref,
        "redemption_id": grant.redemption_id,
        "guest_ref": grant.guest_ref,
        "meeting_ref": grant.meeting_ref,
        "room_ref": grant.room_ref,
        "guest_grant_digest": compact_digest(compact_guest_grant),
    }
    return _sign(payload, GUEST_MEDIA_REQUEST_JOSE_TYPE), payload


def verify_guest_media_grant(compact_jws):
    """Verify fresh Core authorization for one participant token."""

    payload = _verify(compact_jws, GUEST_MEDIA_GRANT_JOSE_TYPE, MEDIA_GRANT_FIELDS)
    _validate_times(payload, MAX_MEDIA_SECONDS)
    if (
        payload.get("version") != CONTRACT_VERSION
        or payload.get("type") != GUEST_MEDIA_GRANT_TYPE
        or payload.get("issuer") != settings.MASTRAO_ROOM_EFFECT_ISSUER
        or payload.get("audience") != settings.MASTRAO_ROOM_EFFECT_AUDIENCE
        or payload.get("purpose") != "media_guest"
    ):
        raise GuestHandoffRefused()
    _validate_refs(
        payload,
        (
            "media_grant_ref",
            "media_request_id",
            "invitation_ref",
            "redemption_id",
            "guest_ref",
            "meeting_ref",
            "room_ref",
        ),
    )
    if not EXTERNAL_ID.fullmatch(payload.get("organization_external_id", "")):
        raise GuestHandoffRefused()
    for name in ("provider_binding_digest", "credential_digest"):
        if not DIGEST.fullmatch(payload.get(name, "")):
            raise GuestHandoffRefused()
    return payload
