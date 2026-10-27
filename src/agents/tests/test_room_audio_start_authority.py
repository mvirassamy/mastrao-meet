"""Tests for signed room-audio run starts."""

import base64
import json
import time
import unittest

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from room_audio_start_authority import (
    JOSE_TYPE,
    LIVE_SOURCE_PROFILE,
    POST_SOURCE_PROFILE,
    RoomAudioStartRefused,
    RoomAudioStartVerifier,
    RoomAudioStartVerifierConfig,
)


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _canonical(value: dict) -> bytes:
    return (
        json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8") + b"\n"
    )


class RoomAudioStartVerifierTests(unittest.TestCase):
    """Exercise signature, room, pipeline and lifetime refusal boundaries."""

    def setUp(self):
        """Create one exact room binding and signing key per test."""
        self.now = int(time.time())
        self.private_key = Ed25519PrivateKey.generate()
        public = self.private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        self.verifier = RoomAudioStartVerifier(
            RoomAudioStartVerifierConfig(
                issuer="tenant-core",
                audience="meeting-room-audio-worker",
                key_id="room-audio-key-v1",
                public_jwk=json.dumps(
                    {"kty": "OKP", "crv": "Ed25519", "x": _encode(public)}
                ),
                room_name="room_legal",
            )
        )

    def test_accepts_exact_post_start(self):
        """An exact post pipeline effect authorizes one bounded room run."""
        effect = self._effect()

        verified = self.verifier.verify(self._sign(effect), observed_at=self.now)

        self.assertEqual(verified, effect)

    def test_accepts_exact_live_start(self):
        """The live pipeline requires its exact 24 kHz mono PCM profile."""
        effect = self._effect(pipeline="live", source_profile=LIVE_SOURCE_PROFILE)

        verified = self.verifier.verify(self._sign(effect), observed_at=self.now)

        self.assertEqual(verified, effect)

    def test_refuses_cross_room_profile_expiry_and_unbounded_duration(self):
        """A worker never starts from ambiguous or unsafe signed metadata."""
        cases = (
            self._effect(room_ref="room_other"),
            self._effect(source_profile=LIVE_SOURCE_PROFILE),
            self._effect(expires_at=self.now),
            self._effect(maximum_duration_seconds=14_401),
            self._effect(empty_room_grace_seconds=301),
        )
        for effect in cases:
            with self.subTest(effect=effect):
                with self.assertRaises(RoomAudioStartRefused):
                    self.verifier.verify(self._sign(effect), observed_at=self.now)

    def test_refuses_tampered_signature_and_extra_fields(self):
        """Signature and exact-schema checks fail closed before job acceptance."""
        signed = self._sign(self._effect())
        protected, payload, signature = signed.split(".")
        replacement = "A" if signature[0] != "A" else "B"
        with self.assertRaises(RoomAudioStartRefused):
            self.verifier.verify(
                f"{protected}.{payload}.{replacement}{signature[1:]}",
                observed_at=self.now,
            )
        with self.assertRaises(RoomAudioStartRefused):
            self.verifier.verify(
                self._sign(self._effect(untrusted=True)), observed_at=self.now
            )

    def _effect(self, **overrides):
        effect = {
            "version": 1,
            "type": "mastrao.core-meeting-room-audio-start-effect",
            "issuer": "tenant-core",
            "audience": "meeting-room-audio-worker",
            "operation": "start_meeting_room_audio_run",
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
            "source_profile": POST_SOURCE_PROFILE,
            "maximum_duration_seconds": 600,
            "empty_room_grace_seconds": 30,
            "issued_at": self.now,
            "expires_at": self.now + 20,
            "jti": "room_audio_start_1",
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


if __name__ == "__main__":
    unittest.main()
