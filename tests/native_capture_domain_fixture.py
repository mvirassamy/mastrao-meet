"""HF24 producer persistence proofs; synthetic facts, no wire serializer.

Run directly for unit proofs. To exercise real PostgreSQL, the existing Core
startLocalPostgres helper must pass {"socket_directory": ...} on stdin with
--postgres-fixture. No environment/settings file or application server is used.
The test encoder below deliberately is NOT the Core domain wire contract.
"""

import copy
import hashlib
import json
import sys
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from threading import Barrier, Event
from types import SimpleNamespace
from unittest import mock
from uuid import UUID

from native_capture_domain_support import (
    apply_synthetic_room_close,
    configure_domain_tests,
    configure_synthetic_signing,
    synthetic_rtc_binding,
    synthetic_rtc_capture,
    synthetic_rtc_publication,
)

POSTGRES = "--postgres-fixture" in sys.argv
WIRE_ASSERTIONS = {}
if POSTGRES:
    sys.argv.remove("--postgres-fixture")
    configure_domain_tests(json.load(sys.stdin)["socket_directory"])
else:
    configure_domain_tests()

from django.conf import settings
from django.core.management import call_command
from django.db import (
    DatabaseError,
    IntegrityError,
    connection,
    connections,
    transaction,
)
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone

import jwt
import psycopg
import requests
import responses

from core import mastrao_native_capture_domain as domain
from core import models
from core.mastrao_rtc_correlation import correlate_verified_observation


def synthetic_persistence_encoder(facts, domain_ref, revision):
    """Only test persistence. This intentionally has no production wire format."""
    identity = repr(
        (facts.observations, facts.epochs, facts.captures, facts.overflow_reason)
    )
    facts_digest = hashlib.sha256(identity.encode()).hexdigest()
    payload = {
        "synthetic_test_only": True,
        "identity": facts_digest,
        "candidate_revision": revision,
    }
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    return domain.PreparedDomainRevision(payload, digest, facts_digest)


class NativeCaptureDomainUnitTest(unittest.TestCase):
    def test_frozen_core_golden_digests_and_ascii_set_order(self):
        vectors = json.loads(
            (
                Path(__file__).parent
                / "fixtures/synthetic_rtc_native_capture_domain_vectors.json"
            ).read_text()
        )["vectors"]
        for vector in vectors:
            facts = vector["facts"]
            self.assertEqual(domain._sha256_canonical(facts), vector["digest"])
            self.assertTrue(domain._canonical_json(facts).endswith(b"\n"))
        facts = vectors[0]["facts"]
        recording = SimpleNamespace(
            **{
                key: facts[key]
                for key in (
                    "organization_external_id",
                    "meeting_ref",
                    "room_ref",
                    "provider_binding_digest",
                )
            }
        )
        raw_observations = tuple(
            {
                "id": UUID(row["observation_ref"]),
                "event_id": row["event_id"],
                "payload_digest": row["payload_digest"],
                "event_type": row["event_type"],
                "event_time_seconds": row["observed_at"],
                "room_sid": row["room_sid"],
                "participant_sid": row["participant_sid"],
                "track_sid": row["track_sid"],
            }
            for row in reversed(facts["observations"])
        )
        raw_epochs = tuple(
            {
                "id": UUID(row["epoch_ref"]),
                "connection__room_sid": row["room_sid"],
                "connection__participant_sid": row["participant_sid"],
                "connection__correlation": "missing_join",
                "track_sid": row["track_sid"],
                "ended": False,
                "conflict": False,
            }
            for row in reversed(facts["epochs"])
        )
        raw = domain.NativeCaptureDomainFacts(
            SimpleNamespace(closing_at=None),
            recording,
            raw_observations,
            raw_epochs,
            (),
            None,
            None,
        )
        semantic = domain._semantic_snapshot(raw, UUID(facts["domain_ref"]))
        cursor = SimpleNamespace(facts_digest="", producer_revision=6)
        prepared = domain._revision_content(semantic, cursor)
        self.assertEqual(prepared.digest, vectors[0]["digest"])
        self.assertEqual(
            prepared.snapshot, {**facts, "snapshot_digest": vectors[0]["digest"]}
        )

    def test_refuses_non_postgres_without_emulating_isolation(self):
        if POSTGRES:
            self.skipTest("Dummy database guard is covered in unit-only invocation")
        with self.assertRaisesRegex(domain.DomainSnapshotRefused, "PostgreSQL"):
            domain.prepare_native_capture_domain(None)

    def test_invalid_digest_never_saves(self):
        cursor = mock.Mock()
        prepared = domain.PreparedDomainRevision({"synthetic": True}, "wrong", "a" * 64)
        with self.assertRaises(domain.DomainSnapshotRefused):
            domain._persist_revision(cursor, prepared)
        cursor.save.assert_not_called()

    def test_same_facts_preserve_persisted_content_ack_and_revision(self):
        cursor = mock.Mock(facts_digest="a" * 64)
        prepared = domain.PreparedDomainRevision({"candidate": 8}, "b" * 64, "a" * 64)
        self.assertIs(domain._persist_revision(cursor, prepared), cursor)
        cursor.save.assert_not_called()


