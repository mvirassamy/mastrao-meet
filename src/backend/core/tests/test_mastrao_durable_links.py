"""Signed exchanges and persisted retry identity for durable meeting entry."""

# pylint: disable=missing-function-docstring,protected-access,redefined-outer-name
import base64
import hashlib
import json
import time
from unittest.mock import patch

from django.core.cache import cache
from django.test import Client

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core import models
from core.mastrao_guest_contract import GuestHandoffRefused
from core.mastrao_guest_grant import SESSION_NONCE_KEY
from core.mastrao_host_contract import HostHandoffRefused
from core.mastrao_platform_facade import PlatformFacadeError
from core.mastrao_room_contract import _canonical_json
from core.tests.test_mastrao_guest_invitation import _guest_grant, _room_binding
from core.tests.test_mastrao_host_handoff import _grant

pytestmark = pytest.mark.django_db
SHARE = "share_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdef"
REDEMPTION = "redemption_" + "1" * 32


def _b64(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _signed(key, payload, typ):
    protected = _b64(_canonical_json({"alg": "EdDSA", "kid": "local-key", "typ": typ}))
    body = _b64(_canonical_json(payload))
    return f"{protected}.{body}.{_b64(key.sign(f'{protected}.{body}'.encode()))}"


def _verified(key, compact):
    protected, body, signature = compact.split(".")
    key.public_key().verify(
        base64.urlsafe_b64decode(signature + "=="), f"{protected}.{body}".encode()
    )
    return json.loads(base64.urlsafe_b64decode(body + "=="))


@pytest.fixture(autouse=True)
def signed_settings(settings):
    key = Ed25519PrivateKey.generate()
    private = key.private_bytes(
        serialization.Encoding.Raw,
        serialization.PrivateFormat.Raw,
        serialization.NoEncryption(),
    )
    public = key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    settings.ALLOWED_HOSTS = ["testserver"]
    settings.APPLICATION_BASE_URL = "http://testserver"
    settings.MASTRAO_MEETING_INTEGRATION_CONFIGURED = True
    settings.MASTRAO_ROOM_EFFECT_ISSUER = "cabinet-core-local"
    settings.MASTRAO_ROOM_EFFECT_AUDIENCE = "mastrao-meet-local"
    settings.MASTRAO_ROOM_EFFECT_KEY_ID = "local-key"
    settings.MASTRAO_ROOM_EFFECT_PUBLIC_JWK = json.dumps(
        {"kty": "OKP", "crv": "Ed25519", "x": _b64(public)}
    )
    settings.MASTRAO_ROOM_RECEIPT_ISSUER = "mastrao-meet-local"
    settings.MASTRAO_ROOM_RECEIPT_AUDIENCE = "cabinet-core-local"
    settings.MASTRAO_ROOM_RECEIPT_KEY_ID = "local-key"
    settings.MASTRAO_ROOM_RECEIPT_PRIVATE_JWK = json.dumps(
        {"kty": "OKP", "crv": "Ed25519", "d": _b64(private)}
    )
    cache.clear()
    return key


def _guest_post(client, **changes):
    return client.post(
        "/handoff/guest/share/",
        data=json.dumps(
            {
                "organization_external_id": "organization_0123456789",
                "share_ref": SHARE,
                "redemption_id": REDEMPTION,
                **changes,
            }
        ),
        content_type="application/json",
        HTTP_ORIGIN="http://testserver",
        HTTP_SEC_FETCH_SITE="same-origin",
    )


def _establish(client):
    assert (
        client.post(
            "/handoff/guest/session/",
            data="{}",
            content_type="application/json",
            HTTP_ORIGIN="http://testserver",
            HTTP_SEC_FETCH_SITE="same-origin",
        ).status_code
        == 200
    )


def test_signed_share_retry_survives_new_request_without_extending_grant(
    signed_settings,
):
    binding = _room_binding()
    client = Client()
    _establish(client)
    nonce = client.session[SESSION_NONCE_KEY]
    grant = _guest_grant(binding, SHARE)
    grant["redemption_id"] = REDEMPTION
    compact = _signed(signed_settings, grant, "mastrao-meeting-guest-grant+jws")
    attempts = []

    def core(_setting, path, body, _field):
        assert path == "/internal/v1/meetings/guest-share-links/redeem"
        assertion = _verified(signed_settings, body["redemption_assertion"])
        assert (
            assertion["share_ref_digest"] == hashlib.sha256(SHARE.encode()).hexdigest()
        )
        assert (
            assertion["session_nonce_digest"]
            == hashlib.sha256(nonce.encode()).hexdigest()
        )
        assert assertion["expires_at"] - assertion["issued_at"] == 30
        attempts.append(assertion)
        if len(attempts) == 1:
            raise GuestHandoffRefused(status=503)
        return {"guest_grant": compact}

    with patch("core.mastrao_guest_share_link._post_core", side_effect=core):
        assert _guest_post(client).status_code == 503
        # A new request/session reader has only the browser cookies; no process local state.
        restarted = Client()
        restarted.cookies = client.cookies.copy()
        response = _guest_post(restarted)
        assert response.status_code == 200
        persisted = models.MastraoGuestGrant.objects.get()
        dates = persisted.issued_at, persisted.expires_at
        assert _guest_post(restarted).status_code == 200
        persisted.refresh_from_db()
        assert (persisted.issued_at, persisted.expires_at) == dates
        assert models.MastraoGuestGrant.objects.count() == 1
        assert (
            persisted.admission_state == models.MastraoGuestGrant.AdmissionState.WAITING
        )
    assert len({attempt["jti"] for attempt in attempts}) == 3
    assert {attempt["redemption_id"] for attempt in attempts} == {REDEMPTION}
    assert response.json() == {"room_url": f"/{binding.room_ref}"}
    assert response["Cache-Control"] == "private, no-store"
    assert SHARE not in response.content.decode()


@pytest.mark.parametrize(
    "mutation",
    [
        {"organization_external_id": "other_org"},
        {"provider_binding_digest": "c" * 64},
        {"credential_digest": "d" * 64},
        {"expires_at": 1},
    ],
)
def test_crossed_or_expired_signed_share_grant_cannot_enter(mutation, signed_settings):
    binding = _room_binding()
    client = Client()
    _establish(client)
    grant = {**_guest_grant(binding, SHARE), "redemption_id": REDEMPTION, **mutation}
    compact = _signed(signed_settings, grant, "mastrao-meeting-guest-grant+jws")
    with patch(
        "core.mastrao_guest_share_link._post_core",
        return_value={"guest_grant": compact},
    ):
        assert _guest_post(client).status_code == 404
    assert not models.MastraoGuestGrant.objects.exists()


def test_unknown_revoked_or_acl_denied_share_is_opaque():
    client = Client()
    _establish(client)
    with patch(
        "core.mastrao_guest_share_link._post_core", side_effect=GuestHandoffRefused()
    ):
        response = _guest_post(client)
    assert response.status_code == 404
    assert response.json() == {"message": "Unavailable"}
    assert not models.MastraoGuestGrant.objects.exists()


def _host_client():
    user = models.User(sub="oidc-host")
    user.set_unusable_password()
    user.save()
    client = Client()
    client.force_login(user)
    session = client.session
    session["oidc_access_token"] = "local-oidc-access-token"
    session.save()
    return client, user


def _host_handoff(key, binding, **changes):
    now = int(time.time())
    claims = {
        "version": 1,
        "type": "mastrao.core-meeting-host-handoff",
        "issuer": "cabinet-core-local",
        "audience": "mastrao-meet-local",
        "purpose": "join_as_host",
        "handoff_ref": "handoff_0123456789abcdef",
        "organization_external_id": "organization_0123456789",
        "meeting_ref": binding.meeting_ref,
        "room_ref": binding.room_ref,
        "host_ref": "host_0123456789abcdef",
        "platform_session_ref": "platformsession_0123456789abcdef",
        "provider_binding_digest": binding.provider_binding_digest,
        "issued_at": now,
        "expires_at": now + 120,
        "grant_expires_at": now + 3600,
        "nonce": "nonce_0123456789abcdef",
    }
    return _signed(key, {**claims, **changes}, "mastrao-meeting-host-handoff+jws")


def _recover(client, binding, key="1" * 32):
    return client.post(
        f"/api/v1.0/rooms/{binding.room_ref}/host-handoff/",
        data="",
        content_type="application/octet-stream",
        HTTP_X_IDEMPOTENCY_KEY=key,
    )


def test_host_recovery_preserves_oidc_and_replays_original_authority(signed_settings):
    binding = _room_binding()
    client, user = _host_client()
    handoff = _host_handoff(signed_settings, binding)
    attempts = []
    compact = None

    def core(**kwargs):
        nonlocal compact
        assertion = _verified(signed_settings, kwargs["body"]["redemption_assertion"])
        attempts.append(assertion)
        if compact is None:
            grant = _grant(binding)
            grant["redemption_id"] = assertion["redemption_id"]
            grant["credential_digest"] = hashlib.sha256(handoff.encode()).hexdigest()
            compact = _signed(signed_settings, grant, "mastrao-meeting-host-grant+jws")
            raise HostHandoffRefused(status=503)
        return {"host_grant": compact}

    with (
        patch(
            "core.mastrao_host_recovery.request_platform",
            return_value=(
                {
                    "meeting_ref": binding.meeting_ref,
                    "room_ref": binding.room_ref,
                    "host_handoff": handoff,
                },
                201,
            ),
        ) as platform,
        patch("core.mastrao_host_handoff.post_core_json", side_effect=core),
    ):
        assert _recover(client, binding).status_code == 503
        restarted = Client()
        restarted.cookies = client.cookies.copy()
        assert _recover(restarted, binding).status_code == 200
        persisted = models.MastraoHostGrant.objects.get()
        dates = persisted.issued_at, persisted.expires_at
        assert _recover(restarted, binding).status_code == 200
        persisted.refresh_from_db()
        assert (persisted.issued_at, persisted.expires_at) == dates
        assert models.MastraoHostGrant.objects.count() == 1
        assert restarted.session["_auth_user_id"] == str(user.pk)
        assert all(
            call.kwargs["options"]["idempotency_key"] == "1" * 32
            for call in platform.call_args_list
        )
    assert len({attempt["redemption_id"] for attempt in attempts}) == 1
    assert len({attempt["jti"] for attempt in attempts}) == 3


def test_host_recovery_requires_oidc_and_current_platform_acl():
    binding = _room_binding()
    with patch("core.mastrao_host_recovery.request_platform") as platform:
        assert _recover(Client(), binding).status_code == 401
        platform.assert_not_called()
    client, _ = _host_client()
    with (
        patch(
            "core.mastrao_host_recovery.request_platform",
            side_effect=PlatformFacadeError(status=404),
        ),
        patch("core.mastrao_host_handoff.post_core_json") as core,
    ):
        assert _recover(client, binding).status_code == 404
        core.assert_not_called()
    assert not models.MastraoHostGrant.objects.exists()


def test_expired_host_attempt_requires_a_fresh_platform_key(signed_settings):
    binding = _room_binding()
    client, _ = _host_client()
    now = int(time.time())
    expired = _host_handoff(
        signed_settings, binding, issued_at=now - 130, expires_at=now - 10
    )
    fresh = _host_handoff(signed_settings, binding)

    def platform_response(handoff):
        return {
            "meeting_ref": binding.meeting_ref,
            "room_ref": binding.room_ref,
            "host_handoff": handoff,
        }, 201

    def core(**kwargs):
        assertion = _verified(signed_settings, kwargs["body"]["redemption_assertion"])
        grant = {
            **_grant(binding),
            "redemption_id": assertion["redemption_id"],
            "credential_digest": hashlib.sha256(fresh.encode()).hexdigest(),
        }
        return {
            "host_grant": _signed(
                signed_settings, grant, "mastrao-meeting-host-grant+jws"
            )
        }

    with (
        patch(
            "core.mastrao_host_recovery.request_platform",
            side_effect=[platform_response(expired), platform_response(fresh)],
        ) as platform,
        patch(
            "core.mastrao_host_handoff.post_core_json", side_effect=core
        ) as post_core,
    ):
        assert _recover(client, binding, "1" * 32).status_code == 404
        post_core.assert_not_called()
        assert _recover(client, binding, "2" * 32).status_code == 200
    assert [
        call.kwargs["options"]["idempotency_key"] for call in platform.call_args_list
    ] == ["1" * 32, "2" * 32]
    assert models.MastraoHostGrant.objects.count() == 1


def test_host_recovery_requires_csrf_for_authenticated_mutation():
    binding = _room_binding()
    client, _ = _host_client()
    enforced = Client(enforce_csrf_checks=True)
    enforced.cookies = client.cookies.copy()
    with patch("core.mastrao_host_recovery.request_platform") as platform:
        assert _recover(enforced, binding).status_code == 403
        platform.assert_not_called()


def test_host_retry_identity_is_scoped_to_meeting_and_platform_session(signed_settings):
    first = _room_binding()
    second = _room_binding("2" * 32)
    client, _ = _host_client()
    attempts = []

    def core(**kwargs):
        attempts.append(
            _verified(signed_settings, kwargs["body"]["redemption_assertion"])[
                "redemption_id"
            ]
        )
        raise HostHandoffRefused(status=503)

    responses = []
    for binding, changes in [
        (first, {}),
        (second, {}),
        (first, {}),
        (first, {"platform_session_ref": "platformsession_2222222222222222"}),
    ]:
        responses.append(
            (
                {
                    "meeting_ref": binding.meeting_ref,
                    "room_ref": binding.room_ref,
                    "host_handoff": _host_handoff(signed_settings, binding, **changes),
                },
                201,
            )
        )
    with (
        patch("core.mastrao_host_recovery.request_platform", side_effect=responses),
        patch("core.mastrao_host_handoff.post_core_json", side_effect=core),
    ):
        for binding in [first, second, first, first]:
            assert _recover(client, binding).status_code == 503
    assert attempts[0] == attempts[2]
    assert len({attempts[0], attempts[1], attempts[3]}) == 3
