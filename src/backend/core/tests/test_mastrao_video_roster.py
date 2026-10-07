"""Provider-free proofs of the video start authorization boundary."""

# Test names express intent; generated SDK members and pytest fixtures are dynamic.
# pylint: disable=missing-function-docstring,no-member,unused-argument,too-many-arguments,too-many-positional-arguments
# Pytest fixture names and the provider-free boundary require direct private access.
# pylint: disable=redefined-outer-name,protected-access
# Pytest dependencies name each external boundary explicitly.
# ruff: noqa: PLR0913, PLR0917

import json
import time
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest import mock
from uuid import uuid4

from django.utils import timezone

import pytest
import requests
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from livekit import api

from core import mastrao_video_roster as roster
from core import models
from core.factories import RoomFactory, UserFactory
from core.mastrao_recording_adapter import (
    VIDEO_START_REFUSED,
    _apply_start,
    _persist_video_refusal,
    _prepare_start,
    _provider_registration_started_at,
)
from core.mastrao_recording_contract import (
    START_EFFECT_JOSE_TYPE,
    START_EFFECT_TYPE,
    RecordingContractRefused,
    _effect_arguments,
    compact_digest,
    verify_recording_start_effect,
)
from core.mastrao_room_contract import (
    _base64url_decode,
    _base64url_encode,
    _canonical_json,
    _sha256_canonical,
)
from core.recording.worker.exceptions import RecordingStartError


@pytest.fixture
def provider_boundary(authority):
    recording = SimpleNamespace(
        pk="recording-local",
        room_id="room-local",
        worker_id="EG_0123456789abcdef",
        status=models.RecordingStatusChoices.INITIATED,
    )
    local_effect = SimpleNamespace(
        pk="effect-local",
        state=models.MastraoRecordingEffect.State.APPLYING,
        receipt_claims={},
        save=mock.Mock(),
    )
    authority.recording_id = recording.pk
    with (
        mock.patch(
            "core.mastrao_recording_adapter._prepare_start",
            return_value=(authority, local_effect, True),
        ) as prepare,
        mock.patch.object(models.Recording.objects, "select_related") as records,
        mock.patch.object(
            models.MastraoRecordingEffect.objects, "select_for_update"
        ) as effects,
        mock.patch.object(models.MastraoRecordingBinding.objects, "filter"),
        mock.patch(
            "core.mastrao_recording_adapter.transaction.atomic", side_effect=nullcontext
        ),
        mock.patch(
            "core.mastrao_recording_adapter._persist_video_refusal",
            side_effect=_persist_video_refusal.__wrapped__,
        ),
        mock.patch(
            "core.mastrao_recording_adapter.build_start_receipt_claims", return_value={}
        ),
        mock.patch(
            "core.mastrao_recording_adapter.sign_start_receipt",
            return_value="signed.receipt.test",
        ),
        mock.patch("core.mastrao_recording_adapter.get_worker_service"),
        mock.patch("core.mastrao_recording_adapter.WorkerServiceMediator") as worker,
    ):
        records.return_value.get.return_value = recording
        effects.return_value.get.return_value = local_effect
        yield recording, local_effect, worker.return_value, prepare


def test_core_callback_precedes_the_first_provider_start(provider_boundary):
    recording, _, worker, _ = provider_boundary
    calls = []
    worker.start.side_effect = lambda _: calls.append("sfu")
    with mock.patch(
        "core.mastrao_recording_adapter.authorize_video_start",
        create=True,
        side_effect=lambda *_: (
            calls.append("core")
            or {
                "version": 1,
                "authorized": True,
                "start_status": "authorized",
                "decision_lock": "start_in_progress",
            }
        ),
    ):
        _apply_start({"resolve_only": False, "claim_id": "claim_0123456789abcdef"})
    assert calls == ["core", "sfu"]
    worker.start.assert_called_once_with(recording)


def test_core_refusal_never_invokes_the_provider(provider_boundary):
    _, _, worker, _ = provider_boundary
    with (
        mock.patch(
            "core.mastrao_recording_adapter.authorize_video_start",
            create=True,
            return_value={
                "version": 1,
                "authorized": False,
                "start_status": "refused",
                "decision_lock": "open",
            },
        ),
        pytest.raises(RecordingContractRefused),
    ):
        _apply_start({"resolve_only": False, "claim_id": "claim_0123456789abcdef"})
    worker.start.assert_not_called()


