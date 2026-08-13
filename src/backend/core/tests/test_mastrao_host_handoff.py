"""Focused proofs for the browser host handoff and temporary media grant."""

import time
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import mock

from django.db import connections
from django.test import Client, override_settings
from django.urls import reverse

import pytest

from core import models
from core.api.permissions import HasMediaHostPrivilegesOnRoom, HasPrivilegesOnRoom
from core.mastrao_host_contract import HostHandoffRefused, verify_host_grant
from core.mastrao_host_grant import SESSION_NONCE_KEY, active_host_grant
from core.mastrao_host_handoff import _safe_json_response
from core.mastrao_identity import mastrao_technical_owner_subject


def _room_binding():
    owner_ref = "owner_0123456789abcdef"
    owner = models.User(
        sub=mastrao_technical_owner_subject(owner_ref),
        is_device=True,
    )
    owner.set_unusable_password()
    owner.save()
    room = models.Room.objects.create(
        name="Mastrao room",
        slug="room_0123456789abcdef",
        access_level=models.RoomAccessLevel.RESTRICTED,
    )
    models.ResourceAccess.objects.create(
        resource=room,
        user=owner,
        role=models.RoleChoices.OWNER,
    )
    return models.MastraoRoomBinding.objects.create(
        effect_key="effect_0123456789abcdef",
        arguments_digest="a" * 64,
        meeting_ref="meeting_0123456789abcdef",
        room_ref="room_0123456789abcdef",
        owner_ref=owner_ref,
        room=room,
        owner=owner,
        provider_binding_digest="b" * 64,
    )


def _grant(binding):
    now = int(time.time())
    return {
        "version": 1,
        "type": "mastrao.core-meeting-host-grant",
        "issuer": "cabinet-core-local",
        "audience": "mastrao-meet-local",
        "purpose": "media_host",
        "grant_ref": "grant_0123456789abcdef",
        "handoff_ref": "handoff_0123456789abcdef",
        "organization_external_id": "organization_0123456789",
        "meeting_ref": binding.meeting_ref,
        "room_ref": binding.room_ref,
        "host_ref": "host_0123456789abcdef",
        "platform_session_ref": "platformsession_0123456789abcdef",
        "provider_binding_digest": binding.provider_binding_digest,
        "redemption_id": "redemption_0123456789abcdef",
        "credential_digest": "c" * 64,
        "issued_at": now,
        "expires_at": now + 3_600,
    }


def test_core_redemption_response_is_streamed_and_bounded():
    """The private redemption response cannot exceed its in-memory bound."""

    response = mock.Mock(
        headers={},
        status_code=200,
        iter_content=mock.Mock(return_value=[b'{"host_grant":"', b"x" * 20_000]),
    )

    with pytest.raises(HostHandoffRefused) as error:
        _safe_json_response(response)

    assert error.value.status == 503
    response.close.assert_called_once_with()


def test_malformed_host_grant_maps_shared_jose_errors_to_host_refusal():
    """Shared JOSE helpers cannot leak the room-adapter exception domain."""

    with pytest.raises(HostHandoffRefused):
        verify_host_grant("not-base64!.payload.signature")


