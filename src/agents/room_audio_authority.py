"""Verify and apply signed Tenant Core room-audio track authority."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass

from livekit import rtc

from room_audio_jws import (
    MAX_COMPACT_JWS_BYTES,
    RoomAudioJwsConfig,
    RoomAudioJwsRefused,
    RoomAudioJwsVerifier,
)
from room_audio_runtime import RoomAudioRun

AUTHORITY_TOPIC = "mastrao.room-audio.track-authority.v1"
JOSE_TYPE = "mastrao-meeting-room-audio-track-effect+jws"
MAX_EFFECT_LIFETIME_SECONDS = 30
REFERENCE = re.compile(r"^[A-Za-z0-9._:-]{1,200}$")
TRACK_REFERENCE = re.compile(r"^[A-Za-z0-9_-]{8,200}$")
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
    "authorization_generation",
    "track_ref",
    "media_token_binding_ref",
    "authorization_ref",
    "authorization_digest",
    "issued_at",
    "expires_at",
    "jti",
}


class RoomAudioAuthorityRefused(ValueError):
    """Reject authority that is not exact, current and bound to this run."""


@dataclass(frozen=True)
class RoomAudioAuthorityConfig:
    """Immutable worker bindings for one room-audio run."""

    issuer: str
    audience: str
    key_id: str
    public_jwk: str
    organization_external_id: str
    meeting_ref: str
    room_ref: str
    audio_run_ref: str
    pipeline: str


class RoomAudioAuthority:
    """Consume server-originated reliable Core effects exactly once."""

    def __init__(self, *, config: RoomAudioAuthorityConfig, runtime: RoomAudioRun):
        """Bind the verifier to one exact room run and public signing key."""
        self._config = config
        self._runtime = runtime
        try:
            self._jws = RoomAudioJwsVerifier(
                RoomAudioJwsConfig(
                    key_id=config.key_id,
                    public_jwk=config.public_jwk,
                )
            )
        except RoomAudioJwsRefused as error:
            raise RoomAudioAuthorityRefused(str(error)) from error
        self._seen_jti: set[str] = set()

    def consume(
        self, packet: rtc.DataPacket, *, observed_at: int | None = None
    ) -> None:
        """Verify one LiveKit packet and apply its exact track transition."""
        if packet.topic != AUTHORITY_TOPIC:
            raise RoomAudioAuthorityRefused("unexpected authority topic")
        if packet.kind != rtc.DataPacketKind.KIND_RELIABLE:
            raise RoomAudioAuthorityRefused("authority transport is not reliable")
        if packet.participant is not None:
            raise RoomAudioAuthorityRefused("authority is participant-originated")
        if len(packet.data) > MAX_COMPACT_JWS_BYTES:
            raise RoomAudioAuthorityRefused("authority payload exceeds size bound")
        try:
            compact = packet.data.decode("ascii")
        except UnicodeDecodeError as error:
            raise RoomAudioAuthorityRefused("authority is not ASCII JWS") from error
        effect = self._verify(compact, observed_at=observed_at)
        jti = effect["jti"]
        if jti in self._seen_jti:
            raise RoomAudioAuthorityRefused("authority replayed")
        self._apply(effect)
        self._seen_jti.add(jti)

    def _verify(self, compact: str, *, observed_at: int | None) -> dict:
        try:
            effect = self._jws.verify(compact, jose_type=JOSE_TYPE, fields=FIELDS)
        except RoomAudioJwsRefused as error:
            raise RoomAudioAuthorityRefused(str(error)) from error
        self._validate(effect, observed_at=observed_at)
        return effect

    def _validate(self, effect: dict, *, observed_at: int | None) -> None:
        now = int(time.time()) if observed_at is None else observed_at
        issued_at = effect["issued_at"]
        expires_at = effect["expires_at"]
        generation = effect["authorization_generation"]
        if (
            effect["version"] != 1
            or effect["type"] != "mastrao.core-meeting-room-audio-track-effect"
            or effect["operation_version"] != 1
            or effect["operation"]
            not in {
                "admit_meeting_room_audio_track",
                "revoke_meeting_room_audio_track",
            }
            or effect["issuer"] != self._config.issuer
            or effect["audience"] != self._config.audience
            or effect["organization_external_id"]
            != self._config.organization_external_id
            or effect["meeting_ref"] != self._config.meeting_ref
            or effect["room_ref"] != self._config.room_ref
            or effect["audio_run_ref"] != self._config.audio_run_ref
            or effect["pipeline"] != self._config.pipeline
            or type(generation) is not int
            or generation <= 0
            or type(issued_at) is not int
            or type(expires_at) is not int
            or issued_at > now
            or expires_at <= now
            or not 1 <= expires_at - issued_at <= MAX_EFFECT_LIFETIME_SECONDS
            or type(effect["retention_expires_at"]) is not int
            or effect["retention_expires_at"] <= expires_at
        ):
            raise RoomAudioAuthorityRefused("authority binding refused")
        for name in (
            "issuer",
            "audience",
            "organization_external_id",
            "meeting_ref",
            "room_ref",
            "audio_run_ref",
            "policy_ref",
            "media_token_binding_ref",
            "authorization_ref",
            "jti",
        ):
            if not isinstance(effect[name], str) or not REFERENCE.fullmatch(
                effect[name]
            ):
                raise RoomAudioAuthorityRefused(f"invalid {name}")
        if not TRACK_REFERENCE.fullmatch(effect["track_ref"]):
            raise RoomAudioAuthorityRefused("invalid track_ref")
        for name in (
            "policy_digest",
            "notice_digest",
            "provider_binding_digest",
            "authorization_digest",
        ):
            if not isinstance(effect[name], str) or not SHA256.fullmatch(effect[name]):
                raise RoomAudioAuthorityRefused(f"invalid {name}")

    def _apply(self, effect: dict) -> None:
        arguments = {
            "generation": effect["authorization_generation"],
            "authorization_digest": effect["authorization_digest"],
        }
        if effect["operation"] == "admit_meeting_room_audio_track":
            self._runtime.admit_track(
                effect["track_ref"],
                expires_at_ms=effect["expires_at"] * 1_000,
                **arguments,
            )
            return
        self._runtime.revoke_track(effect["track_ref"], **arguments)
