"""Durable delivery tests for canonical room-audio track authority."""

# Generated LiveKit protobuf members are resolved dynamically.
# pylint: disable=missing-function-docstring,redefined-outer-name

import base64
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from django.test import Client

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from livekit.protocol.models import ParticipantInfo

from core import models
from core.factories import RoomFactory, UserFactory
from core.mastrao_room_audio_authority import (
    TOPIC,
    _semantic_digest,
    room_audio_worker_identity,
)
from core.mastrao_room_contract import _canonical_json

pytestmark = pytest.mark.django_db
ENDPOINT = "/internal/mastrao/room-audio/tracks/dispatch/"


def test_semantic_digest_matches_tenant_core_canonical_json():
    effect = {
        "version": 3,
        "operation": "start",
        "participant_ref": "participant_fixture",
        "participant_label": {
            "source": "meet_session_display_name",
            "display_name": "Matthias",
        },
        "issuer": "renewable-issuer",
        "audience": "renewable-audience",
        "issued_at": 10,
        "expires_at": 20,
        "jti": "renewable-jti",
    }

    assert _semantic_digest(effect) == (
        "93a2c707b46c62d95134489fb138d0465c0c051bf5c98ae127537fa671c2cfff"
    )


def test_semantic_digest_matches_tenant_core_for_unicode_participant_label():
    effect = {
        "version": 3,
        "operation": "start",
        "participant_ref": "participant_fixture",
        "participant_label": {
            "source": "meet_session_display_name",
            "display_name": "LDS 01 Hôte",
        },
        "issuer": "renewable-issuer",
        "audience": "renewable-audience",
        "issued_at": 10,
        "expires_at": 20,
        "jti": "renewable-jti",
    }

    assert _semantic_digest(effect) == (
        "ca7a315c437aed7883eaf3bf933543e9d91afffeed928974e37ca712e4c25f39"
    )


def test_room_audio_worker_identity_is_stable():
    assert room_audio_worker_identity("room_audio_fixture", "post") == (
        "room-audio-worker-post-92e2b6959c485ddf6e511997ceb9681f"
    )