@unittest.skipUnless(
    POSTGRES, "Requires the existing Core disposable PostgreSQL helper"
)
class NativeCaptureDomainPostgresTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        call_command("migrate", verbosity=0, interactive=False)

    def setUp(self):
        call_command("flush", verbosity=0, interactive=False)
        self.public_key = configure_synthetic_signing()
        self.binding, self.recording = synthetic_rtc_binding()

    def prepare(self, encode=synthetic_persistence_encoder):
        def adapted(facts, cursor):
            return encode(facts, cursor.pk, cursor.producer_revision + 1)

        with mock.patch.object(domain, "_encode_revision", side_effect=adapted):
            return domain.prepare_native_capture_domain(self.binding.pk)

    def test_collects_all_epochs_uncertain_and_failed_without_source(self):
        publication, epoch, capture = synthetic_rtc_capture(self.binding)
        second = synthetic_rtc_publication(self.binding)
        synthetic_rtc_publication(self.binding, event="track_unpublished")
        seen = []

        def inspect(facts, domain_ref, revision):
            seen.append(facts)
            self.assertEqual(facts.recording_binding.pk, self.recording.pk)
            self.assertEqual(len(facts.observations), 3)
            self.assertEqual(len(facts.epochs), 1)
            self.assertEqual(len(facts.captures), 1)
            self.assertEqual(facts.epochs[0]["id"], epoch.pk)
            self.assertTrue(facts.epochs[0]["ended"])
            self.assertTrue(facts.epochs[0]["conflict"])
            self.assertEqual(
                {
                    row["id"]
                    for row in facts.observations
                    if row["event_type"] == "track_published"
                },
                {publication.pk, second.pk},
            )
            self.assertIsNone(facts.captures[0]["source_receipt"])
            self.assertNotIn("asr_receipt", facts.captures[0])
            return synthetic_persistence_encoder(facts, domain_ref, revision)

        first = self.prepare(inspect)
        self.assertIsNone(seen[-1].captures[0]["provider_job_ref"])
        capture.provider_job_ref = "EG_SyntheticFailed"
        capture.observed_status = 4
        capture.drained_at = timezone.now()
        capture.save()
        second_revision = self.prepare(inspect)
        self.assertEqual(second_revision.producer_revision, first.producer_revision + 1)
        self.assertEqual(seen[-1].captures[0]["observed_status"], 4)
        self.assertIsNone(seen[-1].captures[0]["source_manifest"])
        self.assertFalse(models.MastraoMediaTokenBinding.objects.exists())

    def test_room_finished_does_not_manufacture_applied_closure(self):
        models.MastraoRtcObservation.objects.create(
            room_binding=self.binding,
            event_id=f"EV_{self.binding.pk.hex}",
            payload_digest="f" * 64,
            event_type="room_finished",
            event_time_seconds=1,
            room_sid="RM_synthetic",
        )
        self.binding.closing_at = timezone.now()
        self.binding.save()

        def inspect(facts, domain_ref, revision):
            self.assertEqual(facts.observations[0]["event_type"], "room_finished")
            self.assertIsNone(facts.closure)
            self.assertIsNotNone(facts.room_binding.closing_at)
            return synthetic_persistence_encoder(facts, domain_ref, revision)

        self.prepare(inspect)

    def test_missing_or_inexact_recording_never_uses_grants(self):
        for field, value in (
            ("meeting_ref", "inexact_meeting"),
            ("room_ref", "inexact_room"),
            ("provider_binding_digest", "f" * 64),
            ("organization_external_id", ""),
        ):
            with self.subTest(field=field):
                original = getattr(self.recording, field)
                models.MastraoRecordingBinding.objects.filter(
                    pk=self.recording.pk
                ).update(**{field: value})
                with self.assertRaises(domain.DomainSnapshotRefused):
                    self.prepare()
                models.MastraoRecordingBinding.objects.filter(
                    pk=self.recording.pk
                ).update(**{field: original})
        self.assertFalse(
            models.MastraoNativeCaptureDomain.objects.filter(
                room_binding=self.binding
            ).exists()
        )

    def test_persisted_content_survives_ack_loss_and_claim_expiry(self):
        cursor = self.prepare()
        original = copy.deepcopy(cursor.snapshot)
        first = domain.claim_native_capture_domain(cursor.pk)
        self.assertIsNone(domain.claim_native_capture_domain(cursor.pk))
        models.MastraoNativeCaptureDomain.objects.filter(pk=cursor.pk).update(
            claim_until=timezone.now() - timedelta(seconds=1)
        )
        self.assertEqual(
            domain.finish_native_capture_domain(first, acknowledged=True), 0
        )
        self.assertEqual(
            domain.finish_native_capture_domain(first, acknowledged=False), 0
        )
        second = domain.claim_native_capture_domain(cursor.pk)
        self.assertNotEqual(second.token, first.token)
        self.assertEqual(second.snapshot, original)
        self.assertEqual(second.producer_revision, first.producer_revision)
        self.assertEqual(
            domain.finish_native_capture_domain(first, acknowledged=True), 0
        )
        self.assertEqual(
            domain.finish_native_capture_domain(second, acknowledged=True), 1
        )
        replay = self.prepare()
        self.assertEqual(replay.producer_revision, first.producer_revision)
        self.assertIsNotNone(replay.acknowledged_at)
        self.assertIsNone(domain.claim_native_capture_domain(cursor.pk))

    def test_new_revision_fences_every_claim_identifier(self):
        cursor = self.prepare()
        old = domain.claim_native_capture_domain(cursor.pk)
        synthetic_rtc_publication(self.binding)
        newer = self.prepare()
        self.assertEqual(newer.producer_revision, cursor.producer_revision + 1)
        current = domain.claim_native_capture_domain(cursor.pk)
        for forged in (
            old,
            replace(current, producer_revision=old.producer_revision),
            replace(current, snapshot_digest=old.snapshot_digest),
            replace(current, token=old.token),
        ):
            self.assertEqual(
                domain.finish_native_capture_domain(forged, acknowledged=True), 0
            )
            self.assertEqual(
                domain.finish_native_capture_domain(forged, acknowledged=False), 0
            )
        self.assertEqual(
            domain.finish_native_capture_domain(current, acknowledged=True), 1
        )

    def assert_expiry_during_row_lock_prevents_mutation(self, action):
        cursor = domain.prepare_native_capture_domain(self.binding.pk)
        claim = domain.claim_native_capture_domain(cursor.pk)
        expiry = timezone.now() + timedelta(seconds=1)
        models.MastraoNativeCaptureDomain.objects.filter(pk=cursor.pk).update(
            claim_until=expiry
        )
        before = models.MastraoNativeCaptureDomain.objects.values().get(pk=cursor.pk)
        params = connection.get_connection_params()
        worker_ready = Event()
        worker_pid = []

        def waiting_action():
            try:
                connection.ensure_connection()
                worker_pid.append(connection.connection.info.backend_pid)
                worker_ready.set()
                if action == "payload_invalidation":
                    return domain._persist_payload_invalidation(claim)
                return domain.finish_native_capture_domain(
                    claim, acknowledged=action == "ack"
                )
            finally:
                connections.close_all()

        with psycopg.connect(**params) as locker:
            locker.execute(
                "SELECT id FROM meet_mastrao_native_capture_domain WHERE id=%s FOR UPDATE",
                [cursor.pk],
            )
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(waiting_action)
                try:
                    self.assertTrue(worker_ready.wait(5))
                    with psycopg.connect(**params, autocommit=True) as observer:
                        deadline = time.monotonic() + 5
                        blocked = False
                        while time.monotonic() < deadline:
                            blocked = bool(
                                observer.execute(
                                    "SELECT 1 FROM pg_stat_activity WHERE pid=%s AND wait_event_type='Lock'",
                                    [worker_pid[0]],
                                ).fetchone()
                            )
                            if blocked:
                                break
                            time.sleep(0.01)
                        self.assertTrue(blocked, "Expected a real PG row lock wait")
                        while timezone.now() <= expiry + timedelta(milliseconds=100):
                            time.sleep(0.01)
                    self.assertFalse(future.done())
                    self.assertGreater(timezone.now(), expiry)
                finally:
                    locker.commit()
                changed = future.result(timeout=5)
        if action != "payload_invalidation":
            self.assertEqual(changed, 0)
        after = models.MastraoNativeCaptureDomain.objects.values().get(pk=cursor.pk)
        self.assertEqual(after, before)

    def test_ack_expiring_during_pg_row_lock_cannot_mutate(self):
        self.assert_expiry_during_row_lock_prevents_mutation("ack")

    def test_retry_expiring_during_pg_row_lock_cannot_mutate(self):
        self.assert_expiry_during_row_lock_prevents_mutation("retry")

    def test_invalidation_expiring_during_pg_row_lock_cannot_mutate(self):
        self.assert_expiry_during_row_lock_prevents_mutation("payload_invalidation")

    def test_failed_delivery_retries_same_content_after_delay(self):
        cursor = self.prepare()
        first = domain.claim_native_capture_domain(cursor.pk)
        self.assertEqual(
            domain.finish_native_capture_domain(first, acknowledged=False), 1
        )
        self.assertIsNone(domain.claim_native_capture_domain(cursor.pk))
        models.MastraoNativeCaptureDomain.objects.filter(pk=cursor.pk).update(
            next_attempt_at=timezone.now()
        )
        second = domain.claim_native_capture_domain(cursor.pk)
        self.assertEqual(second.snapshot, first.snapshot)
        self.assertEqual(second.snapshot_digest, first.snapshot_digest)
        self.assertEqual(second.producer_revision, first.producer_revision)

    def test_limit_sentinels_never_expose_partial_inventory(self):
        synthetic_rtc_capture(self.binding)
        for index in range(domain.MAX_OBSERVATIONS):
            synthetic_rtc_publication(self.binding, track=f"TR_synthetic_{index}")

        def inspect(facts, domain_ref, revision):
            self.assertEqual(facts.overflow_reason, "observation_limit_exceeded")
            self.assertEqual(
                (facts.observations, facts.epochs, facts.captures), ((), (), ())
            )
            return synthetic_persistence_encoder(facts, domain_ref, revision)

        self.prepare(inspect)

    def test_epoch_limit_includes_epochs_without_capture(self):
        _, epoch, _ = synthetic_rtc_capture(self.binding)
        models.MastraoRtcTrackEpoch.objects.bulk_create(
            [
                models.MastraoRtcTrackEpoch(
                    connection_id=epoch.connection_id, track_sid=f"TR_synthetic_{index}"
                )
                for index in range(domain.MAX_EPOCHS)
            ]
        )

        def inspect(facts, domain_ref, revision):
            self.assertEqual(facts.overflow_reason, "epoch_limit_exceeded")
            self.assertEqual(facts.epochs, ())
            return synthetic_persistence_encoder(facts, domain_ref, revision)

        self.prepare(inspect)

    def test_repeatable_read_remains_coherent_during_concurrent_drain(self):
        _, _, capture = synthetic_rtc_capture(self.binding)
        initial = self.prepare()
        original_collect = domain._collect_facts

        def concurrent_drain(room_binding_id, baseline):
            # This read establishes the actual PG snapshot before the writer commits.
            self.assertIsNone(
                models.MastraoNativeCaptureStart.objects.get(pk=capture.pk).drained_at
            )
            with psycopg.connect(**connection.get_connection_params()) as independent:
                independent.execute(
                    "UPDATE meet_mastrao_native_capture_start SET provider_job_ref=%s, observed_status=4, drained_at=now() WHERE id=%s",
                    ["EG_SyntheticConcurrent", capture.pk],
                )
            return original_collect(room_binding_id, baseline)

        with mock.patch.object(domain, "_collect_facts", side_effect=concurrent_drain):
            coherent = self.prepare()
        self.assertEqual(coherent.producer_revision, initial.producer_revision)
        after = self.prepare()
        self.assertEqual(after.producer_revision, initial.producer_revision + 1)
        with connection.cursor() as sql:
            sql.execute("SHOW transaction_isolation")
            self.assertEqual(sql.fetchone()[0], "read committed")

    def test_cursor_lock_serializes_concurrent_producers(self):
        self.prepare()
        entered, release, second_started = Event(), Event(), Event()

        def first_encoder(facts, cursor):
            if not entered.is_set():
                entered.set()
                if not release.wait(10):
                    raise AssertionError("synthetic first producer was not released")
            return synthetic_persistence_encoder(
                facts, cursor.pk, cursor.producer_revision + 1
            )

        def run(started=None):
            try:
                if started is not None:
                    started.set()
                return domain.prepare_native_capture_domain(self.binding.pk)
            finally:
                connections.close_all()

        with (
            mock.patch.object(domain, "_encode_revision", side_effect=first_encoder),
            ThreadPoolExecutor(max_workers=2) as pool,
        ):
            first = pool.submit(run)
            self.assertTrue(entered.wait(10))
            second = pool.submit(run, second_started)
            try:
                self.assertTrue(second_started.wait(10))
                with self.assertRaises(TimeoutError):
                    second.result(timeout=0.15)
            finally:
                release.set()
            self.assertEqual(
                first.result(timeout=10).producer_revision,
                second.result(timeout=10).producer_revision,
            )

    def test_nested_transaction_is_refused(self):
        with (
            transaction.atomic(),
            self.assertRaisesRegex(domain.DomainSnapshotRefused, "top-level"),
        ):
            self.prepare()

    def test_encoder_failure_rolls_back_cursor_and_revision(self):
        def fail(*args):
            raise RuntimeError("synthetic encoder failure")

        with self.assertRaises(RuntimeError):
            self.prepare(fail)
        self.assertFalse(
            models.MastraoNativeCaptureDomain.objects.filter(
                room_binding=self.binding
            ).exists()
        )

    def test_serialization_retry_is_bounded_and_rolls_back(self):
        attempts = []

        def conflict(*args):
            attempts.append(connection.in_atomic_block)
            error = DatabaseError("synthetic SQLSTATE serialization failure")
            raise error from psycopg.errors.SerializationFailure()

        with (
            mock.patch.object(domain, "_collect_facts", side_effect=conflict),
            self.assertRaises(DatabaseError),
        ):
            self.prepare()
        self.assertEqual(attempts, [True] * domain.SNAPSHOT_ATTEMPTS)
        self.assertFalse(
            models.MastraoNativeCaptureDomain.objects.filter(
                room_binding=self.binding
            ).exists()
        )

    def test_database_constraints_guard_revision_and_claim_pair(self):
        cursor = self.prepare()
        for update in ({"snapshot_digest": "invalid"}, {"claim_token": cursor.pk}):
            with self.assertRaises(IntegrityError), transaction.atomic():
                models.MastraoNativeCaptureDomain.objects.filter(pk=cursor.pk).update(
                    **update
                )

    def assert_wire_request(self, request, *, outcome="recorded", mutation=None):
        self.assertFalse(connection.in_atomic_block)
        body = json.loads(request.body)
        self.assertEqual(set(body), {"domain"})
        assertion = jwt.decode(
            body["domain"],
            self.public_key,
            algorithms=["EdDSA"],
            options={"verify_aud": False},
        )
        self.assertEqual(assertion["audience"], "synthetic-core-domain")
        self.assertEqual(assertion["issuer"], "synthetic-meet-domain")
        self.assertEqual(assertion["expires_at"] - assertion["issued_at"], 30)
        self.assertEqual(
            jwt.get_unverified_header(body["domain"])["typ"], domain.DOMAIN_JOSE
        )
        # Independent committed PG read proves persistence before the HTTP call.
        with psycopg.connect(**connection.get_connection_params()) as independent:
            row = independent.execute(
                "SELECT producer_revision, snapshot_digest, snapshot FROM meet_mastrao_native_capture_domain WHERE id=%s",
                [assertion["domain_ref"]],
            ).fetchone()
        self.assertEqual(
            row[:2], (assertion["producer_revision"], assertion["snapshot_digest"])
        )
        envelope = {"issuer", "audience", "issued_at", "expires_at", "jti"}
        persisted = row[2]
        if isinstance(persisted, str):
            persisted = json.loads(persisted)
        self.assertEqual(
            persisted,
            {key: value for key, value in assertion.items() if key not in envelope},
        )
        WIRE_ASSERTIONS[assertion["snapshot_digest"]] = assertion
        budget = domain.MAX_BODY_BYTES
        if assertion["kind"] == "invalidation":
            budget = domain.MAX_INVALIDATION_BYTES
        self.assertLessEqual(len(request.body), budget)
        ack = {
            "version": 1,
            "domain_ref": assertion["domain_ref"],
            "producer_revision": assertion["producer_revision"],
            "snapshot_digest": assertion["snapshot_digest"],
            "outcome": outcome,
            "current_revision": assertion["producer_revision"],
            "current_snapshot_digest": assertion["snapshot_digest"],
        }
        if mutation:
            ack.update(mutation)
        return 200, {"Content-Type": "application/json"}, json.dumps(ack)

    def test_signed_wire_has_distinct_closure_capture_and_source_facts(self):
        _, epoch, capture = synthetic_rtc_capture(self.binding)
        terminal = synthetic_rtc_publication(self.binding, event="track_unpublished")
        capture.provider_job_ref, capture.observed_status = "EG_SyntheticFailed", 4
        capture.drained_at = timezone.now()
        capture.save()
        apply_synthetic_room_close(self.binding, self.recording)
        cursor = domain.prepare_native_capture_domain(self.binding.pk)
        snapshot = cursor.snapshot
        self.assertEqual(
            snapshot["epochs"][0]["end_observation_refs"], [str(terminal.pk)]
        )
        self.assertEqual(snapshot["epochs"][0]["epoch_ref"], str(epoch.pk))
        self.assertEqual(snapshot["epochs"][0]["admission"], "capture")
        self.assertEqual(snapshot["captures"][0]["observed_status"], 4)
        self.assertIsNone(snapshot["captures"][0]["source_receipt_ref"])
        self.assertIsNotNone(snapshot["closure"])
        self.assertIsNotNone(snapshot["close_requested_at"])
        self.assertNotIn("asr", json.dumps(snapshot))
        with responses.RequestsMock() as http:
            http.add_callback(
                responses.POST,
                settings.MASTRAO_CORE_NATIVE_CAPTURE_DOMAIN_ENDPOINT,
                callback=self.assert_wire_request,
            )
            self.assertEqual(domain.deliver_native_capture_domain(cursor.pk), 1)
        replay = domain.prepare_native_capture_domain(self.binding.pk)
        self.assertEqual(replay.snapshot, snapshot)
        self.assertIsNotNone(replay.acknowledged_at)

    def test_close_claims_hash_preserves_compact_receipt_and_historical_expiry(self):
        proof = apply_synthetic_room_close(self.binding, self.recording)
        closure = models.MastraoRoomClosure.objects.get(room_binding=self.binding)
        claims = copy.deepcopy(closure.receipt_claims)
        compact_digest = hashlib.sha256(proof["receipt"].encode("ascii")).hexdigest()
        self.assertEqual(closure.receipt_digest, compact_digest)
        first = domain.prepare_native_capture_domain(self.binding.pk)
        exported = first.snapshot["closure"]["receipt_digest"]
        self.assertEqual(exported, domain._sha256_canonical(claims))
        self.assertNotEqual(exported, compact_digest)
        historical_now = timezone.now() + timedelta(days=1)
        self.assertLess(claims["expires_at"], historical_now.timestamp())
        with mock.patch.object(domain.timezone, "now", return_value=historical_now):
            replay = domain.prepare_native_capture_domain(self.binding.pk)
        self.assertEqual(replay.snapshot, first.snapshot)
        self.assertEqual(replay.producer_revision, first.producer_revision)
        closure.refresh_from_db()
        self.assertEqual(closure.receipt_claims, claims)
        self.assertEqual(closure.receipt_digest, compact_digest)

    def test_refuses_missing_malformed_or_misbound_close_claims_without_revision(self):
        apply_synthetic_room_close(self.binding, self.recording)
        closure = models.MastraoRoomClosure.objects.get(room_binding=self.binding)
        claims = copy.deepcopy(closure.receipt_claims)
        first = domain.prepare_native_capture_domain(self.binding.pk)
        variants = [{}, [], {**claims, "extra": "denied"}]
        for field in (
            "organization_external_id",
            "meeting_ref",
            "room_ref",
            "provider_binding_digest",
            "close_ref",
            "effect_key",
            "arguments_digest",
            "type",
            "operation",
            "status",
            "issuer",
            "audience",
        ):
            variants.append({**claims, field: "wrong_binding_value"})
        variants.extend(
            [
                {**claims, "version": True},
                {**claims, "operation_version": True},
                {**claims, "issued_at": True},
                {**claims, "expires_at": 1.5},
                {**claims, "expires_at": 9_007_199_254_740_992},
                {**claims, "expires_at": claims["issued_at"]},
                {**claims, "expires_at": claims["issued_at"] + 31},
                {**claims, "provider_observation": "already_absent"},
                {**claims, "provider_observation": []},
                {**claims, "jti": []},
                {**claims, "jti": "!"},
                {key: value for key, value in claims.items() if key != "jti"},
            ]
        )
        for invalid in variants:
            with self.subTest(claims=invalid):
                models.MastraoRoomClosure.objects.filter(pk=closure.pk).update(
                    receipt_claims=invalid
                )
                with self.assertRaises(domain.DomainSnapshotRefused):
                    domain.prepare_native_capture_domain(self.binding.pk)
                current = models.MastraoNativeCaptureDomain.objects.get(pk=first.pk)
                self.assertEqual(current.snapshot, first.snapshot)
                self.assertEqual(current.producer_revision, first.producer_revision)
                self.assertEqual(current.inventory_baseline, first.inventory_baseline)
        models.MastraoRoomClosure.objects.filter(pk=closure.pk).update(
            receipt_claims=claims
        )
        self.assertEqual(
            domain.prepare_native_capture_domain(self.binding.pk).snapshot,
            first.snapshot,
        )

    def test_three_overflows_persist_higher_invalidation_and_fence_old_ack(self):
        for reason in (
            "observation_limit_exceeded",
            "epoch_limit_exceeded",
            "payload_limit_exceeded",
        ):
            with self.subTest(reason=reason):
                call_command("flush", verbosity=0, interactive=False)
                self.binding, self.recording = synthetic_rtc_binding()
                first = domain.prepare_native_capture_domain(self.binding.pk)
                old = domain.claim_native_capture_domain(first.pk)
                if reason == "epoch_limit_exceeded":
                    _, epoch, _ = synthetic_rtc_capture(self.binding)
                    models.MastraoRtcTrackEpoch.objects.bulk_create(
                        [
                            models.MastraoRtcTrackEpoch(
                                connection_id=epoch.connection_id,
                                track_sid=f"TR_synthetic_{index}",
                            )
                            for index in range(domain.MAX_EPOCHS)
                        ]
                    )
                else:
                    count = domain.MAX_OBSERVATIONS
                    if reason == "observation_limit_exceeded":
                        count += 1
                    models.MastraoRtcObservation.objects.bulk_create(
                        [
                            models.MastraoRtcObservation(
                                room_binding=self.binding,
                                event_id=f"EV_{index}_" + "e" * 115,
                                payload_digest="d" * 64,
                                event_type="track_published",
                                event_time_seconds=1,
                                room_sid="RM_" + "r" * 120,
                                participant_sid="PA_" + "p" * 120,
                                track_sid="TR_" + "t" * 116 + str(index).zfill(4),
                                track_type=0,
                                track_source=2,
                            )
                            for index in range(count)
                        ]
                    )
                invalid = domain.prepare_native_capture_domain(self.binding.pk)
                self.assertEqual(invalid.producer_revision, first.producer_revision + 1)
                self.assertEqual(invalid.snapshot["kind"], "invalidation")
                self.assertEqual(invalid.snapshot["reason"], reason)
                self.assertNotIn("observations", invalid.snapshot)
                self.assertNotIn("counts", invalid.snapshot)
                self.assertEqual(
                    domain.finish_native_capture_domain(old, acknowledged=True), 0
                )
                self.assertEqual(
                    domain.finish_native_capture_domain(old, acknowledged=False), 0
                )
                with responses.RequestsMock() as http:
                    http.add(
                        responses.POST,
                        settings.MASTRAO_CORE_NATIVE_CAPTURE_DOMAIN_ENDPOINT,
                        body=requests.ConnectionError("synthetic ACK loss"),
                    )
                    self.assertEqual(
                        domain.deliver_native_capture_domain(invalid.pk), 0
                    )
                models.MastraoNativeCaptureDomain.objects.filter(pk=invalid.pk).update(
                    next_attempt_at=timezone.now()
                )
                replay = domain.prepare_native_capture_domain(self.binding.pk)
                self.assertEqual(replay.producer_revision, invalid.producer_revision)
                self.assertEqual(replay.snapshot_digest, invalid.snapshot_digest)
                with responses.RequestsMock() as http:
                    http.add_callback(
                        responses.POST,
                        settings.MASTRAO_CORE_NATIVE_CAPTURE_DOMAIN_ENDPOINT,
                        callback=self.assert_wire_request,
                    )
                    self.assertEqual(
                        domain.deliver_native_capture_domain(invalid.pk), 1
                    )

    def test_strict_ack_and_supersession_never_claim_closed(self):
        cursor = domain.prepare_native_capture_domain(self.binding.pk)
        mutations = [
            {"version": True},
            {"producer_revision": True},
            {"snapshot_digest": "a" * 64},
            {"domain_ref": str(self.binding.pk)},
            {"current_revision": True},
            {"current_revision": 0},
            {"current_snapshot_digest": "f" * 64},
            {"outcome": "closed"},
            {"outcome": "superseded"},
            {"extra": "unaccepted"},
        ]
        for mutation in mutations:
            with self.subTest(mutation=mutation), responses.RequestsMock() as http:
                http.add_callback(
                    responses.POST,
                    settings.MASTRAO_CORE_NATIVE_CAPTURE_DOMAIN_ENDPOINT,
                    callback=lambda request, mutation=mutation: (
                        self.assert_wire_request(request, mutation=mutation)
                    ),
                )
                self.assertEqual(domain.deliver_native_capture_domain(cursor.pk), 0)
                cursor.refresh_from_db()
                self.assertIsNone(cursor.acknowledged_at)
                models.MastraoNativeCaptureDomain.objects.filter(pk=cursor.pk).update(
                    next_attempt_at=timezone.now()
                )
        with responses.RequestsMock() as http:
            http.add_callback(
                responses.POST,
                settings.MASTRAO_CORE_NATIVE_CAPTURE_DOMAIN_ENDPOINT,
                callback=lambda request: self.assert_wire_request(
                    request,
                    outcome="superseded",
                    mutation={
                        "current_revision": 2,
                        "current_snapshot_digest": "f" * 64,
                    },
                ),
            )
            self.assertEqual(domain.deliver_native_capture_domain(cursor.pk), 1)

    def test_reconciler_endpoint_off_and_periodic_late_publication(self):
        original = settings.MASTRAO_CORE_NATIVE_CAPTURE_DOMAIN_ENDPOINT
        settings.MASTRAO_CORE_NATIVE_CAPTURE_DOMAIN_ENDPOINT = ""
        with responses.RequestsMock() as http:
            self.assertEqual(domain.reconcile_native_capture_domains(), 0)
            self.assertFalse(http.calls)
        self.assertFalse(models.MastraoNativeCaptureDomain.objects.exists())
        settings.MASTRAO_CORE_NATIVE_CAPTURE_DOMAIN_ENDPOINT = original
        with responses.RequestsMock() as http:
            http.add_callback(
                responses.POST, original, callback=self.assert_wire_request
            )
            self.assertEqual(domain.reconcile_native_capture_domains(), 1)
            first = models.MastraoNativeCaptureDomain.objects.get()
            synthetic_rtc_publication(self.binding)
            models.MastraoNativeCaptureDomain.objects.update(
                next_snapshot_at=timezone.now()
            )
            self.assertEqual(domain.reconcile_native_capture_domains(), 1)
            first.refresh_from_db()
            self.assertEqual(first.producer_revision, 2)
            self.assertEqual(first.snapshot["counts"]["observations"], 1)
            self.assertEqual(len(http.calls), 2)

    def test_video_and_screen_facts_stay_out_of_microphone_domain(self):
        microphone = synthetic_rtc_publication(self.binding)
        correlate_verified_observation(microphone)
        video = synthetic_rtc_publication(self.binding, track="TR_synthetic_video")
        video.track_type, video.track_source = 1, 1
        video.save()
        correlate_verified_observation(video)
        ended = synthetic_rtc_publication(
            self.binding, track=video.track_sid, event="track_unpublished"
        )
        ended.track_type, ended.track_source = 1, 1
        ended.save()
        correlate_verified_observation(ended)
        early_screen_end = synthetic_rtc_publication(
            self.binding, track="TR_synthetic_screen", event="track_unpublished"
        )
        early_screen_end.track_type, early_screen_end.track_source = 1, 3
        early_screen_end.save()
        correlate_verified_observation(early_screen_end)
        cursor = domain.prepare_native_capture_domain(self.binding.pk)
        self.assertEqual(
            cursor.snapshot["counts"], {"observations": 1, "epochs": 1, "captures": 0}
        )
        self.assertEqual(
            cursor.snapshot["observations"][0]["observation_ref"], str(microphone.pk)
        )
        self.assertEqual(cursor.snapshot["epochs"][0]["admission"], "unadmitted")
        self.assertIsNone(cursor.snapshot["epochs"][0]["refusal_digest"])

    def assert_late_video_preserves_exported_terminal(self, *, invalidate_first):
        terminal = synthetic_rtc_publication(self.binding, event="track_unpublished")
        terminal.track_type, terminal.track_source = 0, 0
        terminal.save()
        correlate_verified_observation(terminal)
        first = domain.prepare_native_capture_domain(self.binding.pk)
        claim = domain.claim_native_capture_domain(first.pk)
        self.assertEqual(
            domain.finish_native_capture_domain(claim, acknowledged=True), 1
        )
        self.assertEqual(
            first.snapshot["counts"], {"observations": 1, "epochs": 1, "captures": 0}
        )
        self.assertFalse(first.snapshot["epochs"][0]["conflict"])
        previous_revision = first.producer_revision
        if invalidate_first:
            synthetic_rtc_publication(
                self.binding, track="TR_synthetic_other_microphone"
            )
            candidate = domain.prepare_native_capture_domain(self.binding.pk)
            claim = domain.claim_native_capture_domain(candidate.pk)
            domain._persist_payload_invalidation(claim)
            candidate.refresh_from_db()
            self.assertEqual(candidate.snapshot["kind"], "invalidation")
            self.assertEqual(candidate.inventory_baseline["kind"], "inventory")
            self.assertIn(
                first.snapshot["observations"][0],
                candidate.inventory_baseline["observations"],
            )
            previous_revision = candidate.producer_revision
        video = synthetic_rtc_publication(self.binding)
        video.track_type, video.track_source = 1, 1
        video.save()
        correlate_verified_observation(video)
        second = domain.prepare_native_capture_domain(self.binding.pk)
        self.assertEqual(second.producer_revision, previous_revision + 1)
        self.assertEqual(second.snapshot["kind"], "inventory")
        original = first.snapshot["observations"][0]
        self.assertIn(original, second.snapshot["observations"])
        self.assertNotIn(
            str(video.pk),
            [row["observation_ref"] for row in second.snapshot["observations"]],
        )
        epoch = second.snapshot["epochs"][0]
        self.assertEqual(epoch["epoch_ref"], first.snapshot["epochs"][0]["epoch_ref"])
        self.assertEqual(epoch["end_observation_refs"], [str(terminal.pk)])
        self.assertTrue(epoch["ended"])
        self.assertTrue(epoch["conflict"])
        self.assertEqual(epoch["admission"], "unadmitted")
        self.assertIsNone(epoch["refusal_digest"])
        self.assertIsNone(epoch["capture_ref"])
        self.assertEqual(second.snapshot["counts"]["epochs"], 1)
        self.assertEqual(second.snapshot["counts"]["captures"], 0)
        with responses.RequestsMock() as http:
            http.add_callback(
                responses.POST,
                settings.MASTRAO_CORE_NATIVE_CAPTURE_DOMAIN_ENDPOINT,
                callback=self.assert_wire_request,
            )
            self.assertEqual(domain.deliver_native_capture_domain(second.pk), 1)
        replay = domain.prepare_native_capture_domain(self.binding.pk)
        self.assertEqual(replay.producer_revision, second.producer_revision)
        self.assertEqual(replay.snapshot, second.snapshot)
        self.assertIsNotNone(replay.acknowledged_at)

    def test_acknowledged_terminal_survives_late_video_as_conflicted_epoch(self):
        self.assert_late_video_preserves_exported_terminal(invalidate_first=False)

    def test_inventory_baseline_survives_invalidation_then_late_video(self):
        self.assert_late_video_preserves_exported_terminal(invalidate_first=True)

    def test_pending_invalidations_are_delivered_before_inventories(self):
        ordinary = domain.prepare_native_capture_domain(self.binding.pk)
        second_binding, _ = synthetic_rtc_binding()
        models.MastraoRtcObservation.objects.bulk_create(
            [
                models.MastraoRtcObservation(
                    room_binding=second_binding,
                    event_id=f"EV_synthetic_priority_{index}",
                    payload_digest="f" * 64,
                    event_type="track_published",
                    event_time_seconds=1,
                    room_sid="RM_synthetic",
                    participant_sid="PA_synthetic",
                    track_sid=f"TR_synthetic_{index}",
                    track_type=0,
                    track_source=2,
                )
                for index in range(domain.MAX_OBSERVATIONS + 1)
            ]
        )
        invalid = domain.prepare_native_capture_domain(second_binding.pk)
        models.MastraoNativeCaptureDomain.objects.update(
            next_snapshot_at=timezone.now() + timedelta(days=1)
        )
        with responses.RequestsMock() as http:
            http.add_callback(
                responses.POST,
                settings.MASTRAO_CORE_NATIVE_CAPTURE_DOMAIN_ENDPOINT,
                callback=self.assert_wire_request,
            )
            self.assertEqual(domain.reconcile_native_capture_domains(limit=1), 1)
            invalid.refresh_from_db()
            self.assertIsNotNone(invalid.acknowledged_at)
        ordinary.refresh_from_db()
        self.assertIsNone(ordinary.acknowledged_at)

    def test_real_serialization_conflict_restarts_whole_snapshot(self):
        cursor = domain.prepare_native_capture_domain(self.binding.pk)
        original = domain._lock_domain
        attempts = []

        def conflict_once(binding_id):
            attempts.append(True)
            if len(attempts) == 1:
                models.MastraoNativeCaptureDomain.objects.get(pk=cursor.pk)
                with psycopg.connect(
                    **connection.get_connection_params()
                ) as independent:
                    independent.execute(
                        "UPDATE meet_mastrao_native_capture_domain SET next_snapshot_at=now() WHERE id=%s",
                        [cursor.pk],
                    )
            # The first SELECT FOR UPDATE now raises a real PostgreSQL 40001.
            return original(binding_id)

        with mock.patch.object(domain, "_lock_domain", side_effect=conflict_once):
            replay = domain.prepare_native_capture_domain(self.binding.pk)
        self.assertEqual(len(attempts), 2)
        self.assertEqual(replay.producer_revision, cursor.producer_revision)

    def test_concurrent_cursor_creation_keeps_one_domain_identity(self):
        rendezvous = Barrier(2)
        original = domain._lock_domain

        def synchronized_creation(binding_id):
            exists = models.MastraoNativeCaptureDomain.objects.filter(
                room_binding_id=binding_id
            ).exists()
            if not exists:
                rendezvous.wait(timeout=10)
            return original(binding_id)

        def run():
            try:
                return domain.prepare_native_capture_domain(self.binding.pk)
            finally:
                connections.close_all()

        with (
            mock.patch.object(
                domain, "_lock_domain", side_effect=synchronized_creation
            ),
            ThreadPoolExecutor(max_workers=2) as pool,
        ):
            futures = [pool.submit(run) for _ in range(2)]
            first, second = [future.result(timeout=15) for future in futures]
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(first.producer_revision, 1)
        self.assertEqual(second.producer_revision, 1)
        self.assertEqual(models.MastraoNativeCaptureDomain.objects.count(), 1)

    def test_signed_budget_growth_after_prepare_persists_invalidation_before_send(self):
        for index in range(12):
            synthetic_rtc_publication(self.binding, track=f"TR_synthetic_{index}")
        cursor = domain.prepare_native_capture_domain(self.binding.pk)
        initial_budget = len(json.dumps(domain._wire_body(cursor.snapshot)).encode())
        original_kid = settings.MASTRAO_RECORDING_RECEIPT_KEY_ID
        settings.MASTRAO_RECORDING_RECEIPT_KEY_ID += "longer" * 50
        try:
            with (
                mock.patch.object(domain, "MAX_BODY_BYTES", initial_budget),
                responses.RequestsMock() as http,
            ):
                http.add_callback(
                    responses.POST,
                    settings.MASTRAO_CORE_NATIVE_CAPTURE_DOMAIN_ENDPOINT,
                    callback=self.assert_wire_request,
                )
                self.assertEqual(domain.deliver_native_capture_domain(cursor.pk), 1)
        finally:
            settings.MASTRAO_RECORDING_RECEIPT_KEY_ID = original_kid
        cursor.refresh_from_db()
        self.assertEqual(cursor.producer_revision, 2)
        self.assertEqual(cursor.snapshot["reason"], "payload_limit_exceeded")

    def test_source_receipt_projection_excludes_manifest_and_asr(self):
        _, _, capture = synthetic_rtc_capture(self.binding)
        synthetic_rtc_publication(self.binding, event="track_unpublished")
        capture.provider_job_ref, capture.observed_status = "EG_SyntheticComplete", 3
        capture.drained_at = timezone.now()
        capture.source_manifest = {
            "object_ref": "recordings/synthetic-private.ogg",
            "coverage": "epoch_only",
        }
        capture.source_receipt = {"source_ref": "e0000000-0000-4000-8000-000000000000"}
        capture.asr_receipt = {"synthetic_result_state": "pending"}
        capture.save()
        cursor = domain.prepare_native_capture_domain(self.binding.pk)
        projected = cursor.snapshot["captures"][0]
        self.assertEqual(
            projected["manifest_digest"],
            domain._sha256_canonical(capture.source_manifest),
        )
        self.assertEqual(
            projected["source_receipt_ref"], capture.source_receipt["source_ref"]
        )
        self.assertNotIn("recordings/", json.dumps(cursor.snapshot))
        self.assertNotIn("asr", json.dumps(cursor.snapshot))
        capture.asr_receipt = {"synthetic_result_state": "failed"}
        capture.save()
        replay = domain.prepare_native_capture_domain(self.binding.pk)
        self.assertEqual(replay.producer_revision, cursor.producer_revision)
        with responses.RequestsMock() as http:
            http.add_callback(
                responses.POST,
                settings.MASTRAO_CORE_NATIVE_CAPTURE_DOMAIN_ENDPOINT,
                callback=self.assert_wire_request,
            )
            self.assertEqual(domain.deliver_native_capture_domain(cursor.pk), 1)

    def test_upgrade_adds_cursor_without_touching_existing_rtc_fact(self):
        self.addCleanup(call_command, "migrate", verbosity=0, interactive=False)
        previous = [("core", "0061_native_track_observation_delivery")]
        latest = [("core", "0063_native_capture_domain_inventory_baseline")]
        executor = MigrationExecutor(connection)
        executor.migrate(previous)
        try:
            publication = synthetic_rtc_publication(self.binding)
        finally:
            MigrationExecutor(connection).migrate(latest)
        self.assertTrue(
            models.MastraoRtcObservation.objects.filter(pk=publication.pk).exists()
        )
        self.assertFalse(models.MastraoNativeCaptureDomain.objects.exists())
        cursor = domain.prepare_native_capture_domain(self.binding.pk)
        self.assertEqual(cursor.producer_revision, 1)
        self.assertEqual(
            cursor.snapshot["observations"][0]["observation_ref"], str(publication.pk)
        )

    def test_baseline_migration_preserves_inventory_revision_ack_and_claim(self):
        self.addCleanup(call_command, "migrate", verbosity=0, interactive=False)
        synthetic_rtc_capture(self.binding)
        prepared = domain.prepare_native_capture_domain(self.binding.pk)
        domain.claim_native_capture_domain(prepared.pk)
        before = models.MastraoNativeCaptureDomain.objects.values().get(pk=prepared.pk)
        previous = [("core", "0062_native_capture_domain_delivery")]
        latest = [("core", "0063_native_capture_domain_inventory_baseline")]
        try:
            MigrationExecutor(connection).migrate(previous)
        finally:
            MigrationExecutor(connection).migrate(latest)
        after = models.MastraoNativeCaptureDomain.objects.values().get(pk=prepared.pk)
        self.assertEqual(after["inventory_baseline"], before["snapshot"])
        self.assertEqual(after, before)


if __name__ == "__main__":
    suite = unittest.main(verbosity=2, exit=False)
    if POSTGRES and suite.result.wasSuccessful():
        sys.stdout.write(
            json.dumps({"synthetic_domain_assertions": list(WIRE_ASSERTIONS.values())})
            + "\n"
        )
    sys.exit(not suite.result.wasSuccessful())
