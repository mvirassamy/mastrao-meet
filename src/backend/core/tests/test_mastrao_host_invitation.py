"""Focused proofs for durable host invitation exchange into short handoffs."""

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from importlib import import_module
from unittest import mock

from django.conf import settings
from django.contrib.auth import SESSION_KEY
from django.core.cache import cache
from django.test import Client, override_settings
from django.urls import reverse
from django.utils import timezone

import pytest

from core import models
from core.mastrao_host_contract import HostHandoffRefused, compact_digest
from core.mastrao_host_grant import SESSION_NONCE_KEY, SESSION_PLATFORM_REF_KEY
from core.mastrao_host_invitation import (
    EXCHANGE_COOKIE,
    _admit_exchange_attempt,
    _exchange_claim,
    _exchange_invitation,
)
from core.tests import test_mastrao_host_handoff
from core.tests.test_mastrao_host_handoff import _grant, _room_binding

fixture_local_handoff_verification = (
    test_mastrao_host_handoff.fixture_local_handoff_verification
)

HOST_INVITATION = "host_invitation_" + "a" * 48
ORIGIN = "https://meet.mastrao.test"


def _prepare_exchange(client):
    return client.post(
        reverse("establish_mastrao_host_invitation_session"),
        data="{}",
        content_type="application/json",
        HTTP_ORIGIN=ORIGIN,
        HTTP_SEC_FETCH_SITE="same-origin",
    )


def _post_invitation(client, exchange_suffix="0" * 32, *, prepare=True):
    if prepare:
        prepared = _prepare_exchange(client)
        assert prepared.status_code == 200
    return client.post(
        reverse("exchange_mastrao_host_invitation"),
        data=json.dumps(
            {
                "host_invitation": HOST_INVITATION,
                "exchange_id": f"exchange_{exchange_suffix}",
            }
        ),
        content_type="application/json",
        HTTP_ORIGIN=ORIGIN,
        HTTP_SEC_FETCH_SITE="same-origin",
    )


def _handoff(binding, grant):
    return {
        "meeting_ref": binding.meeting_ref,
        "room_ref": binding.room_ref,
        "handoff_ref": grant["handoff_ref"],
        "platform_session_ref": grant["platform_session_ref"],
        "provider_binding_digest": binding.provider_binding_digest,
        "expires_at": grant["issued_at"] + 120,
        "grant_expires_at": grant["expires_at"],
    }


@pytest.mark.django_db(transaction=True)
@override_settings(
    APPLICATION_BASE_URL=ORIGIN,
    MASTRAO_HOST_HANDOFF_ENABLED=True,
)
def test_host_invitation_uses_existing_short_handoff_session(client):
    """The durable bearer only resolves a short handoff; existing host code does the rest."""

    binding = _room_binding()
    grant = _grant(binding)
    handoff = _handoff(binding, grant)
    with (
        mock.patch(
            "core.mastrao_host_invitation._exchange_invitation",
            return_value=("short.handoff.signature", handoff),
        ) as exchange,
        mock.patch(
            "core.mastrao_host_handoff._redeem",
            return_value=(grant, "short.grant.signature"),
        ) as redeem,
    ):
        response = _post_invitation(client)

    assert response.status_code == 200
    assert response.json() == {"room_url": f"/{binding.room.slug}"}
    assert response["Cache-Control"] == "private, no-store"
    assert response["Referrer-Policy"] == "no-referrer"
    assert response["Content-Security-Policy"] == "frame-ancestors 'none'"
    assert response.cookies["csrftoken"].value
    exchange.assert_called_once()
    exchange_args = exchange.call_args.args
    assert exchange_args[0] == HOST_INVITATION
    assert exchange_args[1] == f"exchange_{'0' * 32}"
    assert exchange_args[2].startswith("hostsession_")
    assert exchange_args[3] > int(timezone.now().timestamp())
    redeem.assert_called_once_with("short.handoff.signature")
    assert models.MastraoHostIdentity.objects.count() == 1
    assert models.MastraoHostGrant.objects.count() == 1
    assert models.ResourceAccess.objects.count() == 1


