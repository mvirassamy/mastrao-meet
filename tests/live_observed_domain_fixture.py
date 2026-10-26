"""Frozen wire and real PostgreSQL lifecycle; no audio/provider or Core claims.

Use --postgres-fixture with {socket_directory: ...} on stdin. LocalCorePeer is
an explicit signed HTTP double; the parent bridge owns actual Core authority.
"""

# Real models load only after explicit settings.configure().

import copy
import hashlib
import json
import sys
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from threading import Barrier, Event
from types import SimpleNamespace
from unittest import mock
from uuid import uuid4

from live_observed_domain_support import (
    LocalCorePeer,
    ack_claims,
    close_room,
    configure_live_signing,
    configure_live_tests,
    context_claims,
    core_assertion,
    room_binding,
    rtc,
)

POSTGRES = "--postgres-fixture" in sys.argv
if POSTGRES:
    sys.argv.remove("--postgres-fixture")
    configure_live_tests(json.load(sys.stdin)["socket_directory"])
else:
    configure_live_tests()

from django.conf import settings
from django.core.management import call_command
from django.db import (
    DatabaseError,
    IntegrityError,
    connection,
    connections,
    transaction,
)
from django.utils import timezone

from core import mastrao_live_observed_domain as domain
from core import mastrao_live_observed_domain_contract as wire
from core import models

FIXTURE_PATH = (
    Path(__file__).resolve().parent / "fixtures/live-observed-domain-fixtures.json"
)


class LiveDomainWireTest(unittest.TestCase):
    def setUp(self):
        self.core, self.public = configure_live_signing()
        self.vectors = json.loads(FIXTURE_PATH.read_text())
        self.snapshot = copy.deepcopy(self.vectors["vectors"][0]["payload"])
        self.snapshot["policy"]["retention_expires_at"] = int(time.time()) + 3600
        self.claim = SimpleNamespace(
            jti=uuid4(),
            binding=self.snapshot["binding"],
            snapshot=self.snapshot,
            domain_ref=self.snapshot["domain_ref"],
            revision=1,
            digest=wire._sha256_canonical(self.snapshot),
        )

    def test_exact_frozen_golden_bytes_and_digests(self):
        for vector in self.vectors["vectors"]:
            with self.subTest(vector=vector["name"]):
                encoded = wire.canonical_bytes(vector["payload"])
                self.assertEqual(encoded.decode(), vector["canonical_utf8"])
                self.assertEqual(hashlib.sha256(encoded).hexdigest(), vector["sha256"])
                if vector["schema"] == "snapshot":
                    wire.validate_snapshot(vector["payload"])

    def test_frozen_negative_snapshots_refused(self):
        for vector in self.vectors["negative"]:
            if vector["schema"] != "snapshot":
                continue
            with (
                self.subTest(vector=vector["name"]),
                self.assertRaises((wire.LiveDomainRefused, wire.DomainPayloadOverflow)),
            ):
                wire.validate_snapshot(
                    json.loads(
                        vector["raw_json"],
                        parse_int=lambda value: -0.0 if value == "-0" else int(value),
                    )
                )

    def test_context_signature_binding_attempt_time_and_union(self):
        good = context_claims(self.claim)
        cases = [
            ("issuer", "wrong"),
            ("audience", "wrong"),
            ("jti", "short"),
            ("request_jti", uuid4().hex),
            ("version", True),
            ("operation_version", True),
            ("issued_at", int(time.time()) + 1),
            ("expires_at", int(time.time())),
            ("expires_at", int(time.time()) + 31),
            ("outcome", "other"),
            ("type", "mastrao.core-live-observed-domain-ack"),
            ("policy", None),
        ]
        signed = core_assertion(self.core, wire.CONTEXT_JOSE, good)
        self.assertEqual(
            wire.verify_context(signed, self.claim.binding, self.claim.jti.hex), good
        )
        for name, value in cases:
            bad = {**good, name: value}
            with (
                self.subTest(field=name, value=value),
                self.assertRaises(wire.LiveDomainRefused),
            ):
                wire.verify_context(
                    core_assertion(self.core, wire.CONTEXT_JOSE, bad),
                    self.claim.binding,
                    self.claim.jti.hex,
                )
        for name in self.claim.binding:
            bad = copy.deepcopy(good)
            bad["binding"][name] = (
                "f" * 64 if name.endswith("digest") else "different_reference_001"
            )
            with self.subTest(binding=name), self.assertRaises(wire.LiveDomainRefused):
                wire.verify_context(
                    core_assertion(self.core, wire.CONTEXT_JOSE, bad),
                    self.claim.binding,
                    self.claim.jti.hex,
                )
        with self.assertRaises(wire.LiveDomainRefused):
            wire.verify_context(signed[:-1], self.claim.binding, self.claim.jti.hex)

    def test_ack_exact_heads_policy_domain_attempt_and_signature(self):
        good = ack_claims(self.claim)
        self.assertEqual(
            wire.verify_ack(
                core_assertion(self.core, wire.ACK_JOSE, good),
                self.snapshot,
                self.claim.jti.hex,
            ),
            good,
        )
        for name, value in [
            ("requested_revision", 2),
            ("current_revision", 2),
            ("current_digest", "e" * 64),
            ("requested_digest", "e" * 64),
            ("request_jti", uuid4().hex),
            ("domain_ref", str(uuid4())),
            ("outcome", "superseded"),
            (
                "policy",
                {
                    **good["policy"],
                    "retention_expires_at": good["policy"]["retention_expires_at"] + 1,
                },
            ),
        ]:
            with self.subTest(field=name), self.assertRaises(wire.LiveDomainRefused):
                wire.verify_ack(
                    core_assertion(self.core, wire.ACK_JOSE, {**good, name: value}),
                    self.snapshot,
                    self.claim.jti.hex,
                )
        superseded = {
            **good,
            "outcome": "superseded",
            "current_revision": 2,
            "current_digest": "e" * 64,
        }
        wire.verify_ack(
            core_assertion(self.core, wire.ACK_JOSE, superseded),
            self.snapshot,
            self.claim.jti.hex,
        )

    def test_canonical_limits_nfc_safe_integers_and_entries(self):
        for value in [
            {"s": "e\u0301"},
            {"s": "\ud800"},
            {"v": 1.5},
            {"v": -0.0},
            {"v": 2**53},
            {"s": "x" * 16385},
        ]:
            with (
                self.subTest(value=repr(value)[:50]),
                self.assertRaises(wire.LiveDomainRefused),
            ):
                wire.canonical_bytes(value)
        with self.assertRaises(wire.DomainPayloadOverflow):
            wire.canonical_bytes([0] * 4096)

    def test_publication_transport_over_16k_and_signed_64k_budget(self):
        observation = {
            "observation_ref": "11111111-1111-4111-8111-000000000001",
            "event_id": "EV_fixture",
            "payload_digest": "d" * 64,
            "event_type": "track_published",
            "observed_at": 1,
            "room_sid": "RM_fixture",
            "participant_sid": "PA_fixture",
            "track_sid": "TR_fixture",
            "track_type": 0,
            "track_source": 2,
        }
        self.snapshot["observations"] = [
            {
                **observation,
                "observation_ref": f"11111111-1111-4111-8111-{i:012x}",
                "event_id": f"EV_fixture_{i}",
            }
            for i in range(60)
        ]
        body = wire.publication_request(self.snapshot, uuid4().hex)
        self.assertGreater(len(body["request"]), 16384)
        self.assertLessEqual(len(json.dumps(body).encode()), 65536)
        self.snapshot["observations"] = [
            {
                **observation,
                "observation_ref": f"11111111-1111-4111-8111-{i:012x}",
                "event_id": f"EV_{i}" + "x" * 100,
            }
            for i in range(128)
        ]
        with self.assertRaises(wire.DomainPayloadOverflow):
            wire.publication_request(self.snapshot, uuid4().hex)


