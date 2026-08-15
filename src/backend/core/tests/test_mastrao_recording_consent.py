"""Focused recording-consent media gate and native-regression proofs."""

import hashlib
import io
import time
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest import mock
from urllib.parse import urlencode

from django.db import transaction as django_transaction
from django.test import Client
from django.utils import timezone

import pytest
from livekit import api as livekit_api
from rest_framework.test import APIClient

from core import models, utils
from core.factories import RoomFactory, UserFactory
from core.mastrao_recording_access import RETRY_COOKIE, SESSION_KEY
from core.mastrao_recording_adapter import (
    _apply_start,
    _apply_stop,
    _handle,
    _prepare_start,
)
from core.mastrao_recording_artifact import (
    _prepare_artifact_receipt,
    finalize_mastrao_artifact,
)
from core.mastrao_recording_contract import (
    RecordingContractRefused,
    build_start_receipt_claims,
)
from core.mastrao_recording_reconciler import reconcile_mastrao_recording
from core.mastrao_recording_session import (
    _sync_binding,
    activate_recording,
    media_allowed,
    public_projection,
    recording_session_status,
)
from core.models import RoomAccessLevel
from core.recording.worker.exceptions import RecordingStopError


def _recorded(state, decision="absent"):
    return {
        "mode": "recorded",
        "recording_state": state,
        "decision": decision,
    }


def test_recording_media_gate_matches_capture_semantics():
    assert media_allowed(None)
    assert media_allowed({"mode": "disabled"})
    assert not media_allowed({"mode": "unset"})
    for state in ("collecting", "authorized", "starting", "active"):
        assert not media_allowed(_recorded(state))
        assert media_allowed(_recorded(state, "accepted"))
    assert not media_allowed(_recorded("stopping", "accepted"))
    for state in ("cancelled", "failed", "processing", "available"):
        assert media_allowed(_recorded(state))


def test_livekit_egress_reference_is_a_valid_provider_receipt_reference():
    claims = build_start_receipt_claims(
        {
            "organization_external_id": "organization_0123456789",
            "meeting_ref": "meeting_0123456789abcdef",
            "room_ref": "room_0123456789abcdef",
            "recording_ref": "recording_0123456789abcdef",
            "provider_binding_digest": "a" * 64,
            "effect_key": "effect_0123456789abcdef",
            "arguments_digest": "b" * 64,
            "jti": "request_0123456789abcdef",
        },
        "EG_oju7PDAhx8k7",
        "started",
    )
    assert claims["provider_recording_ref"] == "EG_oju7PDAhx8k7"


def test_feature_off_does_not_call_core_or_change_native_projection(settings):
    settings.MASTRAO_MEETING_RECORDING_ENABLED = False
    room = mock.Mock()
    room.slug = "native-room"
    room.mastrao_binding = mock.Mock()
    with (
        mock.patch(
            "core.mastrao_recording_session.models.MastraoRecordingBinding.objects.filter"
        ) as bindings,
        mock.patch("core.mastrao_recording_session.post_core_json") as post_core_json,
    ):
        bindings.return_value.exists.return_value = False
        assert recording_session_status(mock.Mock(), room) is None
    post_core_json.assert_not_called()


def test_feature_off_keeps_existing_recording_policy_fail_closed(settings):
    settings.MASTRAO_MEETING_RECORDING_ENABLED = False
    settings.MASTRAO_CORE_RECORDING_SESSION_STATUS_ENDPOINT = (
        "http://cabinet-core:3911/internal/v1/meetings/recording/session-status"
    )
    participant = {
        "kind": "guest",
        "compact": "header.payload.signature",
        "session_digest": "d" * 64,
        "claims": {
            "organization_external_id": "organization_0123456789",
            "meeting_ref": "meeting_0123456789abcdef",
            "room_ref": "room_0123456789abcdef",
        },
    }
    room = mock.Mock()
    room.slug = "room_0123456789abcdef0123456789abcdef"
    room.mastrao_binding.room_ref = participant["claims"]["room_ref"]
    status = {
        "version": 1,
        **participant["claims"],
        "mode": "recorded",
        "recording_ref": "recording_0123456789abcdef",
        "policy_ref": "policy_0123456789abcdef",
        "notice_version": "notice_0123456789abcdef",
        "notice_digest": "a" * 64,
        "purpose": "meeting_recording",
        "scope": "room_composite_audio_video_screen",
        "retention_expires_at": int(time.time()) + 3600,
        "recording_state": "active",
        "decision": "absent",
    }
    with (
        mock.patch(
            "core.mastrao_recording_session.models.MastraoRecordingBinding.objects.filter"
        ) as bindings,
        mock.patch(
            "core.mastrao_recording_session._participant", return_value=participant
        ),
        mock.patch(
            "core.mastrao_recording_session.post_core_json", return_value=status
        ) as post_core_json,
        mock.patch("core.mastrao_recording_session._sync_binding"),
    ):
        bindings.return_value.exists.return_value = True
        projection = recording_session_status(mock.Mock(), room)

    post_core_json.assert_called_once()
    assert projection["mode"] == "recorded"
    assert not media_allowed(projection)