@pytest.mark.django_db(transaction=True)
@override_settings(
    APPLICATION_BASE_URL=ORIGIN,
    MASTRAO_HOST_HANDOFF_ENABLED=True,
)
def test_same_host_invitation_can_rejoin_through_a_fresh_short_handoff(client):
    """Core may re-exchange the durable link while Meet keeps one host identity."""

    binding = _room_binding()
    first = _grant(binding)
    second = {
        **first,
        "handoff_ref": "handoff_rejoin_0123456789",
        "grant_ref": "grant_rejoin_012345678901",
        "redemption_id": "redemption_rejoin_01234567",
        "credential_digest": "d" * 64,
    }
    first_handoff = _handoff(binding, first)
    second_handoff = _handoff(binding, second)
    with (
        mock.patch(
            "core.mastrao_host_invitation._exchange_invitation",
            side_effect=[
                ("first.handoff.signature", first_handoff),
                ("second.handoff.signature", second_handoff),
            ],
        ) as exchange,
        mock.patch(
            "core.mastrao_host_handoff._redeem",
            side_effect=[
                (first, "first.grant.signature"),
                (second, "second.grant.signature"),
            ],
        ),
    ):
        first_response = _post_invitation(client, "1" * 32)
        first_nonce = client.session[SESSION_NONCE_KEY]
        second_response = _post_invitation(client, "2" * 32)

    assert first_response.status_code == second_response.status_code == 200
    assert client.session[SESSION_NONCE_KEY] == first_nonce
    assert models.MastraoHostIdentity.objects.count() == 1
    assert models.MastraoHostGrant.objects.count() == 2
    assert models.ResourceAccess.objects.count() == 1
    first_exchange, second_exchange = exchange.call_args_list
    assert first_exchange.args[2:] == second_exchange.args[2:]


@pytest.mark.django_db(transaction=True)
@override_settings(
    APPLICATION_BASE_URL=ORIGIN,
    MASTRAO_HOST_HANDOFF_ENABLED=True,
)
def test_exact_exchange_retry_restores_a_usable_browser_session():
    """A lost success response can be retried without redeeming the handoff twice."""

    client = Client(enforce_csrf_checks=True)
    binding = _room_binding()
    compact_handoff = "retry.handoff.signature"
    grant = {
        **_grant(binding),
        "credential_digest": compact_digest(compact_handoff),
    }
    handoff = _handoff(binding, grant)
    prepared = _prepare_exchange(client)
    assert prepared.status_code == 200
    exchange_cookie = client.cookies[EXCHANGE_COOKIE].value
    real_cache_set = cache.set

    def publish_after_session_save(key, value, *args, **kwargs):
        if "mastrao-host-invitation-result" in key:
            session_store = import_module(settings.SESSION_ENGINE).SessionStore(
                session_key=value["session_key"]
            )
            assert session_store.exists(value["session_key"])
            assert session_store.load()[SESSION_KEY] == value["user_id"]
        return real_cache_set(key, value, *args, **kwargs)

    with (
        mock.patch(
            "core.mastrao_host_invitation._exchange_invitation",
            return_value=(compact_handoff, handoff),
        ) as exchange,
        mock.patch(
            "core.mastrao_host_handoff._redeem",
            return_value=(grant, "retry.grant.signature"),
        ) as redeem,
        mock.patch(
            "core.mastrao_host_invitation.cache.set",
            side_effect=publish_after_session_save,
        ),
    ):
        first_response = _post_invitation(client, prepare=False)
        client.cookies.clear()
        client.cookies[EXCHANGE_COOKIE] = exchange_cookie
        second_response = _post_invitation(client, prepare=False)

    assert first_response.status_code == second_response.status_code == 200
    assert exchange.call_count == 2
    redeem.assert_called_once_with(compact_handoff)
    assert models.MastraoHostGrant.objects.count() == 1
    assert settings.SESSION_COOKIE_NAME in second_response.cookies
    assert "csrftoken" in second_response.cookies
    current_user = client.get("/api/v1.0/users/me/")
    assert current_user.status_code == 200
    protected_post = client.post(
        f"/api/v1.0/rooms/{binding.room.id}/update-participant/",
        data="{}",
        content_type="application/json",
        HTTP_X_CSRFTOKEN=client.cookies["csrftoken"].value,
    )
    assert protected_post.status_code == 400


