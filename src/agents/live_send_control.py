"""Bounded worker access to the private Meet send-authority gateway."""

from __future__ import annotations

import asyncio
import ipaddress
import json
import math
import os
import re
import time
from dataclasses import dataclass, field
from http import HTTPStatus
from urllib.parse import urlsplit

import aiohttp

MAX_BODY_BYTES = 16_384
MAX_CAPABILITY_BYTES = 8192
REQUEST_TIMEOUT_SECONDS = 5
PREPARE_ATTEMPTS = 3
PREPARE_RETRY_SECONDS = 1
PREPARE_TIMEOUT_SECONDS = 12
LEASE_SECONDS = 15
RENEW_SECONDS = 5
_REFERENCE = re.compile(r"^[A-Za-z0-9_-]{16,160}$")
_RESPONSE_FIELDS = {
    "version",
    "source_ref",
    "admission_ref",
    "attempt_ref",
    "worker_instance_ref",
    "action",
    "state",
    "lease_expires_at",
    "start_authorized",
    "send_authorized",
    "replayed",
}


class ControlError(RuntimeError):
    """Reject control input without exposing capabilities or upstream bodies."""


class ControlUnavailable(ControlError):
    """Allow bounded preparation retries, never ambiguous claim retries."""


class ControlResponseLost(ControlUnavailable):
    """Retain an unknown command outcome after interrupted HTTP transport."""


def _reference(value: object) -> str:
    if not isinstance(value, str) or not _REFERENCE.fullmatch(value):
        raise ControlError("invalid control reference")
    return value


def _json_object(raw: str | bytes) -> dict[str, object]:
    def unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ControlError("duplicate control field")
            result[key] = value
        return result

    try:
        value = json.loads(raw, object_pairs_hook=unique_pairs)
    except (ValueError, UnicodeError, RecursionError) as error:
        raise ControlError("invalid control JSON") from error
    if not isinstance(value, dict):
        raise ControlError("control JSON must be an object")
    return value


def _internal_endpoint(url: str) -> bool:
    """Allow plain HTTP only for literal private IPs or explicit internal names."""
    try:
        parsed = urlsplit(url)
        hostname = parsed.hostname or ""
        port = parsed.port
    except ValueError:
        return False
    if (
        parsed.scheme not in {"http", "https"}
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or not parsed.path.startswith("/")
        or port == 0
        or any(character.isspace() for character in url)
    ):
        return False
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        if hostname in {"backend", "meet-backend", "localhost", "host.docker.internal"}:
            return True
        # Private names are explicit; arbitrary compose/public names are refused.
        return bool(
            re.fullmatch(r"[a-zA-Z0-9.-]+\.(?:internal|svc\.cluster\.local)", hostname)
        )
    return bool(
        address.is_loopback
        or address in ipaddress.ip_network("10.0.0.0/8")
        or address in ipaddress.ip_network("172.16.0.0/12")
        or address in ipaddress.ip_network("192.168.0.0/16")
        or address in ipaddress.ip_network("fc00::/7")
    )