def test_feature_off_refuses_browser_recording_activation(settings):
    settings.MASTRAO_MEETING_RECORDING_ENABLED = False

    with pytest.raises(RecordingContractRefused):
        activate_recording(mock.Mock(), mock.Mock(), "activationrequest_0123456789")


def test_session_status_posts_only_the_bound_participant_grant(settings):
    settings.MASTRAO_MEETING_RECORDING_ENABLED = True
    settings.MASTRAO_CORE_RECORDING_SESSION_STATUS_ENDPOINT = (
        "http://cabinet-core:3911/internal/v1/meetings/recording/session-status"
    )
    participant = {
        "kind": "guest",
        "compact": "header.payload.signature",
        "session_digest": "d" * 64,
        "claims": {
            "organization_external_id": "organization_0123456789",
            "meeting_ref": "meeting_0123456789abcdef",
            "room_ref": "room_0123456789abcdef",
        },
    }
    room = mock.Mock()
    room.mastrao_binding.room_ref = participant["claims"]["room_ref"]
    status = {
        "version": 1,
        **participant["claims"],
        "mode": "disabled",
    }
    with (
        mock.patch(
            "core.mastrao_recording_session._participant", return_value=participant
        ),
        mock.patch(
            "core.mastrao_recording_session.post_core_json", return_value=status
        ) as post_core_json,
        mock.patch("core.mastrao_recording_session._sync_binding"),
    ):
        assert recording_session_status(mock.Mock(), room) == {
            **status,
            "participant_kind": "guest",
        }
    assert post_core_json.call_args.kwargs["body"] == {
        "participant_grant": "header.payload.signature",
        "participant_session_digest": "d" * 64,
    }


def test_sync_binding_does_not_touch_an_unchanged_projection():
    retention_expires_at = 2_000_000_000
    room_binding = SimpleNamespace(provider_binding_digest="b" * 64)
    room = SimpleNamespace(mastrao_binding=room_binding)
    status = {
        "mode": "recorded",
        "organization_external_id": "organization_0123456789",
        "meeting_ref": "meeting_0123456789abcdef",
        "room_ref": "room_0123456789abcdef",
        "recording_ref": "recording_0123456789abcdef",
        "policy_ref": "policy_0123456789abcdef",
        "notice_version": "notice_0123456789abcdef",
        "notice_digest": "a" * 64,
        "purpose": "meeting_recording",
        "scope": "room_composite_audio_video_screen",
        "retention_expires_at": retention_expires_at,
        "recording_state": "processing",
    }
    binding = SimpleNamespace(
        recording_ref=status["recording_ref"],
        organization_external_id=status["organization_external_id"],
        meeting_ref=status["meeting_ref"],
        room_ref=status["room_ref"],
        provider_binding_digest=room_binding.provider_binding_digest,
        policy_ref=status["policy_ref"],
        notice_version=status["notice_version"],
        notice_digest=status["notice_digest"],
        purpose=status["purpose"],
        scope=status["scope"],
        retention_expires_at=datetime.fromtimestamp(retention_expires_at, tz=UTC),
        state=models.MastraoRecordingBinding.State.PROCESSING,
        save=mock.Mock(),
    )

    with mock.patch(
        "core.mastrao_recording_session.models.MastraoRecordingBinding.objects.filter"
    ) as bindings:
        bindings.return_value.first.return_value = binding
        assert _sync_binding(room, status) is binding

    binding.save.assert_not_called()


def test_recorded_public_projection_exposes_only_safe_participant_kind():
    projection = public_projection(
        {
            **_recorded("collecting"),
            "recording_ref": "recording_0123456789abcdef",
            "notice_version": "notice_0123456789abcdef",
            "notice_digest": "a" * 64,
            "purpose": "meeting_recording",
            "scope": "room_composite_audio_video_screen",
            "retention_expires_at": 2_000_000_000,
            "participant_kind": "guest",
        }
    )

    assert projection["participant_kind"] == "guest"
    assert "participant_ref" not in projection
    assert "participant_session_digest" not in projection


def test_recorded_absent_never_mints_livekit_token(db):
    room = RoomFactory(access_level=RoomAccessLevel.PUBLIC)
    status = _recorded("collecting")
    status.update(
        {
            "recording_ref": "recording_0123456789abcdef",
            "notice_version": "notice_0123456789abcdef",
            "notice_digest": "a" * 64,
            "purpose": "meeting_recording",
            "scope": "room_composite_audio_video_screen",
            "retention_expires_at": 2_000_000_000,
            "participant_kind": "guest",
        }
    )
    client = APIClient()
    with (
        mock.patch("core.api.viewsets.recording_session_status", return_value=status),
        mock.patch.object(utils, "generate_livekit_config") as generate,
    ):
        response = client.post(
            f"/api/v1.0/rooms/{room.id}/request-entry/", {"username": "guest"}
        )
    assert response.status_code == 200
    assert response.json()["livekit"] is None
    assert response.json()["recording"]["decision"] == "absent"
    generate.assert_not_called()


