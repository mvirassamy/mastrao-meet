"""PostgreSQL proof of mutable video decisions with request idempotence."""

from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

from django.db import IntegrityError, connection
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone

import pytest

from core import models
from core.factories import RoomFactory, UserFactory
from core.mastrao_recording_session import record_decision


@pytest.mark.django_db(transaction=True)
def test_yes_no_yes_fails_before_migration_and_succeeds_after_with_exact_replay():
    """Run the real decision boundary against both schema versions."""
    assert connection.vendor == "postgresql"
    executor = MigrationExecutor(connection)
    executor.migrate([("core", "0050_native_source_manifest")])
    try:
        room = RoomFactory()
        room_binding = models.MastraoRoomBinding.objects.create(
            room=room,
            owner=UserFactory(),
            effect_key=f"effect_{uuid4().hex}",
            arguments_digest="a" * 64,
            meeting_ref="meeting_0123456789abcdef",
            room_ref="room_0123456789abcdef",
            owner_ref="owner_0123456789abcdef",
            provider_binding_digest="b" * 64,
        )
        binding = models.MastraoRecordingBinding.objects.create(
            room_binding=room_binding,
            organization_external_id="organization_test",
            meeting_ref=room_binding.meeting_ref,
            room_ref=room_binding.room_ref,
            recording_ref="recording_0123456789abcdef",
            provider_binding_digest="b" * 64,
            policy_ref="policy_0123456789abcdef",
            notice_version="notice_0123456789abcdef",
            notice_digest="c" * 64,
            retention_expires_at=timezone.now() + timedelta(days=30),
        )
        status = {
            "mode": "recorded",
            "organization_external_id": binding.organization_external_id,
            "meeting_ref": binding.meeting_ref,
            "room_ref": binding.room_ref,
            "recording_ref": binding.recording_ref,
            "policy_ref": binding.policy_ref,
            "notice_version": binding.notice_version,
            "notice_digest": binding.notice_digest,
            "purpose": binding.purpose,
            "scope": binding.scope,
            "retention_expires_at": int(binding.retention_expires_at.timestamp()),
        }
        participant = {
            "kind": "guest",
            "ref": "guest_0123456789abcdef",
            "session_digest": "d" * 64,
            "compact": "grant.payload.signature",
        }
        receipts = {}
        decisions = []

        def core(**kwargs):
            payload = kwargs["body"]["decision_assertion"]
            key = payload["decision_request_id"]
            if key not in receipts:
                decisions.append(payload["decision"])
                receipts[key] = {
                    "version": 1,
                    "meeting_ref": binding.meeting_ref,
                    "recording_ref": binding.recording_ref,
                    "decision": payload["decision"],
                    "recording_state": "collecting",
                    "state_version": len(receipts) + 1,
                }
            return receipts[key]

        with (
            patch(
                "core.mastrao_recording_session._participant", return_value=participant
            ),
            patch(
                "core.mastrao_recording_session.recording_session_status",
                return_value=status,
            ),
            patch("core.mastrao_recording_session._sync_binding", return_value=binding),
            patch(
                "core.mastrao_recording_session.sign_decision_assertion",
                side_effect=lambda payload: payload,
            ),
            patch(
                "core.mastrao_recording_session.compact_digest", return_value="e" * 64
            ),
            patch("core.mastrao_recording_session.post_core_json", side_effect=core),
        ):
            record_decision(object(), room, "accepted", "decision_first_0123456789")
            record_decision(object(), room, "refused", "decision_second_0123456789")
            with pytest.raises(IntegrityError) as rejected:
                record_decision(object(), room, "accepted", "decision_third_0123456789")
            assert (
                rejected.value.__cause__.diag.constraint_name
                == "unique_mastrao_recording_session_decision"
            )
            MigrationExecutor(connection).migrate(
                [("core", "0051_mutable_video_decisions")]
            )
            record_decision(object(), room, "accepted", "decision_third_0123456789")
            record_decision(object(), room, "accepted", "decision_third_0123456789")
        assert decisions == ["accepted", "refused", "accepted"]
        assert (
            list(
                binding.decisions.order_by("created_at").values_list(
                    "decision", flat=True
                )
            )
            == decisions
        )
        assert binding.decisions.count() == 3
    finally:
        MigrationExecutor(connection).migrate(
            [("core", "0051_mutable_video_decisions")]
        )
