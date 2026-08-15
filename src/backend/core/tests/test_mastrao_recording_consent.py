"""Focused recording-consent media gate and native-regression proofs."""

import hashlib
import io
from datetime import timedelta
from unittest import mock

from django.test import Client
from django.utils import timezone

from rest_framework.test import APIClient

from core import models, utils
from core.factories import RoomFactory, UserFactory
from core.mastrao_recording_access import RETRY_COOKIE, SESSION_KEY
from core.mastrao_recording_adapter import _prepare_start
from core.mastrao_recording_artifact import finalize_mastrao_artifact
from core.mastrao_recording_contract import build_start_receipt_claims
from core.mastrao_recording_session import media_allowed, recording_session_status
from core.models import RoomAccessLevel


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
    room.mastrao_binding = mock.Mock()
    with mock.patch("core.mastrao_recording_session.post_core_json") as post_core_json:
        assert recording_session_status(mock.Mock(), room) is None
    post_core_json.assert_not_called()


def test_session_status_posts_only_the_bound_participant_grant(settings):
    settings.MASTRAO_MEETING_RECORDING_ENABLED = True
    settings.MASTRAO_CORE_RECORDING_SESSION_STATUS_ENDPOINT = (
        "http://cabinet-core:3911/internal/v1/meetings/recording/session-status"
    )
    participant = {
        "compact": "header.payload.signature",
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
        assert recording_session_status(mock.Mock(), room) == status
    assert post_core_json.call_args.kwargs["body"] == {
        "participant_grant": "header.payload.signature"
    }


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


def test_recording_start_locks_only_the_non_nullable_binding(db):
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
        "retention_expires_at": int(
            (timezone.now() + timedelta(days=30)).timestamp()
        ),
        "effect_key": "effect_start_0123456789",
        "arguments_digest": "d" * 64,
        "jti": "request_0123456789abcdef",
    }

    recording_binding, local_effect = _prepare_start(effect)

    assert recording_binding.recording is None
    assert local_effect.recording_binding == recording_binding
    assert local_effect.operation == "start"


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


def test_artifact_download_rejects_old_cookie_and_other_browser(db):
    access, retry = _artifact_access()
    wrong_cookie = _client_for_access(access, f"{retry}-old")
    assert wrong_cookie.get("/recordings/download/current").status_code == 404

    other_browser = Client()
    other_browser.cookies[RETRY_COOKIE] = retry
    assert other_browser.get("/recordings/download/current").status_code == 404


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
            "artifactRef": kwargs["body"] and binding.artifact_ref
        }
        finalize_mastrao_artifact(recording)
    binding.refresh_from_db()
    assert binding.state == models.MastraoRecordingBinding.State.FINALIZED
    assert binding.byte_size == len(payload)
    assert binding.checksum_digest == hashlib.sha256(payload).hexdigest()
    assert binding.artifact_receipt_claims["content_type"] == "video/mp4"
    post_core_json.assert_called_once()