@pytest.fixture
def authority(settings):
    settings.MASTRAO_RECORDING_EFFECT_ISSUER = "cabinet-core"
    settings.MASTRAO_RECORDING_EFFECT_AUDIENCE = "mastrao-meet"
    settings.MASTRAO_RECORDING_RECEIPT_ISSUER = "mastrao-meet"
    settings.MASTRAO_RECORDING_RECEIPT_AUDIENCE = "cabinet-core"
    settings.MASTRAO_CORE_RECORDING_SESSION_STATUS_ENDPOINT = (
        "http://cabinet-core:8081/internal/v1/meetings/recording/session-status"
    )
    settings.MASTRAO_CORE_RECORDING_TIMEOUT_SECONDS = 3
    binding = models.MastraoRoomBinding(
        id=uuid4(),
        room_id="00000000-0000-0000-0000-000000000001",
        meeting_ref="meeting_0123456789abcdef",
        room_ref="room_0123456789abcdef",
        provider_binding_digest="b" * 64,
    )
    binding._state.fields_cache["closure"] = None
    return models.MastraoRecordingBinding(
        id=uuid4(),
        room_binding=binding,
        organization_external_id="organization_0123456789",
        meeting_ref=binding.meeting_ref,
        room_ref=binding.room_ref,
        recording_ref="recording_0123456789abcdef",
        provider_binding_digest=binding.provider_binding_digest,
        policy_ref="policy_0123456789abcdef",
        notice_version="notice_0123456789abcdef",
        notice_digest="c" * 64,
        purpose="meeting_recording",
        scope="room_composite_audio_video_screen",
        retention_expires_at=timezone.now() + timedelta(days=30),
    )


@pytest.fixture
def effect(authority, settings):
    now = int(time.time())
    value = {
        "version": 1,
        "type": START_EFFECT_TYPE,
        "issuer": settings.MASTRAO_RECORDING_EFFECT_ISSUER,
        "audience": settings.MASTRAO_RECORDING_EFFECT_AUDIENCE,
        "operation": "start_room_composite_recording",
        "operation_version": 1,
        "organization_external_id": authority.organization_external_id,
        "meeting_ref": authority.meeting_ref,
        "room_ref": authority.room_ref,
        "recording_ref": authority.recording_ref,
        "provider_binding_digest": authority.provider_binding_digest,
        "effect_key": "effect_start_0123456789abcdef",
        "claim_id": "claim_start_0123456789abcdef",
        "policy_ref": authority.policy_ref,
        "notice_version": authority.notice_version,
        "notice_digest": authority.notice_digest,
        "purpose": authority.purpose,
        "scope": authority.scope,
        "retention_expires_at": int(authority.retention_expires_at.timestamp()),
        "resolve_only": False,
        "issued_at": now,
        "expires_at": now + 30,
        "jti": "request_start_0123456789abcdef",
    }
    value["arguments_digest"] = _sha256_canonical(_effect_arguments(value, "start"))
    return value


@pytest.fixture
def signer(settings):
    key = Ed25519PrivateKey.generate()
    jwk = {
        "kty": "OKP",
        "crv": "Ed25519",
        "x": _base64url_encode(key.public_key().public_bytes_raw()),
        "d": _base64url_encode(key.private_bytes_raw()),
    }
    settings.MASTRAO_RECORDING_RECEIPT_PRIVATE_JWK = json.dumps(jwk)
    settings.MASTRAO_RECORDING_RECEIPT_KEY_ID = "roster-test-key"
    settings.MASTRAO_RECORDING_EFFECT_PUBLIC_JWK = json.dumps(jwk)
    settings.MASTRAO_RECORDING_EFFECT_KEY_ID = "roster-test-key"
    return key


def _connected(authority, kind="host", index=1):
    binding = authority.room_binding
    common = {
        "id": uuid4(),
        "room_binding": binding,
        "meeting_ref": binding.meeting_ref,
        "room_ref": binding.room_ref,
        "provider_binding_digest": binding.provider_binding_digest,
        "grant_ref": f"grant_0123456789abcdef_{index}",
        "grant_digest": compact_digest(f"exact.compact.grant_{index}"),
        "session_nonce_digest": f"{index:064x}",
    }
    if kind == "host":
        grant = models.MastraoHostGrant(
            **common,
            identity=models.MastraoHostIdentity(
                id=uuid4(),
                host_ref=f"host_0123456789abcdef_{index}",
                user=models.User(id=uuid4(), is_active=True),
            ),
        )
    else:
        grant = models.MastraoGuestGrant(
            **common,
            guest_ref=f"guest_0123456789abcdef_{index}",
            admission_state="allowed",
            decision_allow=True,
            decision_confirmed_at=timezone.now(),
            decision_receipt_digest="d" * 64,
        )
    issued = models.MastraoMediaTokenBinding(
        id=uuid4(),
        room_binding=binding,
        host_grant=grant if kind == "host" else None,
        guest_grant=grant if kind == "guest" else None,
        rtc_identity=f"rtc-identity-{index}",
        grant_digest=grant.grant_digest,
        session_nonce_digest=grant.session_nonce_digest,
        # Already connected: expiry must not act as a meeting duration gate.
        expires_at=timezone.now() - timedelta(seconds=1),
    )
    participant = api.ParticipantInfo(
        sid=f"PA_connection_{index}",
        identity=issued.rtc_identity,
        kind=api.ParticipantInfo.STANDARD,
        attributes={roster.MEDIA_BINDING_ATTRIBUTE: str(issued.pk)},
    )
    connection = models.MastraoRtcConnection(
        room_binding=binding,
        room_sid="RM_current_incarnation",
        participant_sid=participant.sid,
        media_token_binding=issued,
        correlation="correlated",
        ended=False,
    )
    return participant, connection


