"""Strict worker v2 leg receipts, serialized commands and terminal ambiguity."""

import asyncio
import time
import unittest

from test_live_send_control import LEG, Gateway, authority

from live_send_control import CONTROL_VERSION, ControlError, ControlResponseLost

NEXT = "next_leg_reference_0001"


class RotationAuthorityTests(unittest.IsolatedAsyncioTestCase):
    """Exercise protocol decisions without granting a second RTC attempt."""

    async def test_rotate_binds_new_leg_and_exact_sample_then_renews_and_stops_it(self):
        """One source/attempt owns distinct immutable socket permissions."""
        gateway = Gateway()
        lease = authority(gateway)
        self.assertTrue(await lease.claim())
        self.assertEqual(lease.leg_ref, LEG)
        self.assertTrue(lease.can_send_leg(LEG))
        await lease.rotate(NEXT, 7200)
        self.assertFalse(lease.can_send_leg(LEG))
        self.assertTrue(lease.can_send_leg(NEXT))
        self.assertEqual(lease.audio_start_sample, 7200)
        await lease._send("renew")
        await lease.stopped()
        commands = gateway.requests
        self.assertEqual(
            [b["action"] for b in commands],
            ["prepare", "claim", "rotate", "renew", "stopped"],
        )
        self.assertNotIn("leg_ref", commands[0])
        self.assertEqual(commands[1]["leg_ref"], LEG)
        self.assertEqual(commands[2]["leg_ref"], LEG)
        self.assertEqual(commands[2]["next_leg_ref"], NEXT)
        self.assertEqual(commands[2]["audio_end_sample"], 7200)
        self.assertEqual(commands[3]["leg_ref"], NEXT)
        self.assertEqual(commands[4]["leg_ref"], NEXT)
        self.assertTrue(all(b["version"] == CONTROL_VERSION for b in commands))

    async def test_wrong_leg_start_and_replayed_rotation_receipts_fail_closed(self):
        """A response can advance neither the active leg nor its start offset."""
        for override in (
            {"leg_ref": LEG},
            {"leg_ref": "bad"},
            {"audio_start_sample": 1},
            {"audio_start_sample": True},
            {"audio_start_sample": 2**53},
            {"replayed": True, "start_authorized": False},
            {"version": 1},
        ):
            with self.subTest(override=override):
                gateway = Gateway()
                lease = authority(gateway)
                await lease.claim()
                gateway.responses["rotate"] = override
                with self.assertRaises(ControlError):
                    await lease.rotate(NEXT, 7200)
                self.assertFalse(lease.can_send())
                self.assertFalse(lease.can_send_leg(NEXT))
                self.assertEqual(lease.leg_ref, LEG)
                with self.assertRaises(ControlError):
                    await lease.rotate(NEXT, 7200)
                self.assertEqual(
                    [b["action"] for b in gateway.requests].count("rotate"), 1
                )

    async def test_malformed_successor_and_sample_positions_fail_before_send(self):
        """Reference and JSON-safe position validation protect the actual boundary."""
        for candidate, sample in (
            ("bad", 0),
            (NEXT, -1),
            (NEXT, True),
            (NEXT, 2**53),
            (LEG, 0),
        ):
            with self.subTest(candidate=candidate, sample=sample):
                gateway = Gateway()
                lease = authority(gateway)
                await lease.claim()
                with self.assertRaises(ControlError):
                    await lease.rotate(candidate, sample)
                self.assertFalse(lease.can_send())
                self.assertFalse(gateway.entered["rotate"].is_set())

    async def test_initial_leg_and_zero_start_are_required_from_server_facts(self):
        """Controlled v1 and guessed leg identities are not compatibility paths."""
        for override in ({"version": 1}, {"leg_ref": "bad"}):
            gateway = Gateway()
            gateway.responses["prepare"] = override
            with self.assertRaises(ControlError):
                await authority(gateway).claim()
            self.assertFalse(gateway.entered["claim"].is_set())
        gateway = Gateway()
        gateway.responses["claim"] = {"audio_start_sample": 1}
        with self.assertRaises(ControlError):
            await authority(gateway).claim()

    async def test_renew_and_rotate_cannot_cross_in_flight(self):
        """The successor command cannot overtake a pending old-leg renewal."""
        gateway = Gateway()
        lease = authority(gateway)
        await lease.claim()
        gateway.release["renew"].clear()
        renewal = asyncio.create_task(lease._send("renew"))
        await gateway.entered["renew"].wait()
        rotation = asyncio.create_task(lease.rotate(NEXT, 7200))
        try:
            await asyncio.sleep(0)
            self.assertFalse(gateway.entered["rotate"].is_set())
            gateway.release["renew"].set()
            await asyncio.gather(renewal, rotation)
            self.assertTrue(lease.can_send_leg(NEXT))
        finally:
            gateway.release["renew"].set()
            await asyncio.gather(renewal, rotation, return_exceptions=True)

    async def test_independent_watchdog_fences_while_rotate_waits_for_renew(self):
        """A held command lock cannot hold the lease watchdog or authorize rotation."""
        gateway = Gateway()
        lease = authority(gateway)
        await lease.claim()
        lease.deadline = time.monotonic() + 0.02
        gateway.release["renew"].clear()
        renewal = asyncio.create_task(lease._send("renew"))
        await gateway.entered["renew"].wait()
        rotation = asyncio.create_task(lease.rotate(NEXT, 7200))
        try:
            await asyncio.wait_for(lease.watch_expiry(), 1)
            self.assertFalse(lease.can_send())
            gateway.release["renew"].set()
            result = await asyncio.gather(renewal, rotation, return_exceptions=True)
            self.assertIsInstance(result[1], ControlError)
            self.assertFalse(gateway.entered["rotate"].is_set())
        finally:
            gateway.release["renew"].set()
            await asyncio.gather(renewal, rotation, return_exceptions=True)

    async def test_lost_rotate_can_only_ack_candidate_and_cannot_retry_open(self):
        """Unknown transport outcome preserves exact successor cleanup identity."""
        gateway = Gateway()
        lease = authority(gateway)
        await lease.claim()
        gateway.errors["rotate"] = ControlResponseLost("fixture lost response")
        with self.assertRaises(ControlResponseLost):
            await lease.rotate(NEXT, 7200)
        self.assertFalse(lease.can_send())
        gateway.audio_start = 7200
        await lease.stopped()
        self.assertEqual(gateway.requests[-1]["leg_ref"], NEXT)
        with self.assertRaises(ControlError):
            await lease.rotate(NEXT, 7200)
        self.assertEqual([b["action"] for b in gateway.requests].count("rotate"), 1)

    async def test_late_renew_cannot_replace_expired_authority_before_watchdog_runs(
        self,
    ):
        """Check elapsed lease at the receipt boundary as well as the watchdog."""
        gateway = Gateway()
        lease = authority(gateway)
        await lease.claim()
        lease.deadline = time.monotonic() + 0.02
        gateway.release["renew"].clear()
        renewal = asyncio.create_task(lease._send("renew"))
        try:
            await gateway.entered["renew"].wait()
            await asyncio.sleep(0.03)
            gateway.release["renew"].set()
            with self.assertRaises(ControlError):
                await renewal
            self.assertFalse(lease.can_send())
        finally:
            gateway.release["renew"].set()
            await asyncio.gather(renewal, return_exceptions=True)
