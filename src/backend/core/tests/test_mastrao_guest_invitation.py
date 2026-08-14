"""Focused proofs for anonymous canonical guest admission."""

import hashlib
import json
import time
from datetime import timedelta
from unittest import mock

from django.conf import settings
from django.core.cache import cache
from django.test import Client, override_settings
from django.urls import reverse
from django.utils import timezone

import pytest

from core import models, utils
from core.mastrao_guest_contract import GuestHandoffRefused, compact_digest
from core.mastrao_guest_grant import (
    SESSION_COMPACT_GRANT_KEY,
    SESSION_GRANT_REF_KEY,
    SESSION_NONCE_KEY,
)
from core.mastrao_guest_handoff import GUEST_RETRY_COOKIE, decide_guest_admission
from core.mastrao_identity import mastrao_technical_owner_subject

pytestmark = pytest.mark.django_db


def _room_binding(suffix="0123456789abcdef0123456789abcdef"):
    owner_ref = f"owner_{suffix}"
    owner = models.User(sub=mastrao_technical_owner_subject(owner_ref), is_device=True)
    owner.set_unusable_password()
    owner.save()
    room = models.Room.objects.create(
        name=f"room_{suffix}",
        slug=f"room_{suffix}",
        access_level=models.RoomAccessLevel.RESTRICTED,
    )
    models.ResourceAccess.objects.create(
        resource=room,
        user=owner,
        role=models.RoleChoices.OWNER,
    )
    return models.MastraoRoomBinding.objects.create(
        effect_key=f"effect_{suffix}",
        arguments_digest="a" * 64,
        meeting_ref=f"meeting_{suffix}",
        room_ref=f"room_{suffix}",
        owner_ref=owner_ref,
        room=room,
        owner=owner,
        provider_binding_digest="b" * 64,
    )


def _guest_grant(binding, compact_invitation="aaa.bbb.ccc"):
    now = int(time.time())
    return {
        "version": 1,
        "type": "mastrao.core-meeting-guest-grant",
        "issuer": "cabinet-core-local",
        "audience": "mastrao-meet-local",
        "purpose": "guest_lobby",
        "grant_ref": "guestgrant_0123456789abcdef",
        "invitation_ref": "invitation_0123456789abcdef",
        "redemption_id": "redemption_0123456789abcdef0123456789abcdef",
        "guest_ref": "guest_0123456789abcdef",
        "organization_external_id": "organization_0123456789",
        "meeting_ref": binding.meeting_ref,
        "room_ref": binding.room_ref,
        "provider_binding_digest": binding.provider_binding_digest,
        "credential_digest": compact_digest(compact_invitation),
        "issued_at": now,
        "expires_at": now + 3_600,
    }


def _redeem_guest(client, binding, invitation="aaa.bbb.ccc"):
    grant = _guest_grant(binding, invitation)
    established = client.post(
        reverse("establish_mastrao_guest_session"),
        data="{}",
        content_type="application/json",
        HTTP_ORIGIN="http://meet.test",
        HTTP_SEC_FETCH_SITE="same-origin",
    )
    assert established.status_code == 200
    with (
        mock.patch(
            "core.mastrao_guest_handoff.verify_guest_invitation",
            return_value={"invitation_ref": grant["invitation_ref"]},
        ),
        mock.patch(
            "core.mastrao_guest_handoff._redeem",
            return_value=(grant, "ddd.eee.fff"),
        ),
    ):
        response = client.post(
            reverse("consume_mastrao_guest_invitation"),
            data=json.dumps(
                {
                    "guest_invitation": invitation,
                    "redemption_id": grant["redemption_id"],
                }
            ),
            content_type="application/json",
            HTTP_ORIGIN="http://meet.test",
            HTTP_SEC_FETCH_SITE="same-origin",
        )
    return response, grant


@override_settings(
    APPLICATION_BASE_URL="http://meet.test",
    MASTRAO_GUEST_INVITATION_ENABLED=True,
)
def test_guest_retry_cookie_is_established_without_server_session_state():
    """The recovery nonce is host-only and does not allocate Redis session state."""

    client = Client(HTTP_HOST="meet.test")
    response = client.post(
        reverse("establish_mastrao_guest_session"),
        data="{}",
        content_type="application/json",
        HTTP_ORIGIN="http://meet.test",
        HTTP_SEC_FETCH_SITE="same-origin",
    )

    assert response.status_code == 200
    assert GUEST_RETRY_COOKIE in response.cookies
    cookie = response.cookies[GUEST_RETRY_COOKIE]
    assert cookie["httponly"] is True
    assert cookie["samesite"] == "Lax"
    assert cookie["path"] == "/"
    assert SESSION_NONCE_KEY not in client.session


