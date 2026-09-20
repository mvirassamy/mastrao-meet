"""Issuance journal proof; RTC connection and capture authority are separate."""

# Imported pytest fixtures and generated LiveKit protobuf members are resolved dynamically.
# pylint: disable=missing-function-docstring,redefined-outer-name

import hashlib
from types import SimpleNamespace
from unittest import mock

from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied
from django.db import DatabaseError, IntegrityError, transaction
from django.test import RequestFactory
from django.utils import timezone

import jwt
import pytest

from core import models
from core.factories import RoomFactory, UserFactory
from core.mastrao_guest_contract import GuestHandoffRefused
from core.mastrao_guest_handoff import guest_media_config
from core.mastrao_host_grant import SESSION_NONCE_KEY, SESSION_PLATFORM_REF_KEY
from core.mastrao_identity import mastrao_host_subject
from core.mastrao_media_token_binding import generate_host_media_config
from core.mastrao_room_lifecycle import MastraoRoomClosed
from core.services.lobby import LobbyService

pytestmark = pytest.mark.django_db


def _digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


@pytest.fixture(autouse=True)
def isolated_binding_settings(settings):
    """Use no app cache or external service in this integration fixture."""
    settings.MASTRAO_MEDIA_TOKEN_BINDING_ENABLED = True
    settings.CACHES = {
        "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}
    }