def _b64(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _signed(key, payload):
    header = {
        "alg": "EdDSA",
        "kid": "room-audio-test-key",
        "typ": "mastrao-meeting-room-audio-track-effect+jws",
    }
    protected = _b64(_canonical_json(header))
    body = _b64(_canonical_json(payload))
    signature = key.sign(f"{protected}.{body}".encode())
    return f"{protected}.{body}.{_b64(signature)}"


@pytest.fixture
def authority_key(settings):
    key = Ed25519PrivateKey.generate()
    public = key.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    settings.MASTRAO_RECORDING_EFFECT_ISSUER = "tenant-core-test"
    settings.MASTRAO_RECORDING_EFFECT_AUDIENCE = "mastrao-meet-test"
    settings.MASTRAO_RECORDING_EFFECT_KEY_ID = "room-audio-test-key"
    settings.MASTRAO_RECORDING_EFFECT_PUBLIC_JWK = json.dumps(
        {"kty": "OKP", "crv": "Ed25519", "x": _b64(public)}
    )
    return key


@pytest.fixture
def binding():
    return models.MastraoRoomBinding.objects.create(
        effect_key="room_audio_effect_fixture",
        arguments_digest="a" * 64,
        meeting_ref="meeting_audio_fixture",
        room_ref="room_audio_fixture",
        owner_ref="owner_audio_fixture",
        provider_binding_digest="b" * 64,
        room=RoomFactory(name="room_audio_authority_fixture"),
        owner=UserFactory(),
    )


def _effect(binding, **changes):
    now = int(time.time())
    return {
        "version": 3,
        "type": "mastrao.core-meeting-room-audio-track-effect",
        "issuer": "tenant-core-test",
        "audience": "mastrao-meet-test",
        "operation": "admit_meeting_room_audio_track",
        "operation_version": 1,
        "organization_external_id": "organization_audio_fixture",
        "meeting_ref": binding.meeting_ref,
        "room_ref": binding.room_ref,
        "audio_run_ref": "audio_run_fixture_12345678",
        "policy_ref": "policy_audio_fixture_12345678",
        "policy_digest": "c" * 64,
        "notice_digest": "d" * 64,
        "provider_binding_digest": binding.provider_binding_digest,
        "retention_expires_at": now + 3600,
        "pipeline": "post",
        "authorization_generation": 1,
        "track_ref": "track_audio_fixture_12345678",
        "participant_ref": "participant_audio_fixture_12345678",
        "participant_label": {
            "source": "meet_session_display_name",
            "display_name": "LDS fixture",
        },
        "media_token_binding_ref": "media_token_binding_fixture_12345678",
        "authorization_ref": "authorization_audio_fixture_12345678",
        "authorization_digest": "e" * 64,
        "authorization_expires_at": now + 1_800,
        "issued_at": now,
        "expires_at": now + 30,
        "jti": "effect_audio_fixture_12345678",
        **changes,
    }


@pytest.mark.parametrize(
    "changes",
    (
        {"version": 2},
        {"participant_ref": ""},
        {"participant_label": None},
        {
            "participant_label": {
                "source": "untrusted_source",
                "display_name": "LDS fixture",
            }
        },
        {
            "participant_label": {
                "source": "meet_session_display_name",
                "display_name": " ",
            }
        },
    ),
)
def test_effect_refuses_non_v3_participant_binding(authority_key, binding, changes):
    compact = _signed(authority_key, _effect(binding, **changes))

    response = _post(compact)

    assert response.status_code == 404
    assert not models.MastraoRoomAudioTrackEffect.objects.exists()


def _livekit_client(*, error=None, worker_active=True):
    send_data = AsyncMock(side_effect=error)
    participants = []
    if worker_active:
        participants.append(
            SimpleNamespace(
                identity=room_audio_worker_identity("room_audio_fixture", "post"),
                state=ParticipantInfo.ACTIVE,
            )
        )
    return SimpleNamespace(
        room=SimpleNamespace(
            list_participants=AsyncMock(
                return_value=SimpleNamespace(participants=participants)
            ),
            send_data=send_data,
        ),
        aclose=AsyncMock(),
    )


def _post(compact):
    return Client().post(
        ENDPOINT,
        data=json.dumps({"room_audio_track_effect": compact}),
        content_type="application/json",
    )


def test_effect_is_persisted_then_delivered_to_exact_worker(authority_key, binding):
    effect = _effect(binding)
    compact = _signed(authority_key, effect)
    livekit = _livekit_client()

    with patch(
        "core.mastrao_room_audio_authority.utils.create_livekit_client",
        return_value=livekit,
    ):
        response = _post(compact)

    assert response.status_code == 200
    assert response.json()["effect_digest"] == _semantic_digest(effect)
    row = models.MastraoRoomAudioTrackEffect.objects.get()
    assert row.state == models.MastraoRoomAudioTrackEffect.State.DELIVERED
    assert row.attempt_count == 1
    assert row.compact_effect == compact
    request = livekit.room.send_data.await_args.args[0]
    assert request.room == str(binding.room_id)
    assert request.data == compact.encode("ascii")
    assert request.kind == 0
    assert request.topic == TOPIC
    assert request.destination_identities == [
        room_audio_worker_identity("room_audio_fixture", "post")
    ]


def test_delivery_failure_keeps_one_pending_effect_for_retry(authority_key, binding):
    compact = _signed(authority_key, _effect(binding))
    unavailable = _livekit_client(error=OSError("provider unavailable"))

    with patch(
        "core.mastrao_room_audio_authority.utils.create_livekit_client",
        return_value=unavailable,
    ):
        response = _post(compact)

    assert response.status_code == 503
    row = models.MastraoRoomAudioTrackEffect.objects.get()
    assert row.state == models.MastraoRoomAudioTrackEffect.State.PENDING
    assert row.attempt_count == 1

    recovered = _livekit_client()
    with patch(
        "core.mastrao_room_audio_authority.utils.create_livekit_client",
        return_value=recovered,
    ):
        response = _post(compact)

    assert response.status_code == 200
    row.refresh_from_db()
    assert row.state == models.MastraoRoomAudioTrackEffect.State.DELIVERED
    assert row.attempt_count == 2
    assert models.MastraoRoomAudioTrackEffect.objects.count() == 1


def test_pending_effect_accepts_monotonic_authority_renewal(authority_key, binding):
    original_effect = _effect(binding)
    original = _signed(authority_key, original_effect)
    renewed_effect = _effect(
        binding,
        authorization_expires_at=original_effect["authorization_expires_at"] + 60,
        jti="effect_audio_fixture_renewed_12345678",
    )
    renewed = _signed(authority_key, renewed_effect)

    with patch(
        "core.mastrao_room_audio_authority.utils.create_livekit_client",
        return_value=_livekit_client(error=OSError("provider unavailable")),
    ):
        assert _post(original).status_code == 503

    recovered = _livekit_client()
    with patch(
        "core.mastrao_room_audio_authority.utils.create_livekit_client",
        return_value=recovered,
    ):
        response = _post(renewed)

    assert response.status_code == 200
    assert response.json()["effect_digest"] == _semantic_digest(renewed_effect)
    row = models.MastraoRoomAudioTrackEffect.objects.get()
    assert row.state == models.MastraoRoomAudioTrackEffect.State.DELIVERED
    assert row.attempt_count == 2
    assert row.effect_jti == renewed_effect["jti"]
    assert row.compact_effect == renewed
    assert recovered.room.send_data.await_args.args[0].data == renewed.encode("ascii")


def test_pending_effect_rejects_shorter_authority_renewal(authority_key, binding):
    original_effect = _effect(binding)
    original = _signed(authority_key, original_effect)
    shortened = _signed(
        authority_key,
        _effect(
            binding,
            authorization_expires_at=(original_effect["authorization_expires_at"] - 60),
            jti="effect_audio_fixture_shortened_12345678",
        ),
    )

    with patch(
        "core.mastrao_room_audio_authority.utils.create_livekit_client",
        return_value=_livekit_client(error=OSError("provider unavailable")),
    ):
        assert _post(original).status_code == 503

    recovered = _livekit_client()
    with patch(
        "core.mastrao_room_audio_authority.utils.create_livekit_client",
        return_value=recovered,
    ):
        assert _post(shortened).status_code == 409

    recovered.room.send_data.assert_not_awaited()
    row = models.MastraoRoomAudioTrackEffect.objects.get()
    assert row.state == models.MastraoRoomAudioTrackEffect.State.PENDING
    assert row.effect_jti == original_effect["jti"]


@patch("core.mastrao_room_audio_authority.asyncio.sleep", new_callable=AsyncMock)
def test_delivery_waits_for_active_worker_before_sending(sleep, authority_key, binding):
    compact = _signed(authority_key, _effect(binding))
    livekit = _livekit_client(worker_active=False)

    with patch(
        "core.mastrao_room_audio_authority.utils.create_livekit_client",
        return_value=livekit,
    ):
        response = _post(compact)

    assert response.status_code == 503
    assert livekit.room.list_participants.await_count == 12
    assert sleep.await_count == 11
    livekit.room.send_data.assert_not_awaited()
    row = models.MastraoRoomAudioTrackEffect.objects.get()
    assert row.state == models.MastraoRoomAudioTrackEffect.State.PENDING


def test_refreshed_envelope_replays_the_same_delivered_authority(
    authority_key, binding
):
    first_effect = _effect(binding)
    first = _signed(authority_key, first_effect)
    renewed_effect = _effect(
        binding,
        authorization_expires_at=first_effect["authorization_expires_at"] + 60,
        jti="effect_audio_fixture_87654321",
    )
    second = _signed(
        authority_key,
        renewed_effect,
    )
    livekit = _livekit_client()

    with patch(
        "core.mastrao_room_audio_authority.utils.create_livekit_client",
        return_value=livekit,
    ):
        assert _post(first).status_code == 200
        response = _post(second)

    assert response.status_code == 200
    assert response.json()["effect_digest"] == _semantic_digest(renewed_effect)

    assert livekit.room.send_data.await_count == 1
    assert models.MastraoRoomAudioTrackEffect.objects.count() == 1
    row = models.MastraoRoomAudioTrackEffect.objects.get()
    assert row.compact_effect == first


def test_same_generation_with_conflicting_authority_is_rejected(authority_key, binding):
    first = _signed(authority_key, _effect(binding))
    second = _signed(
        authority_key,
        _effect(
            binding,
            authorization_digest="f" * 64,
            jti="effect_audio_fixture_87654321",
        ),
    )
    livekit = _livekit_client()

    with patch(
        "core.mastrao_room_audio_authority.utils.create_livekit_client",
        return_value=livekit,
    ):
        assert _post(first).status_code == 200
        assert _post(second).status_code == 409

    assert livekit.room.send_data.await_count == 1
    assert models.MastraoRoomAudioTrackEffect.objects.count() == 1


def test_cross_room_effect_is_rejected_before_provider_delivery(authority_key, binding):
    compact = _signed(
        authority_key,
        _effect(binding, provider_binding_digest="f" * 64),
    )
    livekit = _livekit_client()

    with patch(
        "core.mastrao_room_audio_authority.utils.create_livekit_client",
        return_value=livekit,
    ):
        response = _post(compact)

    assert response.status_code == 404
    livekit.room.send_data.assert_not_awaited()
    assert not models.MastraoRoomAudioTrackEffect.objects.exists()
