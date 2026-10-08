"""Recording consent, media admission and capture-control proofs."""

import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest import mock

from django.utils import timezone

import pytest
from rest_framework.test import APIClient

from core import models, utils
from core.factories import RoomFactory, UserFactory
from core.mastrao_recording_adapter import _handle, _prepare_start
from core.mastrao_recording_contract import (
    RecordingContractRefused,
    build_start_receipt_claims,
)
from core.mastrao_recording_session import (
    _sync_binding,
    activate_recording,
    media_allowed,
    public_projection,
    recording_session_status,
)
from core.models import RoomAccessLevel


@pytest.fixture(autouse=True)
def recording_rollout_settings(settings):
    """Keep focused tests explicit while production defaults remain closed."""

    settings.MASTRAO_MEETING_RECORDING_START_ENABLED = True
    settings.MASTRAO_MEETING_RECORDING_ARTIFACT_ACCESS_ENABLED = True
    settings.MASTRAO_RECORDING_NOTICE_VERSION = "notice_0123456789abcdef"
    settings.MASTRAO_RECORDING_NOTICE_DIGEST = "a" * 64


def _recorded(state, decision="absent"):
    return {
        "mode": "recorded",
        "recording_state": state,
        "decision": decision,
    }


def test_recording_media_gate_matches_capture_semantics():
    """Require consent during capture and allow media after terminal states."""

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
    """Accept the exact LiveKit egress reference in the start receipt."""

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
    """Leave unbound native rooms unchanged when recording is disabled."""

    settings.MASTRAO_MEETING_RECORDING_ENABLED = False
    settings.MASTRAO_MEETING_RECORDING_START_ENABLED = False
    settings.MASTRAO_MEETING_RECORDING_ARTIFACT_ACCESS_ENABLED = False
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
    """Keep enforcing an existing recorded policy when rollout is disabled."""

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
        "transcription_mode": "disabled",
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


def test_recorded_projection_refuses_notice_manifest_drift(settings):
    """Refuse a recorded policy whose notice digest differs from the manifest."""

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
        "mode": "recorded",
        "recording_ref": "recording_0123456789abcdef",
        "policy_ref": "policy_0123456789abcdef",
        "notice_version": settings.MASTRAO_RECORDING_NOTICE_VERSION,
        "notice_digest": "b" * 64,
        "purpose": "meeting_recording",
        "scope": "room_composite_audio_video_screen",
        "retention_expires_at": int(time.time()) + 3600,
        "recording_state": "collecting",
        "decision": "absent",
        "transcription_mode": "disabled",
    }
    with (
        mock.patch(
            "core.mastrao_recording_session._participant", return_value=participant
        ),
        mock.patch(
            "core.mastrao_recording_session.post_core_json", return_value=status
        ),
    ):
        with pytest.raises(RecordingContractRefused) as error:
            recording_session_status(mock.Mock(), room)

    assert error.value.status == 503


def test_feature_off_refuses_browser_recording_activation(settings):
    """Refuse browser activation while recording is disabled."""

    settings.MASTRAO_MEETING_RECORDING_ENABLED = False

    with pytest.raises(RecordingContractRefused):
        activate_recording(mock.Mock(), mock.Mock(), "activationrequest_0123456789")


def test_session_status_posts_only_the_bound_participant_grant(settings):
    """Send only the bound participant grant and session digest to Core."""

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
    """Avoid updating an unchanged persisted recording projection."""

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
    """Expose participant kind without participant identity or session digest."""

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


@pytest.mark.usefixtures("db")
def test_recorded_absent_never_mints_livekit_token():
    """Withhold the LiveKit token until the guest accepts recording."""

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


@pytest.mark.usefixtures("db")
def test_recorded_terminal_state_allows_unrecorded_token():
    """Allow an unrecorded token after recording reaches a terminal state."""

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


@pytest.mark.usefixtures("db")
def test_recording_start_locks_only_the_non_nullable_binding(settings):
    """Prepare recording and start effect through the non-nullable binding."""

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


@pytest.mark.usefixtures("db")
def test_start_control_off_refuses_a_new_recording_start(settings):
    """Refuse a new start without creating a binding when capture is disabled."""

    settings.MASTRAO_MEETING_RECORDING_ENABLED = True
    settings.MASTRAO_MEETING_RECORDING_START_ENABLED = False
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
    """Deliver an authorized stop even after all recording controls close."""

    settings.MASTRAO_MEETING_RECORDING_ENABLED = False
    settings.MASTRAO_MEETING_RECORDING_START_ENABLED = False
    settings.MASTRAO_MEETING_RECORDING_ARTIFACT_ACCESS_ENABLED = False
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