def test_recorded_terminal_state_allows_unrecorded_token(db):
    room = RoomFactory(access_level=RoomAccessLevel.PUBLIC)
    status = _recorded("cancelled", "refused")
    status.update(
        {
            "recording_ref": "recording_0123456789abcdef",
            "notice_version": "notice_0123456789abcdef",
            "notice_digest": "a" * 64,
            "purpose": "meeting_recording",
            "scope": "room_composite_audio_video_screen",
            "retention_expires_at": 2_000_000_000,
            "participant_kind": "guest",
        }
    )
    client = APIClient()
    with (
        mock.patch("core.api.viewsets.recording_session_status", return_value=status),
        mock.patch.object(
            utils, "generate_livekit_config", return_value={"token": "unrecorded"}
        ) as generate,
        mock.patch("core.services.lobby.ensure_livekit_room"),
    ):
        response = client.post(
            f"/api/v1.0/rooms/{room.id}/request-entry/", {"username": "guest"}
        )
    assert response.status_code == 200
    assert response.json()["livekit"] == {"token": "unrecorded"}
    generate.assert_called_once()


def test_recording_start_locks_only_the_non_nullable_binding(db, settings):
    settings.MASTRAO_MEETING_RECORDING_ENABLED = True
    owner = UserFactory()
    room = RoomFactory(access_level=RoomAccessLevel.RESTRICTED)
    room_binding = models.MastraoRoomBinding.objects.create(
        effect_key="effect_room_0123456789",
        arguments_digest="a" * 64,
        meeting_ref="meeting_0123456789abcdef",
        room_ref="room_0123456789abcdef",
        owner_ref="owner_0123456789abcdef",
        room=room,
        owner=owner,
        provider_binding_digest="b" * 64,
    )
    effect = {
        "organization_external_id": "organization_0123456789",
        "meeting_ref": room_binding.meeting_ref,
        "room_ref": room_binding.room_ref,
        "recording_ref": "recording_0123456789abcdef",
        "provider_binding_digest": room_binding.provider_binding_digest,
        "policy_ref": "policy_0123456789abcdef",
        "notice_version": "notice_0123456789abcdef",
        "notice_digest": "c" * 64,
        "purpose": "meeting_recording",
        "scope": "room_composite_audio_video_screen",
        "retention_expires_at": int((timezone.now() + timedelta(days=30)).timestamp()),
        "effect_key": "effect_start_0123456789",
        "arguments_digest": "d" * 64,
        "jti": "request_0123456789abcdef",
    }

    recording_binding, local_effect, first_delivery = _prepare_start(effect)

    assert recording_binding.recording is not None
    assert local_effect.recording_binding == recording_binding
    assert local_effect.operation == "start"
    assert local_effect.state == models.MastraoRecordingEffect.State.APPLYING
    assert first_delivery


def test_feature_off_refuses_a_new_recording_start(db, settings):
    settings.MASTRAO_MEETING_RECORDING_ENABLED = False
    owner = UserFactory()
    room = RoomFactory(access_level=RoomAccessLevel.RESTRICTED)
    room_binding = models.MastraoRoomBinding.objects.create(
        effect_key="effect_room_disabled_012345",
        arguments_digest="a" * 64,
        meeting_ref="meeting_disabled_0123456789",
        room_ref="room_disabled_0123456789abc",
        owner_ref="owner_disabled_0123456789ab",
        room=room,
        owner=owner,
        provider_binding_digest="b" * 64,
    )
    effect = {
        "organization_external_id": "organization_0123456789",
        "meeting_ref": room_binding.meeting_ref,
        "room_ref": room_binding.room_ref,
        "recording_ref": "recording_disabled_01234567",
        "provider_binding_digest": room_binding.provider_binding_digest,
        "policy_ref": "policy_disabled_0123456789",
        "notice_version": "notice_disabled_012345678",
        "notice_digest": "c" * 64,
        "purpose": "meeting_recording",
        "scope": "room_composite_audio_video_screen",
        "retention_expires_at": int((timezone.now() + timedelta(days=30)).timestamp()),
        "effect_key": "effect_start_disabled_012345",
        "arguments_digest": "d" * 64,
        "jti": "request_start_disabled_01234",
    }

    with pytest.raises(RecordingContractRefused):
        _prepare_start(effect)

    assert not models.MastraoRecordingBinding.objects.filter(
        room_binding=room_binding
    ).exists()


def test_feature_off_does_not_block_stop_delivery(settings, rf):
    settings.MASTRAO_MEETING_RECORDING_ENABLED = False
    effect = {"operation": "stop"}
    verifier = mock.Mock(return_value=effect)
    applier = mock.Mock(return_value="receipt.payload.signature")
    request = rf.post(
        "/api/v1.0/recording/stop/",
        {"recording_stop_effect": "header.payload.signature"},
        content_type="application/json",
    )

    response = _handle(
        request,
        "recording_stop_effect",
        verifier,
        applier,
        "recording_stop_receipt",
    )

    assert response.status_code == 200
    applier.assert_called_once_with(effect)


def _provider_egress(recording, status):
    return SimpleNamespace(
        egress_id="EG_oju7PDAhx8k7",
        room_name=str(recording.room_id),
        status=status,
        room_composite=SimpleNamespace(
            file_outputs=[SimpleNamespace(filepath=recording.key)]
        ),
    )


