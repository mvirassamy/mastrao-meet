"""Exact canonical bytes, durable ordering, bounds and terminal receipt failures."""

import asyncio
import hashlib
import json
import unittest
from unittest.mock import MagicMock, patch

from test_live_send_control import LEG, SOURCE, Gateway, authority

from live_delivery_codec import (
    MAX_EVENT_BYTES,
    MAX_EVENTS_PER_SOURCE,
    event_bytes,
    event_digest,
    validate_receipt,
    validated_language,
)
from live_durable_publisher import DurablePublisher
from live_send_control import MAX_SAFE_SAMPLE, ControlError, ControlResponseLost


def final_event(**changes):
    """Build canonical contract input without a receipt-derived clock."""
    return {
        "version": 2,
        "source_ref": SOURCE,
        "leg_ref": LEG,
        "sequence": 1,
        "kind": "final",
        "item_ref": "provider_item",
        "revision": 1,
        "text": '  e\u0301 é 😀\n"\\  ',
        "language": "fr",
        "timing": {"state": "unavailable"},
        **changes,
    }


def receipt_for(event, **changes):
    """Represent Core's exact public durable result, without owner/lease extras."""
    return {
        "version": 2,
        "source_ref": event["source_ref"],
        "leg_ref": event["leg_ref"],
        "sequence": event["sequence"],
        "event_digest": event_digest(event),
        "state": "collecting",
        "confirmed_sequence": event["sequence"],
        "final_event_count": 1,
        "incomplete": True,
        "meeting_coverage": "unverified",
        **changes,
    }


class DeliveryCodecTests(unittest.TestCase):
    """Keep Python bytes interoperable with compact schema-ordered JSON."""

    def test_exact_utf8_bytes_order_and_digest_preserve_decomposed_text(self):
        """Shuffled input cannot change field order or normalize Unicode content."""
        event = final_event()
        expected = (
            '{"version":2,"source_ref":"source_reference_0001",'
            '"leg_ref":"initial_leg_reference_0001","sequence":1,"kind":"final",'
            '"item_ref":"provider_item","revision":1,'
            '"text":"  e\u0301 é 😀\\n\\"\\\\  ","language":"fr",'
            '"timing":{"state":"unavailable"}}'
        ).encode()
        self.assertEqual(event_bytes(dict(reversed(list(event.items())))), expected)
        self.assertEqual(event_digest(event), hashlib.sha256(expected).hexdigest())
        self.assertEqual(json.loads(expected)["text"], event["text"])
        self.assertNotEqual(event_digest(event), event_digest(final_event(text="é")))

    def test_bounds_reject_bool_unsafe_numbers_unknown_fields_and_surrogates(self):
        """Malformed finals never cross a network or become truncated text."""
        for changes in (
            {"version": True},
            {"version": 1},
            {"sequence": True},
            {"sequence": 0},
            {"sequence": MAX_EVENTS_PER_SOURCE + 1},
            {"revision": False},
            {"revision": 0},
            {"revision": MAX_SAFE_SAMPLE + 1},
            {"item_ref": ""},
            {"item_ref": "provider.id"},
            {"item_ref": "p" * 161},
            {"source_ref": "bad"},
            {"leg_ref": "bad"},
            {"text": " "},
            {"text": "a" * 4001},
            {"text": "😀" * 2001},
            {"text": "invalid\ud800"},
            {"text": "invalid\udfff"},
            {"language": "invented"},
            {"timing": {"state": "audio_relative", "stream_origin_ms": 0}},
            {"extra": True},
            {"kind": "interim"},
        ):
            with self.subTest(changes=changes), self.assertRaises(ControlError):
                event_bytes(final_event(**changes))
        self.assertLess(
            len(event_bytes(final_event(text="😀" * 2000))), MAX_EVENT_BYTES
        )
        event_bytes(
            final_event(revision=MAX_SAFE_SAMPLE, sequence=MAX_EVENTS_PER_SOURCE)
        )
        with patch("live_delivery_codec.MAX_EVENT_BYTES", 10):
            with self.assertRaises(ControlError):
                event_bytes(final_event())

    def test_end_requires_total_finals_strictly_below_its_sequence(self):
        """An end cannot claim finals that did not precede it."""
        event = {
            "version": 2,
            "source_ref": SOURCE,
            "leg_ref": LEG,
            "sequence": 2,
            "kind": "end",
            "reason": "capture_incomplete",
            "final_event_count": 1,
        }
        event_bytes(event)
        for count in (True, -1, 2, MAX_EVENTS_PER_SOURCE + 1):
            with self.subTest(count=count), self.assertRaises(ControlError):
                event_bytes({**event, "final_event_count": count})

    def test_language_is_validated_without_coercion_or_fabrication(self):
        """SDK defaults, enum objects and malformed tags become explicit null."""
        for language in (None, "", "english", "FR", "en-us", " fr", 123, object()):
            self.assertIsNone(validated_language(language))
        for language in ("fr", "en", "en-US", "zh-CN"):
            self.assertEqual(validated_language(language), language)

    def test_receipt_requires_every_exact_field_and_confirmed_result(self):
        """Receipt shape, content and binding all participate in the ACK barrier."""
        event = final_event()
        receipt = receipt_for(event)
        validate_receipt(receipt, event, 1)
        changes = (
            {"version": True},
            {"version": 1},
            {"source_ref": "another_source_ref"},
            {"leg_ref": "another_leg_ref_0001"},
            {"sequence": True},
            {"sequence": 2},
            {"confirmed_sequence": True},
            {"confirmed_sequence": 0},
            {"event_digest": "0" * 64},
            {"state": "ended"},
            {"final_event_count": True},
            {"final_event_count": 0},
            {"incomplete": 1},
            {"incomplete": False},
            {"meeting_coverage": "complete"},
            {"lease_expires_at": 1234},
        )
        for change in changes:
            with self.subTest(change=change), self.assertRaises(ControlError):
                validate_receipt({**receipt, **change}, event, 1)
        for field in receipt:
            with self.subTest(missing=field), self.assertRaises(ControlError):
                validate_receipt(
                    {k: v for k, v in receipt.items() if k != field}, event, 1
                )


