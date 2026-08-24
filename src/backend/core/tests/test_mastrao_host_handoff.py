"""Focused proofs for the browser host handoff and temporary media grant."""

import base64
import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from unittest import mock

from django.contrib.sessions.backends.cache import SessionStore
from django.core.cache import cache
from django.db import connections
from django.test import Client, override_settings
from django.urls import reverse
from django.utils import timezone

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core import models
from core.api.permissions import HasMediaHostPrivilegesOnRoom, HasPrivilegesOnRoom
from core.mastrao_host_contract import (
    HOST_HANDOFF_JOSE_TYPE,
    HOST_HANDOFF_TYPE,
    HostHandoffRefused,
    verify_host_grant,
)
from core.mastrao_host_contract import (
    verify_host_handoff as verify_host_handoff_contract,
)
from core.mastrao_host_grant import (
    SESSION_NONCE_KEY,
    SESSION_PLATFORM_REF_KEY,
    active_host_close_grant,
    active_host_grant,
    host_platform_return_projection,
)
from core.mastrao_host_handoff import _admit_public_attempt, _safe_json_response
from core.mastrao_identity import mastrao_host_subject, mastrao_technical_owner_subject
from core.mastrao_room_contract import _canonical_json

from meet.settings import scrub_mastrao_handoff_credentials


def _b64(value):
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _host_handoff(private_key, suffix):
    now = int(time.time())
    payload = {
        "version": 1,
        "type": HOST_HANDOFF_TYPE,
        "issuer": "cabinet-core-local",
        "audience": "mastrao-meet-local",
        "purpose": "join_as_host",
        "handoff_ref": f"handoff_{suffix:016d}",
        "organization_external_id": "organization_0123456789",
        "meeting_ref": "meeting_0123456789abcdef",
        "room_ref": "room_0123456789abcdef",
        "host_ref": "host_0123456789abcdef",
        "platform_session_ref": "platformsession_0123456789abcdef",
        "provider_binding_digest": "b" * 64,
        "issued_at": now,
        "expires_at": now + 120,
        "grant_expires_at": now + 3_600,
        "nonce": f"nonce_{suffix:016d}",
    }
    header = {
        "alg": "EdDSA",
        "kid": "core-room-key",
        "typ": HOST_HANDOFF_JOSE_TYPE,
    }
    protected = _b64(_canonical_json(header))
    encoded_payload = _b64(_canonical_json(payload))
    signature = private_key.sign(f"{protected}.{encoded_payload}".encode("ascii"))
    return f"{protected}.{encoded_payload}.{_b64(signature)}"


@pytest.fixture(name="handoff_signing")
def fixture_handoff_signing():
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    configuration = override_settings(
        MASTRAO_ROOM_EFFECT_ISSUER="cabinet-core-local",
        MASTRAO_ROOM_EFFECT_AUDIENCE="mastrao-meet-local",
        MASTRAO_ROOM_EFFECT_PUBLIC_JWK=json.dumps(
            {"kty": "OKP", "crv": "Ed25519", "x": _b64(public_key)}
        ),
        MASTRAO_ROOM_EFFECT_KEY_ID="core-room-key",
    )
    configuration.enable()
    try:
        yield private_key
    finally:
        configuration.disable()


@pytest.fixture(name="local_handoff_verification", autouse=True)
def fixture_local_handoff_verification():
    with mock.patch("core.mastrao_host_handoff.verify_host_handoff") as verifier:
        yield verifier


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


@override_settings(
    CACHES={
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "mastrao-host-handoff-global-limit",
        }
    },
    MASTRAO_HOST_HANDOFF_GLOBAL_ATTEMPTS_PER_MINUTE=1,
)
def test_invalid_handoffs_do_not_consume_global_capacity(
    handoff_signing, local_handoff_verification
):
    """Invalid signatures cannot consume legitimate redemption capacity."""

    local_handoff_verification.side_effect = verify_host_handoff_contract
    with mock.patch(
        "core.mastrao_host_handoff._increment_attempt_counter"
    ) as increment_counter:
        for suffix in ("first", "second", "third"):
            with pytest.raises(HostHandoffRefused):
                _admit_public_attempt(mock.Mock(), f"{suffix}.payload.signature")
        increment_counter.assert_not_called()

    _admit_public_attempt(mock.Mock(), _host_handoff(handoff_signing, 1))


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_host_handoff_refuses_nonstandard_json_constants(constant):
    """Non-standard JSON numbers remain inside the opaque refusal boundary."""

    header = _b64(b'{"alg":"EdDSA"}')
    payload = _b64(f'{{"issued_at":{constant}}}'.encode("ascii"))
    signature = _b64(b"invalid-signature")

    with pytest.raises(HostHandoffRefused):
        verify_host_handoff_contract(f"{header}.{payload}.{signature}")