@dataclass(frozen=True)
class LiveControlClient:
    """Use only the configured endpoint; never follow metadata URLs or redirects."""

    url: str
    capability: str = field(repr=False)

    @classmethod
    def from_metadata(cls, metadata: str) -> LiveControlClient | None:
        """Keep jobs without liveControl on the existing legacy path."""
        if not metadata:
            return None
        if not isinstance(metadata, str) or len(metadata.encode()) > MAX_BODY_BYTES:
            raise ControlError("invalid job metadata")
        job = _json_object(metadata)
        if "liveControl" not in job:
            return None
        value = job["liveControl"]
        if not isinstance(value, dict) or set(value) != {
            "version",
            "url",
            "capability",
        }:
            raise ControlError("invalid liveControl metadata")
        configured = os.getenv("LIVE_TRANSCRIPT_CONTROL_URL", "")
        if (
            type(value["version"]) is not int
            or value["version"] != 1
            or not isinstance(value["url"], str)
            or value["url"] != configured
            or not _internal_endpoint(configured)
        ):
            raise ControlError("live control endpoint is not configured or allowed")
        capability = value["capability"]
        if (
            not isinstance(capability, str)
            or not 1 <= len(capability) <= MAX_CAPABILITY_BYTES
            or not re.fullmatch(r"[A-Za-z0-9._~:-]+", capability)
        ):
            raise ControlError("invalid live control capability")
        return cls(configured, capability)

    async def post(self, body: dict[str, object]) -> dict[str, object]:
        """Send once, with bounded duration/bytes and no proxy or redirect reuse."""
        try:
            async with aiohttp.ClientSession(
                trust_env=False,
                timeout=aiohttp.ClientTimeout(
                    total=REQUEST_TIMEOUT_SECONDS,
                    ceil_threshold=REQUEST_TIMEOUT_SECONDS + 1,
                ),
                auto_decompress=False,
                read_bufsize=4096,
                max_line_size=4096,
                max_field_size=8192,
                cookie_jar=aiohttp.DummyCookieJar(),
            ) as session:
                async with session.post(
                    self.url,
                    json=body,
                    headers={
                        "Authorization": f"Bearer {self.capability}",
                        "Accept-Encoding": "identity",
                    },
                    allow_redirects=False,
                ) as response:
                    if response.status in {
                        HTTPStatus.NOT_FOUND,
                        HTTPStatus.BAD_GATEWAY,
                        HTTPStatus.SERVICE_UNAVAILABLE,
                        HTTPStatus.GATEWAY_TIMEOUT,
                    }:
                        raise ControlUnavailable("live control temporarily unavailable")
                    if response.status != HTTPStatus.OK:
                        raise ControlError("live control request refused")
                    if (
                        response.headers.get("Content-Encoding", "identity")
                        != "identity"
                    ):
                        raise ControlError("compressed control response refused")
                    raw = bytearray()
                    async for chunk in response.content.iter_chunked(4096):
                        raw.extend(chunk)
                        if len(raw) > MAX_BODY_BYTES:
                            raise ControlError("live control response too large")
                    return _json_object(bytes(raw))
        except (aiohttp.ClientError, TimeoutError) as error:
            raise ControlResponseLost("live control transport failed") from error