@pytest.fixture
def sfu(authority):
    host, connection = _connected(authority)
    client = mock.Mock()
    client.room.list_rooms = mock.AsyncMock(
        return_value=api.ListRoomsResponse(
            rooms=[
                api.Room(
                    sid="RM_current_incarnation",
                    name=str(authority.room_binding.room_id),
                )
            ]
        )
    )
    client.room.list_participants = mock.AsyncMock(
        return_value=api.ListParticipantsResponse(participants=[host])
    )
    client.aclose = mock.AsyncMock()
    with (
        mock.patch.object(roster.utils, "create_livekit_client", return_value=client),
        mock.patch.object(models.MastraoRtcConnection.objects, "filter") as query,
    ):
        query.return_value.select_related.return_value = [connection]
        yield client, query, host, connection


@pytest.fixture
def core_reply():
    decision = {
        "version": 1,
        "authorized": True,
        "start_status": "authorized",
        "decision_lock": "start_in_progress",
    }
    with mock.patch("core.mastrao_core_http.requests.Session") as session_type:
        session = session_type.return_value.__enter__.return_value
        response = mock.Mock(status_code=200, headers={})
        response.iter_content.return_value = [json.dumps(decision).encode()]
        session.post.return_value = response
        yield session, response


def _claims(compact, signer):
    protected, encoded, signature = compact.split(".")
    signer.public_key().verify(
        _base64url_decode(signature), f"{protected}.{encoded}".encode("ascii")
    )
    return json.loads(_base64url_decode(protected)), json.loads(
        _base64url_decode(encoded)
    )


def _recording(authority):
    return SimpleNamespace(room_id=authority.room_binding.room_id)


def test_real_signed_roster_posts_exact_digest_and_fresh_sfu_incarnation(
    authority, effect, signer, sfu, core_reply, settings
):
    client, query, _, connection = sfu
    session, response = core_reply
    decision = roster.authorize_video_start(effect, authority, _recording(authority))
    assert decision["authorized"] is True
    session.post.assert_called_once()
    target = session.post.call_args.args[0]
    assert target == "http://cabinet-core:8081" + roster.START_AUTHORIZATION_PATH
    assert session.trust_env is False
    kwargs = session.post.call_args.kwargs
    assert kwargs["allow_redirects"] is False and kwargs["stream"] is True
    assert set(kwargs["json"]) == {"roster_assertion"}
    assert 0 < kwargs["timeout"] <= 3
    header, payload = _claims(kwargs["json"]["roster_assertion"], signer)
    assert header == {
        "alg": "EdDSA",
        "kid": "roster-test-key",
        "typ": roster.ROSTER_JOSE_TYPE,
    }
    assert set(payload) == {
        "version",
        "type",
        "issuer",
        "audience",
        "organization_external_id",
        "meeting_ref",
        "room_ref",
        "recording_ref",
        "effect_key",
        "claim_id",
        "observed_at",
        "issued_at",
        "expires_at",
        "jti",
        "participants",
    }
    assert payload["type"] == "mastrao.meet-video-roster"
    assert payload["issuer"] == settings.MASTRAO_RECORDING_RECEIPT_ISSUER
    assert payload["audience"] == settings.MASTRAO_RECORDING_RECEIPT_AUDIENCE
    assert payload["claim_id"] == effect["claim_id"]
    assert 0 <= payload["issued_at"] - payload["observed_at"] <= 10
    assert 0 < payload["expires_at"] - payload["issued_at"] <= 120
    issued = connection.media_token_binding
    assert payload["participants"] == [
        {
            "participant_kind": "host",
            "participant_ref": issued.host_grant.identity.host_ref,
            "grant_ref": issued.host_grant.grant_ref,
            "participant_session_digest": issued.session_nonce_digest,
            "participant_grant_digest": issued.grant_digest,
        }
    ]
    query.assert_called_once_with(
        room_binding=authority.room_binding,
        room_sid="RM_current_incarnation",
        participant_sid__in=[connection.participant_sid],
    )
    client.room.list_participants.assert_awaited_once_with(
        api.ListParticipantsRequest(room=str(authority.room_binding.room_id))
    )
    client.aclose.assert_awaited_once()
    response.close.assert_called_once()


