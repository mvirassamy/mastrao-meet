"""Private gateway protocol, strict receipts and conservative lease evidence."""

import asyncio
import json
import os
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from aiohttp import web

from live_send_control import (
    ControlError,
    ControlResponseLost,
    ControlUnavailable,
    LiveControlClient,
    LiveSendAuthority,
)

SOURCE = "source_reference_0001"
ADMISSION = "admission_reference_0001"
ATTEMPT = "aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa"
WORKER = "bbbbbbbb-bbbb-4bbb-bbbb-bbbbbbbbbbbb"


class Gateway:
    """Expose protocol barriers, using the same exact response shape as Core."""

    def __init__(self):
        """Start with a new active source and no provider activity."""
        self.requests = []
        self.entered = {
            action: asyncio.Event()
            for action in ("prepare", "claim", "renew", "stopped")
        }
        self.release = {action: asyncio.Event() for action in self.entered}
        for gate in self.release.values():
            gate.set()
        self.responses = {}
        self.errors = {}
        self.before_stopped = None

    async def post(self, body):
        """Hold or corrupt individual actions without fabricating extra fields."""
        action = body["action"]
        self.requests.append(body.copy())
        self.entered[action].set()
        await self.release[action].wait()
        if action in self.errors:
            raise self.errors[action]
        if action == "prepare":
            result = {"version": 1, "source_ref": SOURCE, "admission_ref": ADMISSION}
        else:
            stopped = action == "stopped"
            result = {
                "version": 1,
                "source_ref": SOURCE,
                "admission_ref": ADMISSION,
                "attempt_ref": body["attempt_ref"],
                "worker_instance_ref": body["worker_instance_ref"],
                "action": action,
                "state": "stopped" if stopped else "active",
                "lease_expires_at": None if stopped else int(time.time()) + 12,
                "start_authorized": action == "claim",
                "send_authorized": not stopped,
                "replayed": False,
            }
        result.update(self.responses.get(action, {}))
        if action == "stopped" and self.before_stopped is not None:
            self.before_stopped()
        return result


def authority(gateway):
    """Build a worker epoch with only real RTC-shaped service identifiers."""
    return LiveSendAuthority(
        gateway,
        room_sid="RM_room",
        participant_sid="PA_participant",
        track_sid="TR_track",
        attempt_ref=ATTEMPT,
        worker_instance_ref=WORKER,
    )


