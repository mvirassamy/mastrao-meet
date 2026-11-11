"""Publish a completed room-audio bundle to Meet's durable outbox."""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from datetime import timedelta
from hashlib import sha256
from urllib.parse import urlparse
from uuid import UUID

import aiohttp
from livekit import api

from room_audio_manifest import RoomAudioSourceBundle

CALLBACK_PATH = "/internal/mastrao/room-audio/sources/complete/"
CALLBACK_HOSTS = {
    "127.0.0.1",
    "127.0.0.1.nip.io",
    "::1",
    "host.docker.internal",
    "localhost",
    "meet-api",
    "meet-backend",
}
MAX_RESPONSE_BYTES = 16_384
MAX_TIMEOUT_SECONDS = 30
HTTP_OK = 200
HTTP_SERVER_ERROR_MIN = 500
HTTP_SERVER_ERROR_MAX = 599
RETRYABLE_HTTP_STATUSES = frozenset({408, 429})
RETRY_DELAYS_SECONDS = (0.25, 1.0)
WORKER_IDENTITY = re.compile(r"^room-audio-worker-(?:post|live)-[a-f0-9]{32}$")


class RoomAudioSourcePublicationError(RuntimeError):
    """One source bundle was not durably accepted by Meet."""


class _RetryablePublicationError(RuntimeError):
    """One transient callback attempt failed without exposing response data."""


@dataclass(frozen=True)
class RoomAudioSourcePublisherConfig:
    """Trusted callback and LiveKit signer configuration."""

    endpoint: str
    room_name: str
    worker_identity: str
    livekit_api_key: str
    livekit_api_secret: str
    timeout_seconds: float = 5.0


class DurableRoomAudioSourcePublisher:
    """Submit one exact bundle using its persisted Core start capability."""

    def __init__(
        self,
        *,
        config: RoomAudioSourcePublisherConfig,
        compact_start_effect: str,
    ):
        """Bind publication to one trusted internal endpoint and start effect."""
        self._endpoint = _validate_endpoint(config.endpoint)
        if not isinstance(compact_start_effect, str) or not compact_start_effect:
            raise ValueError("compact_start_effect is required")
        if not 0 < config.timeout_seconds <= MAX_TIMEOUT_SECONDS:
            raise ValueError("timeout_seconds is invalid")
        if not config.room_name or not WORKER_IDENTITY.fullmatch(
            config.worker_identity
        ):
            raise ValueError("room audio worker binding is invalid")
        if not config.livekit_api_key or not config.livekit_api_secret:
            raise ValueError("LiveKit callback signer is required")
        self._compact_start_effect = compact_start_effect
        self._config = config
        self._timeout = aiohttp.ClientTimeout(total=config.timeout_seconds)

    async def publish(self, bundle: RoomAudioSourceBundle) -> None:
        """Wait until Meet and Tenant Core durably accept the exact source."""
        body = {
            "room_audio_start_effect": self._compact_start_effect,
            "source_manifest": bundle.manifest,
            "source_gaps": list(bundle.gaps),
        }
        try:
            async with aiohttp.ClientSession(timeout=self._timeout) as session:
                await self._publish_with_retries(session, body, bundle)
        except RoomAudioSourcePublicationError:
            raise
        except (aiohttp.ClientError, TimeoutError) as error:
            raise RoomAudioSourcePublicationError(
                "room audio source publication unavailable"
            ) from error

    async def _publish_with_retries(self, session, body, bundle) -> None:
        attempt_count = len(RETRY_DELAYS_SECONDS) + 1
        for attempt in range(attempt_count):
            try:
                await self._publish_once(session, body, bundle)
                return
            except (
                _RetryablePublicationError,
                aiohttp.ClientError,
                TimeoutError,
            ) as error:
                if attempt == attempt_count - 1:
                    raise RoomAudioSourcePublicationError(
                        "room audio source publication unavailable"
                    ) from error
                await asyncio.sleep(RETRY_DELAYS_SECONDS[attempt])

    async def _publish_once(self, session, body, bundle) -> None:
        authorization = self._authorization_token()
        async with session.post(
            self._endpoint,
            json=body,
            headers={
                "Authorization": f"Bearer {authorization}",
                "X-Forwarded-Proto": "https",
            },
            allow_redirects=False,
        ) as response:
            raw = await response.content.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                raise RoomAudioSourcePublicationError(
                    "room audio source response exceeded limit"
                )
            if _is_retryable_status(response.status):
                raise _RetryablePublicationError("transient callback refusal")
            if response.status != HTTP_OK:
                raise RoomAudioSourcePublicationError(
                    "room audio source was not accepted"
                )
            try:
                payload = json.loads(raw)
            except (UnicodeError, ValueError) as error:
                raise RoomAudioSourcePublicationError(
                    "room audio source was not accepted"
                ) from error
            if not _is_success(payload, bundle):
                raise RoomAudioSourcePublicationError(
                    "room audio source was not accepted"
                )

    def _authorization_token(self) -> str:
        grants = api.VideoGrants(
            room=self._config.room_name,
            room_join=True,
            can_publish=False,
            can_publish_data=False,
            can_subscribe=False,
        )
        return (
            api.AccessToken(
                self._config.livekit_api_key,
                self._config.livekit_api_secret,
            )
            .with_identity(self._config.worker_identity)
            .with_grants(grants)
            .with_ttl(timedelta(seconds=MAX_TIMEOUT_SECONDS))
            .to_jwt()
        )


def _validate_endpoint(value: str) -> str:
    endpoint = urlparse(value)
    if (
        endpoint.scheme != "http"
        or endpoint.hostname not in CALLBACK_HOSTS
        or endpoint.path != CALLBACK_PATH
        or endpoint.username
        or endpoint.password
        or endpoint.query
        or endpoint.fragment
    ):
        raise ValueError("room audio source endpoint is invalid")
    return value


def _is_retryable_status(status: int) -> bool:
    return (
        status in RETRYABLE_HTTP_STATUSES
        or HTTP_SERVER_ERROR_MIN <= status <= HTTP_SERVER_ERROR_MAX
    )


def _is_success(payload, bundle: RoomAudioSourceBundle) -> bool:
    canonical_manifest = (
        json.dumps(
            bundle.manifest,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        ).encode("utf-8")
        + b"\n"
    )
    expected_digest = sha256(canonical_manifest).hexdigest()
    try:
        source_id = (
            UUID(payload.get("source_id", "")) if isinstance(payload, dict) else None
        )
    except (AttributeError, TypeError, ValueError):
        return False
    return (
        isinstance(payload, dict)
        and set(payload) == {"state", "manifest_digest", "source_id", "replayed"}
        and payload["state"] == "delivered"
        and payload["manifest_digest"] == expected_digest
        and str(source_id) == payload["source_id"]
        and isinstance(payload["replayed"], bool)
    )
