"""Verify one signed Tenant Core room-audio start effect."""

from __future__ import annotations

import base64
import json
import re
import time
from dataclasses import dataclass

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

JOSE_TYPE = "mastrao-meeting-room-audio-start-effect+jws"
MAX_COMPACT_JWS_BYTES = 16_384
MAX_EFFECT_LIFETIME_SECONDS = 30
MAX_AUDIO_DURATION_SECONDS = 14_400
MAX_EMPTY_ROOM_GRACE_SECONDS = 300
COMPACT_JWS_PART_COUNT = 3
REFERENCE = re.compile(r"^[A-Za-z0-9._:-]{1,200}$")
SHA256 = re.compile(r"^[a-f0-9]{64}$")
FIELDS = {
    "version",
    "type",
    "issuer",
    "audience",
    "operation",
    "operation_version",
    "organization_external_id",
    "meeting_ref",
    "room_ref",
    "audio_run_ref",
    "policy_ref",
    "policy_digest",
    "notice_digest",
    "provider_binding_digest",
    "retention_expires_at",
    "pipeline",
    "source_profile",
    "maximum_duration_seconds",
    "empty_room_grace_seconds",
    "issued_at",
    "expires_at",
    "jti",
}
POST_SOURCE_PROFILE = {
    "format": "authorized_room_mix_ogg_opus_v1",
    "codec": "opus",
    "container": "ogg",
    "channel_count": 1,
}
LIVE_SOURCE_PROFILE = {
    "format": "authorized_room_mix_pcm_s16le_v1",
    "codec": "pcm_s16le",
    "sample_rate_hz": 24_000,
    "channel_count": 1,
}


class RoomAudioStartRefused(ValueError):
    """Reject a start effect that is not exact, current and Core-signed."""


@dataclass(frozen=True)
class RoomAudioStartVerifierConfig:
    """Immutable signature and room bindings for one worker dispatch."""

    issuer: str
    audience: str
    key_id: str
    public_jwk: str
    room_name: str


class RoomAudioStartVerifier:
    """Validate a compact start effect before accepting a LiveKit job."""

    def __init__(self, config: RoomAudioStartVerifierConfig):
        """Load the exact public key and room binding once."""
        self._config = config
        self._public_key = self._load_public_key(config.public_jwk)

    def verify(self, compact: str, *, observed_at: int | None = None) -> dict:
        """Return the exact effect only when every signed invariant holds."""
        if (
            not isinstance(compact, str)
            or len(compact.encode("utf-8")) > MAX_COMPACT_JWS_BYTES
        ):
            raise RoomAudioStartRefused("invalid start effect size")
        parts = compact.split(".")
        if len(parts) != COMPACT_JWS_PART_COUNT:
            raise RoomAudioStartRefused("invalid compact JWS")
        try:
            header_bytes = self._decode(parts[0])
            payload_bytes = self._decode(parts[1])
            signature = self._decode(parts[2])
            header = json.loads(header_bytes)
            effect = json.loads(payload_bytes)
        except (UnicodeError, json.JSONDecodeError, ValueError, TypeError) as error:
            raise RoomAudioStartRefused("invalid signed start effect") from error
        expected_header = {
            "alg": "EdDSA",
            "kid": self._config.key_id,
            "typ": JOSE_TYPE,
        }
        if header != expected_header or not isinstance(effect, dict):
            raise RoomAudioStartRefused("unexpected start effect envelope")
        if set(effect) != FIELDS or payload_bytes != self._canonical_json(effect):
            raise RoomAudioStartRefused("non-canonical start effect")
        try:
            self._public_key.verify(signature, f"{parts[0]}.{parts[1]}".encode("ascii"))
        except (InvalidSignature, ValueError, TypeError) as error:
            raise RoomAudioStartRefused("invalid start effect signature") from error
        self._validate(effect, observed_at=observed_at)
        return effect

    def _validate(self, effect: dict, *, observed_at: int | None) -> None:
        now = int(time.time()) if observed_at is None else observed_at
        issued_at = effect["issued_at"]
        expires_at = effect["expires_at"]
        maximum_duration = effect["maximum_duration_seconds"]
        empty_room_grace = effect["empty_room_grace_seconds"]
        pipeline = effect["pipeline"]
        if (
            type(effect["version"]) is not int
            or effect["version"] != 1
            or effect["type"] != "mastrao.core-meeting-room-audio-start-effect"
            or type(effect["operation_version"]) is not int
            or effect["operation_version"] != 1
            or effect["operation"] != "start_meeting_room_audio_run"
            or effect["issuer"] != self._config.issuer
            or effect["audience"] != self._config.audience
            or effect["room_ref"] != self._config.room_name
            or pipeline not in {"post", "live"}
            or effect["source_profile"] != self._source_profile(pipeline)
            or type(maximum_duration) is not int
            or not 1 <= maximum_duration <= MAX_AUDIO_DURATION_SECONDS
            or type(empty_room_grace) is not int
            or not 0 <= empty_room_grace <= MAX_EMPTY_ROOM_GRACE_SECONDS
            or type(issued_at) is not int
            or type(expires_at) is not int
            or issued_at > now
            or expires_at <= now
            or not 1 <= expires_at - issued_at <= MAX_EFFECT_LIFETIME_SECONDS
            or type(effect["retention_expires_at"]) is not int
            or effect["retention_expires_at"] <= expires_at
        ):
            raise RoomAudioStartRefused("start effect binding refused")
        for name in (
            "issuer",
            "audience",
            "organization_external_id",
            "meeting_ref",
            "room_ref",
            "audio_run_ref",
            "policy_ref",
            "jti",
        ):
            if not isinstance(effect[name], str) or not REFERENCE.fullmatch(
                effect[name]
            ):
                raise RoomAudioStartRefused(f"invalid {name}")
        for name in (
            "policy_digest",
            "notice_digest",
            "provider_binding_digest",
        ):
            if not isinstance(effect[name], str) or not SHA256.fullmatch(effect[name]):
                raise RoomAudioStartRefused(f"invalid {name}")

    @staticmethod
    def _source_profile(pipeline: str) -> dict:
        if pipeline == "post":
            return POST_SOURCE_PROFILE
        return LIVE_SOURCE_PROFILE

    @staticmethod
    def _load_public_key(value: str) -> Ed25519PublicKey:
        try:
            jwk = json.loads(value)
            if set(jwk) != {"kty", "crv", "x"}:
                raise ValueError
            if jwk["kty"] != "OKP" or jwk["crv"] != "Ed25519":
                raise ValueError
            return Ed25519PublicKey.from_public_bytes(
                RoomAudioStartVerifier._decode(jwk["x"])
            )
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
            raise RoomAudioStartRefused("invalid authority public key") from error

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