def test_host_handoff_reports_malformed_public_key_as_unavailable(handoff_signing):
    """Invalid configured key material remains a service failure."""

    handoff = _host_handoff(handoff_signing, 1)
    malformed_jwk = json.dumps({"kty": "OKP", "crv": "Ed25519", "x": "AA"})
    with (
        override_settings(MASTRAO_ROOM_EFFECT_PUBLIC_JWK=malformed_jwk),
        pytest.raises(HostHandoffRefused) as error,
    ):
        verify_host_handoff_contract(handoff)

    assert error.value.status == 503


@override_settings(
    CACHES={
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "mastrao-host-handoff-valid-global-limit",
        }
    },
    MASTRAO_HOST_HANDOFF_GLOBAL_ATTEMPTS_PER_MINUTE=2,
)
def test_valid_handoffs_share_global_capacity(
    handoff_signing, local_handoff_verification
):
    """Valid distinct credentials share the redemption-work ceiling."""

    local_handoff_verification.side_effect = verify_host_handoff_contract
    _admit_public_attempt(mock.Mock(), _host_handoff(handoff_signing, 1))
    _admit_public_attempt(mock.Mock(), _host_handoff(handoff_signing, 2))

    with pytest.raises(HostHandoffRefused) as error:
        _admit_public_attempt(mock.Mock(), _host_handoff(handoff_signing, 3))

    assert error.value.status == 503


def test_sentry_scrubs_host_handoff_credentials():
    event = {
        "request": {
            "data": {
                "host_handoff": "header.payload.signature",
                "host_grant": "grant.payload.signature",
                "close_assertion": "close.payload.signature",
                "room_close_effect": "effect.payload.signature",
                "room_close_receipt": "receipt.payload.signature",
                "safe": "kept",
            }
        }
    }

    scrubbed = scrub_mastrao_handoff_credentials(event, {})

    assert scrubbed["request"]["data"] == {
        "host_handoff": "[Filtered]",
        "host_grant": "[Filtered]",
        "close_assertion": "[Filtered]",
        "room_close_effect": "[Filtered]",
        "room_close_receipt": "[Filtered]",
        "safe": "kept",
    }


def test_sentry_scrubs_raw_guest_confirmation_credentials():
    event = {
        "request": {
            "data": json.dumps(
                {
                    "decision_grant": "decision.payload.signature",
                    "receipt_assertion": "receipt.payload.signature",
                }
            )
        }
    }

    scrubbed = scrub_mastrao_handoff_credentials(event, {})

    assert scrubbed["request"]["data"] == "[Filtered]"


def test_sentry_scrubs_transcription_effects_and_receipts():
    event = {
        "request": {
            "data": {
                "transcription_submit_effect": "effect.payload.signature",
                "transcription_artifact_receipt": "receipt.payload.signature",
                "transcription_egress_request": "request.payload.signature",
                "transcription_egress_grant": "grant.payload.signature",
                "transcription_terminal_receipt": "terminal.payload.signature",
                "safe": "kept",
            }
        }
    }
    scrubbed = scrub_mastrao_handoff_credentials(event, {})
    assert scrubbed["request"]["data"] == {
        "transcription_submit_effect": "[Filtered]",
        "transcription_artifact_receipt": "[Filtered]",
        "transcription_egress_request": "[Filtered]",
        "transcription_egress_grant": "[Filtered]",
        "transcription_terminal_receipt": "[Filtered]",
        "safe": "kept",
    }

    raw = {
        "request": {
            "data": json.dumps(
                {"transcription_failure_receipt": "receipt.payload.signature"}
            )
        }
    }
    assert scrub_mastrao_handoff_credentials(raw, {})["request"]["data"] == (
        "[Filtered]"
    )

    headers = {
        "request": {
            "headers": {
                "X-Mastrao-Transcription-Egress-Grant": "grant.payload.signature",
                "Accept": "application/json",
            }
        }
    }
    assert scrub_mastrao_handoff_credentials(headers, {})["request"]["headers"] == {
        "X-Mastrao-Transcription-Egress-Grant": "[Filtered]",
        "Accept": "application/json",
    }


def _assert_host_platform_return(client, binding, grant):
    with mock.patch("core.mastrao_host_grant.verify_host_grant", return_value=grant):
        response = client.get(f"/api/v1.0/rooms/{binding.room.id}/")
    assert response.status_code == 200
    assert response.json()["platform_return"] == {
        "url": (
            "https://platform.mastrao.test/api/meeting-return"
            "?organization_ref=organization_0123456789"
            "&meeting_ref=meeting_0123456789abcdef"
        ),
        "expires_at": grant["expires_at"],
    }