class DurablePublisherTests(unittest.IsolatedAsyncioTestCase):
    """Exercise publication authority with synthetic exact gateway responses."""

    async def asyncSetUp(self):
        """Claim one immutable source before publishing any durable text."""
        self.gateway = Gateway()
        self.lease = authority(self.gateway)
        await self.lease.claim()
        self.failed = MagicMock()
        self.publisher = DurablePublisher(self.lease, self.failed)

    def deliveries(self):
        """Inspect only events actually handed to the control transport."""
        return [b for b in self.gateway.requests if b["action"] == "deliver"]

    async def test_same_provider_item_across_legs_keeps_source_order_and_total(self):
        """Item bytes remain original while leg identity separates final storage."""
        text = final_event()["text"]
        await self.publisher.publish_final("provider_item", 1, text, "invalid")
        await self.publisher.publish_final("provider_item", 1, text, "invalid")
        await self.lease.rotate("next_leg_reference_0001", 480)
        await self.publisher.publish_final("provider_item", 1, text, "fr")
        await asyncio.gather(self.publisher.finish(None), self.publisher.finish(None))
        await self.publisher.finish(None)
        bodies = self.deliveries()
        events = [b["event"] for b in bodies]
        self.assertEqual([e["sequence"] for e in events], [1, 2, 3])
        self.assertEqual([e["kind"] for e in events], ["final", "final", "end"])
        self.assertEqual([e["item_ref"] for e in events[:2]], ["provider_item"] * 2)
        self.assertEqual(
            [e["leg_ref"] for e in events], [LEG] + [self.lease.leg_ref] * 2
        )
        self.assertEqual([e["text"] for e in events[:2]], [text] * 2)
        self.assertIsNone(events[0]["language"])
        self.assertEqual(events[-1]["final_event_count"], 2)
        self.assertEqual(events[-1]["reason"], "capture_incomplete")
        for body in bodies:
            self.assertEqual(body["leg_ref"], body["event"]["leg_ref"])
            self.assertEqual(body["source_ref"], body["event"]["source_ref"])
            self.assertEqual(
                set(body),
                {
                    "version",
                    "action",
                    "room_sid",
                    "participant_sid",
                    "track_sid",
                    "attempt_ref",
                    "worker_instance_ref",
                    "source_ref",
                    "admission_ref",
                    "leg_ref",
                    "event",
                },
            )
        self.failed.assert_not_called()

    async def test_known_local_capacity_loss_can_deliver_gap_and_end_but_stays_failed(
        self,
    ):
        """Reserve closure capacity without dropping, truncating or retrying finals."""
        with patch("live_durable_publisher.MAX_EVENTS_PER_SOURCE", 3):
            await self.publisher.publish_final("one", 1, "first", "fr")
            with self.assertRaises(ControlError) as failure:
                await self.publisher.publish_final("two", 1, "second", "fr")
            self.assertFalse(self.lease.can_send())
            with self.assertRaises(ControlError) as closed:
                await self.publisher.finish("capture_incomplete")
        self.assertIs(failure.exception, closed.exception)
        events = [b["event"] for b in self.deliveries()]
        self.assertEqual([e["kind"] for e in events], ["final", "gap", "end"])
        self.assertEqual([e["sequence"] for e in events], [1, 2, 3])
        self.assertEqual(events[-1]["final_event_count"], 1)

    async def test_lost_refused_or_incoherent_receipt_is_terminal_without_any_retry(
        self,
    ):
        """A failed send fences before returning, and closure cannot conceal it."""
        for refusal in (
            ControlResponseLost("fixture lost response"),
            ControlError("fixture refused response"),
            {"confirmed_sequence": 0},
        ):
            with self.subTest(refusal=refusal):
                gateway = Gateway()
                lease = authority(gateway)
                await lease.claim()
                publisher = DurablePublisher(lease, MagicMock())
                if isinstance(refusal, BaseException):
                    gateway.errors["deliver"] = refusal
                else:
                    gateway.responses["deliver"] = refusal
                with self.assertRaises(ControlError) as failed:
                    await publisher.publish_final("provider_item", 1, "final", "fr")
                self.assertFalse(lease.can_send())
                with self.assertRaises(ControlError) as again:
                    await publisher.publish_final("provider_item", 1, "final", "fr")
                self.assertIs(failed.exception, again.exception)
                with self.assertRaises(ControlError) as closed:
                    await publisher.finish("delivery_failed")
                self.assertIs(failed.exception, closed.exception)
                deliveries = [b for b in gateway.requests if b["action"] == "deliver"]
                self.assertEqual(len(deliveries), 1)

    async def test_independent_track_publisher_starts_at_one_and_ack_does_not_renew(
        self,
    ):
        """Room-level UI order grants no shared durable order or extra audio lease."""
        deadline = self.lease.deadline
        await self.publisher.publish_final("one", 1, "first", "fr")
        self.assertEqual(self.lease.deadline, deadline)
        gateway = Gateway()
        lease = authority(gateway)
        await lease.claim()
        publisher = DurablePublisher(lease, MagicMock())
        await publisher.publish_final("one", 1, "other track", "en")
        event = gateway.requests[-1]["event"]
        self.assertEqual(event["sequence"], 1)

    async def test_conflicting_replay_is_not_sent_or_rewritten(self):
        """Equal identities cannot replace acknowledged bytes at the same revision."""
        await self.publisher.publish_final("provider_item", 1, "e\u0301", "fr")
        with self.assertRaises(ControlError):
            await self.publisher.publish_final("provider_item", 1, "é", "fr")
        self.assertEqual(len(self.deliveries()), 1)
        self.assertFalse(self.lease.can_send())

    async def test_failed_end_is_attempted_once_and_error_is_shared(self):
        """Concurrent shutdown waiters retain a refusal rather than report ended."""
        self.gateway.errors["deliver"] = ControlError("fixture end refused")
        results = await asyncio.gather(
            self.publisher.finish(None),
            self.publisher.finish(None),
            return_exceptions=True,
        )
        self.assertIsInstance(results[0], ControlError)
        self.assertIs(results[0], results[1])
        with self.assertRaises(ControlError) as later:
            await self.publisher.finish(None)
        self.assertIs(later.exception, results[0])
        self.assertEqual([b["event"]["kind"] for b in self.deliveries()], ["end"])

    async def test_cancelled_finish_waiter_does_not_cancel_or_repeat_end(self):
        """Keep closure running for another waiter after caller cancellation."""
        self.gateway.release["deliver"].clear()
        first = asyncio.create_task(self.publisher.finish(None))
        await asyncio.wait_for(self.gateway.entered["deliver"].wait(), 1)
        first.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await first
        second = asyncio.create_task(self.publisher.finish(None))
        self.assertFalse(self.publisher._finish_task.done())
        self.gateway.release["deliver"].set()
        await asyncio.wait_for(second, 1)
        await self.publisher.finish(None)
        self.failed.assert_not_called()
        self.assertEqual([b["event"]["kind"] for b in self.deliveries()], ["end"])

    async def test_cancelled_finish_operation_retains_terminal_failure(self):
        """Cancellation during end delivery fences and never retries that end."""
        self.gateway.release["deliver"].clear()
        first = asyncio.create_task(self.publisher.finish(None))
        await asyncio.wait_for(self.gateway.entered["deliver"].wait(), 1)
        self.publisher._finish_task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await first
        self.assertFalse(self.lease.can_send())
        with self.assertRaises(asyncio.CancelledError):
            await self.publisher.finish(None)
        self.failed.assert_called_once()
        self.assertEqual(len(self.deliveries()), 1)

    async def test_finish_cancelled_before_start_still_fences_without_delivery(self):
        """A cancelled task cannot bypass the closure coroutine's failure latch."""
        first = asyncio.create_task(self.publisher.finish(None))
        await asyncio.sleep(0)
        self.publisher._finish_task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await first
        self.assertFalse(self.lease.can_send())
        self.failed.assert_called_once()
        self.assertIsInstance(self.publisher._error, asyncio.CancelledError)
        with self.assertRaises(asyncio.CancelledError):
            await self.publisher.finish(None)
        self.assertEqual(self.deliveries(), [])
