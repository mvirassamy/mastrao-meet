"""Focused proofs for the browser host handoff and temporary media grant."""

import time
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import mock

from django.contrib.sessions.backends.cache import SessionStore
from django.db import connections
from django.test import Client, override_settings
from django.urls import reverse

import pytest

from core import models
from core.api.permissions import HasMediaHostPrivilegesOnRoom, HasPrivilegesOnRoom
from core.mastrao_host_contract import HostHandoffRefused, verify_host_grant
from core.mastrao_host_grant import (
    SESSION_NONCE_KEY,
    SESSION_PLATFORM_REF_KEY,
    active_host_grant,
)
from core.mastrao_host_handoff import _safe_json_response
from core.mastrao_identity import mastrao_host_subject, mastrao_technical_owner_subject

from meet.settings import scrub_mastrao_handoff_credentials


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


def test_sentry_scrubs_host_handoff_credentials():
    event = {
        "request": {
            "data": {
                "host_handoff": "header.payload.signature",
                "host_grant": "grant.payload.signature",
                "safe": "kept",
            }
        }
    }

    scrubbed = scrub_mastrao_handoff_credentials(event, {})

    assert scrubbed["request"]["data"] == {
        "host_handoff": "[Filtered]",
        "host_grant": "[Filtered]",
        "safe": "kept",
    }


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
    assert client.session[SESSION_PLATFORM_REF_KEY] == grant["platform_session_ref"]
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

    forbidden = [
        ("patch", f"/api/v1.0/rooms/{binding.room.id}/", {"name": "Escaped"}),
        ("delete", f"/api/v1.0/rooms/{binding.room.id}/", None),
        (
            "post",
            f"/api/v1.0/rooms/{binding.room.id}/invite/",
            {"emails": ["host@example.test"]},
        ),
        (
            "post",
            f"/api/v1.0/rooms/{binding.room.id}/start-recording/",
            {"mode": "room_composite"},
        ),
        (
            "post",
            f"/api/v1.0/rooms/{binding.room.id}/stop-recording/",
            {},
        ),
        (
            "post",
            f"/api/v1.0/rooms/{binding.room.id}/update-participant-role/",
            {"participant_identity": str(host.id), "role": "administrator"},
        ),
    ]
    for method, url, body in forbidden:
        response = getattr(client, method)(url, body or {})
        assert response.status_code in {401, 403, 404}
    assert models.Room.objects.filter(pk=binding.room_id).exists()
    assert models.ResourceAccess.objects.count() == 1
    assert models.Recording.objects.count() == 0

    assert active_host_grant(request, binding.room) is not None
    host.is_active = False
    host.save(update_fields=["is_active"])
    assert active_host_grant(request, binding.room) is None
    assert client.get("/api/v1.0/users/me/").status_code in {401, 403}
    host.is_active = True
    host.save(update_fields=["is_active"])

    replacement = models.User(sub="oidc_replacement_user")
    replacement.set_unusable_password()
    replacement.save()
    client.force_login(replacement)
    replacement_request = mock.Mock(user=replacement, session=client.session)
    assert active_host_grant(replacement_request, binding.room) is None
    assert client.get("/api/v1.0/users/me/").status_code == 200


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    "drift",
    ["provider_digest", "access_level", "device_owner", "owner_access"],
)
@override_settings(
    MASTRAO_HOST_HANDOFF_ENABLED=True,
    MASTRAO_PLATFORM_ORIGIN="https://platform.mastrao.test",
)
def test_host_handoff_refuses_room_binding_drift(client, drift):
    """A Core grant cannot bless a room whose provider binding has drifted."""

    binding = _room_binding()
    grant = _grant(binding)
    if drift == "provider_digest":
        grant["provider_binding_digest"] = "f" * 64
    elif drift == "access_level":
        binding.room.access_level = models.RoomAccessLevel.PUBLIC
        binding.room.save(update_fields=["access_level"])
    elif drift == "device_owner":
        binding.owner.is_device = False
        binding.owner.save(update_fields=["is_device"])
    else:
        models.ResourceAccess.objects.filter(
            resource=binding.room, user=binding.owner
        ).delete()

    with mock.patch(
        "core.mastrao_host_handoff._redeem",
        return_value=(grant, "aaa.bbb.ccc"),
    ):
        response = client.post(
            reverse("consume_mastrao_host_handoff"),
            data="host_handoff=drift.payload.signature",
            content_type="application/x-www-form-urlencoded",
            HTTP_ORIGIN="https://platform.mastrao.test",
            HTTP_SEC_FETCH_SITE="cross-site",
        )

    assert response.status_code == 404
    assert models.MastraoHostGrant.objects.count() == 0
    assert models.MastraoHostIdentity.objects.count() == 0


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
def test_lost_session_save_requires_and_accepts_a_fresh_remint():
    """A grant committed before cookie failure stays burned; a new handoff recovers."""

    binding = _room_binding()
    first = _grant(binding)
    url = reverse("consume_mastrao_host_handoff")
    original_save = SessionStore.save
    save_calls = 0

    def fail_response_save(session, *args, **kwargs):
        nonlocal save_calls
        save_calls += 1
        if save_calls > 1:
            raise RuntimeError("lost session store")
        return original_save(session, *args, **kwargs)

    with (
        mock.patch(
            "core.mastrao_host_handoff._redeem",
            return_value=(first, "aaa.bbb.ccc"),
        ),
        mock.patch(
            "django.contrib.sessions.backends.cache.SessionStore.save",
            new=fail_response_save,
        ),
        pytest.raises(RuntimeError, match="lost session store"),
    ):
        Client().post(
            url,
            data="host_handoff=lost.payload.signature",
            content_type="application/x-www-form-urlencoded",
            HTTP_ORIGIN="https://platform.mastrao.test",
            HTTP_SEC_FETCH_SITE="cross-site",
        )

    assert models.MastraoHostGrant.objects.count() == 1
    remint = {
        **first,
        "handoff_ref": "handoff_fresh_remint_0123",
        "grant_ref": "grant_fresh_remint_012345",
        "redemption_id": "redemption_fresh_remint_01",
        "credential_digest": "d" * 64,
    }
    with mock.patch(
        "core.mastrao_host_handoff._redeem",
        return_value=(remint, "ddd.eee.fff"),
    ):
        recovered = Client().post(
            url,
            data="host_handoff=fresh.payload.signature",
            content_type="application/x-www-form-urlencoded",
            HTTP_ORIGIN="https://platform.mastrao.test",
            HTTP_SEC_FETCH_SITE="cross-site",
        )
    assert recovered.status_code == 303
    assert models.MastraoHostGrant.objects.count() == 2


