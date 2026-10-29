"""Durable reconciliation tests for room-audio worker start effects."""

# Generated LiveKit protobuf members are resolved dynamically.
# pylint: disable=missing-function-docstring,redefined-outer-name

import asyncio
import base64
import json
import time
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from django.test import Client
from django.utils import timezone

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core import models
from core.factories import RoomFactory, UserFactory
from core.mastrao_room_audio_run import (
    LIVE_SOURCE_PROFILE,
    POST_SOURCE_PROFILE,
    _release_claim,
    claim_room_audio_run_effect,
    persist_room_audio_run_effect,
    verify_room_audio_run_effect,
)
from core.mastrao_room_contract import _canonical_json

pytestmark = pytest.mark.django_db
ENDPOINT = "/internal/mastrao/room-audio/runs/dispatch/"


def _b64(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _signed(key, payload):
    header = {
        "alg": "EdDSA",
        "kid": "room-audio-run-test-key",
        "typ": "mastrao-meeting-room-audio-start-effect+jws",
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
    settings.MASTRAO_RECORDING_EFFECT_KEY_ID = "room-audio-run-test-key"
    settings.MASTRAO_RECORDING_EFFECT_PUBLIC_JWK = json.dumps(
        {"kty": "OKP", "crv": "Ed25519", "x": _b64(public)}
    )
    return key


@pytest.fixture
def binding():
    return models.MastraoRoomBinding.objects.create(
        effect_key="room_audio_run_effect_fixture",
        arguments_digest="a" * 64,
        meeting_ref="meeting_audio_run_fixture",
        room_ref="room_audio_run_fixture",
        owner_ref="owner_audio_run_fixture",
        provider_binding_digest="b" * 64,
        room=RoomFactory(name="room_audio_run_authority_fixture"),
        owner=UserFactory(),
    )


def _effect(binding, **changes):
    now = int(time.time())
    effect = {
        "version": 2,
        "type": "mastrao.core-meeting-room-audio-start-effect",
        "issuer": "tenant-core-test",
        "audience": "mastrao-meet-test",
        "operation": "start_meeting_room_audio_run",
        "operation_version": 1,
        "organization_external_id": "organization_audio_run_fixture",
        "meeting_ref": binding.meeting_ref,
        "room_ref": binding.room_ref,
        "audio_run_ref": "audio_run_fixture_12345678",
        "policy_ref": "policy_audio_run_fixture_12345678",
        "policy_digest": "c" * 64,
        "notice_digest": "d" * 64,
        "provider_binding_digest": binding.provider_binding_digest,
        "provider_room_sid": "RM_roomaudio123",
        "retention_expires_at": now + 3_600,
        "pipeline": "post",
        "source_profile": POST_SOURCE_PROFILE,
        "maximum_duration_seconds": 600,
        "empty_room_grace_seconds": 30,
        "issued_at": now,
        "expires_at": now + 20,
        "jti": "effect_audio_run_fixture_12345678",
    }
    effect.update(changes)
    return effect


def _post(compact):
    return Client().post(
        ENDPOINT,
        data=json.dumps({"room_audio_start_effect": compact}),
        content_type="application/json",
    )


def _livekit_client(*, dispatches=(), dispatch_id="AD_room_audio", error=None):
    client = SimpleNamespace()
    client.agent_dispatch = SimpleNamespace(
        list_dispatch=AsyncMock(return_value=dispatches),
        create_dispatch=AsyncMock(
            side_effect=error,
            return_value=SimpleNamespace(id=dispatch_id),
        ),
    )
    client.aclose = AsyncMock()
    return client


def test_start_is_persisted_then_dispatched(authority_key, binding):
    compact = _signed(authority_key, _effect(binding))
    livekit = _livekit_client()

    with patch(
        "core.mastrao_room_audio_run.utils.create_livekit_client",
        return_value=livekit,
    ):
        response = _post(compact)

    assert response.status_code == 200
    row = models.MastraoRoomAudioRunEffect.objects.get()
    assert row.state == models.MastraoRoomAudioRunEffect.State.DELIVERED
    assert row.attempt_count == 1
    assert row.dispatch_id == "AD_room_audio"
    request = livekit.agent_dispatch.create_dispatch.await_args.args[0]
    assert request.agent_name == "room-audio-worker"
    assert request.room == str(binding.room_id)
    assert request.metadata == compact


def test_live_start_targets_the_live_worker(authority_key, binding):
    compact = _signed(
        authority_key,
        _effect(
            binding,
            pipeline="live",
            source_profile=LIVE_SOURCE_PROFILE,
        ),
    )
    livekit = _livekit_client()

    with patch(
        "core.mastrao_room_audio_run.utils.create_livekit_client",
        return_value=livekit,
    ):
        response = _post(compact)

    assert response.status_code == 200
    request = livekit.agent_dispatch.create_dispatch.await_args.args[0]
    assert request.agent_name == "room-audio-live-worker"
    row = models.MastraoRoomAudioRunEffect.objects.get()
    assert row.destination_identity.startswith("room-audio-worker-live-")


def test_timeout_reconciles_existing_exact_dispatch(authority_key, binding):
    compact = _signed(authority_key, _effect(binding))
    unavailable = _livekit_client(error=asyncio.TimeoutError())

    with patch(
        "core.mastrao_room_audio_run.utils.create_livekit_client",
        return_value=unavailable,
    ):
        response = _post(compact)

    assert response.status_code == 503
    row = models.MastraoRoomAudioRunEffect.objects.get()
    assert row.state == models.MastraoRoomAudioRunEffect.State.PENDING
    assert row.attempt_count == 1

    existing = SimpleNamespace(
        id="AD_reconciled",
        agent_name="room-audio-worker",
        metadata=compact,
    )
    refreshed = _signed(
        authority_key,
        _effect(binding, jti="effect_audio_run_fixture_refreshed"),
    )
    recovered = _livekit_client(dispatches=[existing])
    with patch(
        "core.mastrao_room_audio_run.utils.create_livekit_client",
        return_value=recovered,
    ):
        response = _post(refreshed)

    assert response.status_code == 200
    row.refresh_from_db()
    assert row.state == models.MastraoRoomAudioRunEffect.State.DELIVERED
    assert row.attempt_count == 2
    assert row.dispatch_id == "AD_reconciled"
    assert row.compact_effect == refreshed
    recovered.agent_dispatch.create_dispatch.assert_not_awaited()
    assert models.MastraoRoomAudioRunEffect.objects.count() == 1


def test_refreshed_envelope_replays_the_same_delivered_run(authority_key, binding):
    first = _signed(authority_key, _effect(binding))
    second = _signed(
        authority_key,
        _effect(binding, jti="effect_audio_run_fixture_87654321"),
    )
    livekit = _livekit_client()

    with patch(
        "core.mastrao_room_audio_run.utils.create_livekit_client",
        return_value=livekit,
    ):
        assert _post(first).status_code == 200
        assert _post(second).status_code == 200

    assert livekit.agent_dispatch.create_dispatch.await_count == 1
    assert models.MastraoRoomAudioRunEffect.objects.count() == 1


def test_conflicting_same_run_is_rejected(authority_key, binding):
    first = _signed(authority_key, _effect(binding))
    second = _signed(
        authority_key,
        _effect(
            binding,
            maximum_duration_seconds=900,
            jti="effect_audio_run_fixture_87654321",
        ),
    )
    livekit = _livekit_client()

    with patch(
        "core.mastrao_room_audio_run.utils.create_livekit_client",
        return_value=livekit,
    ):
        assert _post(first).status_code == 200
        assert _post(second).status_code == 409

    assert livekit.agent_dispatch.create_dispatch.await_count == 1
    assert models.MastraoRoomAudioRunEffect.objects.count() == 1


def test_conflicting_room_pipeline_with_another_run_is_rejected(authority_key, binding):
    first = _signed(authority_key, _effect(binding))
    second = _signed(
        authority_key,
        _effect(
            binding,
            audio_run_ref="audio_run_fixture_87654321",
            jti="effect_audio_run_fixture_87654321",
        ),
    )
    livekit = _livekit_client()

    with patch(
        "core.mastrao_room_audio_run.utils.create_livekit_client",
        return_value=livekit,
    ):
        assert _post(first).status_code == 200
        assert _post(second).status_code == 409

    assert livekit.agent_dispatch.create_dispatch.await_count == 1
    assert models.MastraoRoomAudioRunEffect.objects.count() == 1


def test_post_and_live_runs_can_coexist_for_one_room(authority_key, binding):
    post = _signed(authority_key, _effect(binding))
    live = _signed(
        authority_key,
        _effect(
            binding,
            pipeline="live",
            source_profile=LIVE_SOURCE_PROFILE,
            audio_run_ref="audio_run_live_fixture_12345678",
            jti="effect_audio_run_live_fixture_12345678",
        ),
    )
    livekit = _livekit_client()

    with patch(
        "core.mastrao_room_audio_run.utils.create_livekit_client",
        return_value=livekit,
    ):
        assert _post(post).status_code == 200
        assert _post(live).status_code == 200

    assert livekit.agent_dispatch.create_dispatch.await_count == 2
    assert models.MastraoRoomAudioRunEffect.objects.count() == 2


def test_cross_room_effect_is_rejected_before_provider(authority_key, binding):
    compact = _signed(
        authority_key,
        _effect(binding, provider_binding_digest="f" * 64),
    )
    livekit = _livekit_client()

    with patch(
        "core.mastrao_room_audio_run.utils.create_livekit_client",
        return_value=livekit,
    ):
        response = _post(compact)

    assert response.status_code == 404
    livekit.agent_dispatch.list_dispatch.assert_not_awaited()
    livekit.agent_dispatch.create_dispatch.assert_not_awaited()
    assert not models.MastraoRoomAudioRunEffect.objects.exists()


def test_stale_attempt_cannot_release_reclaimed_delivery(authority_key, binding):
    compact = _signed(authority_key, _effect(binding))
    effect = verify_room_audio_run_effect(compact)
    row = persist_room_audio_run_effect(effect, compact)
    stale_started_at = timezone.now() - timedelta(seconds=10)
    models.MastraoRoomAudioRunEffect.objects.filter(pk=row.pk).update(
        state=models.MastraoRoomAudioRunEffect.State.IN_FLIGHT,
        attempt_started_at=stale_started_at,
    )

    reclaimed, should_deliver = claim_room_audio_run_effect(row.pk)

    assert should_deliver is True
    assert reclaimed.attempt_started_at != stale_started_at
    assert _release_claim(row.pk, stale_started_at) is False
    row.refresh_from_db()
    assert row.state == models.MastraoRoomAudioRunEffect.State.IN_FLIGHT
    assert row.attempt_started_at == reclaimed.attempt_started_at