@pytest.mark.django_db(transaction=True)
@override_settings(
    APPLICATION_BASE_URL=ORIGIN,
    MASTRAO_HOST_HANDOFF_ENABLED=True,
)
def test_exact_exchange_retry_refuses_a_session_rebound_to_another_context(client):
    """A cached result cannot restore a session whose host context has changed."""

    binding = _room_binding()
    compact_handoff = "drift.handoff.signature"
    grant = {
        **_grant(binding),
        "credential_digest": compact_digest(compact_handoff),
    }
    handoff = _handoff(binding, grant)
    prepared = _prepare_exchange(client)
    assert prepared.status_code == 200
    exchange_cookie = client.cookies[EXCHANGE_COOKIE].value
    with (
        mock.patch(
            "core.mastrao_host_invitation._exchange_invitation",
            return_value=(compact_handoff, handoff),
        ),
        mock.patch(
            "core.mastrao_host_handoff._redeem",
            return_value=(grant, "drift.grant.signature"),
        ) as redeem,
    ):
        assert _post_invitation(client, prepare=False).status_code == 200
        session = client.session
        session[SESSION_PLATFORM_REF_KEY] = "hostsession_" + "f" * 32
        session.save()
        client.cookies.clear()
        client.cookies[EXCHANGE_COOKIE] = exchange_cookie
        retry = _post_invitation(client, prepare=False)

    assert retry.status_code == 404
    redeem.assert_called_once_with(compact_handoff)


def test_identical_exchange_retries_have_one_distributed_owner():
    """Concurrent retries cannot both consume the same short handoff."""

    owner_entered = threading.Event()
    release_owner = threading.Event()

    def attempt():
        with _exchange_claim("hostsession_" + "4" * 32, "same.handoff") as owner:
            if owner:
                owner_entered.set()
                assert release_owner.wait(timeout=2)
            return owner

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(attempt)
        assert owner_entered.wait(timeout=2)
        second = executor.submit(attempt)
        assert second.result(timeout=2) is False
        release_owner.set()
        assert first.result(timeout=2) is True


@pytest.mark.django_db(transaction=True)
@override_settings(
    APPLICATION_BASE_URL=ORIGIN,
    MASTRAO_HOST_HANDOFF_ENABLED=True,
)
def test_core_terminal_host_invitation_refusal_is_opaque(client):
    """Every terminal Core policy decision is the same non-revealing 404 to the browser."""

    with mock.patch(
        "core.mastrao_host_invitation._exchange_invitation",
        side_effect=HostHandoffRefused(status=404),
    ) as exchange:
        response = _post_invitation(client)

    assert response.status_code == 404
    assert response.json() == {"message": "Not found"}
    assert response["Cache-Control"] == "private, no-store"
    exchange.assert_called_once()
    assert models.MastraoHostGrant.objects.count() == 0


@pytest.mark.django_db(transaction=True)
@override_settings(
    APPLICATION_BASE_URL=ORIGIN,
    MASTRAO_HOST_HANDOFF_ENABLED=True,
)
def test_meet_refuses_a_short_handoff_after_local_room_closure(client):
    """A stale Core response cannot bypass Meet's local closing fence."""

    binding = _room_binding()
    binding.closing_at = timezone.now()
    binding.save(update_fields=["closing_at", "updated_at"])
    with (
        mock.patch(
            "core.mastrao_host_invitation._exchange_invitation",
            return_value=(
                "stale.handoff.signature",
                _handoff(binding, _grant(binding)),
            ),
        ),
        mock.patch(
            "core.mastrao_host_handoff._redeem",
            return_value=(_grant(binding), "stale.grant.signature"),
        ),
    ):
        response = _post_invitation(client)

    assert response.status_code == 404
    assert models.MastraoHostGrant.objects.count() == 0