class LiveSendAuthority:
    """Retain one immutable RTC attempt and a conservative continuation deadline."""

    def __init__(  # noqa: PLR0913 - immutable RTC/worker protocol binding
        self,
        client: LiveControlClient,
        *,
        room_sid: str,
        participant_sid: str,
        track_sid: str,
        attempt_ref: str,
        worker_instance_ref: str,
    ):
        """Create an epoch before contacting Meet; the caller retains its tombstone."""
        self.client = client
        self.binding = {
            "room_sid": room_sid,
            "participant_sid": participant_sid,
            "track_sid": track_sid,
            "attempt_ref": attempt_ref,
            "worker_instance_ref": worker_instance_ref,
        }
        for key, prefix in (
            ("room_sid", "RM"),
            ("participant_sid", "PA"),
            ("track_sid", "TR"),
        ):
            if not re.fullmatch(rf"{prefix}_[A-Za-z0-9_-]{{1,96}}", self.binding[key]):
                raise ControlError("invalid RTC identifier")
        _reference(attempt_ref)
        _reference(worker_instance_ref)
        self.source: dict[str, str] = {}
        self.deadline = 0.0
        self.claim_sent = False
        self._claim_can_ack_stop = False
        self._preparing = False
        self.fenced = False

    def can_send(self) -> bool:
        """Check on every frame and immediately before a provider connection."""
        return not self.fenced and time.monotonic() < self.deadline

    def fence(self) -> None:
        """Permanently revoke local admission, including a late renew response."""
        self.fenced = True

    def _body(self, action: str) -> dict[str, object]:
        return {"version": 1, "action": action, **self.binding, **self.source}

    async def claim(self) -> bool:
        """Prepare and make one claim; ambiguous/replayed starts are never retried."""
        if self._preparing or self.claim_sent or self.fenced:
            return False
        self._preparing = True
        prepared = await self._prepare()
        if prepared is None:
            return False
        if (
            set(prepared) != {"version", "source_ref", "admission_ref"}
            or type(prepared["version"]) is not int
            or prepared["version"] != 1
        ):
            raise ControlError("invalid prepare response")
        self.source = {
            "source_ref": _reference(prepared["source_ref"]),
            "admission_ref": _reference(prepared["admission_ref"]),
        }
        if self.fenced:
            return False
        self.claim_sent = True
        try:
            response = await self._send("claim")
        except (ControlResponseLost, TimeoutError, asyncio.CancelledError):
            # The exact attempt may have committed. Cleanup may ACK its owner,
            # but an ambiguous claim must never be retried or open a provider.
            self._claim_can_ack_stop = True
            raise
        # A received incoherent body/binding/lease raises before this point:
        # merely sending a claim cannot justify an ACK from run-finally.
        self._claim_can_ack_stop = True
        return response["start_authorized"] is True and self.can_send()

    async def _prepare(self) -> dict[str, object] | None:
        """Wait briefly for persisted RTC facts/consent, with one stable attempt."""
        try:
            async with asyncio.timeout(PREPARE_TIMEOUT_SECONDS):
                for index in range(PREPARE_ATTEMPTS):
                    if self.fenced:
                        return None
                    try:
                        return await self.client.post(self._body("prepare"))
                    except ControlUnavailable:
                        if index + 1 == PREPARE_ATTEMPTS:
                            raise
                    await asyncio.sleep(PREPARE_RETRY_SECONDS)
        except TimeoutError as error:
            raise ControlUnavailable("live preparation window expired") from error
        return None

    async def _send(self, action: str) -> dict[str, object]:
        started = time.monotonic()
        wall_started = time.time()
        response = await self.client.post(self._body(action))
        elapsed = time.monotonic() - started
        self._validate_response(response, action)
        if response["send_authorized"]:
            expires = response["lease_expires_at"]
            if type(expires) is not int or not 0 < expires <= 2**53 - 1:
                raise ControlError("invalid lease expiry")
            # Subtract the entire request time. Do not mint 15 seconds on receipt,
            # or extend a lease because the local wall clock moved backwards.
            remaining = expires - max(wall_started, time.time()) - elapsed
            if not math.isfinite(remaining) or not 0 < remaining <= LEASE_SECONDS:
                raise ControlError("expired or inconsistent lease")
            if not self.fenced:
                self.deadline = time.monotonic() + remaining
        else:
            self.fence()
        return response

    def _validate_response(self, response: dict[str, object], action: str) -> None:
        expected = {
            **self.source,
            "attempt_ref": self.binding["attempt_ref"],
            "worker_instance_ref": self.binding["worker_instance_ref"],
            "action": action,
        }
        if (
            set(response) != _RESPONSE_FIELDS
            or type(response["version"]) is not int
            or response["version"] != 1
            or any(response[key] != value for key, value in expected.items())
            or any(
                type(response[key]) is not bool
                for key in ("start_authorized", "send_authorized", "replayed")
            )
        ):
            raise ControlError("invalid send response binding")
        active = response["state"] == "active"
        if response["state"] not in {"active", "stop_requested", "stopped"}:
            raise ControlError("invalid send response state")
        if active:
            if action == "stopped" or not response["send_authorized"]:
                raise ControlError("inconsistent active response")
        elif (
            response["lease_expires_at"] is not None
            or response["send_authorized"]
            or response["start_authorized"]
        ):
            raise ControlError("inconsistent stopped response")
        if action == "stopped" and response["state"] != "stopped":
            raise ControlError("stop acknowledgement refused")
        if response["start_authorized"] and (action != "claim" or response["replayed"]):
            raise ControlError("replayed or renewed start is forbidden")

    async def renew_until_fenced(self) -> None:
        """Stop on any refusal, loss or malformed response; never reopen a source."""
        while self.can_send():
            await asyncio.sleep(RENEW_SECONDS)
            if not self.can_send():
                return
            await self._send("renew")

    async def watch_expiry(self) -> None:
        """Observe the monotonic deadline independently of control I/O."""
        while self.can_send():
            await asyncio.sleep(max(0, self.deadline - time.monotonic()))
        self.fence()

    async def stopped(self) -> None:
        """Acknowledge only after the caller completed local close and delivery."""
        self.fence()
        if self._claim_can_ack_stop:
            await self._send("stopped")
