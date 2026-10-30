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
        "version": 2,
        "operation": "start",
        "issuer": "renewable-issuer",
        "audience": "renewable-audience",
        "issued_at": 10,
        "expires_at": 20,
        "jti": "renewable-jti",
    }

    assert _semantic_digest(effect) == (
        "e8f37e9da0aa9a3b2d984232fa87b4b1fed9e989e564d53e57403a5cba44fa5f"
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
        "version": 1,
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
        "media_token_binding_ref": "media_token_binding_fixture_12345678",
        "authorization_ref": "authorization_audio_fixture_12345678",
        "authorization_digest": "e" * 64,
        "issued_at": now,
        "expires_at": now + 30,
        "jti": "effect_audio_fixture_12345678",
        **changes,
    }


def _livekit_client(*, error=None):
    send_data = AsyncMock(side_effect=error)
    return SimpleNamespace(
        room=SimpleNamespace(send_data=send_data),
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


def test_refreshed_envelope_replays_the_same_delivered_authority(
    authority_key, binding
):
    first = _signed(authority_key, _effect(binding))
    second = _signed(
        authority_key,
        _effect(binding, jti="effect_audio_fixture_87654321"),
    )
    livekit = _livekit_client()

    with patch(
        "core.mastrao_room_audio_authority.utils.create_livekit_client",
        return_value=livekit,
    ):
        assert _post(first).status_code == 200
        assert _post(second).status_code == 200

    assert livekit.room.send_data.await_count == 1
    assert models.MastraoRoomAudioTrackEffect.objects.count() == 1


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