@override_settings(
    APPLICATION_BASE_URL=ORIGIN,
    MASTRAO_HOST_HANDOFF_ENABLED=True,
)
def test_host_invitation_requires_same_origin_and_exact_input(client):
    """Cross-site and malformed submissions never reach the private Core exchange."""

    url = reverse("exchange_mastrao_host_invitation")
    with mock.patch("core.mastrao_host_invitation._exchange_invitation") as exchange:
        wrong_origin = client.post(
            url,
            data=json.dumps(
                {
                    "host_invitation": HOST_INVITATION,
                    "exchange_id": f"exchange_{'0' * 32}",
                }
            ),
            content_type="application/json",
            HTTP_ORIGIN="https://attacker.test",
            HTTP_SEC_FETCH_SITE="cross-site",
        )
        malformed = client.post(
            url,
            data=json.dumps(
                {
                    "host_invitation": "short",
                    "exchange_id": "invalid",
                }
            ),
            content_type="application/json",
            HTTP_ORIGIN=ORIGIN,
            HTTP_SEC_FETCH_SITE="same-origin",
        )

    assert wrong_origin.status_code == malformed.status_code == 404
    exchange.assert_not_called()


@override_settings(
    APPLICATION_BASE_URL=ORIGIN,
    MASTRAO_HOST_HANDOFF_ENABLED=True,
)
def test_host_invitation_exchange_requires_a_prepared_session(client):
    """Core is never called before the browser has received its stable session."""

    with mock.patch("core.mastrao_host_invitation._exchange_invitation") as exchange:
        response = client.post(
            reverse("exchange_mastrao_host_invitation"),
            data=json.dumps(
                {
                    "host_invitation": HOST_INVITATION,
                    "exchange_id": f"exchange_{'0' * 32}",
                }
            ),
            content_type="application/json",
            HTTP_ORIGIN=ORIGIN,
            HTTP_SEC_FETCH_SITE="same-origin",
        )

    assert response.status_code == 404
    exchange.assert_not_called()


@override_settings(
    APPLICATION_BASE_URL=ORIGIN,
    MASTRAO_HOST_HANDOFF_ENABLED=True,
)
def test_host_invitation_preparation_is_stateless(client):
    """Issuing an exchange cookie does not allocate a public server session."""

    response = _prepare_exchange(client)

    assert response.status_code == 200
    assert EXCHANGE_COOKIE in response.cookies
    assert settings.SESSION_COOKIE_NAME not in response.cookies


@override_settings(MASTRAO_HOST_HANDOFF_GLOBAL_ATTEMPTS_PER_MINUTE=3)
def test_host_invitation_attempt_limits_cover_scoped_and_global_budgets():
    """Both pre-Core budgets fail closed before private work can start."""

    with (
        mock.patch(
            "core.mastrao_host_invitation._increment_attempt_counter",
            return_value=6,
        ),
        pytest.raises(HostHandoffRefused) as scoped,
    ):
        _admit_exchange_attempt(HOST_INVITATION, f"hostsession_{'1' * 32}")
    assert scoped.value.status == 404

    with (
        mock.patch(
            "core.mastrao_host_invitation._increment_attempt_counter",
            side_effect=[1, 4],
        ),
        pytest.raises(HostHandoffRefused) as global_budget,
    ):
        _admit_exchange_attempt(HOST_INVITATION, f"hostsession_{'1' * 32}")
    assert global_budget.value.status == 503


@pytest.mark.parametrize("status", [404, 503])
@override_settings(
    APPLICATION_BASE_URL=ORIGIN,
    MASTRAO_HOST_HANDOFF_ENABLED=True,
)
def test_host_invitation_budget_refusal_never_calls_core(client, status):
    """Every admission-budget refusal happens before the private Core request."""

    prepared = _prepare_exchange(client)
    assert prepared.status_code == 200
    with (
        mock.patch(
            "core.mastrao_host_invitation._admit_exchange_attempt",
            side_effect=HostHandoffRefused(status=status),
        ),
        mock.patch("core.mastrao_host_invitation._exchange_invitation") as exchange,
    ):
        response = _post_invitation(client, prepare=False)

    assert response.status_code == status
    exchange.assert_not_called()


