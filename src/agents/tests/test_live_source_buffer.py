"""Source PCM evidence stays independent of capture and meeting wall clocks."""

import asyncio
import hashlib
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from live_continuation_fixtures import committed
from test_live_audio_timing import completed
from test_live_durable_delivery import final_event
from test_live_send_control import Gateway, authority

from live_delivery_codec import MAX_LEG_SAMPLES, event_bytes, event_digest
from live_durable_publisher import DurablePublisher
from live_input_drain import InputDrain, InputDrainError
from live_send_control import MAX_SAFE_SAMPLE, ControlError
from live_transcriber import TrackTranscriber


def buffer_timing(start, end):
    """Describe enclosing source samples without claiming speech boundaries."""
    return {
        "state": "source_buffer",
        "sample_rate": 24000,
        "start_sample": start,
        "end_sample": end,
    }


class CommitWindowTests(unittest.IsolatedAsyncioTestCase):
    """Resolve unordered item identities without retaining transcript copies."""

    def setUp(self):
        """Create only a local socket ledger, without provider or credentials."""
        self.drain = InputDrain()

    def commit(self, samples):
        """Complete a synthetic append and commit at their transport boundary."""
        self.drain.appended(samples)
        window = self.drain.committing()
        self.drain.commit_sent(window)
        return window

    async def test_ack_and_final_before_commit_send_returns_cannot_certify_it(self):
        """Reservation handles early receive, but successful send is still required."""
        self.drain.appended(1200)
        window = self.drain.committing()
        self.drain.observe(committed("item"))
        self.drain.observe(completed("item", "text"))
        proof = asyncio.create_task(self.drain.input_window("item"))
        try:
            await asyncio.sleep(0)
            self.assertFalse(proof.done())
            self.drain.commit_sent(window)
            self.assertEqual(await proof, (0, 1200))
        finally:
            self.drain.fence()
            await proof

    async def test_failed_commit_never_certifies_an_early_ack_and_final(self):
        """An ambiguous send stays unknown even when synthetic receive raced it."""
        self.drain.appended(1200)
        self.drain.committing()
        self.drain.observe(committed("item"))
        self.drain.observe(completed("item", "text"))
        self.drain.fail("live-input-socket-write-failed")
        self.assertIsNone(await self.drain.input_window("item"))

    async def test_duplicate_ack_never_consumes_another_fifo_entry(self):
        """Empty completions keep their commit position for the following final."""
        self.commit(1200)
        self.commit(2400)
        self.drain.observe(committed("second", "first"))
        self.drain.observe(completed("second", "second final"))
        self.drain.observe(committed("second", "first"))
        self.drain.observe(committed("first"))
        self.drain.observe(completed("first", ""))
        self.assertEqual(await self.drain.input_window("first"), (0, 1200))
        self.assertEqual(await self.drain.input_window("second"), (1200, 3600))
        self.assertEqual(await self.drain.wait_completed(), frozenset({"second"}))
        self.assertEqual(self.drain._completed, {"first": False, "second": True})

    async def test_fence_wakes_an_unknown_item_without_waiting_for_timeout(self):
        """Stopping never adds a commit or a new failure to obtain evidence."""
        self.commit(1200)
        self.drain.observe(completed("unknown", "text"))
        proof = asyncio.create_task(self.drain.input_window("unknown"))
        await asyncio.sleep(0)
        self.drain.fence()
        self.assertIsNone(await asyncio.wait_for(proof, 1))
        self.assertEqual(self.drain.commits, 1)
        self.assertIsNone(self.drain._error)

    async def test_zero_length_commit_has_no_positive_buffer_proof(self):
        """A fabricated completion for an empty buffer cannot gain source bounds."""
        self.commit(0)
        self.drain.observe(committed("empty"))
        self.drain.observe(completed("empty", "not evidence"))
        self.assertIsNone(await self.drain.input_window("empty"))

    async def test_forked_or_changed_predecessor_invalidates_pending_proof(self):
        """The ACK graph must identify one input-only chain, not just enough IDs."""
        for invalid in (committed("second"), committed("first", "second")):
            with self.subTest(invalid=invalid):
                drain = InputDrain()
                for _ in range(2):
                    drain.appended(1200)
                    drain.commit_sent(drain.committing())
                drain.observe(committed("first"))
                with self.assertRaises(InputDrainError):
                    drain.observe(invalid)
                self.assertIsNone(await drain.input_window("first"))