@override_settings(
    APPLICATION_BASE_URL="http://meet.test",
    MASTRAO_GUEST_INVITATION_ENABLED=True,
)
def test_exact_redemption_retry_recovers_after_session_response_loss():
    """The pre-established nonce recovers grant session fields after a lost response."""

    binding = _room_binding()
    client = Client(HTTP_HOST="meet.test")
    first, grant = _redeem_guest(client, binding)
    assert first.status_code == 200

    session = client.session
    session.pop(SESSION_GRANT_REF_KEY)
    session.pop(SESSION_COMPACT_GRANT_KEY)
    session.save()

    replay, _ = _redeem_guest(client, binding)
    assert replay.status_code == 200
    assert client.session[SESSION_GRANT_REF_KEY] == grant["grant_ref"]
    assert client.session[SESSION_COMPACT_GRANT_KEY] == "ddd.eee.fff"
    assert models.MastraoGuestGrant.objects.count() == 1

    other_browser = Client(HTTP_HOST="meet.test")
    refused, _ = _redeem_guest(other_browser, binding)
    assert refused.status_code == 404
    assert models.MastraoGuestGrant.objects.count() == 1


@override_settings(
    APPLICATION_BASE_URL="http://meet.test",
    MASTRAO_GUEST_INVITATION_ENABLED=True,
)
def test_guest_verification_sheds_load_before_crypto_when_capacity_is_full():
    """Concurrent invalid credentials cannot occupy every request worker."""

    client = Client(HTTP_HOST="meet.test")
    with (
        mock.patch(
            "core.mastrao_guest_handoff._GUEST_VERIFY_SLOTS",
            mock.Mock(acquire=mock.Mock(return_value=False)),
        ),
        mock.patch("core.mastrao_guest_handoff.verify_guest_invitation") as verify,
    ):
        response = client.post(
            reverse("consume_mastrao_guest_invitation"),
            data=json.dumps(
                {
                    "guest_invitation": "aaa.bbb.ccc",
                    "redemption_id": "redemption_0123456789abcdef0123456789abcdef",
                }
            ),
            content_type="application/json",
            HTTP_ORIGIN="http://meet.test",
            HTTP_SEC_FETCH_SITE="same-origin",
        )

    assert response.status_code == 503
    verify.assert_not_called()


@override_settings(
    APPLICATION_BASE_URL="http://meet.test",
    MASTRAO_GUEST_INVITATION_ENABLED=True,
)
def test_guest_redemption_creates_only_room_bound_anonymous_grant(settings):
    """A redeemed invitation creates no durable user or room ACL."""

    settings.LOBBY_KEY_PREFIX = "guest-test-lobby"
    binding = _room_binding()
    client = Client(HTTP_HOST="meet.test")

    response, grant = _redeem_guest(client, binding)

    assert response.status_code == 200
    assert response.json() == {"room_url": f"/{binding.room.slug}"}
    assert response["Cache-Control"] == "private, no-store"
    assert response["Referrer-Policy"] == "no-referrer"
    assert models.MastraoGuestGrant.objects.count() == 1
    assert models.User.objects.count() == 1
    assert models.ResourceAccess.objects.count() == 1
    assert SESSION_NONCE_KEY in client.session
    assert client.session[SESSION_GRANT_REF_KEY] == grant["grant_ref"]

    raw = Client(HTTP_HOST="meet.test")
    assert raw.get(f"/api/v1.0/rooms/{binding.room.slug}/").status_code == 404
    guest_room = client.get(f"/api/v1.0/rooms/{binding.room.slug}/")
    assert guest_room.status_code == 200
    assert "livekit" not in guest_room.json()

    native = models.Room.objects.create(
        name="Native public room", access_level=models.RoomAccessLevel.PUBLIC
    )
    assert raw.get(f"/api/v1.0/rooms/{native.id}/").status_code == 200

    with mock.patch.object(utils, "notify_participants", return_value=None):
        waiting = client.post(
            f"/api/v1.0/rooms/{binding.room.id}/request-entry/",
            {"username": "Invité test"},
        )
    assert waiting.status_code == 200
    assert waiting.json()["status"] == "waiting"
    assert waiting.json()["id"] == grant["guest_ref"]
    assert waiting.json()["livekit"] is None