@override_settings(
    APPLICATION_BASE_URL=ORIGIN,
    MASTRAO_HOST_HANDOFF_ENABLED=True,
)
def test_host_invitation_full_exchange_slots_refuse_before_core(client):
    """A saturated worker refuses immediately without calling Core."""

    prepared = _prepare_exchange(client)
    assert prepared.status_code == 200
    with (
        mock.patch(
            "core.mastrao_host_invitation._EXCHANGE_SLOTS.acquire",
            return_value=False,
        ),
        mock.patch("core.mastrao_host_invitation._exchange_invitation") as exchange,
    ):
        response = _post_invitation(client, prepare=False)

    assert response.status_code == 503
    exchange.assert_not_called()


@override_settings(
    MASTRAO_CORE_HOST_INVITATION_EXCHANGE_ENDPOINT=(
        "http://cabinet-core/internal/v1/application-meetings/host-invitations/exchange"
    )
)
def test_host_invitation_exchange_requests_one_short_handoff():
    """Meet sends only the bearer and retry identity, then verifies Core's handoff."""

    with (
        mock.patch(
            "core.mastrao_host_invitation.post_core_json",
            return_value={
                "version": 1,
                "meeting_ref": "meeting_0123456789abcdef",
                "handoff_ref": "handoff_0123456789abcdef",
                "expires_at": 1_800_000_000,
                "host_handoff": "short.handoff.signature",
            },
        ) as post,
        mock.patch(
            "core.mastrao_host_invitation.verify_host_handoff",
            return_value={
                "meeting_ref": "meeting_0123456789abcdef",
                "handoff_ref": "handoff_0123456789abcdef",
                "platform_session_ref": f"hostsession_{'1' * 32}",
                "grant_expires_at": 1_800_014_280,
                "expires_at": 1_800_000_000,
            },
        ) as verify,
    ):
        result, claims = _exchange_invitation(
            HOST_INVITATION,
            f"exchange_{'0' * 32}",
            f"hostsession_{'1' * 32}",
            1_800_014_280,
        )

    assert result == "short.handoff.signature"
    assert claims["platform_session_ref"] == f"hostsession_{'1' * 32}"
    post.assert_called_once_with(
        endpoint=(
            "http://cabinet-core/internal/v1/application-meetings/"
            "host-invitations/exchange"
        ),
        expected_path=("/internal/v1/application-meetings/host-invitations/exchange"),
        body={
            "credential": HOST_INVITATION,
            "exchange_id": f"exchange_{'0' * 32}",
            "session_ref": f"hostsession_{'1' * 32}",
            "session_expires_at": 1_800_014_280,
        },
        timeout=5.0,
        refusal=HostHandoffRefused,
        expected_fields={
            "version",
            "meeting_ref",
            "handoff_ref",
            "expires_at",
            "host_handoff",
        },
    )
    verify.assert_called_once_with("short.handoff.signature")


@pytest.mark.parametrize(
    ("claim", "invalid_value"),
    [
        ("platform_session_ref", f"hostsession_{'2' * 32}"),
        ("grant_expires_at", 1_800_014_281),
    ],
)
@override_settings(
    MASTRAO_CORE_HOST_INVITATION_EXCHANGE_ENDPOINT=(
        "http://cabinet-core/internal/v1/application-meetings/host-invitations/exchange"
    )
)
def test_host_invitation_exchange_rejects_unbound_handoff(claim, invalid_value):
    """A signed handoff must remain bound to the requested browser session."""

    handoff = {
        "meeting_ref": "meeting_0123456789abcdef",
        "handoff_ref": "handoff_0123456789abcdef",
        "platform_session_ref": f"hostsession_{'1' * 32}",
        "grant_expires_at": 1_800_014_280,
        "expires_at": 1_800_000_000,
    }
    handoff[claim] = invalid_value
    with (
        mock.patch(
            "core.mastrao_host_invitation.post_core_json",
            return_value={
                "version": 1,
                "meeting_ref": "meeting_0123456789abcdef",
                "handoff_ref": "handoff_0123456789abcdef",
                "expires_at": 1_800_000_000,
                "host_handoff": "short.handoff.signature",
            },
        ),
        mock.patch(
            "core.mastrao_host_invitation.verify_host_handoff",
            return_value=handoff,
        ),
        pytest.raises(HostHandoffRefused),
    ):
        _exchange_invitation(
            HOST_INVITATION,
            f"exchange_{'0' * 32}",
            f"hostsession_{'1' * 32}",
            1_800_014_280,
        )