def test_actual_callback_boundary_uses_signed_roster_before_sfu_start(
    authority, effect, signer, sfu, core_reply, provider_boundary
):
    recording, _, worker, _ = provider_boundary
    recording.room_id = authority.room_binding.room_id
    session, _ = core_reply
    calls = []
    session.post.side_effect = lambda *_args, **_kwargs: (
        calls.append("core") or session.post.return_value
    )
    worker.start.side_effect = lambda _: calls.append("sfu")
    _apply_start(effect)
    assert calls == ["core", "sfu"]
    worker.start.assert_called_once_with(recording)


def test_humans_are_included_but_recorder_and_agent_are_excluded(
    authority, effect, signer, sfu, core_reply
):
    client, query, host, connection = sfu
    guest, guest_connection = _connected(authority, "guest", 2)
    recorder = api.ParticipantInfo(
        sid="PA_recorder", identity="recorder", kind=api.ParticipantInfo.EGRESS
    )
    transcription_agent = api.ParticipantInfo(
        sid="PA_transcription_agent",
        identity="multi-user-transcriber-room",
        kind=api.ParticipantInfo.AGENT,
        permission=api.ParticipantPermission(agent=True),
    )
    client.room.list_participants.return_value = api.ListParticipantsResponse(
        participants=[host, guest, recorder, transcription_agent]
    )
    query.return_value.select_related.return_value = [connection, guest_connection]
    roster.authorize_video_start(effect, authority, _recording(authority))
    _, payload = _claims(
        core_reply[0].post.call_args.kwargs["json"]["roster_assertion"], signer
    )
    assert [p["participant_kind"] for p in payload["participants"]] == ["host", "guest"]
    assert (
        payload["participants"][1]["participant_grant_digest"]
        == guest_connection.media_token_binding.grant_digest
    )


@pytest.mark.parametrize(
    "change",
    [
        "unknown",
        "wrong_identity",
        "wrong_marker",
        "ended",
        "token_reused",
        "missing_join",
        "wrong_grant",
        "lobby_guest",
        "duplicate_session",
    ],
)
def test_unknown_or_ambiguous_roster_never_calls_core_or_provider(
    authority, effect, sfu, core_reply, provider_boundary, change
):
    client, query, participant, connection = sfu
    recording, _, worker, _ = provider_boundary
    recording.room_id = authority.room_binding.room_id
    if change == "unknown":
        query.return_value.select_related.return_value = []
    elif change == "wrong_identity":
        participant.identity = "other"
    elif change == "wrong_marker":
        participant.attributes[roster.MEDIA_BINDING_ATTRIBUTE] = str(uuid4())
    elif change == "ended":
        connection.ended = True
    elif change in {"token_reused", "missing_join"}:
        connection.correlation = change
    elif change == "wrong_grant":
        connection.media_token_binding.grant_digest = "f" * 64
    elif change == "lobby_guest":
        participant, connection = _connected(authority, "guest")
        connection.media_token_binding.guest_grant.admission_state = "waiting"
        query.return_value.select_related.return_value = [connection]
    elif change == "duplicate_session":
        another = api.ParticipantInfo()
        another.CopyFrom(participant)
        another.sid, another.identity = "PA_duplicate", "different-rtc-identity"
        original = connection.media_token_binding
        duplicate_issued = models.MastraoMediaTokenBinding(
            id=uuid4(),
            room_binding=authority.room_binding,
            host_grant=original.host_grant,
            rtc_identity=another.identity,
            grant_digest=original.grant_digest,
            session_nonce_digest=original.session_nonce_digest,
        )
        another.attributes[roster.MEDIA_BINDING_ATTRIBUTE] = str(duplicate_issued.pk)
        duplicate = models.MastraoRtcConnection(
            room_binding=authority.room_binding,
            participant_sid=another.sid,
            media_token_binding=duplicate_issued,
            correlation="correlated",
        )
        query.return_value.select_related.return_value = [connection, duplicate]
        client.room.list_participants.return_value = api.ListParticipantsResponse(
            participants=[participant, another]
        )
    if change != "duplicate_session":
        client.room.list_participants.return_value = api.ListParticipantsResponse(
            participants=[participant]
        )
    with pytest.raises(RecordingContractRefused):
        _apply_start(effect)
    core_reply[0].post.assert_not_called()
    worker.start.assert_not_called()