def test_start_retry_discovers_exact_egress_without_starting_again(db, settings):
    settings.MASTRAO_MEETING_RECORDING_ENABLED = True
    owner = UserFactory()
    room = RoomFactory(access_level=RoomAccessLevel.RESTRICTED)
    room_binding = models.MastraoRoomBinding.objects.create(
        effect_key="effect_room_retry_012345",
        arguments_digest="a" * 64,
        meeting_ref="meeting_retry_0123456789",
        room_ref="room_retry_0123456789abcd",
        owner_ref="owner_retry_0123456789ab",
        room=room,
        owner=owner,
        provider_binding_digest="b" * 64,
    )
    effect = {
        "organization_external_id": "organization_0123456789",
        "meeting_ref": room_binding.meeting_ref,
        "room_ref": room_binding.room_ref,
        "recording_ref": "recording_retry_0123456789",
        "provider_binding_digest": room_binding.provider_binding_digest,
        "policy_ref": "policy_retry_0123456789abc",
        "notice_version": "notice_retry_0123456789ab",
        "notice_digest": "c" * 64,
        "purpose": "meeting_recording",
        "scope": "room_composite_audio_video_screen",
        "retention_expires_at": int((timezone.now() + timedelta(days=30)).timestamp()),
        "effect_key": "effect_start_retry_012345",
        "arguments_digest": "d" * 64,
        "resolve_only": False,
        "jti": "request_start_retry_012345",
    }
    binding, _, _ = _prepare_start(effect)
    settings.MASTRAO_MEETING_RECORDING_ENABLED = False
    provider = _provider_egress(
        binding.recording, livekit_api.EgressStatus.EGRESS_ACTIVE
    )
    with (
        mock.patch(
            "core.mastrao_recording_adapter._exact_provider_egress",
            return_value=provider,
        ),
        mock.patch(
            "core.mastrao_recording_adapter.WorkerServiceMediator.start"
        ) as start,
        mock.patch(
            "core.mastrao_recording_adapter.sign_start_receipt",
            return_value="receipt.payload.signature",
        ),
    ):
        assert _apply_start(effect) == "receipt.payload.signature"
    start.assert_not_called()
    binding.refresh_from_db()
    assert binding.provider_recording_ref == provider.egress_id
    assert models.Recording.objects.filter(room=room).count() == 1


def test_resolve_only_start_without_provider_converges_after_grace_period():
    recording = SimpleNamespace(status=models.RecordingStatusChoices.INITIATED)
    recording_binding = SimpleNamespace(recording_id="recording-id")
    local_effect = SimpleNamespace(
        state=models.MastraoRecordingEffect.State.APPLYING,
        created_at=timezone.now() - timedelta(seconds=31),
    )
    effect = {"resolve_only": True}
    with (
        mock.patch(
            "core.mastrao_recording_adapter._prepare_start",
            return_value=(recording_binding, local_effect, False),
        ),
        mock.patch(
            "core.mastrao_recording_adapter.models.Recording.objects.select_related"
        ) as recordings,
        mock.patch(
            "core.mastrao_recording_adapter._exact_provider_egress",
            return_value=None,
        ),
        mock.patch(
            "core.mastrao_recording_adapter.report_mastrao_recording_failure"
        ) as report_failure,
    ):
        recordings.return_value.get.return_value = recording
        with pytest.raises(RecordingContractRefused) as refusal:
            _apply_start(effect)
    assert refusal.value.status == 503
    report_failure.assert_called_once_with(recording, None)


def test_stop_response_loss_reconciles_terminal_exact_egress(db):
    access, _ = _artifact_access()
    binding = access.recording_binding
    recording = binding.recording
    recording.status = models.RecordingStatusChoices.ACTIVE
    recording.worker_id = "EG_oju7PDAhx8k7"
    recording.save(update_fields=["status", "worker_id", "updated_at"])
    binding.state = models.MastraoRecordingBinding.State.ACTIVE
    binding.provider_recording_ref = recording.worker_id
    binding.save(update_fields=["state", "provider_recording_ref", "updated_at"])
    effect = {
        "organization_external_id": binding.organization_external_id,
        "meeting_ref": binding.meeting_ref,
        "room_ref": binding.room_ref,
        "recording_ref": binding.recording_ref,
        "provider_binding_digest": binding.provider_binding_digest,
        "provider_recording_ref": recording.worker_id,
        "effect_key": "effect_stop_retry_0123456",
        "arguments_digest": "e" * 64,
        "jti": "request_stop_retry_012345",
    }
    active = _provider_egress(recording, livekit_api.EgressStatus.EGRESS_ACTIVE)
    terminal = _provider_egress(recording, livekit_api.EgressStatus.EGRESS_COMPLETE)
    with (
        mock.patch(
            "core.mastrao_recording_adapter._exact_provider_egress",
            side_effect=[active, terminal],
        ),
        mock.patch(
            "core.mastrao_recording_adapter.WorkerServiceMediator.stop",
            side_effect=RecordingStopError(),
        ),
        mock.patch(
            "core.mastrao_recording_adapter.sign_stop_receipt",
            return_value="receipt.payload.signature",
        ),
    ):
        assert _apply_stop(effect) == "receipt.payload.signature"
    binding.refresh_from_db()
    assert binding.state == models.MastraoRecordingBinding.State.PROCESSING


