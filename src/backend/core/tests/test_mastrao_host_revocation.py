"""Proofs for host grant revocation and close retries after room tombstones."""

import time
from unittest import mock

from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

import pytest

from core import models
from core.mastrao_host_grant import (
    SESSION_NONCE_KEY,
    SESSION_PLATFORM_REF_KEY,
    active_host_close_grant,
    active_host_grant,
)
from core.mastrao_identity import mastrao_host_subject
from core.tests import test_mastrao_host_handoff
from core.tests.test_mastrao_host_handoff import (
    _grant,
    _room_binding,
)

# Register the same per-test verifier isolation as the handoff proofs.
fixture_local_handoff_verification = (
    test_mastrao_host_handoff.fixture_local_handoff_verification
)


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
    with mock.patch("core.api.serializers.recording_session_status", return_value=None):
        room_response = client.get(f"/api/v1.0/rooms/{binding.room.slug}/")
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
@override_settings(
    MASTRAO_HOST_HANDOFF_ENABLED=True,
    MASTRAO_PLATFORM_ORIGIN="https://platform.mastrao.test",
    SESSION_ENGINE="django.contrib.sessions.backends.db",
)
def test_new_platform_session_invalidates_previous_grants(client):
    """Rotate the session nonce so a new platform session invalidates prior grants."""

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
    """Refuse inactive host identities without creating a temporary grant."""

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