@pytest.mark.parametrize(
    "kind",
    [
        api.ParticipantInfo.INGRESS,
        api.ParticipantInfo.SIP,
        api.ParticipantInfo.BRIDGE,
    ],
)
def test_nonhuman_participant_fails_closed(authority, sfu, kind):
    _, _, participant, _ = sfu
    participant.kind = kind
    with pytest.raises(RecordingContractRefused):
        roster._bound_roster(
            authority.room_binding, "RM_current_incarnation", [participant]
        )


@pytest.mark.parametrize(
    "kind,is_agent,missing_field",
    [
        (api.ParticipantInfo.AGENT, False, None),
        (api.ParticipantInfo.STANDARD, True, None),
        (api.ParticipantInfo.AGENT, True, "identity"),
        (api.ParticipantInfo.AGENT, True, "sid"),
    ],
)
def test_malformed_agent_participant_fails_closed(
    authority, sfu, kind, is_agent, missing_field
):
    _, _, participant, _ = sfu
    participant.kind = kind
    participant.permission.agent = is_agent
    if missing_field:
        setattr(participant, missing_field, "")
    with pytest.raises(RecordingContractRefused):
        roster._bound_roster(
            authority.room_binding, "RM_current_incarnation", [participant]
        )


@pytest.mark.parametrize(
    "participants", [[], [api.ParticipantInfo(kind=api.ParticipantInfo.EGRESS)]]
)
def test_empty_human_roster_is_refused(authority, participants):
    with pytest.raises(RecordingContractRefused):
        roster._bound_roster(
            authority.room_binding, "RM_current_incarnation", participants
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("version", True),
        ("authorized", "true"),
        ("start_status", []),
        ("decision_lock", "unknown"),
        ("extra", "field"),
        ("start_status", "pending"),
        ("decision_lock", "started"),
    ],
)
def test_malformed_core_reply_never_starts_sfu(
    authority, effect, signer, sfu, core_reply, provider_boundary, field, value
):
    recording, _, worker, _ = provider_boundary
    recording.room_id = authority.room_binding.room_id
    body = {
        "version": 1,
        "authorized": True,
        "start_status": "authorized",
        "decision_lock": "start_in_progress",
        field: value,
    }
    core_reply[1].iter_content.return_value = [json.dumps(body).encode()]
    with pytest.raises(RecordingContractRefused):
        _apply_start(effect)
    worker.start.assert_not_called()


@pytest.mark.parametrize("status", ["pending", "refused"])
def test_actual_refusal_is_durable_and_same_claim_does_not_recall_core(
    authority, effect, signer, sfu, core_reply, provider_boundary, status
):
    recording, local_effect, worker, prepare = provider_boundary
    recording.room_id = authority.room_binding.room_id
    body = {
        "version": 1,
        "authorized": False,
        "start_status": status,
        "decision_lock": "open",
    }
    core_reply[1].iter_content.return_value = [json.dumps(body).encode()]
    with pytest.raises(RecordingContractRefused):
        _apply_start(effect)
    assert local_effect.state == models.MastraoRecordingEffect.State.PENDING
    assert local_effect.provider_observation == VIDEO_START_REFUSED
    assert local_effect.receipt_claims == {
        "claim_id": effect["claim_id"],
        "start_authorization": body,
    }
    prepare.return_value = authority, local_effect, False
    with pytest.raises(RecordingContractRefused):
        _apply_start(effect)
    core_reply[0].post.assert_called_once()
    worker.start.assert_not_called()


def test_new_manual_claim_can_rearm_only_a_definitely_refused_start(
    authority, effect, settings
):
    settings.MASTRAO_MEETING_RECORDING_ENABLED = True
    settings.MASTRAO_MEETING_RECORDING_START_ENABLED = True
    authority.recording = models.Recording(
        id=uuid4(), status=models.RecordingStatusChoices.INITIATED
    )
    local_effect = models.MastraoRecordingEffect(
        recording_binding=authority,
        effect_key=effect["effect_key"],
        operation="start",
        arguments_digest=effect["arguments_digest"],
        effect_jti=effect["jti"],
        state=models.MastraoRecordingEffect.State.PENDING,
        provider_observation=VIDEO_START_REFUSED,
        receipt_claims={"claim_id": effect["claim_id"]},
    )
    with (
        mock.patch(
            "core.mastrao_recording_adapter._binding",
            return_value=authority.room_binding,
        ),
        mock.patch.object(
            models.MastraoRecordingBinding.objects, "select_for_update"
        ) as bindings,
        mock.patch.object(
            models.MastraoRecordingEffect.objects, "select_for_update"
        ) as effects,
        mock.patch.object(local_effect, "save"),
        mock.patch.object(authority, "save"),
    ):
        bindings_query = (
            bindings.return_value.select_related.return_value.filter.return_value
        )
        bindings_query.first.return_value = authority
        effects.return_value.filter.return_value.first.return_value = local_effect
        assert _prepare_start.__wrapped__(effect)[2] is False
        next_effect = {**effect, "claim_id": "claim_new_click_0123456789abcdef"}
        assert _prepare_start.__wrapped__(next_effect)[2] is True
        assert local_effect.state == models.MastraoRecordingEffect.State.APPLYING
        assert local_effect.receipt_claims["claim_id"] == next_effect["claim_id"]
        assert local_effect.provider_observation is None
        assert _prepare_start.__wrapped__(next_effect)[2] is False