def test_stop_retry_from_applying_reissues_exact_active_egress(db):
    access, _ = _artifact_access()
    binding = access.recording_binding
    recording = binding.recording
    recording.status = models.RecordingStatusChoices.ACTIVE
    recording.worker_id = "EG_oju7PDAhx8k7"
    recording.save(update_fields=["status", "worker_id", "updated_at"])
    binding.state = models.MastraoRecordingBinding.State.STOPPING
    binding.provider_recording_ref = recording.worker_id
    binding.save(update_fields=["state", "provider_recording_ref", "updated_at"])
    effect = {
        "organization_external_id": binding.organization_external_id,
        "meeting_ref": binding.meeting_ref,
        "room_ref": binding.room_ref,
        "recording_ref": binding.recording_ref,
        "provider_binding_digest": binding.provider_binding_digest,
        "provider_recording_ref": recording.worker_id,
        "effect_key": "effect_stop_applying_012345",
        "arguments_digest": "f" * 64,
        "jti": "request_stop_applying_012345",
    }
    models.MastraoRecordingEffect.objects.create(
        recording_binding=binding,
        effect_key=effect["effect_key"],
        operation=models.MastraoRecordingEffect.Operation.STOP,
        arguments_digest=effect["arguments_digest"],
        effect_jti=effect["jti"],
        state=models.MastraoRecordingEffect.State.APPLYING,
    )
    active = _provider_egress(recording, livekit_api.EgressStatus.EGRESS_ACTIVE)
    with (
        mock.patch(
            "core.mastrao_recording_adapter._exact_provider_egress",
            return_value=active,
        ),
        mock.patch("core.mastrao_recording_adapter.WorkerServiceMediator.stop") as stop,
        mock.patch(
            "core.mastrao_recording_adapter.sign_stop_receipt",
            return_value="receipt.payload.signature",
        ),
    ):
        assert _apply_stop(effect) == "receipt.payload.signature"
    stop.assert_called_once_with(recording)
    binding.refresh_from_db()
    assert binding.state == models.MastraoRecordingBinding.State.PROCESSING


def test_missing_provider_failure_webhook_converges_via_reconciler(db, settings):
    access, _ = _artifact_access()
    binding = access.recording_binding
    recording = binding.recording
    recording.status = models.RecordingStatusChoices.ACTIVE
    recording.worker_id = "EG_oju7PDAhx8k7"
    recording.save(update_fields=["status", "worker_id", "updated_at"])
    binding.state = models.MastraoRecordingBinding.State.ACTIVE
    binding.provider_recording_ref = recording.worker_id
    binding.save(update_fields=["state", "provider_recording_ref", "updated_at"])
    settings.MASTRAO_CORE_RECORDING_FAILURE_ENDPOINT = (
        "http://cabinet-core:3911/internal/v1/meetings/recording/failures"
    )
    failed = _provider_egress(recording, livekit_api.EgressStatus.EGRESS_FAILED)
    with (
        mock.patch(
            "core.mastrao_recording_reconciler._exact_provider_egress",
            return_value=failed,
        ),
        mock.patch(
            "core.mastrao_recording_failure.sign_failure_receipt",
            return_value="failure.payload.signature",
        ),
        mock.patch(
            "core.mastrao_recording_failure.post_core_json",
            return_value={"recordingRef": binding.recording_ref, "state": "failed"},
        ) as post_core_json,
    ):
        assert reconcile_mastrao_recording(binding)
    binding.refresh_from_db()
    assert binding.state == models.MastraoRecordingBinding.State.FAILED
    post_core_json.assert_called_once_with(
        endpoint=settings.MASTRAO_CORE_RECORDING_FAILURE_ENDPOINT,
        expected_path="/internal/v1/meetings/recording/failures",
        body={"recording_failure_receipt": "failure.payload.signature"},
        timeout=settings.MASTRAO_CORE_RECORDING_TIMEOUT_SECONDS,
        refusal=RecordingContractRefused,
        expected_fields={"recordingRef", "state"},
    )


def _artifact_access():
    owner = UserFactory()
    room = RoomFactory(access_level=RoomAccessLevel.RESTRICTED)
    room_binding = models.MastraoRoomBinding.objects.create(
        effect_key="effect_0123456789abcdef",
        arguments_digest="a" * 64,
        meeting_ref="meeting_0123456789abcdef",
        room_ref="room_0123456789abcdef",
        owner_ref="owner_0123456789abcdef",
        room=room,
        owner=owner,
        provider_binding_digest="b" * 64,
    )
    recording = models.Recording.objects.create(
        room=room,
        status=models.RecordingStatusChoices.SAVED,
        mode=models.RecordingModeChoices.SCREEN_RECORDING,
    )
    binding = models.MastraoRecordingBinding.objects.create(
        room_binding=room_binding,
        recording=recording,
        organization_external_id="organization_0123456789",
        meeting_ref=room_binding.meeting_ref,
        room_ref=room_binding.room_ref,
        recording_ref="recording_0123456789abcdef",
        provider_binding_digest=room_binding.provider_binding_digest,
        policy_ref="policy_0123456789abcdef",
        notice_version="notice_0123456789abcdef",
        notice_digest="c" * 64,
        retention_expires_at=timezone.now() + timedelta(days=30),
        state=models.MastraoRecordingBinding.State.FINALIZED,
        artifact_ref="artifact_0123456789abcdef",
        object_ref="recordings/recording_0123456789abcdef.mp4",
        byte_size=3,
        checksum_algorithm="sha256",
        checksum_digest=hashlib.sha256(b"mp4").hexdigest(),
    )
    retry = "retry_0123456789abcdefghijklmnopqrstuvwxyz"
    retry_digest = hashlib.sha256(retry.encode()).hexdigest()
    access = models.MastraoRecordingArtifactAccess.objects.create(
        recording_binding=binding,
        grant_jti="request_0123456789abcdef",
        grant_digest="d" * 64,
        artifact_ref=binding.artifact_ref,
        subject_external_id_digest="e" * 64,
        platform_session_digest="f" * 64,
        retry_cookie_digest=retry_digest,
        expires_at=timezone.now() + timedelta(minutes=1),
    )
    return access, retry


