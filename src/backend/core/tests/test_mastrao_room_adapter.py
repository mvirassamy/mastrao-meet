"""Proofs for the opt-in Mastrao room adapter."""

import base64
import hashlib
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from django.db import close_old_connections
from django.test import Client, override_settings
from django.urls import reverse

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core.mastrao_room_contract import (
    ROOM_EFFECT_JOSE_TYPE,
    ROOM_EFFECT_TYPE,
    _canonical_json,
)
from core.models import (
    MastraoRoomBinding,
    ResourceAccess,
    RoleChoices,
    Room,
    RoomAccessLevel,
    User,
)


def _b64(value):
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _jwk_pair():
    private = Ed25519PrivateKey.generate()
    public_raw = private.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    private_raw = private.private_bytes(
        serialization.Encoding.Raw,
        serialization.PrivateFormat.Raw,
        serialization.NoEncryption(),
    )
    return private, {
        "public": json.dumps({"kty": "OKP", "crv": "Ed25519", "x": _b64(public_raw)}),
        "private": json.dumps(
            {
                "kty": "OKP",
                "crv": "Ed25519",
                "x": _b64(public_raw),
                "d": _b64(private_raw),
            }
        ),
    }


def _digest(value):
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _effect(private_key, *, jti="claim_0123456789abcdef", owner_ref=None):
    owner_ref = owner_ref or "owner_0123456789abcdef"
    now = int(time.time())
    arguments = {
        "version": 1,
        "operation": "ensure_private_room",
        "operation_version": 1,
        "meeting_ref": "meeting_0123456789abcdef",
        "room_ref": "room_0123456789abcdef",
        "owner_ref": owner_ref,
    }
    payload = {
        "version": 1,
        "type": ROOM_EFFECT_TYPE,
        "issuer": "cabinet-core-local",
        "audience": "mastrao-meet-local",
        "operation": "ensure_private_room",
        "operation_version": 1,
        "effect_key": "effect_0123456789abcdef",
        "arguments_digest": _digest(arguments),
        "meeting_ref": arguments["meeting_ref"],
        "room_ref": arguments["room_ref"],
        "owner_ref": arguments["owner_ref"],
        "issued_at": now,
        "expires_at": now + 30,
        "jti": jti,
    }
    header = {
        "alg": "EdDSA",
        "kid": "core-room-key",
        "typ": ROOM_EFFECT_JOSE_TYPE,
    }
    protected = _b64(_canonical_json(header))
    encoded_payload = _b64(_canonical_json(payload))
    signature = private_key.sign(f"{protected}.{encoded_payload}".encode("ascii"))
    return f"{protected}.{encoded_payload}.{_b64(signature)}"


@pytest.fixture(name="adapter_settings")
def fixture_adapter_settings():
    command_private, command_jwks = _jwk_pair()
    _receipt_private, receipt_jwks = _jwk_pair()
    settings_override = override_settings(
        MASTRAO_ROOM_ADAPTER_ENABLED=True,
        MASTRAO_ROOM_EFFECT_ISSUER="cabinet-core-local",
        MASTRAO_ROOM_EFFECT_AUDIENCE="mastrao-meet-local",
        MASTRAO_ROOM_EFFECT_PUBLIC_JWK=command_jwks["public"],
        MASTRAO_ROOM_EFFECT_KEY_ID="core-room-key",
        MASTRAO_ROOM_RECEIPT_ISSUER="mastrao-meet-local",
        MASTRAO_ROOM_RECEIPT_AUDIENCE="cabinet-core-local",
        MASTRAO_ROOM_RECEIPT_PRIVATE_JWK=receipt_jwks["private"],
        MASTRAO_ROOM_RECEIPT_KEY_ID="meet-receipt-key",
    )
    settings_override.enable()
    try:
        yield command_private
    finally:
        settings_override.disable()