@pytest.mark.parametrize(
    "state",
    [
        models.MastraoRecordingEffect.State.APPLIED,
        models.MastraoRecordingEffect.State.APPLYING,
    ],
)
def test_replay_and_recovery_do_not_call_authorization(provider_boundary, state):
    recording, local_effect, worker, prepare = provider_boundary
    local_effect.state = state
    prepare.return_value = prepare.return_value[0], local_effect, False
    provider_egress = SimpleNamespace(egress_id=recording.worker_id)
    with (
        mock.patch("core.mastrao_recording_adapter.authorize_video_start") as authorize,
        mock.patch(
            "core.mastrao_recording_adapter._exact_provider_egress",
            return_value=provider_egress,
        ),
    ):
        recording.save = mock.Mock()
        _apply_start({"resolve_only": True})
    authorize.assert_not_called()
    worker.start.assert_not_called()


def test_delayed_callback_never_starts_sfu(
    authority, effect, signer, sfu, core_reply, provider_boundary
):
    recording, _, worker, _ = provider_boundary
    recording.room_id = authority.room_binding.room_id
    with mock.patch.object(roster, "time", wraps=time) as clock:
        clock.monotonic.side_effect = [100.0, 101.0, 111.0]
        with pytest.raises(RecordingContractRefused):
            _apply_start(effect)
    worker.start.assert_not_called()


def test_snapshot_timeout_closes_sdk_client(authority, sfu):
    sfu[0].room.list_participants.side_effect = TimeoutError
    with pytest.raises(RecordingContractRefused):
        roster._snapshot(str(authority.room_binding.room_id))
    sfu[0].aclose.assert_awaited_once()


def test_rejoin_with_old_sid_is_not_a_current_roster_binding(
    authority, effect, sfu, core_reply
):
    sfu[1].return_value.select_related.return_value = []
    with pytest.raises(RecordingContractRefused):
        roster.authorize_video_start(effect, authority, _recording(authority))
    core_reply[0].post.assert_not_called()


def _core_signed(effect, signer):
    header = _base64url_encode(
        _canonical_json(
            {"alg": "EdDSA", "kid": "roster-test-key", "typ": START_EFFECT_JOSE_TYPE}
        )
    )
    body = _base64url_encode(_canonical_json(effect))
    signature = _base64url_encode(signer.sign(f"{header}.{body}".encode("ascii")))
    return f"{header}.{body}.{signature}"


def test_real_signed_start_effect_requires_claim_id(effect, signer):
    assert verify_recording_start_effect(_core_signed(effect, signer)) == effect
    effect.pop("claim_id")
    with pytest.raises(RecordingContractRefused):
        verify_recording_start_effect(_core_signed(effect, signer))


@pytest.mark.parametrize("claim_id", [None, "short", "invalid reference", 123])
def test_start_claim_id_is_an_exact_opaque_reference(effect, signer, claim_id):
    effect["claim_id"] = claim_id
    with pytest.raises(RecordingContractRefused):
        verify_recording_start_effect(_core_signed(effect, signer))


def test_new_manual_attempt_restarts_provider_registration_grace(authority):
    attempted_at = int(time.time())
    effect = SimpleNamespace(
        created_at=timezone.now() - timedelta(hours=3),
        applied_at=None,
        receipt_claims={"start_attempted_at": attempted_at},
    )
    with mock.patch.object(models.MastraoRecordingEffect.objects, "filter") as query:
        query.return_value.order_by.return_value.first.return_value = effect
        assert _provider_registration_started_at(authority) == datetime.fromtimestamp(
            attempted_at, tz=UTC
        )