def _client_for_access(access, retry, stage="ready"):
    client = Client()
    session = client.session
    session[SESSION_KEY] = {
        "access_id": str(access.id),
        "retry_digest": access.retry_cookie_digest,
        "expires_at": int(access.expires_at.timestamp()),
        "stage": stage,
    }
    session.save()
    client.cookies[RETRY_COOKIE] = retry
    return client


def test_artifact_access_bootstrap_consumes_only_from_its_own_origin(db, settings):
    access, _retry = _artifact_access()
    binding = access.recording_binding
    access.delete()
    now = int(time.time())
    grant = {
        "organization_external_id": binding.organization_external_id,
        "meeting_ref": binding.meeting_ref,
        "recording_ref": binding.recording_ref,
        "artifact_ref": binding.artifact_ref,
        "subject_external_id": "subject_0123456789abcdef",
        "platform_session_digest": "f" * 64,
        "issued_at": now,
        "expires_at": now + 60,
        "jti": "recordingaccess_0123456789abcdef",
    }
    settings.MASTRAO_MEETING_RECORDING_ENABLED = True
    settings.MASTRAO_PLATFORM_ORIGIN = "http://platform.test"
    compact = "header.payload.signature"
    client = Client()
    with mock.patch(
        "core.mastrao_recording_access.verify_recording_access_grant",
        return_value=grant,
    ):
        refused_bootstrap = Client().post(
            "/recordings/access/",
            urlencode({"recording_access_grant": compact}),
            content_type="application/x-www-form-urlencoded",
            HTTP_ORIGIN="null",
            HTTP_SEC_FETCH_SITE="cross-site",
        )
        assert refused_bootstrap.status_code == 404
        bootstrap = client.post(
            "/recordings/access/",
            urlencode({"recording_access_grant": compact}),
            content_type="application/x-www-form-urlencoded",
            HTTP_ORIGIN="null",
            HTTP_SEC_FETCH_SITE="same-site",
        )
        assert bootstrap.status_code == 200
        assert 'lang="fr"' in bootstrap.content.decode()
        assert 'role="status"' in bootstrap.content.decode()
        assert "Vérification de l’intégrité" in bootstrap.content.decode()
        consumed = client.post(
            "/recordings/access/",
            urlencode({"stage": "consume", "recording_access_grant": compact}),
            content_type="application/x-www-form-urlencoded",
            HTTP_ORIGIN="null",
            HTTP_SEC_FETCH_SITE="same-site",
        )
        assert consumed.status_code == 303
        assert consumed["Location"] == "/recordings/download/current"
        refused = client.post(
            "/recordings/access/",
            urlencode({"stage": "consume", "recording_access_grant": compact}),
            content_type="application/x-www-form-urlencoded",
            HTTP_ORIGIN=settings.MASTRAO_PLATFORM_ORIGIN,
            HTTP_SEC_FETCH_SITE="cross-site",
        )
        assert refused.status_code == 404


def test_artifact_download_prepares_then_streams_exactly_once(db):
    access, retry = _artifact_access()
    client = _client_for_access(access, retry)
    prepared = client.get("/recordings/download/current")
    assert prepared.status_code == 303
    access.refresh_from_db()
    assert access.consumed_at is None

    with mock.patch(
        "core.mastrao_recording_access.default_storage.open",
        return_value=io.BytesIO(b"mp4"),
    ):
        streamed = client.get("/recordings/download/current")
        assert streamed.status_code == 200
        assert b"".join(streamed.streaming_content) == b"mp4"
    access.refresh_from_db()
    assert access.consumed_at is not None
    assert client.get("/recordings/download/current").status_code == 404


def test_artifact_download_rejects_changed_object(db):
    access, retry = _artifact_access()
    client = _client_for_access(access, retry, stage="prepared")
    with mock.patch(
        "core.mastrao_recording_access.default_storage.open",
        return_value=io.BytesIO(b"changed"),
    ):
        assert client.get("/recordings/download/current").status_code == 404
    access.refresh_from_db()
    assert access.consumed_at is None