@pytest.mark.django_db(transaction=True)
def test_adapter_creates_one_restricted_room_and_replays(client, adapter_settings):
    """The adapter creates one canonical room and replays its receipt."""

    url = reverse("ensure_mastrao_room")
    first = client.post(
        url,
        data=json.dumps({"room_effect": _effect(adapter_settings)}),
        content_type="application/json",
    )
    assert first.status_code == 200
    first_binding = MastraoRoomBinding.objects.select_related("room", "owner").get()
    assert first_binding.room.access_level == RoomAccessLevel.RESTRICTED
    assert first_binding.room.slug == "room_0123456789abcdef"
    assert first_binding.owner.is_device is True
    assert first_binding.owner.has_usable_password() is False
    assert ResourceAccess.objects.filter(
        resource=first_binding.room,
        user=first_binding.owner,
        role=RoleChoices.OWNER,
    ).exists()

    replay = client.post(
        url,
        data=json.dumps(
            {"room_effect": _effect(adapter_settings, jti="claim_replay_0123456789")}
        ),
        content_type="application/json",
    )
    assert replay.status_code == 200
    assert MastraoRoomBinding.objects.count() == 1
    assert Room.objects.count() == 1
    assert User.objects.count() == 1
    assert ResourceAccess.objects.count() == 1
    assert replay.json()["room_receipt"] != first.json()["room_receipt"]


@pytest.mark.django_db(transaction=True)
def test_adapter_refuses_altered_binding_for_same_effect(client, adapter_settings):
    """A semantic collision on an existing effect key is refused."""

    url = reverse("ensure_mastrao_room")
    first = client.post(
        url,
        data=json.dumps({"room_effect": _effect(adapter_settings)}),
        content_type="application/json",
    )
    assert first.status_code == 200

    altered = client.post(
        url,
        data=json.dumps(
            {
                "room_effect": _effect(
                    adapter_settings, owner_ref="owner_changed_0123456789"
                )
            }
        ),
        content_type="application/json",
    )
    assert altered.status_code == 409
    assert MastraoRoomBinding.objects.count() == 1
    assert Room.objects.count() == 1


@pytest.mark.django_db(transaction=True)
def test_adapter_refuses_disabled_or_tampered_effect(client, adapter_settings):
    """Disabled adapters and invalid signatures fail closed."""

    url = reverse("ensure_mastrao_room")
    token = _effect(adapter_settings)
    protected, payload, encoded_signature = token.split(".")
    signature = bytearray(
        base64.urlsafe_b64decode(
            encoded_signature + "=" * (-len(encoded_signature) % 4)
        )
    )
    signature[0] ^= 1
    tampered = f"{protected}.{payload}.{_b64(signature)}"
    response = client.post(
        url,
        data=json.dumps({"room_effect": tampered}),
        content_type="application/json",
    )
    assert response.status_code == 404
    assert not MastraoRoomBinding.objects.exists()

    with override_settings(MASTRAO_ROOM_ADAPTER_ENABLED=False):
        disabled = client.post(
            url,
            data=json.dumps({"room_effect": token}),
            content_type="application/json",
        )
    assert disabled.status_code == 404
    assert not MastraoRoomBinding.objects.exists()


@pytest.mark.django_db(transaction=True)
def test_adapter_converges_two_concurrent_effects(adapter_settings):
    """Concurrent delivery converges on one binding and room."""

    url = reverse("ensure_mastrao_room")
    token = _effect(adapter_settings)
    barrier = threading.Barrier(2)

    def post_effect():
        close_old_connections()
        try:
            barrier.wait()
            return (
                Client()
                .post(
                    url,
                    data=json.dumps({"room_effect": token}),
                    content_type="application/json",
                )
                .status_code
            )
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as executor:
        statuses = list(executor.map(lambda _index: post_effect(), range(2)))

    assert statuses == [200, 200]
    assert MastraoRoomBinding.objects.count() == 1
    assert Room.objects.count() == 1
    assert User.objects.count() == 1
    assert ResourceAccess.objects.count() == 1
