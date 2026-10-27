"""Tests for publication into Meet's durable room-audio outbox."""

import json
import unittest
from hashlib import sha256
from unittest.mock import Mock, patch
from uuid import uuid4

from livekit import api

from room_audio_manifest import RoomAudioSourceBundle
from room_audio_source_publisher import (
    DurableRoomAudioSourcePublisher,
    RoomAudioSourcePublicationError,
    RoomAudioSourcePublisherConfig,
)

LIVEKIT_KEY = "callback-key"
LIVEKIT_SECRET = "callback-" + "secret-padded-for-livekit-tests"
WORKER_IDENTITY = "room-audio-worker-0123456789abcdef0123456789abcdef"


def _manifest():
    return {
        "audio_run_ref": "audio_run_fixture_12345678",
        "object_ref": f"room-audio/v1/{'a' * 64}.ogg",
    }


def _digest(manifest):
    canonical = (
        json.dumps(
            manifest,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        ).encode()
        + b"\n"
    )
    return sha256(canonical).hexdigest()


class _Content:
    def __init__(self, payload):
        self._payload = payload

    async def read(self, _limit):
        return self._payload


class _Response:
    def __init__(self, *, status, payload):
        self.status = status
        self.content = _Content(payload)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False


class _Session:
    def __init__(self, response):
        self._response = response
        self.post = Mock(return_value=response)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False


class DurableRoomAudioSourcePublisherTests(unittest.IsolatedAsyncioTestCase):
    """Keep the worker callback exact, bounded and fail-closed."""

    def setUp(self):
        """Build one exact callback boundary and source bundle."""
        self.manifest = _manifest()
        self.bundle = RoomAudioSourceBundle(manifest=self.manifest, gaps=())
        self.endpoint = (
            "http://meet-backend/internal/mastrao/room-audio/sources/complete/"
        )
        self.publisher = DurableRoomAudioSourcePublisher(
            config=RoomAudioSourcePublisherConfig(
                endpoint=self.endpoint,
                room_name="room-fixture",
                worker_identity=WORKER_IDENTITY,
                livekit_api_key=LIVEKIT_KEY,
                livekit_api_secret=LIVEKIT_SECRET,
            ),
            compact_start_effect="signed.start.effect",
        )

    async def test_publishes_exact_capability_manifest_and_gaps(self):
        """The callback carries the exact start capability and source bundle."""
        source_id = str(uuid4())
        payload = json.dumps(
            {
                "state": "delivered",
                "manifest_digest": _digest(self.manifest),
                "source_id": source_id,
                "replayed": False,
            }
        ).encode()
        session = _Session(_Response(status=200, payload=payload))

        with patch(
            "room_audio_source_publisher.aiohttp.ClientSession",
            return_value=session,
        ):
            await self.publisher.publish(self.bundle)

        kwargs = session.post.call_args.kwargs
        claims = api.TokenVerifier(LIVEKIT_KEY, LIVEKIT_SECRET).verify(
            kwargs["headers"]["Authorization"].removeprefix("Bearer ")
        )
        assert claims.identity == WORKER_IDENTITY
        assert claims.video.room == "room-fixture"
        assert claims.video.room_join is True
        assert claims.video.can_publish is False
        assert claims.video.can_publish_data is False
        assert claims.video.can_subscribe is False
        assert kwargs == {
            "json": {
                "room_audio_start_effect": "signed.start.effect",
                "source_manifest": self.manifest,
                "source_gaps": [],
            },
            "headers": kwargs["headers"],
            "allow_redirects": False,
        }

    async def test_refuses_success_with_wrong_manifest_digest(self):
        """A response for another manifest cannot acknowledge this source."""
        payload = json.dumps(
            {
                "state": "delivered",
                "manifest_digest": "0" * 64,
                "source_id": str(uuid4()),
                "replayed": False,
            }
        ).encode()
        session = _Session(_Response(status=200, payload=payload))

        with patch(
            "room_audio_source_publisher.aiohttp.ClientSession",
            return_value=session,
        ):
            with self.assertRaises(RoomAudioSourcePublicationError):
                await self.publisher.publish(self.bundle)

    async def test_refuses_non_success_status_without_leaking_body(self):
        """Private error bodies are reduced to one closed local exception."""
        session = _Session(_Response(status=503, payload=b'{"error":"private"}'))

        with patch(
            "room_audio_source_publisher.aiohttp.ClientSession",
            return_value=session,
        ):
            with self.assertRaisesRegex(
                RoomAudioSourcePublicationError,
                "was not accepted",
            ):
                await self.publisher.publish(self.bundle)

    def test_refuses_redirecting_or_public_endpoint_shapes(self):
        """Only the exact non-redirecting internal callback path is accepted."""
        invalid_endpoints = (
            "https://meet-backend/internal/mastrao/room-audio/sources/complete/",
            "http://attacker.example/internal/mastrao/room-audio/sources/complete/",
        )
        for invalid in invalid_endpoints:
            with self.subTest(endpoint=invalid):
                with self.assertRaisesRegex(ValueError, "endpoint is invalid"):
                    DurableRoomAudioSourcePublisher(
                        config=RoomAudioSourcePublisherConfig(
                            endpoint=invalid,
                            room_name="room-fixture",
                            worker_identity=WORKER_IDENTITY,
                            livekit_api_key=LIVEKIT_KEY,
                            livekit_api_secret=LIVEKIT_SECRET,
                        ),
                        compact_start_effect="signed.start.effect",
                    )


if __name__ == "__main__":
    unittest.main()
