"""Tests for signed room-audio track authority."""

import base64
import json
import time
import unittest
from unittest.mock import Mock

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from livekit import rtc

from room_audio_authority import (
    AUTHORITY_TOPIC,
    JOSE_TYPE,
    RoomAudioAuthority,
    RoomAudioAuthorityConfig,
    RoomAudioAuthorityRefused,
)


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _canonical(value: dict) -> bytes:
    return (
        json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8") + b"\n"
    )


class RoomAudioAuthorityTests(unittest.TestCase):
    """Exercise exact transport, signature, binding and replay checks."""

    def setUp(self):
        """Create one worker binding and signing key per test."""
        self.now = int(time.time())
        self.private_key = Ed25519PrivateKey.generate()
        public = self.private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        config = RoomAudioAuthorityConfig(
            issuer="tenant-core",
            audience="meeting-room-audio-worker",
            key_id="room-audio-key-v1",
            public_jwk=json.dumps(
                {"kty": "OKP", "crv": "Ed25519", "x": _encode(public)}
            ),
            organization_external_id="org_legal",
            meeting_ref="meeting_legal",
            room_ref="room_legal",
            audio_run_ref="audio_run_legal",
            pipeline="post",
        )
        self.runtime = Mock()
        self.authority = RoomAudioAuthority(config=config, runtime=self.runtime)

    def test_applies_server_reliable_admission_once(self):
        """A current server packet admits once and is then replay-protected."""
        effect = self._effect()
        packet = self._packet(self._sign(effect))

        self.authority.consume(packet, observed_at=self.now)

        self.runtime.admit_track.assert_called_once_with(
            "TR_legaltrack",
            generation=1,
            expires_at_ms=(self.now + 20) * 1_000,
            authorization_digest="d" * 64,
        )
        with self.assertRaises(RoomAudioAuthorityRefused):
            self.authority.consume(packet, observed_at=self.now)

    def test_applies_revocation(self):
        """A signed revoke maps to the runtime's monotonic revocation path."""
        effect = self._effect(
            operation="revoke_meeting_room_audio_track",
            authorization_generation=2,
            jti="authority_revoke_1",
        )

        self.authority.consume(self._packet(self._sign(effect)), observed_at=self.now)

        self.runtime.revoke_track.assert_called_once_with(
            "TR_legaltrack",
            generation=2,
            authorization_digest="d" * 64,
        )

    def test_refuses_non_server_or_lossy_transport(self):
        """Only reliable packets emitted by LiveKit server APIs are accepted."""
        compact = self._sign(self._effect())
        for packet in (
            self._packet(compact, kind=rtc.DataPacketKind.KIND_LOSSY),
            self._packet(compact, participant=Mock()),
            self._packet(compact, topic="wrong"),
        ):
            with self.subTest(packet=packet):
                with self.assertRaises(RoomAudioAuthorityRefused):
                    self.authority.consume(packet, observed_at=self.now)
        self.runtime.admit_track.assert_not_called()

    def test_refuses_bad_signature_expiry_and_cross_room_binding(self):
        """Signature, time and exact run bindings all fail closed."""
        cases = (
            self._effect(room_ref="room_other"),
            self._effect(expires_at=self.now),
        )
        for effect in cases:
            with self.subTest(effect=effect):
                with self.assertRaises(RoomAudioAuthorityRefused):
                    self.authority.consume(
                        self._packet(self._sign(effect)), observed_at=self.now
                    )
        signed = self._sign(self._effect())
        protected, payload, signature = signed.split(".")
        replacement = "A" if signature[0] != "A" else "B"
        tampered = f"{protected}.{payload}.{replacement}{signature[1:]}"
        with self.assertRaises(RoomAudioAuthorityRefused):
            self.authority.consume(self._packet(tampered), observed_at=self.now)
        self.runtime.admit_track.assert_not_called()

    def _effect(self, **overrides):
        effect = {
            "version": 1,
            "type": "mastrao.core-meeting-room-audio-track-effect",
            "issuer": "tenant-core",
            "audience": "meeting-room-audio-worker",
            "operation": "admit_meeting_room_audio_track",
            "operation_version": 1,
            "organization_external_id": "org_legal",
            "meeting_ref": "meeting_legal",
            "room_ref": "room_legal",
            "audio_run_ref": "audio_run_legal",
            "policy_ref": "policy_legal",
            "policy_digest": "a" * 64,
            "notice_digest": "b" * 64,
            "provider_binding_digest": "c" * 64,
            "retention_expires_at": self.now + 3_600,
            "pipeline": "post",
            "authorization_generation": 1,
            "track_ref": "TR_legaltrack",
            "media_token_binding_ref": "media_binding_legal",
            "authorization_ref": "authorization_legal",
            "authorization_digest": "d" * 64,
            "issued_at": self.now,
            "expires_at": self.now + 20,
            "jti": "authority_admit_1",
        }
        effect.update(overrides)
        return effect

    def _sign(self, effect):
        header = _encode(
            _canonical({"alg": "EdDSA", "kid": "room-audio-key-v1", "typ": JOSE_TYPE})
        )
        payload = _encode(_canonical(effect))
        signature = self.private_key.sign(f"{header}.{payload}".encode("ascii"))
        return f"{header}.{payload}.{_encode(signature)}"

    @staticmethod
    def _packet(compact, **overrides):
        values = {
            "data": compact.encode("ascii"),
            "kind": rtc.DataPacketKind.KIND_RELIABLE,
            "participant": None,
            "topic": AUTHORITY_TOPIC,
        }
        values.update(overrides)
        return rtc.DataPacket(**values)


if __name__ == "__main__":
    unittest.main()
