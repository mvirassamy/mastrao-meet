"""Real Django view, CSRF and cache sessions; ORM and authority peers are controlled."""

# Pytest resolves fixture arguments; the descriptive test names state each contract.
# pylint: disable=missing-function-docstring,redefined-outer-name,unused-argument

import base64
import json
import time
from types import SimpleNamespace
from unittest.mock import patch

from django.core.cache import cache
from django.middleware.csrf import _get_new_csrf_string
from django.test import Client
from django.urls import path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core.mastrao_host_contract import HostHandoffRefused, compact_digest
from core.mastrao_host_grant import SESSION_NONCE_KEY
from core.mastrao_host_recovery import recover_meeting_host
from core.tests.test_mastrao_durable_links import (
    _host_handoff,
    _signed,
    _verified,
)

urlpatterns = [
    path("recover/<str:room_ref>/", recover_meeting_host),
]


@pytest.fixture
def signed_settings(settings):
    key = Ed25519PrivateKey.generate()
    public = (
        base64.urlsafe_b64encode(key.public_key().public_bytes_raw())
        .rstrip(b"=")
        .decode()
    )
    private = base64.urlsafe_b64encode(key.private_bytes_raw()).rstrip(b"=").decode()
    settings.ALLOWED_HOSTS = ["testserver"]
    settings.MASTRAO_MEETING_INTEGRATION_CONFIGURED = True
    settings.MASTRAO_ROOM_EFFECT_ISSUER = "cabinet-core-local"
    settings.MASTRAO_ROOM_EFFECT_AUDIENCE = "mastrao-meet-local"
    settings.MASTRAO_ROOM_EFFECT_KEY_ID = "local-key"
    settings.MASTRAO_ROOM_EFFECT_PUBLIC_JWK = json.dumps(
        {"kty": "OKP", "crv": "Ed25519", "x": public}
    )
    settings.MASTRAO_ROOM_RECEIPT_ISSUER = "mastrao-meet-local"
    settings.MASTRAO_ROOM_RECEIPT_AUDIENCE = "cabinet-core-local"
    settings.MASTRAO_ROOM_RECEIPT_KEY_ID = "local-key"
    settings.MASTRAO_ROOM_RECEIPT_PRIVATE_JWK = json.dumps(
        {"kty": "OKP", "crv": "Ed25519", "d": private}
    )
    return key


@pytest.fixture
def view_session(settings, signed_settings):
    """No database connection; production view/middleware/session code is loaded."""
    settings.ROOT_URLCONF = __name__
    settings.MIDDLEWARE = [
        "django.contrib.sessions.middleware.SessionMiddleware",
        "django.middleware.csrf.CsrfViewMiddleware",
        "django.contrib.auth.middleware.AuthenticationMiddleware",
    ]
    settings.CACHES = {
        "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}
    }
    settings.SESSION_ENGINE = "django.contrib.sessions.backends.cache"
    settings.MASTRAO_HOST_HANDOFF_GLOBAL_ATTEMPTS_PER_MINUTE = 100
    cache.clear()
    user = SimpleNamespace(is_authenticated=True, pk="oidc-user", sub="oidc-host")
    binding = SimpleNamespace(
        pk="binding-fixture",
        meeting_ref="meeting_0123456789abcdef",
        room_ref="room_0123456789abcdef0123456789abcdef",
        room=SimpleNamespace(slug="room_0123456789abcdef0123456789abcdef"),
        provider_binding_digest="a" * 64,
    )
    client = Client(enforce_csrf_checks=True)
    session = client.session
    session["oidc_access_token"] = "controlled-oidc-token"
    session.save()
    client.cookies[settings.SESSION_COOKIE_NAME] = session.session_key
    client.cookies[settings.CSRF_COOKIE_NAME] = _get_new_csrf_string()
    with (
        patch("django.contrib.auth.get_user", return_value=user),
        patch(
            "core.mastrao_host_recovery.models.MastraoRoomBinding.objects"
        ) as manager,
        patch(
            "core.mastrao_host_recovery._commit_grant", return_value=(None, binding)
        ) as commit,
    ):
        manager.select_related.return_value.filter.return_value.first.return_value = (
            binding
        )
        yield client, binding, commit


def post(client, binding, csrf=True):
    headers = {"HTTP_X_IDEMPOTENCY_KEY": "1" * 32}
    if csrf:
        headers["HTTP_X_CSRFTOKEN"] = client.cookies["csrftoken"].value
    return client.post(
        f"/recover/{binding.room_ref}/",
        data="",
        content_type="application/octet-stream",
        **headers,
    )