@pytest.mark.django_db(transaction=True)
@override_settings(
    MASTRAO_HOST_HANDOFF_ENABLED=True,
    MASTRAO_PLATFORM_ORIGIN="https://platform.mastrao.test",
)
def test_core_consume_loss_before_local_commit_requires_a_fresh_remint(client):
    """A Core-consumed bearer is not resurrected when Meet cannot commit it."""

    binding = _room_binding()
    grant = _grant(binding)
    url = reverse("consume_mastrao_host_handoff")
    with (
        mock.patch(
            "core.mastrao_host_handoff._redeem",
            return_value=(grant, "aaa.bbb.ccc"),
        ),
        mock.patch(
            "core.mastrao_host_handoff._commit_grant",
            side_effect=models.MastraoHostGrant.DoesNotExist,
        ),
        pytest.raises(models.MastraoHostGrant.DoesNotExist),
    ):
        client.post(
            url,
            data="host_handoff=consumed.payload.signature",
            content_type="application/x-www-form-urlencoded",
            HTTP_ORIGIN="https://platform.mastrao.test",
            HTTP_SEC_FETCH_SITE="cross-site",
        )
    assert models.MastraoHostGrant.objects.count() == 0

    remint = {
        **grant,
        "handoff_ref": "handoff_after_commit_loss_01",
        "grant_ref": "grant_after_commit_loss_0123",
        "redemption_id": "redemption_after_commit_loss",
        "credential_digest": "e" * 64,
    }
    with mock.patch(
        "core.mastrao_host_handoff._redeem",
        return_value=(remint, "eee.fff.ggg"),
    ):
        recovered = client.post(
            url,
            data="host_handoff=remint.payload.signature",
            content_type="application/x-www-form-urlencoded",
            HTTP_ORIGIN="https://platform.mastrao.test",
            HTTP_SEC_FETCH_SITE="cross-site",
        )
    assert recovered.status_code == 303
    assert models.MastraoHostGrant.objects.count() == 1