@pytest.fixture
def binding():
    """One private test room and its canonical projection."""
    return models.MastraoRoomBinding.objects.create(
        effect_key="room_media_binding_fixture",
        arguments_digest="a" * 64,
        meeting_ref="meeting_media_binding_fixture",
        room_ref="room_media_binding_fixture",
        owner_ref="owner_media_binding_fixture",
        provider_binding_digest="b" * 64,
        room=RoomFactory(
            access_level=models.RoomAccessLevel.RESTRICTED,
            name="room_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        ),
        owner=UserFactory(),
    )


@pytest.fixture
def host(binding):
    """Persist a grant bound to this exact host browser session."""
    identity = models.MastraoHostIdentity.objects.create(
        host_ref="host_media_binding_fixture",
        user=UserFactory(sub=mastrao_host_subject("host_media_binding_fixture")),
    )
    return models.MastraoHostGrant.objects.create(
        identity=identity,
        room_binding=binding,
        handoff_ref="handoff_media_binding_fixture",
        grant_ref="hostgrant_media_fixture",
        grant_digest="a" * 64,
        credential_digest="c" * 64,
        meeting_ref=binding.meeting_ref,
        room_ref=binding.room_ref,
        provider_binding_digest=binding.provider_binding_digest,
        platform_session_ref="platform_media_fixture",
        session_nonce_digest=_digest("host-fixture-nonce-" * 3),
        issued_at=timezone.now(),
        expires_at=timezone.now() + timezone.timedelta(hours=1),
    )


def _request(host):
    request = RequestFactory().get("/")
    request.user = host.identity.user
    request.session = {
        SESSION_NONCE_KEY: "host-fixture-nonce-" * 3,
        SESSION_PLATFORM_REF_KEY: host.platform_session_ref,
    }
    return request


def _host_config(host, request=None, **overrides):
    return generate_host_media_config(
        request or _request(host),
        host.room_binding.room,
        **{
            "room_id": str(host.room_binding.room_id),
            "user": host.identity.user,
            "username": "Matt",
            "expires_at": host.expires_at,
            **overrides,
        },
    )


def _claims(result):
    return jwt.decode(
        result["token"],
        settings.LIVEKIT_CONFIGURATION["api_secret"],
        algorithms=["HS256"],
    )


def test_host_lobby_returns_token_only_with_persisted_exact_binding(host):
    """Execute real lobby, ORM and signer; only RTC room creation is stubbed."""
    with mock.patch("core.services.lobby.ensure_livekit_room"):
        _, result = LobbyService().request_entry(
            host.room_binding.room, _request(host), "Matt"
        )
    claims = _claims(result)
    row = models.MastraoMediaTokenBinding.objects.get(
        pk=claims["attributes"]["mastrao.media_token_binding_ref"]
    )
    assert row.host_grant_id == host.pk
    assert row.guest_grant_id is None
    assert row.room_binding_id == host.room_binding_id
    assert row.rtc_identity == claims["sub"] == str(host.identity.user.sub)
    assert row.session_nonce_digest == host.session_nonce_digest
    assert row.grant_digest == row.authorization_digest == host.grant_digest
    assert row.token_digest == _digest(result["token"])
    assert int(row.expires_at.timestamp()) == claims["exp"]
    assert row.expires_at <= host.expires_at
    assert claims["video"]["canUpdateOwnMetadata"] is False
    assert host.grant_ref not in str(claims["attributes"])


def test_repeated_issuance_has_distinct_receipts_for_same_grant(host):
    first, second = _host_config(host), _host_config(host)
    assert first["token"] != second["token"]
    assert models.MastraoMediaTokenBinding.objects.filter(host_grant=host).count() == 2


def test_disabled_binding_keeps_existing_token_without_journal(host, settings):
    settings.MASTRAO_MEDIA_TOKEN_BINDING_ENABLED = False
    result = _host_config(host)
    assert "mastrao.media_token_binding_ref" not in _claims(result)["attributes"]
    assert not models.MastraoMediaTokenBinding.objects.exists()


@pytest.mark.parametrize("mismatch", ["session", "room", "user", "expired", "closing"])
def test_host_rejects_invalid_binding_before_journal(host, mismatch):
    request, overrides = _request(host), {}
    if mismatch == "session":
        request.session[SESSION_NONCE_KEY] = "other-nonce-" * 4
    elif mismatch == "room":
        overrides["room_id"] = str(RoomFactory().id)
    elif mismatch == "user":
        overrides["user"] = UserFactory()
    elif mismatch == "expired":
        models.MastraoHostGrant.objects.filter(pk=host.pk).update(
            expires_at=timezone.now() - timezone.timedelta(seconds=1),
            issued_at=timezone.now() - timezone.timedelta(hours=1),
        )
    else:
        models.MastraoRoomBinding.objects.filter(pk=host.room_binding_id).update(
            closing_at=timezone.now()
        )
    with pytest.raises((PermissionDenied, MastraoRoomClosed)):
        _host_config(host, request=request, **overrides)
    assert not models.MastraoMediaTokenBinding.objects.exists()


def test_failed_persistence_returns_no_token(host):
    persist = models.MastraoMediaTokenBinding.save

    def fail_after_write(row, *args, **kwargs):
        persist(row, *args, **kwargs)
        assert models.MastraoMediaTokenBinding.objects.filter(pk=row.pk).exists()
        raise DatabaseError("synthetic failure after real insert")

    with (
        mock.patch.object(
            models.MastraoMediaTokenBinding,
            "save",
            new=fail_after_write,
        ),
        pytest.raises(DatabaseError),
    ):
        _host_config(host)
    assert not models.MastraoMediaTokenBinding.objects.exists()


def test_database_refuses_issuance_without_exactly_one_grant(host):
    _host_config(host)
    with pytest.raises(IntegrityError), transaction.atomic():
        models.MastraoMediaTokenBinding.objects.update(host_grant=None)
    assert models.MastraoMediaTokenBinding.objects.get().host_grant_id == host.pk


@pytest.mark.parametrize("wrong_session", [False, True])
def test_request_entry_http_returns_persisted_host_binding(host, client, wrong_session):
    """Real Django route/session/signer/DB, without an RTC connection."""
    client.force_login(
        host.identity.user,
        backend="core.authentication.handoff.MastraoHostAuthenticationBackend",
    )
    session = client.session
    session.update(_request(host).session)
    if wrong_session:
        session[SESSION_NONCE_KEY] = "different-browser-nonce-" * 3
    session.save()
    with (
        mock.patch("core.services.lobby.ensure_livekit_room"),
        mock.patch("core.utils.notify_participants") as notify,
    ):
        response = client.post(
            f"/api/v1.0/rooms/{host.room_binding.room_id}/request-entry/",
            {"username": "Matt"},
        )
    if wrong_session:
        assert response.status_code == 404
        notify.assert_not_called()
        assert not models.MastraoMediaTokenBinding.objects.exists()
        return
    assert response.status_code == 200
    claims = _claims(response.json()["livekit"])
    row = models.MastraoMediaTokenBinding.objects.get(
        pk=claims["attributes"]["mastrao.media_token_binding_ref"]
    )
    assert row.host_grant_id == host.pk


def test_lobby_without_media_permission_creates_no_binding(host):
    _, result = LobbyService().request_entry(
        host.room_binding.room, _request(host), "Matt", allow_media=False
    )
    assert result is None
    assert not models.MastraoMediaTokenBinding.objects.exists()


@pytest.fixture
def guest(binding):
    """A confirmed guest, with only synthetic Core response data."""
    return models.MastraoGuestGrant.objects.create(
        room_binding=binding,
        grant_ref="guestgrant_media_fixture",
        redemption_id="redemption_media_fixture",
        invitation_ref="invitation_media_fixture",
        guest_ref="guest_media_fixture",
        organization_external_id="organization_media_fixture",
        grant_digest=_digest("synthetic.guest.grant"),
        credential_digest="c" * 64,
        meeting_ref=binding.meeting_ref,
        room_ref=binding.room_ref,
        provider_binding_digest=binding.provider_binding_digest,
        session_nonce_digest=_digest("guest-fixture-nonce-" * 3),
        issued_at=timezone.now(),
        expires_at=timezone.now() + timezone.timedelta(hours=1),
        admission_state=models.MastraoGuestGrant.AdmissionState.ALLOWED,
        decision_ref="decision_media_fixture",
        decision_allow=True,
        decision_grant_digest="d" * 64,
        decision_receipt_digest="e" * 64,
        decision_confirmed_at=timezone.now(),
    )


def _guest_request(guest):
    return SimpleNamespace(
        user=AnonymousUser(),
        session={
            "mastrao_guest_session_nonce": "guest-fixture-nonce-" * 3,
            "mastrao_guest_grant_ref": guest.grant_ref,
            "mastrao_guest_compact_grant": "synthetic.guest.grant",
        },
    )


@pytest.mark.parametrize("mismatch", ["none", "room", "rotated_session"])
def test_guest_journal_requires_exact_verified_core_media_grant(guest, mismatch):
    media = {
        name: getattr(guest, name)
        for name in (
            "invitation_ref",
            "redemption_id",
            "guest_ref",
            "meeting_ref",
            "room_ref",
            "provider_binding_digest",
            "credential_digest",
        )
    }
    media.update(
        media_request_id="request_media_fixture",
        expires_at=int((timezone.now() + timezone.timedelta(seconds=120)).timestamp()),
    )
    if mismatch == "room":
        media["room_ref"] = "room_another"

    def verify_media(_compact):
        if mismatch == "rotated_session":
            models.MastraoGuestGrant.objects.filter(pk=guest.pk).update(
                session_nonce_digest=_digest("new-browser-session")
            )
        return media

    with (
        mock.patch(
            "core.mastrao_guest_handoff.sign_guest_media_request",
            return_value=(
                "synthetic.request.assertion",
                {"media_request_id": "request_media_fixture"},
            ),
        ),
        mock.patch(
            "core.mastrao_guest_handoff._post_core",
            return_value={"media_grant": "synthetic.media.grant"},
        ) as authorize,
        mock.patch(
            "core.mastrao_guest_handoff.verify_guest_media_grant",
            side_effect=verify_media,
        ),
        mock.patch("core.mastrao_guest_handoff.ensure_livekit_room"),
    ):
        if mismatch != "none":
            with pytest.raises((GuestHandoffRefused, PermissionDenied)):
                guest_media_config(
                    _guest_request(guest),
                    guest.room_binding.room,
                    "Martine",
                    "blue",
                    guest.guest_ref,
                )
            assert not models.MastraoMediaTokenBinding.objects.exists()
            return
        result = guest_media_config(
            _guest_request(guest),
            guest.room_binding.room,
            "Martine",
            "blue",
            guest.guest_ref,
        )
    assert authorize.call_args.args[1] == "/internal/v1/meetings/guest-media/authorize"
    claims = _claims(result)
    row = models.MastraoMediaTokenBinding.objects.get(
        pk=claims["attributes"]["mastrao.media_token_binding_ref"]
    )
    assert row.guest_grant_id == guest.pk and row.host_grant_id is None
    assert row.authorization_digest == _digest("synthetic.media.grant")
    assert row.session_nonce_digest == guest.session_nonce_digest
    assert row.rtc_identity == claims["sub"] == guest.guest_ref
    assert claims["exp"] <= media["expires_at"]


def test_unconfirmed_guest_never_gets_media_or_issuance_row(guest):
    models.MastraoGuestGrant.objects.filter(pk=guest.pk).update(
        decision_confirmed_at=None
    )
    with mock.patch("core.mastrao_guest_handoff._post_core") as authorize:
        assert (
            guest_media_config(
                _guest_request(guest),
                guest.room_binding.room,
                "Martine",
                "blue",
                guest.guest_ref,
            )
            is None
        )
    authorize.assert_not_called()
    assert not models.MastraoMediaTokenBinding.objects.exists()