def platform_body(binding, handoff):
    return {
        "meeting_ref": binding.meeting_ref,
        "room_ref": binding.room_ref,
        "host_handoff": handoff,
    }, 201


def test_late_response_loss_retry_uses_persisted_view_session(
    view_session, signed_settings
):
    client, binding, commit = view_session
    initial = int(time.time())
    handoff = _host_handoff(signed_settings, binding)
    attempts = []
    original = None

    def authority(**kwargs):
        nonlocal original
        redemption = _verified(signed_settings, kwargs["body"]["redemption_assertion"])
        attempts.append(redemption)
        if original is None:
            grant = {
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
                "redemption_id": redemption["redemption_id"],
                "credential_digest": compact_digest(handoff),
                "issued_at": initial,
                "expires_at": initial + 3600,
            }
            original = _signed(signed_settings, grant, "mastrao-meeting-host-grant+jws")
            raise HostHandoffRefused(status=503)
        return {"host_grant": original}

    with (
        patch(
            "core.mastrao_host_recovery.request_platform",
            return_value=platform_body(binding, handoff),
        ),
        patch("core.mastrao_host_handoff.post_core_json", side_effect=authority),
    ):
        assert post(client, binding).status_code == 503
        nonce = client.session[SESSION_NONCE_KEY]
        restarted = Client(enforce_csrf_checks=True)
        restarted.cookies = client.cookies.copy()
        with patch("core.mastrao_host_contract.time.time", return_value=initial + 121):
            assert post(restarted, binding).status_code == 200
        assert restarted.session[SESSION_NONCE_KEY] == nonce
        assert len(restarted.session["mastrao_host_recovery"]) == 1
    assert attempts[0]["redemption_id"] == attempts[1]["redemption_id"]
    assert attempts[0]["jti"] != attempts[1]["jti"]
    assert commit.call_args.args[1]["issued_at"] == initial
    assert commit.call_args.args[1]["expires_at"] == initial + 3600
    assert commit.call_args.kwargs == {"retain_oidc_user": True}


def test_expired_never_attempted_handoff_is_refused_before_core(
    view_session, signed_settings
):
    client, binding, commit = view_session
    now = int(time.time())
    handoff = _host_handoff(
        signed_settings, binding, issued_at=now - 130, expires_at=now - 10
    )
    with (
        patch(
            "core.mastrao_host_recovery.request_platform",
            return_value=platform_body(binding, handoff),
        ),
        patch("core.mastrao_host_handoff.post_core_json") as core,
    ):
        assert post(client, binding).status_code == 404
        core.assert_not_called()
        commit.assert_not_called()


def test_expired_original_grant_cannot_replay(view_session, signed_settings):
    client, binding, commit = view_session
    now = int(time.time())
    handoff = _host_handoff(
        signed_settings,
        binding,
        issued_at=now - 4000,
        expires_at=now - 3880,
        grant_expires_at=now - 400,
    )
    with (
        patch(
            "core.mastrao_host_recovery.request_platform",
            return_value=platform_body(binding, handoff),
        ),
        patch("core.mastrao_host_handoff.post_core_json") as core,
    ):
        assert post(client, binding).status_code == 404
        core.assert_not_called()
        commit.assert_not_called()


def test_view_keeps_csrf_before_any_authority_call(view_session):
    client, binding, commit = view_session
    with patch("core.mastrao_host_recovery.request_platform") as platform:
        assert post(client, binding, csrf=False).status_code == 403
        platform.assert_not_called()
        commit.assert_not_called()


@pytest.mark.parametrize(
    "change",
    [
        {"nonce": "nonce_changed_0123456789"},
        {"host_ref": "host_changed_0123456789"},
        {"platform_session_ref": "platformsession_changed_0123456789"},
    ],
)
def test_persisted_session_attempt_does_not_accept_changed_authority(
    view_session, signed_settings, change
):
    client, binding, commit = view_session
    initial = int(time.time())
    handoff = _host_handoff(signed_settings, binding)
    crossed = _host_handoff(signed_settings, binding, **change)
    with (
        patch(
            "core.mastrao_host_recovery.request_platform",
            side_effect=[
                platform_body(binding, handoff),
                platform_body(binding, crossed),
            ],
        ),
        patch(
            "core.mastrao_host_handoff.post_core_json",
            side_effect=HostHandoffRefused(status=503),
        ) as core,
    ):
        assert post(client, binding).status_code == 503
        assert client.session["mastrao_host_recovery"]
        with patch("core.mastrao_host_contract.time.time", return_value=initial + 121):
            assert post(client, binding).status_code == 404
        assert core.call_count == 1
        commit.assert_not_called()