def test_roster_count_is_bounded_without_truncating_humans():
    humans = [
        api.ParticipantInfo(sid=f"PA_{index}", identity=f"human-{index}")
        for index in range(100)
    ]
    assert len(roster._humans(humans)) == 100
    humans.append(api.ParticipantInfo(sid="PA_extra", identity="extra"))
    with pytest.raises(RecordingContractRefused):
        roster._humans(humans)


def test_core_compact_envelope_limit_fails_closed(
    authority, effect, signer, sfu, core_reply
):
    participants, connections = zip(
        *[_connected(authority, index=index) for index in range(1, 101)], strict=True
    )
    sfu[0].room.list_participants.return_value = api.ListParticipantsResponse(
        participants=participants
    )
    sfu[1].return_value.select_related.return_value = connections
    with pytest.raises(RecordingContractRefused):
        roster.authorize_video_start(effect, authority, _recording(authority))
    core_reply[0].post.assert_not_called()


@pytest.mark.parametrize(
    "endpoint",
    [
        "",
        "https://cabinet-core/internal/v1/meetings/recording/session-status",
        "http://external.example/internal/v1/meetings/recording/session-status",
        "http://cabinet-core/incorrect",
        "http://user:secret@cabinet-core/internal/v1/meetings/recording/session-status",
        "http://cabinet-core/internal/v1/meetings/recording/session-status?x=1",
    ],
)
def test_invalid_core_origin_is_rejected_before_snapshot(
    authority, effect, sfu, settings, endpoint
):
    settings.MASTRAO_CORE_RECORDING_SESSION_STATUS_ENDPOINT = endpoint
    with pytest.raises(RecordingContractRefused):
        roster.authorize_video_start(effect, authority, _recording(authority))
    sfu[0].room.list_rooms.assert_not_awaited()


@pytest.mark.parametrize("age", [11, -1])
def test_stale_or_future_snapshot_never_calls_core(
    authority, effect, signer, sfu, core_reply, age
):
    now = int(time.time())
    with mock.patch.object(roster, "time", wraps=time) as clock:
        clock.time.side_effect = [now, now + age]
        with pytest.raises(RecordingContractRefused):
            roster.authorize_video_start(effect, authority, _recording(authority))
    core_reply[0].post.assert_not_called()


@pytest.mark.parametrize("failure", ["http", "timeout", "oversize"])
def test_callback_transport_failure_never_invokes_provider(
    authority, effect, signer, sfu, core_reply, provider_boundary, failure
):
    recording, _, worker, _ = provider_boundary
    recording.room_id = authority.room_binding.room_id
    session, response = core_reply
    if failure == "http":
        response.status_code = 503
    elif failure == "timeout":
        session.post.side_effect = requests.Timeout
    else:
        response.headers = {"content-length": "20001"}
    with pytest.raises(RecordingContractRefused):
        _apply_start(effect)
    worker.start.assert_not_called()


def test_uncertain_provider_start_is_not_sent_again(
    authority, effect, signer, sfu, core_reply, provider_boundary
):
    recording, local_effect, worker, prepare = provider_boundary
    recording.room_id = authority.room_binding.room_id
    worker.start.side_effect = RecordingStartError
    with mock.patch(
        "core.mastrao_recording_adapter._exact_provider_egress", return_value=None
    ):
        with pytest.raises(RecordingContractRefused):
            _apply_start(effect)
        assert local_effect.state == models.MastraoRecordingEffect.State.APPLYING
        prepare.return_value = authority, local_effect, False
        with pytest.raises(RecordingContractRefused):
            _apply_start(effect)
    core_reply[0].post.assert_called_once()
    worker.start.assert_called_once()


def test_preissued_token_arriving_after_snapshot_is_not_revoked_or_reconsulted(
    authority, effect, signer, sfu, core_reply, provider_boundary
):
    """Simulate late arrival; verify token signature, not an actual SFU join."""
    recording, _, worker, _ = provider_boundary
    recording.room_id = authority.room_binding.room_id
    guest, _ = _connected(authority, "guest", 2)
    rtc_key, rtc_secret = (
        "local-rtc-test",
        "local-rtc-test-secret-at-least-32-characters",
    )
    preissued = (
        api.AccessToken(rtc_key, rtc_secret)
        .with_identity(guest.identity)
        .with_grants(api.VideoGrants(room_join=True, room=str(recording.room_id)))
        .with_attributes(dict(guest.attributes))
        .to_jwt()
    )
    session, response = core_reply

    def authorize_then_arrive(*_args, **_kwargs):
        sfu[0].room.list_participants.return_value.participants.add().CopyFrom(guest)
        return response

    session.post.side_effect = authorize_then_arrive
    _apply_start(effect)
    claims = api.TokenVerifier(rtc_key, rtc_secret).verify(preissued)
    assert claims.identity == guest.identity
    assert claims.video.room_join is True
    _, payload = _claims(
        session.post.call_args.kwargs["json"]["roster_assertion"], signer
    )
    assert len(payload["participants"]) == 1
    assert payload["participants"][0]["participant_kind"] == "host"
    sfu[0].room.list_participants.assert_awaited_once()
    sfu[0].room.remove_participant.assert_not_called()
    sfu[0].room.update_participant.assert_not_called()
    session.post.assert_called_once()
    worker.start.assert_called_once_with(recording)