@override_settings(
    APPLICATION_BASE_URL="http://meet.test",
    MASTRAO_GUEST_INVITATION_ENABLED=True,
)
def test_guest_redemption_rotates_the_anonymous_session_key():
    """A known anonymous session cannot inherit the redeemed guest grant."""

    binding = _room_binding()
    client = Client(HTTP_HOST="meet.test")
    anonymous_session = client.session
    anonymous_session["pre_redemption_state"] = True
    anonymous_session.save()
    old_session_key = anonymous_session.session_key

    response, _ = _redeem_guest(client, binding, "session.rotate.test")

    assert response.status_code == 200
    assert client.session.session_key != old_session_key
    fixed_client = Client(HTTP_HOST="meet.test")
    fixed_client.cookies[settings.SESSION_COOKIE_NAME] = old_session_key
    assert fixed_client.get(f"/api/v1.0/rooms/{binding.room.slug}/").status_code == 404


@override_settings(
    APPLICATION_BASE_URL="http://meet.test",
    MASTRAO_GUEST_INVITATION_ENABLED=True,
)
def test_confirmed_local_allow_is_required_before_guest_media(settings):
    """A guest gets no media until Meet has confirmed the Core decision."""

    settings.LOBBY_KEY_PREFIX = "guest-media-lobby"
    binding = _room_binding()
    client = Client(HTTP_HOST="meet.test")
    _, grant_payload = _redeem_guest(client, binding)
    grant = models.MastraoGuestGrant.objects.get()

    with mock.patch.object(utils, "notify_participants", return_value=None):
        first = client.post(
            f"/api/v1.0/rooms/{binding.room.id}/request-entry/",
            {"username": "Guest"},
        )
    assert first.json()["livekit"] is None

    grant.admission_state = models.MastraoGuestGrant.AdmissionState.ALLOWED
    grant.decision_ref = "decision_0123456789abcdef"
    grant.decision_allow = True
    grant.decision_grant_digest = "d" * 64
    grant.save()
    with mock.patch("core.services.lobby.guest_media_config") as media_config:
        pending = client.post(
            f"/api/v1.0/rooms/{binding.room.id}/request-entry/",
            {"username": "Guest"},
        )
    assert pending.json()["status"] == "waiting"
    media_config.assert_not_called()

    grant.decision_confirmed_at = timezone.now()
    grant.decision_receipt_digest = "e" * 64
    grant.save()
    with mock.patch(
        "core.services.lobby.guest_media_config",
        return_value={"url": "wss://livekit.test", "room": "room", "token": "token"},
    ) as media_config:
        accepted = client.post(
            f"/api/v1.0/rooms/{binding.room.id}/request-entry/",
            {"username": "Guest"},
        )
    assert accepted.status_code == 200
    assert accepted.json()["status"] == "accepted"
    assert accepted.json()["livekit"]["token"] == "token"
    media_config.assert_called_once_with(
        mock.ANY,
        binding.room,
        "Guest",
        mock.ANY,
        grant_payload["guest_ref"],
    )


@override_settings(APPLICATION_BASE_URL="http://meet.test")
def test_host_decision_calls_core_then_confirms_without_acl_mutation():
    """Host admission is authorized and confirmed online without ACL mutation."""

    binding = _room_binding()
    now = timezone.now()
    guest = models.MastraoGuestGrant.objects.create(
        grant_ref="guestgrant_0123456789abcdef",
        redemption_id="redemption_0123456789abcdef",
        invitation_ref="invitation_0123456789abcdef",
        guest_ref="guest_0123456789abcdef",
        organization_external_id="organization_0123456789",
        grant_digest=compact_digest("ddd.eee.fff"),
        credential_digest="c" * 64,
        meeting_ref=binding.meeting_ref,
        room_ref=binding.room_ref,
        provider_binding_digest=binding.provider_binding_digest,
        room_binding=binding,
        session_nonce_digest="f" * 64,
        issued_at=now,
        expires_at=now + timedelta(hours=1),
    )
    cache.set(f"mastrao-guest-compact:{guest.grant_ref}", "ddd.eee.fff", 60)
    host = mock.Mock(grant_ref="hostgrant_0123456789abcdef")
    request = mock.Mock()
    decision_id = (
        "decision_" + hashlib.sha256(f"{guest.grant_ref}:True".encode()).hexdigest()
    )
    decision = {
        "decision_id": decision_id,
        "decision": "allow",
        "invitation_ref": guest.invitation_ref,
        "redemption_id": guest.redemption_id,
        "guest_ref": guest.guest_ref,
        "meeting_ref": guest.meeting_ref,
        "room_ref": guest.room_ref,
        "provider_binding_digest": guest.provider_binding_digest,
        "credential_digest": guest.credential_digest,
    }
    with (
        mock.patch("core.mastrao_guest_handoff.active_host_grant", return_value=host),
        mock.patch(
            "core.mastrao_guest_handoff.active_host_compact_grant",
            return_value="aaa.bbb.ccc",
        ),
        mock.patch(
            "core.mastrao_guest_handoff.sign_guest_decision",
            return_value=("decision.assertion.sig", {}),
        ),
        mock.patch(
            "core.mastrao_guest_handoff.verify_guest_decision_grant",
            return_value=decision,
        ),
        mock.patch(
            "core.mastrao_guest_handoff.sign_guest_decision_receipt",
            return_value=("receipt.assertion.sig", {}),
        ),
        mock.patch(
            "core.mastrao_guest_handoff._post_core",
            side_effect=[
                {"decision_grant": "decision.grant.sig"},
                {"version": 1, "decision_id": decision_id, "state": "confirmed"},
            ],
        ) as post_core,
    ):
        applied = decide_guest_admission(request, binding.room, guest.guest_ref, True)

    applied.refresh_from_db()
    assert applied.admission_state == models.MastraoGuestGrant.AdmissionState.ALLOWED
    assert applied.decision_confirmed_at is not None
    assert post_core.call_args_list[0].args[:2] == (
        "MASTRAO_CORE_GUEST_DECISION_ENDPOINT",
        "/internal/v1/meetings/guest-admissions/decide",
    )
    assert post_core.call_args_list[1].args[:2] == (
        "MASTRAO_CORE_GUEST_CONFIRM_ENDPOINT",
        "/internal/v1/meetings/guest-admissions/confirm",
    )
    assert models.User.objects.count() == 1
    assert models.ResourceAccess.objects.count() == 1


