"""Tests for fail-closed room-audio job admission."""

import base64
import json
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from livekit import rtc

from room_audio_job import handle_room_audio_job_request
from room_audio_start_authority import (
    JOSE_TYPE,
    LIVE_SOURCE_PROFILE,
    POST_SOURCE_PROFILE,
    room_audio_worker_identity,
)


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _canonical(value: dict) -> bytes:
    encoded = json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return encoded + b"\n"


class RoomAudioJobTests(unittest.IsolatedAsyncioTestCase):
    """Exercise exact identity, duplicate and signature refusal boundaries."""

    def setUp(self):
        """Create one signed post-run effect and isolated worker environment."""
        self.now = int(time.time())
        self.private_key = Ed25519PrivateKey.generate()
        public = self.private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        self.environment = {
            "ROOM_AUDIO_EFFECT_ISSUER": "tenant-core",
            "ROOM_AUDIO_EFFECT_AUDIENCE": "meeting-room-audio-worker",
            "ROOM_AUDIO_EFFECT_KEY_ID": "room-audio-key-v1",
            "ROOM_AUDIO_EFFECT_PUBLIC_JWK": json.dumps(
                {"kty": "OKP", "crv": "Ed25519", "x": _encode(public)}
            ),
        }

    async def test_accepts_exact_effect_with_deterministic_identity(self):
        """A current signed effect produces the backend-compatible identity."""
        request = self._request(self._sign(self._effect()))
        livekit = self._livekit(participants=[])
        identity = room_audio_worker_identity("audio_run_fixture_12345678")
        self.assertEqual(
            identity,
            "room-audio-worker-9e24e32762536cd7f33e7b1d32c5a4a8",
        )

        with patch.dict("os.environ", self.environment, clear=True):
            with patch("room_audio_job.api.LiveKitAPI", return_value=livekit):
                await handle_room_audio_job_request(request)

        request.accept.assert_awaited_once_with(
            identity=identity,
            metadata=request.job.metadata,
        )
        request.reject.assert_not_awaited()

    async def test_rejects_duplicate_exact_worker(self):
        """A retry cannot admit a second worker for the same audio run."""
        request = self._request(self._sign(self._effect()))
        identity = room_audio_worker_identity("audio_run_fixture_12345678")
        participant = SimpleNamespace(
            kind=rtc.ParticipantKind.PARTICIPANT_KIND_AGENT,
            identity=identity,
        )
        livekit = self._livekit(participants=[participant])

        with patch.dict("os.environ", self.environment, clear=True):
            with patch("room_audio_job.api.LiveKitAPI", return_value=livekit):
                await handle_room_audio_job_request(request)

        request.reject.assert_awaited_once_with()
        request.accept.assert_not_awaited()

    async def test_rejects_live_pipeline_until_its_worker_is_composed(self):
        """The post-only executable cannot silently accept a live run."""
        request = self._request(
            self._sign(
                self._effect(
                    pipeline="live",
                    source_profile=LIVE_SOURCE_PROFILE,
                )
            )
        )

        with patch.dict("os.environ", self.environment, clear=True):
            await handle_room_audio_job_request(request)

        request.reject.assert_awaited_once_with()
        request.accept.assert_not_awaited()

    async def test_rejects_cross_room_and_missing_key(self):
        """Room mismatch and incomplete trust configuration both fail closed."""
        cases = (
            (self.environment, self._effect(room_ref="room_other")),
            ({}, self._effect()),
        )
        for environment, effect in cases:
            with self.subTest(environment=environment, effect=effect):
                request = self._request(self._sign(effect))
                with patch.dict("os.environ", environment, clear=True):
                    await handle_room_audio_job_request(request)
                request.reject.assert_awaited_once_with()
                request.accept.assert_not_awaited()

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
            "audio_run_ref": "audio_run_fixture_12345678",
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

    @staticmethod
    def _request(metadata):
        return SimpleNamespace(
            room=SimpleNamespace(name="room_legal"),
            job=SimpleNamespace(metadata=metadata),
            accept=AsyncMock(),
            reject=AsyncMock(),
        )

    @staticmethod
    def _livekit(*, participants):
        livekit = AsyncMock()
        livekit.__aenter__.return_value = livekit
        livekit.__aexit__.return_value = None
        livekit.room.list_participants.return_value = SimpleNamespace(
            participants=participants
        )
        return livekit


if __name__ == "__main__":
    unittest.main()