@pytest.mark.django_db(transaction=True)
@override_settings(
    MASTRAO_HOST_HANDOFF_ENABLED=True,
    MASTRAO_PLATFORM_ORIGIN="https://platform.mastrao.test",
)
def test_host_handoff_creates_session_bound_grant_without_durable_access(client):
    """A valid handoff creates only a nonce-bound temporary host grant."""

    binding = _room_binding()
    grant = _grant(binding)
    with mock.patch(
        "core.mastrao_host_handoff._redeem",
        return_value=(grant, "aaa.bbb.ccc"),
    ):
        response = client.post(
            reverse("consume_mastrao_host_handoff"),
            data="host_handoff=first.payload.signature",
            content_type="application/x-www-form-urlencoded",
            HTTP_ORIGIN="https://platform.mastrao.test",
            HTTP_SEC_FETCH_SITE="cross-site",
        )
    assert response.status_code == 303
    assert response["Location"] == f"/{binding.room.slug}"
    assert response["Referrer-Policy"] == "no-referrer"
    assert models.MastraoHostIdentity.objects.count() == 1
    assert models.MastraoHostGrant.objects.count() == 1
    assert models.ResourceAccess.objects.count() == 1
    assert SESSION_NONCE_KEY in client.session
    assert client.session["_auth_user_backend"].endswith(
        "MastraoHostAuthenticationBackend"
    )

    host = models.MastraoHostIdentity.objects.get().user
    request = mock.Mock(user=host, session=client.session)
    assert HasMediaHostPrivilegesOnRoom().has_permission(request, None)
    assert HasMediaHostPrivilegesOnRoom().has_object_permission(
        request, None, binding.room
    )
    assert not HasPrivilegesOnRoom().has_object_permission(request, None, binding.room)

    create_room = client.post("/api/v1.0/rooms/", {"name": "Escaped room"})
    assert create_room.status_code in {401, 403}
    assert models.Room.objects.count() == 1

    trusted = models.Room.objects.create(
        name="Other trusted room",
        access_level=models.RoomAccessLevel.TRUSTED,
    )
    trusted_response = client.get(f"/api/v1.0/rooms/{trusted.id}/")
    assert trusted_response.status_code == 200
    assert "livekit" not in trusted_response.json()

    assert active_host_grant(request, binding.room) is not None
    host.is_active = False
    host.save(update_fields=["is_active"])
    assert active_host_grant(request, binding.room) is None
    assert client.get("/api/v1.0/users/me/").status_code in {401, 403}


@pytest.mark.django_db(transaction=True)
@override_settings(
    MASTRAO_HOST_HANDOFF_ENABLED=True,
    MASTRAO_PLATFORM_ORIGIN="https://platform.mastrao.test",
)
def test_host_handoff_refuses_wrong_origin_and_local_replay(client):
    """Origin mismatch and a second local consume are both refused."""

    binding = _room_binding()
    grant = _grant(binding)
    url = reverse("consume_mastrao_host_handoff")
    with mock.patch(
        "core.mastrao_host_handoff._redeem",
        return_value=(grant, "aaa.bbb.ccc"),
    ):
        wrong_origin = client.post(
            url,
            data="host_handoff=replay.payload.signature",
            content_type="application/x-www-form-urlencoded",
            HTTP_ORIGIN="https://attacker.test",
            HTTP_SEC_FETCH_SITE="cross-site",
        )
        first = client.post(
            url,
            data="host_handoff=replay.payload.signature",
            content_type="application/x-www-form-urlencoded",
            HTTP_ORIGIN="https://platform.mastrao.test",
            HTTP_SEC_FETCH_SITE="cross-site",
        )
        replay = Client().post(
            url,
            data="host_handoff=replay.payload.signature",
            content_type="application/x-www-form-urlencoded",
            HTTP_ORIGIN="https://platform.mastrao.test",
            HTTP_SEC_FETCH_SITE="cross-site",
        )
    assert wrong_origin.status_code == 404
    assert first.status_code == 303
    assert replay.status_code == 404
    assert models.MastraoHostGrant.objects.count() == 1


@pytest.mark.django_db(transaction=True)
@override_settings(
    MASTRAO_HOST_HANDOFF_ENABLED=True,
    MASTRAO_PLATFORM_ORIGIN="https://platform.mastrao.test",
)
def test_concurrent_host_handoff_has_one_local_winner():
    """Two browser submissions cannot create two grants or usable sessions."""

    binding = _room_binding()
    grant = _grant(binding)
    barrier = Barrier(2, timeout=5)

    def redeem_together(_host_handoff):
        barrier.wait()
        return grant, "aaa.bbb.ccc"

    def submit():
        try:
            return (
                Client()
                .post(
                    reverse("consume_mastrao_host_handoff"),
                    data="host_handoff=concurrent.payload.signature",
                    content_type="application/x-www-form-urlencoded",
                    HTTP_ORIGIN="https://platform.mastrao.test",
                    HTTP_SEC_FETCH_SITE="cross-site",
                )
                .status_code
            )
        finally:
            connections.close_all()

    with mock.patch(
        "core.mastrao_host_handoff._redeem",
        side_effect=redeem_together,
    ):
        with ThreadPoolExecutor(max_workers=2) as executor:
            statuses = sorted(executor.map(lambda _: submit(), range(2)))

    assert statuses == [303, 404]
    assert models.MastraoHostGrant.objects.count() == 1
    assert models.MastraoHostIdentity.objects.count() == 1
    assert models.ResourceAccess.objects.count() == 1