@override_settings(APPLICATION_BASE_URL="http://meet.test")
def test_host_decision_recovers_after_core_confirmation_response_loss():
    """A fresh exact receipt converges after Core committed but its response was lost."""

    binding = _room_binding()
    now = timezone.now()
    guest = models.MastraoGuestGrant.objects.create(
        grant_ref="guestgrant_0123456789abcdef",
        redemption_id="redemption_0123456789abcdef",
        invitation_ref="invitation_0123456789abcdef",
        guest_ref="guest_0123456789abcdef",
        organization_external_id="organization_0123456789",
        grant_digest=compact_digest("ddd.eee.fff"),
        credential_digest="c" * 64,
        meeting_ref=binding.meeting_ref,
        room_ref=binding.room_ref,
        provider_binding_digest=binding.provider_binding_digest,
        room_binding=binding,
        session_nonce_digest="f" * 64,
        issued_at=now,
        expires_at=now + timedelta(hours=1),
    )
    cache.set(f"mastrao-guest-compact:{guest.grant_ref}", "ddd.eee.fff", 60)
    host = mock.Mock(grant_ref="hostgrant_0123456789abcdef")
    request = mock.Mock()
    decision_id = (
        "decision_" + hashlib.sha256(f"{guest.grant_ref}:True".encode()).hexdigest()
    )
    decision = {
        "decision_id": decision_id,
        "decision": "allow",
        "invitation_ref": guest.invitation_ref,
        "redemption_id": guest.redemption_id,
        "guest_ref": guest.guest_ref,
        "meeting_ref": guest.meeting_ref,
        "room_ref": guest.room_ref,
        "provider_binding_digest": guest.provider_binding_digest,
        "credential_digest": guest.credential_digest,
    }
    with (
        mock.patch("core.mastrao_guest_handoff.active_host_grant", return_value=host),
        mock.patch(
            "core.mastrao_guest_handoff.active_host_compact_grant",
            return_value="aaa.bbb.ccc",
        ),
        mock.patch(
            "core.mastrao_guest_handoff.sign_guest_decision",
            return_value=("decision.assertion.sig", {}),
        ),
        mock.patch(
            "core.mastrao_guest_handoff.verify_guest_decision_grant",
            return_value=decision,
        ),
        mock.patch(
            "core.mastrao_guest_handoff.sign_guest_decision_receipt",
            side_effect=[
                ("first.receipt.sig", {}),
                ("fresh.receipt.sig", {}),
            ],
        ),
        mock.patch(
            "core.mastrao_guest_handoff._post_core",
            side_effect=[
                {"decision_grant": "decision.grant.sig"},
                GuestHandoffRefused(status=503),
                {"decision_grant": "decision.grant.sig"},
                {"version": 1, "decision_id": decision_id, "state": "confirmed"},
            ],
        ),
    ):
        with pytest.raises(GuestHandoffRefused):
            decide_guest_admission(request, binding.room, guest.guest_ref, True)
        recovered = decide_guest_admission(request, binding.room, guest.guest_ref, True)

    recovered.refresh_from_db()
    assert recovered.decision_confirmed_at is not None
    assert recovered.decision_receipt_digest == compact_digest("fresh.receipt.sig")
    assert models.ResourceAccess.objects.count() == 1