def test_artifact_download_reads_and_serves_one_verified_stream(db):
    access, retry = _artifact_access()
    client = _client_for_access(access, retry, stage="prepared")
    with mock.patch(
        "core.mastrao_recording_access.default_storage.open",
        side_effect=[io.BytesIO(b"mp4"), io.BytesIO(b"changed")],
    ) as storage:
        response = client.get("/recordings/download/current")
        assert response.status_code == 200
        assert b"".join(response.streaming_content) == b"mp4"
    storage.assert_called_once()


def test_artifact_download_storage_failure_is_opaque_and_retryable(db):
    access, retry = _artifact_access()
    client = _client_for_access(access, retry, stage="prepared")
    with mock.patch(
        "core.mastrao_recording_access.default_storage.open",
        side_effect=OSError("storage unavailable"),
    ):
        response = client.get("/recordings/download/current")
    assert response.status_code == 404
    assert response["Content-Type"].startswith("text/html")
    assert "réessayez depuis votre dossier Mastrao" in response.content.decode()
    access.refresh_from_db()
    assert access.consumed_at is None


def test_artifact_download_rejects_old_cookie_and_other_browser(db):
    access, retry = _artifact_access()
    wrong_cookie = _client_for_access(access, f"{retry}-old")
    assert wrong_cookie.get("/recordings/download/current").status_code == 404

    other_browser = Client()
    other_browser.cookies[RETRY_COOKIE] = retry
    assert other_browser.get("/recordings/download/current").status_code == 404


def test_artifact_download_rechecks_retention_at_stream_time(db):
    access, retry = _artifact_access()
    binding = access.recording_binding
    binding.retention_expires_at = timezone.now() - timedelta(seconds=1)
    binding.save(update_fields=["retention_expires_at", "updated_at"])
    client = _client_for_access(access, retry, stage="prepared")
    with mock.patch("core.mastrao_recording_access.default_storage.open") as storage:
        assert client.get("/recordings/download/current").status_code == 404
    storage.assert_not_called()


def test_artifact_finalization_verifies_and_replays_persisted_metadata(db, settings):
    access, _retry = _artifact_access()
    binding = access.recording_binding
    binding.artifact_ref = None
    binding.object_ref = None
    binding.state = models.MastraoRecordingBinding.State.PROCESSING
    binding.save()
    recording = binding.recording
    payload = b"verified-room-composite-mp4"
    settings.MASTRAO_RECORDING_STORAGE_BINDING_DIGEST = "1" * 64
    settings.MASTRAO_RECORDING_REGION_REF = "fr-par"
    settings.MASTRAO_RECORDING_ENCRYPTION_REF = "sse-s3"
    settings.MASTRAO_RECORDING_LIFECYCLE_POLICY_REF = "retention-30-days"
    settings.MASTRAO_CORE_RECORDING_ARTIFACT_ENDPOINT = (
        "http://cabinet-core:3911/internal/v1/meetings/recording/artifacts/finalize"
    )
    with (
        mock.patch(
            "core.mastrao_recording_artifact.default_storage.open",
            return_value=io.BytesIO(payload),
        ),
        mock.patch(
            "core.mastrao_recording_artifact.sign_artifact_receipt",
            return_value="receipt.payload.signature",
        ),
        mock.patch("core.mastrao_recording_artifact.post_core_json") as post_core_json,
    ):
        post_core_json.side_effect = lambda **kwargs: {
            "artifactRef": kwargs["body"]
            and models.MastraoRecordingBinding.objects.get(pk=binding.pk).artifact_ref
        }
        finalize_mastrao_artifact(recording)
    binding.refresh_from_db()
    assert binding.state == models.MastraoRecordingBinding.State.FINALIZED
    assert binding.byte_size == len(payload)
    assert binding.checksum_digest == hashlib.sha256(payload).hexdigest()
    assert binding.artifact_receipt_claims["content_type"] == "video/mp4"
    post_core_json.assert_called_once()


def test_artifact_inspection_runs_outside_database_transaction(db, settings):
    access, _retry = _artifact_access()
    binding = access.recording_binding
    binding.artifact_ref = None
    binding.object_ref = None
    binding.state = models.MastraoRecordingBinding.State.PROCESSING
    binding.retention_expires_at = datetime.fromtimestamp(
        int(binding.retention_expires_at.timestamp()), tz=UTC
    )
    binding.save()
    binding.refresh_from_db()
    settings.MASTRAO_RECORDING_STORAGE_BINDING_DIGEST = "1" * 64
    settings.MASTRAO_RECORDING_REGION_REF = "fr-par"
    settings.MASTRAO_RECORDING_ENCRYPTION_REF = "sse-s3"
    settings.MASTRAO_RECORDING_LIFECYCLE_POLICY_REF = "retention-30-days"
    transaction_depth = 0
    inspection_depths = []
    real_atomic = django_transaction.atomic
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
        "recording_state": "processing",
    }

    @contextmanager
    def tracked_atomic(*args, **kwargs):
        nonlocal transaction_depth
        transaction_depth += 1
        try:
            with real_atomic(*args, **kwargs):
                yield
        finally:
            transaction_depth -= 1

    def inspect(_object_ref):
        inspection_depths.append(transaction_depth)
        _sync_binding(binding.room_binding.room, status)
        return 4, hashlib.sha256(b"mp4!").hexdigest()

    with (
        mock.patch(
            "core.mastrao_recording_artifact.transaction.atomic",
            side_effect=tracked_atomic,
        ),
        mock.patch(
            "core.mastrao_recording_artifact._inspect_object", side_effect=inspect
        ),
    ):
        _prepare_artifact_receipt(binding.recording)

    assert inspection_depths == [0]