@override_settings(MASTRAO_PLATFORM_ORIGIN="https://attacker.test/path")
def test_host_platform_return_rejects_non_origin_configuration():
    grant = mock.Mock()
    with mock.patch(
        "core.mastrao_host_grant.active_host_close_grant", return_value=grant
    ):
        assert host_platform_return_projection(mock.Mock(), mock.Mock()) is None


@override_settings(MASTRAO_PLATFORM_ORIGIN="https://platform.mastrao.test")
def test_host_platform_return_rejects_a_grant_binding_mismatch():
    expires_at = timezone.now() + timedelta(minutes=5)
    stored = mock.Mock(
        grant_ref="grant_0123456789abcdef",
        meeting_ref="meeting_0123456789abcdef",
        room_ref="room_0123456789abcdef",
        platform_session_ref="platformsession_0123456789abcdef",
        provider_binding_digest="b" * 64,
        expires_at=expires_at,
    )
    claims = {
        "grant_ref": stored.grant_ref,
        "organization_external_id": "organization_0123456789",
        "meeting_ref": "meeting_different_01234567",
        "room_ref": stored.room_ref,
        "platform_session_ref": stored.platform_session_ref,
        "provider_binding_digest": stored.provider_binding_digest,
        "expires_at": int(expires_at.timestamp()),
    }
    with (
        mock.patch(
            "core.mastrao_host_grant.active_host_close_grant",
            return_value=stored,
        ),
        mock.patch(
            "core.mastrao_host_grant.active_host_compact_grant",
            return_value="grant.payload.signature",
        ),
        mock.patch("core.mastrao_host_grant.verify_host_grant", return_value=claims),
    ):
        assert host_platform_return_projection(mock.Mock(), mock.Mock()) is None


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
    assert response.cookies["csrftoken"].value
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
    assert client.get("/api/v1.0/users/me/").status_code == 200
    _assert_host_platform_return(client, binding, grant)
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
    assert "platform_return" not in trusted_response.json()

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
@override_settings(
    MASTRAO_HOST_HANDOFF_ENABLED=True,
    MASTRAO_MEETING_CLOSE_ENABLED=True,
    MASTRAO_PLATFORM_ORIGIN="https://platform.mastrao.test",
)
def test_exact_host_can_end_and_retry_after_tombstone(client):
    """A lost response can be retried without restoring any media capability."""

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

    room_response = client.get(f"/api/v1.0/rooms/{binding.room_id}/")
    assert room_response.status_code == 200
    assert room_response.json()["can_end"] is True

    host = models.MastraoHostIdentity.objects.get().user
    request = mock.Mock(user=host, session=client.session)
    binding.closing_at = timezone.now()
    binding.save(update_fields=["closing_at", "updated_at"])
    assert active_host_grant(request, binding.room) is None
    assert active_host_close_grant(request, binding.room) is not None

    models.MastraoRoomClosure.objects.create(
        room_binding=binding,
        organization_external_id="organization_0123456789",
        meeting_ref=binding.meeting_ref,
        room_ref=binding.room_ref,
        provider_binding_digest=binding.provider_binding_digest,
        close_ref="close_0123456789abcdef",
        effect_key="close_effect_0123456789abcdef",
        arguments_digest="c" * 64,
        requested_at=timezone.now(),
    )
    assert active_host_grant(request, binding.room) is None
    assert active_host_close_grant(request, binding.room) is not None

    result = {
        "version": 1,
        "matter_ref": "matter_0123456789abcdef",
        "meeting_ref": binding.meeting_ref,
        "room_ref": binding.room_ref,
        "state": "ended",
        "state_version": 2,
        "requested_at": int(time.time()),
        "ended_at": int(time.time()),
    }
    endpoint = f"/api/v1.0/rooms/{binding.room_id}/end/"
    payload = {"close_request_id": "close_request_0123456789"}
    with mock.patch(
        "core.api.viewsets.request_meeting_close", return_value=result
    ) as close:
        first = client.post(endpoint, payload, content_type="application/json")
        second = client.post(endpoint, payload, content_type="application/json")

    assert first.status_code == second.status_code == 200
    assert first.json() == second.json() == result
    assert [call.args[2] for call in close.call_args_list] == [
        payload["close_request_id"],
        payload["close_request_id"],
    ]

    request_entry = client.post(
        f"/api/v1.0/rooms/{binding.room_id}/request-entry/",
        {"username": "host"},
        content_type="application/json",
    )
    assert request_entry.status_code == 404
    cache.clear()


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
    SESSION_ENGINE="django.contrib.sessions.backends.db",
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
                data="host_handoff=newsessionfirst.payload.signature",
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
                data="host_handoff=newsessionsecond.payload.signature",
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