@pytest.mark.django_db(transaction=True)
@override_settings(
    MASTRAO_HOST_HANDOFF_ENABLED=True,
    MASTRAO_PLATFORM_ORIGIN="https://platform.mastrao.test",
)
def test_same_host_keeps_grants_for_two_meetings_in_one_session(client):
    """A second meeting reuses the host session nonce instead of revoking the first."""

    first_binding = _room_binding()
    first_grant = _grant(first_binding)
    owner_ref = "owner_second_0123456789"
    owner = models.User(
        sub=mastrao_technical_owner_subject(owner_ref),
        is_device=True,
    )
    owner.set_unusable_password()
    owner.save()
    second_room = models.Room.objects.create(
        name="Second Mastrao room",
        slug="room_second_0123456789",
        access_level=models.RoomAccessLevel.RESTRICTED,
    )
    models.ResourceAccess.objects.create(
        resource=second_room,
        user=owner,
        role=models.RoleChoices.OWNER,
    )
    second_binding = models.MastraoRoomBinding.objects.create(
        effect_key="effect_second_0123456789",
        arguments_digest="d" * 64,
        meeting_ref="meeting_second_0123456789",
        room_ref="room_second_0123456789",
        owner_ref=owner_ref,
        room=second_room,
        owner=owner,
        provider_binding_digest="e" * 64,
    )
    second_grant = {
        **_grant(second_binding),
        "host_ref": first_grant["host_ref"],
        "handoff_ref": "handoff_second_0123456789",
        "grant_ref": "grant_second_012345678901",
        "redemption_id": "redemption_second_01234567",
        "credential_digest": "f" * 64,
    }
    url = reverse("consume_mastrao_host_handoff")
    with mock.patch(
        "core.mastrao_host_handoff._redeem",
        side_effect=[
            (first_grant, "aaa.bbb.ccc"),
            (second_grant, "ddd.eee.fff"),
        ],
    ):
        first = client.post(
            url,
            data="host_handoff=first.payload.signature",
            content_type="application/x-www-form-urlencoded",
            HTTP_ORIGIN="https://platform.mastrao.test",
            HTTP_SEC_FETCH_SITE="cross-site",
        )
        first_nonce = client.session[SESSION_NONCE_KEY]
        second = client.post(
            url,
            data="host_handoff=second.payload.signature",
            content_type="application/x-www-form-urlencoded",
            HTTP_ORIGIN="https://platform.mastrao.test",
            HTTP_SEC_FETCH_SITE="cross-site",
        )

    assert first.status_code == second.status_code == 303
    assert client.session[SESSION_NONCE_KEY] == first_nonce
    host = models.MastraoHostIdentity.objects.get().user
    request = mock.Mock(user=host, session=client.session)
    assert active_host_grant(request, first_binding.room) is not None
    assert active_host_grant(request, second_binding.room) is not None
    assert models.ResourceAccess.objects.count() == 2


@pytest.mark.django_db(transaction=True)
@override_settings(
    MASTRAO_HOST_HANDOFF_ENABLED=True,
    MASTRAO_PLATFORM_ORIGIN="https://platform.mastrao.test",
)
def test_new_platform_session_invalidates_previous_grants(client):
    binding = _room_binding()
    first_grant = _grant(binding)
    second_grant = {
        **first_grant,
        "handoff_ref": "handoff_new_session_012345",
        "grant_ref": "grant_new_session_01234567",
        "redemption_id": "redemption_new_session_0123",
        "credential_digest": "d" * 64,
        "platform_session_ref": "platformsession_new_012345",
    }
    url = reverse("consume_mastrao_host_handoff")
    with mock.patch(
        "core.mastrao_host_handoff._redeem",
        side_effect=[
            (first_grant, "aaa.bbb.ccc"),
            (second_grant, "ddd.eee.fff"),
        ],
    ):
        assert (
            client.post(
                url,
                data="host_handoff=first.payload.signature",
                content_type="application/x-www-form-urlencoded",
                HTTP_ORIGIN="https://platform.mastrao.test",
                HTTP_SEC_FETCH_SITE="cross-site",
            ).status_code
            == 303
        )
        first_nonce = client.session[SESSION_NONCE_KEY]
        assert (
            client.post(
                url,
                data="host_handoff=second.payload.signature",
                content_type="application/x-www-form-urlencoded",
                HTTP_ORIGIN="https://platform.mastrao.test",
                HTTP_SEC_FETCH_SITE="cross-site",
            ).status_code
            == 303
        )

    host = models.MastraoHostIdentity.objects.get().user
    request = mock.Mock(user=host, session=client.session)
    assert client.session[SESSION_NONCE_KEY] != first_nonce
    assert (
        client.session[SESSION_PLATFORM_REF_KEY] == second_grant["platform_session_ref"]
    )
    assert (
        active_host_grant(request, binding.room).grant_ref == second_grant["grant_ref"]
    )


@pytest.mark.django_db(transaction=True)
@override_settings(
    MASTRAO_HOST_HANDOFF_ENABLED=True,
    MASTRAO_PLATFORM_ORIGIN="https://platform.mastrao.test",
)
def test_inactive_host_identity_is_refused(client):
    binding = _room_binding()
    grant = _grant(binding)
    first = models.User(sub=mastrao_host_subject(grant["host_ref"]), is_active=False)
    first.set_unusable_password()
    first.save()
    models.MastraoHostIdentity.objects.create(
        host_ref=grant["host_ref"],
        user=first,
    )

    with mock.patch(
        "core.mastrao_host_handoff._redeem",
        return_value=(grant, "aaa.bbb.ccc"),
    ):
        response = client.post(
            reverse("consume_mastrao_host_handoff"),
            data="host_handoff=inactive.payload.signature",
            content_type="application/x-www-form-urlencoded",
            HTTP_ORIGIN="https://platform.mastrao.test",
            HTTP_SEC_FETCH_SITE="cross-site",
        )

    assert response.status_code == 404
    assert models.MastraoHostGrant.objects.count() == 0


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