def test_artifact_finalization_rejects_recording_version_race(db, settings):
    access, _retry = _artifact_access()
    binding = access.recording_binding
    binding.artifact_ref = None
    binding.object_ref = None
    binding.state = models.MastraoRecordingBinding.State.PROCESSING
    binding.save()
    recording = binding.recording
    settings.MASTRAO_RECORDING_STORAGE_BINDING_DIGEST = "1" * 64
    settings.MASTRAO_RECORDING_REGION_REF = "fr-par"
    settings.MASTRAO_RECORDING_ENCRYPTION_REF = "sse-s3"
    settings.MASTRAO_RECORDING_LIFECYCLE_POLICY_REF = "retention-30-days"

    def mutate_recording(_object_ref):
        models.Recording.objects.filter(pk=recording.pk).update(
            status=models.RecordingStatusChoices.EXTERNAL_PROCESS_SUCCESSFUL,
            updated_at=timezone.now() + timedelta(seconds=1),
        )
        return 4, hashlib.sha256(b"mp4!").hexdigest()

    with mock.patch(
        "core.mastrao_recording_artifact._inspect_object",
        side_effect=mutate_recording,
    ):
        try:
            _prepare_artifact_receipt(recording)
        except RecordingContractRefused as error:
            assert error.status == 409
        else:
            raise AssertionError("A changed recording version must invalidate the hash")

    binding.refresh_from_db()
    assert binding.artifact_receipt_claims == {}
    assert binding.artifact_ref is None


def test_artifact_finalization_replays_receipt_after_core_failure(db, settings):
    access, _retry = _artifact_access()
    binding = access.recording_binding
    binding.artifact_ref = None
    binding.object_ref = None
    binding.state = models.MastraoRecordingBinding.State.PROCESSING
    binding.save()
    recording = binding.recording
    settings.MASTRAO_RECORDING_STORAGE_BINDING_DIGEST = "1" * 64
    settings.MASTRAO_RECORDING_REGION_REF = "fr-par"
    settings.MASTRAO_RECORDING_ENCRYPTION_REF = "sse-s3"
    settings.MASTRAO_RECORDING_LIFECYCLE_POLICY_REF = "retention-30-days"
    settings.MASTRAO_CORE_RECORDING_ARTIFACT_ENDPOINT = (
        "http://cabinet-core:3911/internal/v1/meetings/recording/artifacts/finalize"
    )
    with (
        mock.patch(
            "core.mastrao_recording_artifact.default_storage.open",
            return_value=io.BytesIO(b"verified-room-composite-mp4"),
        ),
        mock.patch(
            "core.mastrao_recording_artifact.sign_artifact_receipt",
            return_value="receipt.payload.signature",
        ),
        mock.patch(
            "core.mastrao_recording_artifact.post_core_json",
            side_effect=RecordingContractRefused(status=503),
        ),
    ):
        try:
            finalize_mastrao_artifact(recording)
        except RecordingContractRefused:
            pass
        else:
            raise AssertionError("Core failure should remain visible")

    binding.refresh_from_db()
    first_claims = dict(binding.artifact_receipt_claims)
    assert binding.state == models.MastraoRecordingBinding.State.PROCESSING
    assert first_claims["artifact_ref"] == binding.artifact_ref
    expired_claims = {
        **first_claims,
        "issued_at": int(time.time()) - 31,
        "expires_at": int(time.time()) - 1,
    }
    binding.artifact_receipt_claims = expired_claims
    binding.save(update_fields=["artifact_receipt_claims", "updated_at"])

    with (
        mock.patch(
            "core.mastrao_recording_artifact.default_storage.open",
            return_value=io.BytesIO(b"changed-room-composite-mp4"),
        ),
        mock.patch("core.mastrao_recording_artifact.post_core_json") as post_core_json,
    ):
        with pytest.raises(RecordingContractRefused) as replay_error:
            finalize_mastrao_artifact(recording)
    assert replay_error.value.status == 409
    post_core_json.assert_not_called()

    with (
        mock.patch(
            "core.mastrao_recording_artifact.default_storage.open",
            return_value=io.BytesIO(b"verified-room-composite-mp4"),
        ) as storage_open,
        mock.patch(
            "core.mastrao_recording_artifact.sign_artifact_receipt",
            return_value="receipt.payload.signature",
        ),
        mock.patch(
            "core.mastrao_recording_artifact.post_core_json",
            return_value={"artifactRef": binding.artifact_ref},
        ),
    ):
        finalize_mastrao_artifact(recording)
    storage_open.assert_called_once()
    binding.refresh_from_db()
    assert binding.state == models.MastraoRecordingBinding.State.FINALIZED
    assert binding.artifact_receipt_claims["jti"] != first_claims["jti"]
    assert (
        binding.artifact_receipt_claims["artifact_ref"] == first_claims["artifact_ref"]
    )
    assert (
        binding.artifact_receipt_claims["checksum_digest"]
        == first_claims["checksum_digest"]
    )