class SourceBufferCodecTests(unittest.TestCase):
    """Nested timing bytes use the agreed union and deterministic field order."""

    def test_nested_order_and_digest_do_not_depend_on_input_key_order(self):
        """The exact same UTF-8 event has one digest across implementations."""
        timing = buffer_timing(3360, 7200)
        event = final_event(timing=dict(reversed(list(timing.items()))))
        raw = event_bytes(event)
        self.assertTrue(
            raw.endswith(
                b'"timing":{"state":"source_buffer","sample_rate":24000,'
                b'"start_sample":3360,"end_sample":7200}}'
            )
        )
        self.assertEqual(event_digest(event), hashlib.sha256(raw).hexdigest())
        self.assertEqual(json.loads(raw)["text"], event["text"])
        self.assertEqual(event_digest(event), event_digest(final_event(timing=timing)))

    def test_invalid_units_bounds_unknown_keys_and_bool_are_refused(self):
        """Source windows are positive, safe integer intervals with a bounded span."""
        valid = buffer_timing(0, 1200)
        invalid = [
            None,
            [],
            {"state": "unavailable", "end_sample": 1},
            {**valid, "state": "meeting"},
            {**valid, "sample_rate": 24000.0},
            {**valid, "sample_rate": 48000},
            {**valid, "sample_rate": True},
            {**valid, "start_sample": False},
            {**valid, "start_sample": -1},
            {**valid, "start_sample": 0.0},
            {**valid, "end_sample": True},
            {**valid, "end_sample": 0},
            {**valid, "end_sample": -1},
            {**valid, "end_sample": 1200.0},
            {**valid, "end_sample": MAX_SAFE_SAMPLE + 1},
            {**valid, "end_sample": MAX_LEG_SAMPLES + 1},
            {**valid, "receivedAt": 123},
        ]
        for timing in invalid:
            with self.subTest(timing=timing), self.assertRaises(ControlError):
                event_bytes(final_event(timing=timing))
        event_bytes(final_event(timing=buffer_timing(0, MAX_LEG_SAMPLES)))
        event_bytes(
            final_event(timing=buffer_timing(MAX_SAFE_SAMPLE - 1, MAX_SAFE_SAMPLE))
        )


class SourceBufferPublisherTests(unittest.IsolatedAsyncioTestCase):
    """Durability preserves buffer proof while incomplete status remains sticky."""

    async def asyncSetUp(self):
        """Keep authority and receipt validation on the existing local gateway."""
        self.gateway = Gateway()
        self.lease = authority(self.gateway)
        await self.lease.claim()
        self.publisher = DurablePublisher(self.lease, MagicMock())

    async def test_known_buffer_survives_replay_without_resend_or_clock_promotion(self):
        """A source buffer is content in the digest, never meeting completeness."""
        timing = buffer_timing(0, 2400)
        await self.publisher.publish_final(
            "item", 1, " e\u0301 😀 ", "fr", timing=timing
        )
        await self.publisher.publish_final(
            "item", 1, " e\u0301 😀 ", "fr", timing=timing
        )
        events = [b["event"] for b in self.gateway.requests if b["action"] == "deliver"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["timing"], timing)
        self.assertEqual(events[0]["text"], " e\u0301 😀 ")
        with self.assertRaises(ControlError):
            await self.publisher.publish_final(
                "item", 1, " e\u0301 😀 ", "fr", timing=buffer_timing(0, 2401)
            )

    async def test_rotated_leg_cannot_claim_a_prior_legs_samples(self):
        """The wire validator alone cannot check the current leg's source offset."""
        await self.lease.rotate("next_leg_reference_0001", 3360)
        with self.assertRaises(ControlError):
            await self.publisher.publish_final(
                "item", 1, "text", "fr", timing=buffer_timing(0, 2400)
            )
        self.assertFalse(self.lease.can_send())

    async def test_worker_offset_and_leg_cap_do_not_use_provider_or_wall_times(self):
        """A rotated stream starts at the authorized cumulative source offset."""
        track = TrackTranscriber(
            track=object(),
            participant=SimpleNamespace(identity="fixture"),
            track_sid="TR_track",
            publisher=SimpleNamespace(),
            vad=None,
        )
        track._leg_audio_start_sample = 3360
        track._provider = SimpleNamespace(
            input_window=AsyncMock(return_value=(0, 3840))
        )
        self.assertEqual(
            await track._final_buffer_timing("item"), buffer_timing(3360, 7200)
        )
        for window in (None, (0, 0), (-1, 1200), (0, MAX_LEG_SAMPLES + 1)):
            track._provider.input_window.return_value = window
            self.assertEqual(
                await track._final_buffer_timing("item"), {"state": "unavailable"}
            )
        track._leg_audio_start_sample = MAX_SAFE_SAMPLE
        track._provider.input_window.return_value = (0, 1)
        self.assertEqual(
            await track._final_buffer_timing("item"), {"state": "unavailable"}
        )