@unittest.skipUnless(POSTGRES, "Requires existing disposable Core PostgreSQL helper")
class LiveDomainPostgresTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        call_command("migrate", verbosity=0, interactive=False)

    def setUp(self):
        call_command("flush", verbosity=0, interactive=False)
        self.core, self.public = configure_live_signing()
        self.binding = room_binding()

    def close(self):
        proof = close_room(self.binding)
        self.closure = models.MastraoRoomClosure.objects.get(room_binding=self.binding)
        self.cursor = models.MastraoLiveObservedDomain.objects.get(closure=self.closure)
        return proof

    def eligible(self, *, deadline=None):
        claim = domain.claim_live_observed_domain(self.cursor.pk)
        self.assertIsNotNone(claim)
        value = context_claims(claim, deadline=deadline)
        self.assertTrue(
            domain.finish_live_observed_domain(
                claim, context=core_assertion(self.core, wire.CONTEXT_JOSE, value)
            )
        )
        self.cursor.refresh_from_db()
        return self.cursor

    def prepare(self):
        self.cursor = domain.prepare_live_observed_domain(self.cursor.pk)
        self.assertIsNotNone(self.cursor)
        return self.cursor

    def acknowledge(self, claim):
        return domain.finish_live_observed_domain(
            claim,
            acknowledgement=core_assertion(self.core, wire.ACK_JOSE, ack_claims(claim)),
        )

    def due(self):
        models.MastraoLiveObservedDomain.objects.filter(pk=self.cursor.pk).update(
            next_attempt_at=timezone.now() - timedelta(seconds=1),
            next_snapshot_at=timezone.now() - timedelta(seconds=1),
        )

    def test_real_close_and_authenticated_rtc_without_recording(self):
        rtc(self.binding, "participant_joined", token=uuid4())
        rtc(self.binding)
        self.assertFalse(models.MastraoLiveObservedDomain.objects.exists())
        self.close()
        self.assertEqual(self.cursor.snapshot, {})
        self.assertIsNone(self.cursor.retention_expires_at)
        rtc(self.binding, "room_finished")
        self.eligible()
        result = self.prepare()
        self.assertEqual(result.snapshot["kind"], "inventory")
        self.assertEqual(len(result.snapshot["epochs"]), 1)
        epoch = result.snapshot["epochs"][0]
        self.assertEqual(epoch["correlation"], "unmatched_issuance")
        self.assertIsNone(epoch["media_token_binding_ref"])
        self.assertTrue(epoch["ended"])
        self.assertFalse(models.MastraoRecordingBinding.objects.exists())
        self.assertEqual(
            result.snapshot["binding"]["organization_external_id"], "synthetic_live_org"
        )
        self.assertEqual(
            result.snapshot["binding"]["close_receipt_claims_digest"],
            wire._sha256_canonical(self.closure.receipt_claims),
        )
        self.assertNotEqual(
            result.snapshot["binding"]["close_receipt_claims_digest"],
            self.closure.receipt_digest,
        )

    def test_no_rtc_read_before_verified_eligible_context(self):
        self.close()
        with mock.patch.object(
            domain,
            "_collect_inventory",
            side_effect=AssertionError("RTC before context"),
        ):
            self.assertIsNone(domain.prepare_live_observed_domain(self.cursor.pk))
            claim = domain.claim_live_observed_domain(self.cursor.pk)
            self.assertEqual(claim.snapshot, {})
            self.assertFalse(domain.finish_live_observed_domain(claim))
        self.cursor.refresh_from_db()
        self.assertEqual(self.cursor.snapshot, {})
        self.assertEqual(self.cursor.inventory_baseline, {})
        self.assertFalse(self.cursor.revisions.exists())

    def test_terminal_signed_contexts_delete_operational_cursor(self):
        self.close()
        claim = domain.claim_live_observed_domain(self.cursor.pk)
        value = context_claims(claim, reason="mode_not_live")
        self.assertTrue(
            domain.finish_live_observed_domain(
                claim, context=core_assertion(self.core, wire.CONTEXT_JOSE, value)
            )
        )
        self.closure.refresh_from_db()
        self.assertEqual(self.closure.live_domain_reason, "mode_not_live")
        self.assertFalse(models.MastraoLiveObservedDomain.objects.exists())
        rtc(self.binding)
        self.assertEqual(domain.discover_live_domains(), 0)
        self.assertFalse(models.MastraoLiveObservedDomain.objects.exists())

    def test_signed_policy_absent_and_expired_are_terminal(self):
        for reason in ("policy_absent", "retention_expired"):
            with self.subTest(reason=reason):
                self.binding = room_binding()
                self.close()
                claim = domain.claim_live_observed_domain(self.cursor.pk)
                value = context_claims(
                    claim, reason=reason, deadline=int(time.time()) - 1
                )
                self.assertTrue(
                    domain.finish_live_observed_domain(
                        claim,
                        context=core_assertion(self.core, wire.CONTEXT_JOSE, value),
                    )
                )
                self.closure.refresh_from_db()
                self.assertEqual(self.closure.live_domain_reason, reason)
                self.assertFalse(
                    models.MastraoLiveObservedDomain.objects.filter(
                        closure=self.closure
                    ).exists()
                )

    def test_http_503_close_unconfirmed_remains_retryable_no_terminal(self):
        self.close()
        with LocalCorePeer(self.core, self.public) as peer:
            peer.error_status = 503
            self.assertFalse(domain.deliver_live_observed_domain(self.cursor.pk))
        self.closure.refresh_from_db()
        self.cursor.refresh_from_db()
        self.assertIsNone(self.closure.live_domain_outcome)
        self.assertIsNone(self.cursor.retention_expires_at)
        self.assertEqual(self.cursor.snapshot, {})

    def test_loopback_commit_drop_restart_exact_snapshot_replayed_once(self):
        rtc(self.binding)
        self.close()
        with LocalCorePeer(self.core, self.public) as peer:
            self.assertTrue(domain.deliver_live_observed_domain(self.cursor.pk))
            original = copy.deepcopy(self.prepare().snapshot)
            peer.drop_next = True
            self.assertFalse(domain.deliver_live_observed_domain(self.cursor.pk))
            self.assertEqual(len(peer.commits), 1)
            self.cursor.refresh_from_db()
            self.assertEqual(self.cursor.acked_generation, 0)
            self.assertIsNone(self.cursor.claim_token)
            self.due()
            self.assertTrue(domain.deliver_live_observed_domain(self.cursor.pk))
            requests = [
                r["payload"] for r in peer.requests if "snapshot" in r["payload"]
            ]
            self.assertEqual([r["snapshot"] for r in requests], [original, original])
            self.assertNotEqual(requests[0]["jti"], requests[1]["jti"])
            self.assertEqual(len(peer.commits), 1)
            self.cursor.refresh_from_db()
            self.assertEqual(self.cursor.acked_generation, self.cursor.dirty_generation)
            self.assertEqual(self.cursor.revisions.count(), 1)

    def test_dirty_during_http_send_old_ack_cannot_clean_new_generation(self):
        rtc(self.binding)
        self.close()
        self.eligible()
        self.prepare()
        with LocalCorePeer(self.core, self.public) as peer:

            def late_fact():
                try:
                    rtc(self.binding, "track_unpublished")
                finally:
                    connections.close_all()

            peer.on_publication = late_fact
            self.assertFalse(domain.deliver_live_observed_domain(self.cursor.pk))
        self.cursor.refresh_from_db()
        self.assertEqual(self.cursor.acked_generation, 0)
        self.assertGreater(
            self.cursor.dirty_generation, self.cursor.prepared_generation
        )
        prepared = self.prepare()
        self.assertEqual(prepared.producer_revision, 2)
        self.assertTrue(prepared.snapshot["epochs"][0]["ended"])

    def test_old_claim_new_revision_and_expired_lease_refuse(self):
        rtc(self.binding)
        self.close()
        self.eligible()
        self.prepare()
        old = domain.claim_live_observed_domain(self.cursor.pk)
        rtc(self.binding, "track_unpublished")
        self.prepare()
        self.assertFalse(self.acknowledge(old))
        current = domain.claim_live_observed_domain(self.cursor.pk)
        models.MastraoLiveObservedDomain.objects.filter(pk=self.cursor.pk).update(
            claim_until=timezone.now() - timedelta(seconds=1)
        )
        self.assertFalse(self.acknowledge(current))
        self.cursor.refresh_from_db()
        self.assertEqual(self.cursor.acked_generation, 0)

    def test_same_facts_keep_revision_and_fingerprint(self):
        rtc(self.binding)
        self.close()
        self.eligible()
        first = copy.deepcopy(self.prepare().snapshot)
        self.assertTrue(
            self.acknowledge(domain.claim_live_observed_domain(self.cursor.pk))
        )
        self.prepare()
        self.assertEqual(self.cursor.snapshot, first)
        self.assertEqual(self.cursor.revisions.count(), 1)
        self.assertEqual(self.cursor.acked_generation, self.cursor.dirty_generation)

    def test_empty_inventory_terminal_only_unknown_and_video_exclusion(self):
        rtc(self.binding, "track_unpublished")
        rtc(self.binding, track="TR_video", track_type=1, track_source=1)
        self.close()
        self.eligible()
        self.prepare()
        self.assertEqual(len(self.cursor.snapshot["epochs"]), 1)
        epoch = self.cursor.snapshot["epochs"][0]
        self.assertIsNone(epoch["publication_observation_ref"])
        self.assertEqual(epoch["correlation"], "missing_join")
        self.assertTrue(epoch["ended"])

    def test_observation_overflow_invalidates_old_positive_sticky(self):
        rtc(self.binding)
        self.close()
        self.eligible()
        first = self.prepare().snapshot
        # Signed intake, same track: 129 observations without 33 epochs.
        for _ in range(128):
            rtc(self.binding)
        result = self.prepare()
        self.assertEqual(result.snapshot["reason"], "observation_limit_exceeded")
        self.assertEqual(result.inventory_baseline, first)
        models.MastraoRtcObservation.objects.exclude(
            pk__in=[r["observation_ref"] for r in first["observations"]]
        ).delete()
        self.assertEqual(
            self.prepare().snapshot["reason"], "observation_limit_exceeded"
        )
        self.assertEqual(self.cursor.producer_revision, 2)

    def test_epoch_sentinel_33_is_invalidation_no_positive_truncation(self):
        for index in range(33):
            rtc(self.binding, track=f"TR_{index}")
        self.close()
        self.eligible()
        self.assertEqual(self.prepare().snapshot["reason"], "epoch_limit_exceeded")

    def test_baseline_missing_or_mutated_fact_invalidates(self):
        rtc(self.binding)
        self.close()
        self.eligible()
        self.prepare()
        models.MastraoRtcObservation.objects.update(event_time_seconds=1)
        self.assertEqual(self.prepare().snapshot["reason"], "identity_conflict")

    def test_multiple_rooms_and_correlation_conflict_invalidations(self):
        rtc(self.binding)
        rtc(self.binding, room_sid="RM_other", track="TR_other")
        self.close()
        self.eligible()
        self.assertEqual(self.prepare().snapshot["reason"], "identity_conflict")

    def test_conflict_never_appears_in_positive_inventory(self):
        rtc(self.binding)
        rtc(self.binding, track_type=1, track_source=1)
        self.close()
        self.eligible()
        self.assertEqual(self.prepare().snapshot["reason"], "correlation_conflict")

    def test_unclassified_conflicting_epoch_cannot_disappear_before_baseline(self):
        rtc(self.binding, track_type=0, track_source=0)
        rtc(self.binding, track_type=1, track_source=1)
        epoch = models.MastraoRtcTrackEpoch.objects.get(
            connection__room_binding=self.binding
        )
        self.assertTrue(epoch.conflict)
        self.assertEqual(
            models.MastraoRtcObservation.objects.filter(
                room_binding=self.binding
            ).count(),
            2,
        )
        self.close()
        self.eligible()
        self.assertFalse(self.cursor.inventory_baseline)
        prepared = self.prepare()
        self.assertEqual(prepared.snapshot["kind"], "invalidation")
        self.assertEqual(prepared.snapshot["reason"], "correlation_conflict")
        self.assertFalse(prepared.inventory_baseline)

    def test_full_signed_payload_overflow_invalidates(self):
        rtc(self.binding)
        self.close()
        self.eligible()
        self.prepare()
        original = wire.publication_request

        def overflow_inventory(snapshot, jti):
            if snapshot["kind"] == "inventory":
                raise wire.DomainPayloadOverflow()
            return original(snapshot, jti)

        rtc(self.binding, "track_unpublished")
        with mock.patch.object(
            domain, "publication_request", side_effect=overflow_inventory
        ):
            self.assertEqual(
                self.prepare().snapshot["reason"], "payload_limit_exceeded"
            )
        self.assertEqual(self.cursor.producer_revision, 2)

    def test_1024_revisions_then_single_exhaustion_never_reset(self):
        self.close()
        self.eligible()
        # Test the actual revision writer's full bounded budget. The collector
        # here deliberately varies one control-time fixture; this is not 1024
        # legitimate RTC events or an authority/monotonic-baseline proof.
        observation = {
            "observation_ref": str(uuid4()),
            "event_id": "EV_revision_budget",
            "payload_digest": "d" * 64,
            "event_type": "room_finished",
            "observed_at": 1,
            "room_sid": "RM_live",
            "participant_sid": None,
            "track_sid": None,
            "track_type": None,
            "track_source": None,
        }
        for index in range(1024):
            content = {
                "observations": [{**observation, "observed_at": index + 1}],
                "epochs": [],
            }
            with mock.patch.object(domain, "_collect_inventory", return_value=content):
                self.assertEqual(self.prepare().producer_revision, index + 1)
        self.assertEqual(self.cursor.revisions.count(), 1024)
        with mock.patch.object(
            domain,
            "_collect_inventory",
            return_value={"observations": [], "epochs": []},
        ):
            result = self.prepare()
        self.assertEqual(result.producer_revision, wire.MAX_SAFE_INTEGER)
        self.assertEqual(result.snapshot["reason"], "revision_exhausted")
        self.assertEqual(self.prepare().producer_revision, wire.MAX_SAFE_INTEGER)
        self.assertEqual(self.cursor.revisions.count(), 1025)

    def test_expiry_purges_acknowledged_bodies_baseline_and_fingerprints(self):
        rtc(self.binding)
        self.close()
        self.eligible()
        self.prepare()
        claim = domain.claim_live_observed_domain(self.cursor.pk)
        self.assertTrue(self.acknowledge(claim))
        self.assertTrue(self.cursor.revisions.exists())
        future = self.cursor.retention_expires_at + timedelta(seconds=1)
        with mock.patch.object(domain, "_db_now", return_value=future):
            self.assertEqual(domain.purge_live_observed_domains(), 1)
        self.assertFalse(models.MastraoLiveObservedDomain.objects.exists())
        self.assertFalse(models.MastraoLiveObservedDomainRevision.objects.exists())
        self.assertFalse(self.acknowledge(claim))
        self.closure.refresh_from_db()
        self.assertEqual(self.closure.live_domain_reason, "retention_expired")
        self.assertEqual(
            self.closure.receipt_claims,
            models.MastraoRoomClosure.objects.get(pk=self.closure.pk).receipt_claims,
        )
        self.assertTrue(models.MastraoRtcObservation.objects.exists())
        self.assertEqual(domain.discover_live_domains(), 0)

    def test_expiry_after_row_lock_wait_refuses_ack_and_physically_deletes(self):
        self.close()
        self.eligible()
        self.prepare()
        claim = domain.claim_live_observed_domain(self.cursor.pk)
        future = self.cursor.retention_expires_at + timedelta(seconds=1)
        with mock.patch.object(domain, "_db_now", return_value=future):
            self.assertFalse(self.acknowledge(claim))
        self.assertFalse(models.MastraoLiveObservedDomain.objects.exists())

    def test_purge_runs_with_configuration_absent_or_partial(self):
        for partial in (False, True):
            with self.subTest(partial=partial):
                self.binding = room_binding()
                self.close()
                self.eligible()
                self.prepare()
                future = self.cursor.retention_expires_at + timedelta(seconds=1)
                settings.MASTRAO_CORE_LIVE_OBSERVED_DOMAIN_CONTEXT_ENDPOINT = (
                    "http://invalid" if partial else ""
                )
                settings.MASTRAO_CORE_LIVE_OBSERVED_DOMAIN_ENDPOINT = ""
                with mock.patch.object(domain, "_db_now", return_value=future):
                    self.assertEqual(domain.reconcile_live_observed_domains(), 1)
                self.assertFalse(models.MastraoLiveObservedDomain.objects.exists())
                self.core, self.public = configure_live_signing()

    def test_real_scheduler_without_recordings_consumes_all_stages(self):
        from core.mastrao_recording_reconciler import (  # noqa: PLC0415 -- Django configuration must be resolved first
            reconcile_mastrao_recordings,
        )

        self.close()
        with LocalCorePeer(self.core, self.public):
            with (
                mock.patch(
                    "core.mastrao_recording_reconciler.reconcile_native_recordings",
                    return_value=0,
                ),
                mock.patch(
                    "core.mastrao_recording_reconciler.reconcile_transcription_dispatches",
                    return_value=0,
                ),
            ):
                reconcile_mastrao_recordings()
                reconcile_mastrao_recordings()
        self.cursor.refresh_from_db()
        self.assertGreater(self.cursor.producer_revision, 0)
        self.assertEqual(self.cursor.acked_generation, self.cursor.dirty_generation)
        self.assertFalse(models.MastraoRecordingBinding.objects.exists())

    def test_recovery_discovers_missing_cursor_and_acked_refreshes_late_facts(self):
        self.close()
        self.cursor.delete()
        self.assertEqual(domain.discover_live_domains(), 1)
        self.cursor = models.MastraoLiveObservedDomain.objects.get(closure=self.closure)
        self.eligible()
        self.prepare()
        self.assertTrue(
            self.acknowledge(domain.claim_live_observed_domain(self.cursor.pk))
        )
        # Simulate lost wake: existing trusted inbox changes but dirty callback not
        # delivered; periodic full snapshot must still create a new revision.
        with mock.patch("core.mastrao_rtc_observations.dirty_live_domain_for_room"):
            rtc(self.binding)
        self.due()
        with LocalCorePeer(self.core, self.public):
            domain.reconcile_live_observed_domains()
        self.cursor.refresh_from_db()
        self.assertEqual(self.cursor.producer_revision, 2)
        self.assertEqual(self.cursor.acknowledged_revision, 2)

    def test_fair_context_queue_progress_beyond_poison_first_twenty(self):
        self.close()
        for _ in range(24):
            close_room(room_binding())
        with LocalCorePeer(self.core, self.public) as peer:
            peer.error_status = 503
            domain.reconcile_live_observed_domains(limit=20)
            domain.reconcile_live_observed_domains(limit=20)
            meetings = {r["payload"]["binding"]["meeting_ref"] for r in peer.requests}
        self.assertEqual(len(meetings), 25)
        self.assertEqual(models.MastraoLiveObservedDomain.objects.count(), 25)

    def test_discovery_skips_locked_first_page_before_limit(self):
        self.close()
        bindings = [self.binding] + [room_binding() for _ in range(20)]
        for binding in bindings[1:]:
            close_room(binding)
        # Lost close wake-ups: no RTC bodies or cursors were retained.
        models.MastraoLiveObservedDomain.objects.all().delete()
        release, locked = Event(), Event()

        def hold_first_page():
            try:
                with transaction.atomic():
                    list(
                        models.MastraoRoomBinding.objects.select_for_update().filter(
                            pk__in=[binding.pk for binding in bindings[:20]]
                        )
                    )
                    locked.set()
                    if not release.wait(10):
                        raise AssertionError("Timed out waiting for discovery probe")
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=1) as pool:
            task = pool.submit(hold_first_page)
            self.assertTrue(locked.wait(5))
            try:
                self.assertEqual(domain.discover_live_domains(limit=20), 1)
                self.assertEqual(
                    models.MastraoLiveObservedDomain.objects.get().closure.room_binding_id,
                    bindings[-1].pk,
                )
            finally:
                release.set()
            task.result(timeout=5)
        self.assertEqual(domain.discover_live_domains(limit=20), 20)
        self.assertEqual(models.MastraoLiveObservedDomain.objects.count(), 21)

    def test_claims_concurrent_workers_get_only_one_attempt(self):
        self.close()
        self.eligible()
        self.prepare()

        def claim():
            try:
                return domain.claim_live_observed_domain(self.cursor.pk)
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: claim(), range(2)))
        self.assertEqual(sum(item is not None for item in results), 1)

    def test_bad_receipt_or_tuple_refuses_context_before_http(self):
        self.close()
        claims = copy.deepcopy(self.closure.receipt_claims)
        claims["organization_external_id"] = "other_org"
        models.MastraoRoomClosure.objects.filter(pk=self.closure.pk).update(
            receipt_claims=claims
        )
        with (
            LocalCorePeer(self.core, self.public) as peer,
            self.assertRaises(wire.LiveDomainRefused),
        ):
            domain.deliver_live_observed_domain(self.cursor.pk)
        self.assertEqual(peer.requests, [])
        self.cursor.refresh_from_db()
        self.assertEqual(self.cursor.snapshot, {})

    def test_database_constraints_for_terminal_phase_claim_and_generations(self):
        self.close()
        for changes in [
            {"live_domain_outcome": "not_applicable"},
            {"live_domain_reason": "wrong"},
        ]:
            with (
                self.subTest(changes=changes),
                self.assertRaises(IntegrityError),
                transaction.atomic(),
            ):
                models.MastraoRoomClosure.objects.filter(pk=self.closure.pk).update(
                    **changes
                )
        for changes in [
            {"claim_token": uuid4()},
            {"acked_generation": 2},
            {"snapshot": {"untrusted": 1}},
            {"policy_ref": "partial_policy_01"},
        ]:
            with (
                self.subTest(changes=changes),
                self.assertRaises(IntegrityError),
                transaction.atomic(),
            ):
                models.MastraoLiveObservedDomain.objects.filter(
                    pk=self.cursor.pk
                ).update(**changes)

    def test_policy_and_fingerprint_deadlines_are_fixed_in_postgres(self):
        self.close()
        self.eligible()
        self.prepare()
        deadline = self.cursor.retention_expires_at
        for changes in [
            {"retention_expires_at": deadline + timedelta(seconds=1)},
            {"policy_ref": "replacement_policy_01"},
            {"retention_expires_at": None},
        ]:
            with (
                self.subTest(changes=changes),
                self.assertRaises(IntegrityError),
                transaction.atomic(),
            ):
                models.MastraoLiveObservedDomain.objects.filter(
                    pk=self.cursor.pk
                ).update(**changes)
        with self.assertRaises(IntegrityError), transaction.atomic():
            models.MastraoLiveObservedDomainRevision.objects.filter(
                domain=self.cursor
            ).update(retention_expires_at=deadline + timedelta(seconds=1))

    def test_due_and_purge_skip_locked_old_rooms_without_starving_later_rows(self):
        self.close()
        self.eligible()
        self.prepare()
        blocked_id = self.binding.pk
        for _ in range(2):
            binding = room_binding()
            close_room(binding)
            self.cursor = models.MastraoLiveObservedDomain.objects.get(
                closure__room_binding=binding
            )
            self.eligible()
            self.prepare()
        release, locked = Event(), Event()

        def hold_room():
            try:
                with transaction.atomic():
                    models.MastraoRoomBinding.objects.select_for_update().get(
                        pk=blocked_id
                    )
                    locked.set()
                    if not release.wait(10):
                        raise AssertionError("Timed out waiting for purge probe")
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=1) as pool:
            task = pool.submit(hold_room)
            self.assertTrue(locked.wait(5))
            try:
                eligible = domain._due_domain_ids(
                    models.MastraoLiveObservedDomain.objects.all(),
                    "next_attempt_at",
                    20,
                )
                self.assertEqual(len(eligible), 2)
                future = self.cursor.retention_expires_at + timedelta(days=1)
                with mock.patch.object(domain, "_db_now", return_value=future):
                    self.assertEqual(domain.purge_live_observed_domains(limit=20), 2)
                self.assertEqual(models.MastraoLiveObservedDomain.objects.count(), 1)
            finally:
                release.set()
            task.result(timeout=5)
        with mock.patch.object(domain, "_db_now", return_value=future):
            self.assertEqual(domain.purge_live_observed_domains(limit=20), 1)

    def test_expired_lease_after_actual_lock_wait_does_not_accept_ack(self):
        self.close()
        self.eligible()
        self.prepare()
        claim = domain.claim_live_observed_domain(self.cursor.pk)
        self.cursor.refresh_from_db()
        entered = Event()
        clock = [timezone.now()]
        backend_pid = []

        def finish_waiting():
            try:
                with connection.cursor() as sql:
                    sql.execute("SELECT pg_backend_pid()")
                    backend_pid.append(sql.fetchone()[0])
                entered.set()
                return self.acknowledge(claim)
            finally:
                connections.close_all()

        with (
            mock.patch.object(domain, "_db_now", side_effect=lambda: clock[0]),
            ThreadPoolExecutor(max_workers=1) as pool,
        ):
            with transaction.atomic():
                models.MastraoRoomBinding.objects.select_for_update().get(
                    pk=self.binding.pk
                )
                task = pool.submit(finish_waiting)
                self.assertTrue(entered.wait(5))
                waiting = False
                for _ in range(100):
                    with connection.cursor() as sql:
                        sql.execute("SELECT pg_stat_clear_snapshot()")
                        sql.execute(
                            "SELECT wait_event_type FROM pg_stat_activity WHERE pid = %s",
                            [backend_pid[0]],
                        )
                        waiting = sql.fetchone()[0] == "Lock"
                    if waiting:
                        break
                    time.sleep(0.01)
                self.assertTrue(
                    waiting, "ACK did not actually wait for the PostgreSQL row lock"
                )
                clock[0] = self.cursor.claim_until + timedelta(seconds=1)
            self.assertFalse(task.result(timeout=5))
        self.cursor.refresh_from_db()
        self.assertEqual(self.cursor.acked_generation, 0)
        self.assertTrue(models.MastraoLiveObservedDomain.objects.exists())

    def test_core_context_bad_signature_does_not_dispatch_or_classify(self):
        self.close()
        claim = domain.claim_live_observed_domain(self.cursor.pk)
        value = copy.deepcopy(context_claims(claim))
        value["binding"]["organization_external_id"] = "unauthorized_org"
        with self.assertRaises(wire.LiveDomainRefused):
            domain.finish_live_observed_domain(
                claim, context=core_assertion(self.core, wire.CONTEXT_JOSE, value)
            )
        self.cursor.refresh_from_db()
        self.closure.refresh_from_db()
        self.assertIsNone(self.closure.live_domain_outcome)
        self.assertIsNone(self.cursor.retention_expires_at)
        self.assertEqual(self.cursor.snapshot, {})
        self.assertFalse(self.cursor.revisions.exists())

    def test_w2_corrupt_connection_tuple_invalidates_existing_positive(self):
        rtc(self.binding)
        self.close()
        self.eligible()
        old = copy.deepcopy(self.prepare().snapshot)
        epoch = copy.deepcopy(old["epochs"][0])
        other = {
            **epoch,
            "epoch_ref": str(uuid4()),
            "participant_sid": "PA_other",
            "track_sid": "TR_other",
            "publication_observation_ref": None,
        }
        epochs = sorted([epoch, other], key=lambda row: row["epoch_ref"])
        with mock.patch.object(
            domain,
            "_collect_inventory",
            return_value={"observations": old["observations"], "epochs": epochs},
        ):
            self.assertEqual(self.prepare().snapshot["reason"], "identity_conflict")
        self.assertEqual(self.cursor.inventory_baseline, old)

    def test_authenticated_rtc_actual_64k_transport_overflow_is_invalidation(self):
        for index in range(128):
            rtc(self.binding, event_id=f"EV_{index:03d}_" + "x" * 121)
        self.close()
        self.eligible()
        result = self.prepare()
        self.assertEqual(result.snapshot["reason"], "payload_limit_exceeded")
        self.assertNotIn("observations", result.snapshot)
        self.assertLessEqual(
            len(
                json.dumps(
                    wire.publication_request(result.snapshot, uuid4().hex)
                ).encode()
            ),
            4096,
        )

    def test_signing_budget_growth_after_prepare_persists_higher_invalidation(self):
        rtc(self.binding)
        self.close()
        self.eligible()
        old = copy.deepcopy(self.prepare().snapshot)
        original = domain.publication_request

        def grew(snapshot, jti):
            if snapshot["kind"] == "inventory":
                raise wire.DomainPayloadOverflow()
            return original(snapshot, jti)

        with mock.patch.object(domain, "publication_request", side_effect=grew):
            self.assertFalse(domain.deliver_live_observed_domain(self.cursor.pk))
        self.cursor.refresh_from_db()
        self.assertEqual(self.cursor.producer_revision, 2)
        self.assertEqual(self.cursor.snapshot["reason"], "payload_limit_exceeded")
        self.assertEqual(self.cursor.inventory_baseline, old)
        with LocalCorePeer(self.core, self.public) as peer:
            self.assertTrue(domain.deliver_live_observed_domain(self.cursor.pk))
        self.assertEqual(len(peer.commits), 1)
        self.assertEqual(next(iter(peer.commits.values()))["kind"], "invalidation")

    def test_purge_batch_removes_claimed_and_acked_copies_with_fixed_terminal_marker(
        self,
    ):
        for index in range(25):
            self.binding = room_binding()
            if index % 2:
                models.User.objects.filter(pk=self.binding.owner_id).update(
                    is_active=False
                )
            self.close()
            self.eligible()
            self.prepare()
            claim = domain.claim_live_observed_domain(self.cursor.pk)
            if index % 2 == 0:
                self.assertTrue(self.acknowledge(claim))
        future = self.cursor.retention_expires_at + timedelta(days=1)
        with mock.patch.object(domain, "_db_now", return_value=future):
            self.assertEqual(domain.purge_live_observed_domains(limit=20), 20)
            self.assertEqual(models.MastraoLiveObservedDomain.objects.count(), 5)
            self.assertEqual(domain.purge_live_observed_domains(limit=20), 5)
        self.assertFalse(models.MastraoLiveObservedDomainRevision.objects.exists())
        self.assertEqual(
            models.MastraoRoomClosure.objects.filter(
                live_domain_reason="retention_expired"
            ).count(),
            25,
        )
        terminal_fields = [
            field.name
            for field in models.MastraoRoomClosure._meta.fields
            if field.name.startswith("live_domain_")
        ]
        self.assertEqual(
            set(terminal_fields),
            {"live_domain_outcome", "live_domain_reason", "live_domain_classified_at"},
        )

    def test_expiry_during_extraction_never_commits_new_body(self):
        self.close()
        self.eligible()
        before = self.cursor.retention_expires_at - timedelta(seconds=1)
        after = self.cursor.retention_expires_at + timedelta(seconds=1)
        with mock.patch.object(domain, "_db_now", side_effect=[before, after]):
            self.assertIsNone(domain.prepare_live_observed_domain(self.cursor.pk))
        self.assertFalse(models.MastraoLiveObservedDomain.objects.exists())
        self.assertFalse(models.MastraoLiveObservedDomainRevision.objects.exists())

    def test_real_pg_serialization_conflict_restarts_snapshot(self):
        rtc(self.binding)
        self.close()
        self.eligible()
        snapshot_read, resume = Event(), Event()
        original = domain._lock_domain
        calls, states = [], []

        def racing_lock(domain_id, **kwargs):
            calls.append(domain_id)
            if len(calls) == 1:
                models.MastraoLiveObservedDomain.objects.get(pk=domain_id)
                snapshot_read.set()
                if not resume.wait(5):
                    raise AssertionError("RTC writer did not resume snapshot")
            try:
                return original(domain_id, **kwargs)
            except DatabaseError as error:
                states.append(error.__cause__.sqlstate)
                raise

        def prepare():
            try:
                return domain.prepare_live_observed_domain(self.cursor.pk)
            finally:
                connections.close_all()

        with (
            mock.patch.object(domain, "_lock_domain", side_effect=racing_lock),
            ThreadPoolExecutor(max_workers=1) as pool,
        ):
            task = pool.submit(prepare)
            self.assertTrue(snapshot_read.wait(5))
            try:
                rtc(self.binding, "track_unpublished")
            finally:
                resume.set()
            result = task.result(timeout=5)
        self.assertIn("40001", states)
        self.assertEqual(len(calls), 2)
        self.assertTrue(result.snapshot["epochs"][0]["ended"])
        self.assertEqual(result.producer_revision, 1)
        self.assertEqual(result.revisions.count(), 1)

    def test_close_replay_rtc_and_purge_share_room_first_order(self):
        from core.mastrao_room_close_adapter import (  # noqa: PLC0415 -- configured models
            _apply_tombstone,
        )
        from core.mastrao_room_close_contract import (  # noqa: PLC0415 -- configured models
            CLOSE_BINDING_FIELDS,
        )

        self.close()
        self.eligible()
        self.prepare()
        effect = {key: self.closure.receipt_claims[key] for key in CLOSE_BINDING_FIELDS}
        barrier = Barrier(3)
        future = self.cursor.retention_expires_at + timedelta(seconds=1)

        def invoke(operation):
            try:
                barrier.wait(timeout=5)
                return operation()
            finally:
                connections.close_all()

        operations = [
            lambda: _apply_tombstone(self.closure.pk, effect),
            lambda: rtc(self.binding, "room_finished"),
            domain.purge_live_observed_domains,
        ]
        with (
            mock.patch.object(domain, "_db_now", return_value=future),
            ThreadPoolExecutor(max_workers=3) as pool,
        ):
            tasks = [pool.submit(invoke, operation) for operation in operations]
            for task in tasks:
                task.result(timeout=10)
        self.assertFalse(models.MastraoLiveObservedDomain.objects.exists())
        self.assertFalse(models.MastraoLiveObservedDomainRevision.objects.exists())
        self.closure.refresh_from_db()
        self.assertEqual(self.closure.live_domain_reason, "retention_expired")


if __name__ == "__main__":
    unittest.main(verbosity=2)
