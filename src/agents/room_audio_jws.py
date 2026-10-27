"""Shared canonical JWS envelope verification for room-audio effects."""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

MAX_COMPACT_JWS_BYTES = 16_384
COMPACT_JWS_PART_COUNT = 3


class RoomAudioJwsRefused(ValueError):
    """Reject a non-canonical or incorrectly signed room-audio envelope."""


@dataclass(frozen=True)
class RoomAudioJwsConfig:
    """Immutable Tenant Core signing bindings."""

    key_id: str
    public_jwk: str


class RoomAudioJwsVerifier:
    """Verify exact Ed25519 compact JWS envelopes without policy decisions."""

    def __init__(self, config: RoomAudioJwsConfig):
        """Load one exact authority key for repeated bounded effects."""
        self._key_id = config.key_id
        self._public_key = self._load_public_key(config.public_jwk)

    def verify(self, compact: str, *, jose_type: str, fields: set[str]) -> dict:
        """Return the canonical payload after envelope and signature checks."""
        if (
            not isinstance(compact, str)
            or len(compact.encode("utf-8")) > MAX_COMPACT_JWS_BYTES
        ):
            raise RoomAudioJwsRefused("invalid compact JWS size")
        parts = compact.split(".")
        if len(parts) != COMPACT_JWS_PART_COUNT:
            raise RoomAudioJwsRefused("invalid compact JWS")
        try:
            header_bytes = self._decode(parts[0])
            payload_bytes = self._decode(parts[1])
            signature = self._decode(parts[2])
            header = json.loads(header_bytes)
            effect = json.loads(payload_bytes)
        except (UnicodeError, json.JSONDecodeError, ValueError, TypeError) as error:
            raise RoomAudioJwsRefused("invalid signed authority") from error
        expected_header = {
            "alg": "EdDSA",
            "kid": self._key_id,
            "typ": jose_type,
        }
        if header != expected_header or not isinstance(effect, dict):
            raise RoomAudioJwsRefused("unexpected authority envelope")
        if set(effect) != fields or payload_bytes != self._canonical_json(effect):
            raise RoomAudioJwsRefused("non-canonical authority payload")
        try:
            self._public_key.verify(signature, f"{parts[0]}.{parts[1]}".encode("ascii"))
        except (InvalidSignature, ValueError, TypeError) as error:
            raise RoomAudioJwsRefused("invalid authority signature") from error
        return effect

    @staticmethod
    def _load_public_key(value: str) -> Ed25519PublicKey:
        try:
            jwk = json.loads(value)
            if set(jwk) != {"kty", "crv", "x"}:
                raise ValueError
            if jwk["kty"] != "OKP" or jwk["crv"] != "Ed25519":
                raise ValueError
            return Ed25519PublicKey.from_public_bytes(
                RoomAudioJwsVerifier._decode(jwk["x"])
            )
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
            raise RoomAudioJwsRefused("invalid authority public key") from error

    @staticmethod
    def _decode(value: str) -> bytes:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))

    @staticmethod
    def _canonical_json(value: dict) -> bytes:
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