def _persist_connected_roster(authority, client, connection):
    """Persist the same authority and provenance used by provider-free tests."""
    binding = authority.room_binding
    binding.room = RoomFactory()
    binding.owner = UserFactory()
    binding.owner_ref = "owner_0123456789abcdef"
    binding.effect_key = "effect_room_0123456789abcdef"
    binding.arguments_digest = "a" * 64
    binding.save()
    client.room.list_rooms.return_value = api.ListRoomsResponse(
        rooms=[api.Room(sid="RM_current_incarnation", name=str(binding.room_id))]
    )
    issued = connection.media_token_binding
    grant = issued.host_grant
    grant.identity.user = binding.owner
    grant.identity.save()
    grant.handoff_ref = "handoff_0123456789abcdef"
    grant.credential_digest = "d" * 64
    grant.platform_session_ref = "platformsession_0123456789abcdef"
    grant.issued_at = timezone.now() - timedelta(minutes=2)
    grant.expires_at = timezone.now() - timedelta(minutes=1)
    grant.save()
    issued.issued_at, issued.expires_at = grant.issued_at, grant.expires_at
    issued.authorization_digest, issued.token_digest = grant.grant_digest, "e" * 64
    issued.save()
    with mock.patch.object(
        models.MastraoRtcConnection.objects,
        "filter",
        wraps=models.MastraoRtcConnection.objects.get_queryset().filter,
    ):
        connection.save()


@pytest.mark.django_db
def test_durable_refusal_then_new_manual_claim_and_receipt_replay(
    authority, effect, signer, sfu, core_reply, settings
):
    """Exercise real row locks and fresh ORM reads; SFU and Core remain simulated."""
    settings.MASTRAO_MEETING_RECORDING_ENABLED = True
    settings.MASTRAO_MEETING_RECORDING_START_ENABLED = True
    _persist_connected_roster(authority, sfu[0], sfu[3])
    refusal = {
        "version": 1,
        "authorized": False,
        "start_status": "refused",
        "decision_lock": "open",
    }
    core_reply[1].iter_content.return_value = [json.dumps(refusal).encode()]

    def started(recording):
        recording.worker_id = "EG_0123456789abcdef"
        recording.status = models.RecordingStatusChoices.ACTIVE
        recording.save(update_fields=["worker_id", "status", "updated_at"])

    # Remove only the fixture's ledger mock: use persisted connection provenance.
    with (
        mock.patch.object(
            models.MastraoRtcConnection.objects,
            "filter",
            wraps=models.MastraoRtcConnection.objects.get_queryset().filter,
        ),
        mock.patch("core.mastrao_recording_adapter.get_worker_service"),
        mock.patch("core.mastrao_recording_adapter.WorkerServiceMediator") as worker,
    ):
        worker.return_value.start.side_effect = started
        with pytest.raises(RecordingContractRefused):
            _apply_start(effect)
        stored = models.MastraoRecordingEffect.objects.get(
            effect_key=effect["effect_key"]
        )
        assert stored.state == models.MastraoRecordingEffect.State.PENDING
        assert stored.receipt_claims["claim_id"] == effect["claim_id"]
        assert (
            stored.recording_binding.state
            == models.MastraoRecordingBinding.State.PREPARED
        )
        worker.return_value.start.assert_not_called()
        with pytest.raises(RecordingContractRefused):
            _apply_start(effect)
        core_reply[0].post.assert_called_once()
        core_reply[1].iter_content.return_value = [
            json.dumps(
                {
                    "version": 1,
                    "authorized": True,
                    "start_status": "authorized",
                    "decision_lock": "start_in_progress",
                }
            ).encode()
        ]
        next_effect = {**effect, "claim_id": "claim_new_manual_0123456789abcdef"}
        receipt = _apply_start(next_effect)
        assert _apply_start(next_effect) == receipt
        stored.refresh_from_db()
        assert stored.state == models.MastraoRecordingEffect.State.APPLIED
        worker.return_value.start.assert_called_once()
        assert core_reply[0].post.call_count == 2