class AuthorityTests(unittest.IsolatedAsyncioTestCase):
    """Reject replays, inconsistent booleans and stale/unbounded authority."""

    async def test_only_preparation_retries_missing_webhook_with_identical_binding(
        self,
    ):
        """A delayed authenticated webhook is tolerated before any provider claim."""
        gateway = Gateway()
        lease = authority(gateway)
        original = gateway.post
        preparations = 0

        async def delayed_webhook(body):
            nonlocal preparations
            if body["action"] == "prepare":
                preparations += 1
                if preparations == 1:
                    gateway.requests.append(body.copy())
                    raise ControlUnavailable("synthetic webhook not persisted")
            return await original(body)

        gateway.post = delayed_webhook
        with patch("live_send_control.PREPARE_RETRY_SECONDS", 0):
            self.assertTrue(await lease.claim())
        self.assertEqual(
            [body["action"] for body in gateway.requests],
            ["prepare", "prepare", "claim"],
        )
        self.assertEqual(gateway.requests[0], gateway.requests[1])

    async def test_preparation_exhaustion_and_deadline_never_send_a_claim(self):
        """Unavailable consent/RTC facts cannot loop indefinitely or mint an owner."""
        gateway = Gateway()
        gateway.errors["prepare"] = ControlUnavailable("synthetic unavailable")
        lease = authority(gateway)
        with patch("live_send_control.PREPARE_RETRY_SECONDS", 0):
            with self.assertRaises(ControlUnavailable):
                await lease.claim()
        self.assertEqual([body["action"] for body in gateway.requests], ["prepare"] * 3)
        self.assertFalse(lease.claim_sent)
        self.assertFalse(lease.can_send())
        gateway = Gateway()
        gateway.release["prepare"].clear()
        lease = authority(gateway)
        with patch("live_send_control.PREPARE_TIMEOUT_SECONDS", 0.01):
            with self.assertRaises(ControlUnavailable):
                await lease.claim()
        self.assertEqual(len(gateway.requests), 1)
        self.assertFalse(lease.claim_sent)

    async def test_non_temporary_prepare_errors_and_lost_claim_are_not_retried(self):
        """Only the preparatory journal may retry, never an issued claim."""
        for action, error in (
            ("prepare", ControlError("synthetic forbidden")),
            ("claim", ControlUnavailable("synthetic ambiguous claim")),
        ):
            gateway = Gateway()
            gateway.errors[action] = error
            lease = authority(gateway)
            with self.subTest(action=action):
                with self.assertRaises(ControlError):
                    await lease.claim()
                self.assertFalse(await lease.claim())
                self.assertEqual(
                    [body["action"] for body in gateway.requests].count(action), 1
                )

    async def test_exact_protocol_and_stable_attempt_for_every_action(self):
        """Prepare omits admission; subsequent actions preserve every binding."""
        gateway = Gateway()
        lease = authority(gateway)
        self.assertTrue(await lease.claim())
        self.assertFalse(await lease.claim())
        await lease._send("renew")
        lease.fence()
        await lease.stopped()
        self.assertEqual(
            [body["action"] for body in gateway.requests],
            ["prepare", "claim", "renew", "stopped"],
        )
        base = {
            "version",
            "action",
            "room_sid",
            "participant_sid",
            "track_sid",
            "attempt_ref",
            "worker_instance_ref",
        }
        for body in gateway.requests:
            expected = base
            if body["action"] != "prepare":
                expected = base | {"source_ref", "admission_ref"}
            self.assertEqual(set(body), expected)
            self.assertEqual(body["attempt_ref"], ATTEMPT)
            self.assertEqual(body["worker_instance_ref"], WORKER)
        self.assertFalse(lease.can_send())

    async def test_concurrent_claim_does_not_prepare_or_claim_twice(self):
        """Seal the local attempt before the first prepare await."""
        gateway = Gateway()
        gateway.release["prepare"].clear()
        lease = authority(gateway)
        first = asyncio.create_task(lease.claim())
        await gateway.entered["prepare"].wait()
        self.assertFalse(await lease.claim())
        gateway.release["prepare"].set()
        self.assertTrue(await first)
        self.assertEqual(len(gateway.requests), 2)

    async def test_replay_is_not_start_authority(self):
        """A committed claim whose first response was lost cannot open again."""
        gateway = Gateway()
        gateway.responses["claim"] = {"start_authorized": False, "replayed": True}
        lease = authority(gateway)
        self.assertFalse(await lease.claim())
        self.assertFalse(await lease.claim())
        self.assertEqual(len(gateway.requests), 2)

    async def test_invalid_receipts_never_grant(self):
        """Strict shape, binding, states, primitive types and deadline all matter."""
        invalid = [
            {"version": True},
            {"unexpected": 1},
            {"source_ref": "different_reference_001"},
            {"admission_ref": "different_reference_001"},
            {"attempt_ref": WORKER},
            {"worker_instance_ref": ATTEMPT},
            {"action": "renew"},
            {"state": "unknown"},
            {"send_authorized": 1},
            {"start_authorized": 1},
            {"replayed": 0},
            {"replayed": True},
            {"lease_expires_at": True},
            {"lease_expires_at": 1.5},
            {"lease_expires_at": None},
            {"lease_expires_at": int(time.time()) - 1},
            {"lease_expires_at": int(time.time()) + 30},
            {"state": "stop_requested"},
            {"state": "stopped"},
        ]
        for response in invalid:
            with self.subTest(response=response):
                gateway = Gateway()
                gateway.responses["claim"] = response
                lease = authority(gateway)
                with self.assertRaises(ControlError):
                    await lease.claim()
                self.assertFalse(lease.can_send())

    async def test_past_and_renewed_start_are_refused(self):
        """Renewal never opens a provider, even if its response claims otherwise."""
        gateway = Gateway()
        lease = authority(gateway)
        await lease.claim()
        gateway.responses["renew"] = {"start_authorized": True}
        with self.assertRaises(ControlError):
            await lease._send("renew")

    async def test_late_renew_cannot_remove_fence(self):
        """An in-flight successful renew cannot resurrect local capture."""
        gateway = Gateway()
        lease = authority(gateway)
        await lease.claim()
        gateway.release["renew"].clear()
        renew = asyncio.create_task(lease._send("renew"))
        await gateway.entered["renew"].wait()
        lease.fence()
        gateway.release["renew"].set()
        await renew
        self.assertFalse(lease.can_send())

    async def test_request_elapsed_is_subtracted_from_absolute_lease(self):
        """Slow responses and clock rollback cannot mint time on receipt."""
        gateway = Gateway()
        gateway.responses["claim"] = {"lease_expires_at": 1015}
        lease = authority(gateway)
        clock = SimpleNamespace(
            monotonic=Mock(side_effect=[50, 52, 52, 52]),
            time=Mock(side_effect=[1000, 1002]),
        )
        with patch("live_send_control.time", clock):
            self.assertTrue(await lease.claim())
        # 15 seconds absolute, minus 2 wall seconds and 2 request seconds.
        self.assertEqual(lease.deadline, 63)


class GatewayTransportTests(unittest.IsolatedAsyncioTestCase):
    """Exercise real aiohttp HTTP transport against ephemeral loopback fixtures."""

    async def asyncSetUp(self):
        """Keep all traffic inside a short-lived local fixture server."""
        self.observed = []
        self.status = 200
        self.raw = b'{"version":1}'
        self.wait = None
        self.redirect_hits = 0
        app = web.Application()
        app.router.add_post("/control", self.handle)
        app.router.add_post("/redirected", self.redirected)
        runner = web.AppRunner(app)
        await runner.setup()
        self.addAsyncCleanup(runner.cleanup)
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        self.url = f"http://127.0.0.1:{port}/control"
        self.client = LiveControlClient(self.url, ".payload:timestamp:signature")

    async def handle(self, request):
        """Inspect credentials only in fixture memory and return configured bytes."""
        self.observed.append(
            (request.headers.get("Authorization"), await request.json())
        )
        if self.wait is not None:
            await self.wait.wait()
        return web.Response(
            status=self.status, body=self.raw, headers={"Location": "/redirected"}
        )

    async def redirected(self, request):
        """Detect an accidental credential-bearing redirect."""
        self.redirect_hits += 1
        return web.json_response({"version": 1})

    def metadata(self, **changes):
        """Encode the dispatch metadata shape agreed with the Meet backend."""
        control = {"version": 1, "url": self.url, "capability": self.client.capability}
        control.update(changes)
        return json.dumps({"liveControl": control})

    async def test_transport_sends_exact_body_with_django_capability_and_no_env_proxy(
        self,
    ):
        """Accept colon-delimited signing.dumps tokens without user credentials."""
        with patch.dict(
            os.environ,
            {
                "LIVE_TRANSCRIPT_CONTROL_URL": self.url,
                "HTTP_PROXY": "http://127.0.0.1:1",
                "NO_PROXY": "",
            },
        ):
            client = LiveControlClient.from_metadata(self.metadata())
            self.assertEqual(await client.post({"version": 1}), {"version": 1})
        self.assertEqual(
            self.observed, [(f"Bearer {self.client.capability}", {"version": 1})]
        )
        self.assertNotIn(self.client.capability, repr(self.client))

    async def test_redirect_is_not_followed(self):
        """Refuse redirect responses without forwarding the scoped capability."""
        self.status = 307
        with self.assertRaises(ControlError):
            await self.client.post({"version": 1})
        self.assertEqual(self.redirect_hits, 0)
        self.assertEqual(len(self.observed), 1)

    async def test_explicit_http_refusal_is_not_a_transport_lost_response(self):
        """Do not classify an explicit gateway status as an ambiguous wire loss."""
        self.status = 503
        with self.assertRaises(ControlUnavailable) as refused:
            await self.client.post({"version": 1})
        self.assertNotIsInstance(refused.exception, ControlResponseLost)
        self.raw = b"invalid-json"
        self.status = 200
        with self.assertRaises(ControlError) as malformed:
            await self.client.post({"version": 1})
        self.assertNotIsInstance(malformed.exception, ControlResponseLost)

    async def test_body_and_transport_time_are_bounded(self):
        """Abort oversized bodies and held responses; neither request is retried."""
        self.raw = b"x" * 16_385
        with self.assertRaises(ControlError):
            await self.client.post({"version": 1})
        self.wait = asyncio.Event()
        try:
            with patch("live_send_control.REQUEST_TIMEOUT_SECONDS", 0.02):
                with self.assertRaises(ControlResponseLost):
                    await self.client.post({"version": 1})
        finally:
            self.wait.set()
        self.assertEqual(len(self.observed), 2)

    async def test_malformed_json_duplicate_fields_and_non_objects_are_rejected(self):
        """Reject ambiguous authority data rather than silently selecting a field."""
        for raw in (b"invalid", b"[]", b'{"version":1,"version":2}', b"\xff"):
            self.raw = raw
            with self.subTest(raw=raw):
                with self.assertRaises(ControlError):
                    await self.client.post({"version": 1})

    def test_absent_control_preserves_legacy_and_invalid_metadata_fails_closed(self):
        """Present but malformed control never silently selects the legacy path."""
        self.assertIsNone(LiveControlClient.from_metadata(""))
        self.assertIsNone(LiveControlClient.from_metadata('{"provider":"openai-live"}'))
        invalid = [
            "{",
            "[]",
            '{"liveControl":null}',
            self.metadata(version=True),
            self.metadata(capability="abc\r\nInjected: bearer"),
            self.metadata(extra=True),
        ]
        with patch.dict(os.environ, {"LIVE_TRANSCRIPT_CONTROL_URL": self.url}):
            for metadata in invalid:
                with self.subTest(metadata=metadata):
                    with self.assertRaises(ControlError):
                        LiveControlClient.from_metadata(metadata)

    def test_explicit_internal_compose_names_are_accepted_with_exact_env_match(self):
        """Support actual private deployment names without arbitrary hostname access."""
        for hostname in (
            "backend",
            "meet-backend",
            "localhost",
            "host.docker.internal",
            "meet.internal",
            "meet.ns.svc.cluster.local",
            "10.1.2.3",
            "[::1]",
            "[fd00::1]",
        ):
            url = (
                f"http://{hostname}:8000/internal/v1/meetings/transcripts/live/control"
            )
            with self.subTest(hostname=hostname):
                with patch.dict(os.environ, {"LIVE_TRANSCRIPT_CONTROL_URL": url}):
                    self.assertEqual(
                        LiveControlClient.from_metadata(self.metadata(url=url)).url, url
                    )

    def test_configured_endpoint_must_match_exact_internal_allowlist(self):
        """Metadata cannot redirect control to another port/path or public host."""
        forbidden = [
            "",
            "http://example.com/control",
            "https://example.com/control",
            "http://169.254.169.254/control",
            "http://0.0.0.0/control",
            "http://user:pass@127.0.0.1/control",
            self.url + "?token=oops",
            self.url + "#fragment",
        ]
        for url in forbidden:
            with self.subTest(url=url):
                with patch.dict(os.environ, {"LIVE_TRANSCRIPT_CONTROL_URL": url}):
                    with self.assertRaises(ControlError):
                        LiveControlClient.from_metadata(self.metadata(url=url))
        with patch.dict(os.environ, {"LIVE_TRANSCRIPT_CONTROL_URL": self.url}):
            with self.assertRaises(ControlError):
                LiveControlClient.from_metadata(self.metadata(url=self.url + "/other"))
