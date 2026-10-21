"""Real browser sessions, JOSE and private HTTP; Core is a synthetic peer.

Core's policy, decisions and RLS are tested in Platform. This peer checks the
wire contract without calling any provider or starting a media session.
"""

# Imported pytest fixtures are intentionally shared with existing session tests.
# pylint: disable=missing-function-docstring,redefined-outer-name,unused-import
# pylint: disable=too-many-arguments,too-many-positional-arguments,too-few-public-methods

import json
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

from django.test import Client
from django.utils import timezone

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from core import models
from core.mastrao_live_consent import NOTICE_TEXT
from core.mastrao_recording_contract import compact_digest
from core.mastrao_room_contract import _base64url_decode, _canonical_json
from core.tests.test_mastrao_media_token_binding import (
    _request,
    binding,
    guest,
    host,
    isolated_binding_settings,
)
from core.tests.test_mastrao_native_preentry import _grant_claims
from core.tests.test_mastrao_room_adapter import _b64, _jwk_pair

pytestmark = pytest.mark.django_db
NOTICE_VERSION = "live_microphone_fr_v1"
NOTICE_DIGEST = "19a76d62a39b5c4c3211d17720908bf474fe3080eec010ea0c0470db8c40d249"
OFFER_PATH = "/internal/v1/meetings/transcripts/live/consent/offer"
DECISION_PATH = "/internal/v1/meetings/transcripts/live/consent/decision"
CSRF = "c" * 32


@pytest.fixture
def trust(settings):
    core_key, core_jwk = _jwk_pair()
    _, meet_jwk = _jwk_pair()
    settings.MASTRAO_MEETING_INTEGRATION_CONFIGURED = True
    settings.MASTRAO_ROOM_EFFECT_PUBLIC_JWK = core_jwk["public"]
    settings.MASTRAO_ROOM_EFFECT_KEY_ID = "live-core-fixture"
    settings.MASTRAO_ROOM_EFFECT_ISSUER = "core-fixture"
    settings.MASTRAO_ROOM_EFFECT_AUDIENCE = "meet-fixture"
    settings.MASTRAO_RECORDING_RECEIPT_PRIVATE_JWK = meet_jwk["private"]
    settings.MASTRAO_RECORDING_RECEIPT_KEY_ID = "live-meet-fixture"
    settings.MASTRAO_RECORDING_RECEIPT_ISSUER = "meet-fixture"
    settings.MASTRAO_RECORDING_RECEIPT_AUDIENCE = "core-fixture"
    return SimpleNamespace(
        core_key=core_key, meet_public=json.loads(meet_jwk["public"])
    )


def _browser(grant, kind, trust):
    claims = _grant_claims(grant, kind)
    claims["issued_at"] = int(grant.issued_at.timestamp())
    claims["expires_at"] = int(grant.expires_at.timestamp())
    protected = _b64(
        _canonical_json(
            {
                "alg": "EdDSA",
                "kid": "live-core-fixture",
                "typ": f"mastrao-meeting-{kind}-grant+jws",
            }
        )
    )
    payload = _b64(_canonical_json(claims))
    signature = trust.core_key.sign(f"{protected}.{payload}".encode())
    compact = f"{protected}.{payload}.{_b64(signature)}"
    grant.grant_digest = compact_digest(compact)
    grant.save(update_fields=["grant_digest", "updated_at"])
    browser = Client(enforce_csrf_checks=True)
    if kind == "host":
        browser.force_login(
            grant.identity.user,
            backend="core.authentication.handoff.MastraoHostAuthenticationBackend",
        )
    session = browser.session
    if kind == "host":
        session.update(_request(grant).session)
        session["mastrao_host_compact_grants"] = {grant.grant_ref: compact}
    else:
        session.update(
            {
                "mastrao_guest_session_nonce": "guest-fixture-nonce-" * 3,
                "mastrao_guest_grant_ref": grant.grant_ref,
                "mastrao_guest_compact_grant": compact,
            }
        )
    session.save()
    browser.cookies["csrftoken"] = CSRF
    return browser, compact


def _post(browser, binding, action, body=None, **headers):
    return browser.post(
        f"/api/v1.0/rooms/{binding.room.slug}/live-consent-{action}/",
        {} if body is None else body,
        content_type="application/json",
        HTTP_X_CSRFTOKEN=CSRF,
        **headers,
    )


def _choice(offer, decision="accepted", decision_ref="live_choice_0123456789abcdef"):
    return {
        **{
            key: offer[key]
            for key in (
                "consent_ref",
                "policy_ref",
                "notice_version",
                "notice_digest",
                "retention_expires_at",
            )
        },
        "decision": decision,
        "decision_ref": decision_ref,
    }


@contextmanager
def _core_peer(settings, trust, grant, kind, *, status=200, mutate=None):  # noqa: PLR0913
    # pylint: disable=too-many-statements
    snapshot = {
        "version": 1,
        "organization_external_id": "organization_media_fixture",
        "meeting_ref": grant.meeting_ref,
        "room_ref": grant.room_ref,
        "consent_ref": "live_consent_0123456789abcdef",
        "policy_ref": "live_policy_0123456789abcdef",
        "notice_version": NOTICE_VERSION,
        "notice_digest": NOTICE_DIGEST,
        "purpose": "meeting_live_transcription",
        "scope": "live_microphone_audio_transcript",
        "retention_expires_at": int(time.time()) + 3600,
        "text": NOTICE_TEXT,
        "decision": "absent",
        "capture_authorized": False,
    }
    calls, errors, decisions = [], [], {}
    public = Ed25519PublicKey.from_public_bytes(
        _base64url_decode(trust.meet_public["x"])
    )

    class Handler(BaseHTTPRequestHandler):
        """Validate this synthetic peer's wire input without logging capabilities."""

        def log_message(self, *_args):
            pass

        def do_POST(self):  # pylint: disable=invalid-name
            try:
                length = int(self.headers["Content-Length"])
                assert 0 < length < 20_000
                body = json.loads(self.rfile.read(length))
                assert set(body) == {"request", "participant_grant"}
                header = jwt.get_unverified_header(body["request"])
                suffix = "offer" if self.path == OFFER_PATH else "decision"
                assert self.path in {OFFER_PATH, DECISION_PATH}
                assert header == {
                    "alg": "EdDSA",
                    "kid": "live-meet-fixture",
                    "typ": f"mastrao-live-transcript-consent-{suffix}+jws",
                }
                assertion = jwt.decode(body["request"], public, algorithms=["EdDSA"])
                assert (
                    assertion["type"]
                    == f"mastrao.meet-live-transcript-consent-{suffix}"
                )
                assert assertion["issuer"] == "meet-fixture"
                assert assertion["audience"] == "core-fixture"
                assert (
                    assertion["issued_at"] <= int(time.time()) < assertion["expires_at"]
                )
                assert assertion["expires_at"] - assertion["issued_at"] == 30
                assert assertion["participant_kind"] == kind
                participant_ref = (
                    grant.identity.host_ref if kind == "host" else grant.guest_ref
                )
                assert assertion["participant_ref"] == participant_ref
                assert assertion["grant_ref"] == grant.grant_ref
                assert (
                    assertion["grant_digest"]
                    == grant.grant_digest
                    == compact_digest(body["participant_grant"])
                )
                assert (
                    assertion["participant_session_digest"]
                    == grant.session_nonce_digest
                )
                assert (
                    assertion["provider_binding_digest"]
                    == grant.provider_binding_digest
                )
                calls.append(assertion)
                result, response_status = dict(snapshot), status
                if status != 200:
                    result = {
                        "code": "resource_unavailable",
                        "message": "private diagnostic never forwarded",
                    }
                elif self.path == DECISION_PATH:
                    choice = {key: assertion[key] for key in _choice(snapshot)}
                    snapshot_matches = all(
                        assertion[key] == value
                        for key, value in _choice(snapshot).items()
                        if key not in {"decision", "decision_ref"}
                    )
                    previous = decisions.get(assertion["decision_ref"])
                    if not snapshot_matches or (
                        previous is not None and previous != choice
                    ):
                        response_status, result = (
                            409,
                            {
                                "code": "resource_unavailable",
                                "message": "private conflict detail",
                            },
                        )
                    else:
                        decisions[assertion["decision_ref"]] = choice
                        snapshot["decision"] = assertion["decision"]
                        result = dict(snapshot)
                if mutate is not None and response_status == 200:
                    result = {**result, **mutate}
                data = json.dumps(result, ensure_ascii=False).encode()
                self.send_response(response_status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            except Exception as error:  # noqa: BLE001  # pylint: disable=broad-exception-caught
                errors.append(type(error).__name__)
                self.send_response(500)
                self.end_headers()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    origin = f"http://127.0.0.1:{server.server_port}"
    settings.MASTRAO_CORE_LIVE_CONSENT_OFFER_ENDPOINT = origin + OFFER_PATH
    settings.MASTRAO_CORE_LIVE_CONSENT_DECISION_ENDPOINT = origin + DECISION_PATH
    try:
        yield calls
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)
        assert not errors, errors


@pytest.mark.parametrize("kind", ["host", "guest"])
def test_browser_offer_choice_replay_and_closed_withdrawal(  # noqa: PLR0913, PLR0917
    settings, trust, binding, host, guest, kind
):
    grant = host if kind == "host" else guest
    browser, compact = _browser(grant, kind, trust)
    with _core_peer(settings, trust, grant, kind) as calls:
        response = _post(browser, binding, "offer")
        assert response.status_code == 200
        assert response["Cache-Control"] == "private, no-store"
        offer = response.json()
        choice = _choice(offer)
        first = _post(browser, binding, "decision", choice)
        replay = _post(browser, binding, "decision", choice)
        assert first.status_code == replay.status_code == 200
        assert first.json() == replay.json()
        assert first.json()["capture_authorized"] is False
        assert calls[1]["jti"] != calls[2]["jti"]
        assert calls[1]["decision_ref"] == calls[2]["decision_ref"]
        binding.closing_at = timezone.now()
        binding.save(update_fields=["closing_at", "updated_at"])
        assert _post(browser, binding, "offer").status_code == 404
        assert _post(browser, binding, "decision", choice).status_code == 404
        withdrawn = _post(
            browser,
            binding,
            "decision",
            _choice(offer, "withdrawn", "withdraw_0123456789abcdef"),
        )
        assert withdrawn.status_code == 200
        assert withdrawn.json()["decision"] == "withdrawn"
    assert compact not in str(first.json())
    assert not models.MastraoMediaTokenBinding.objects.exists()
    assert not models.MastraoNativeCaptureStart.objects.exists()
    assert not models.RoomSubtitleControl.objects.exists()


@pytest.mark.parametrize("kind", ["host", "guest"])
@pytest.mark.parametrize("action", ["offer", "decision"])
def test_guest_and_host_requests_require_csrf(  # noqa: PLR0913, PLR0917
    settings, trust, binding, host, guest, kind, action
):
    grant = host if kind == "host" else guest
    browser, _ = _browser(grant, kind, trust)
    with _core_peer(settings, trust, grant, kind) as calls:
        response = browser.post(
            f"/api/v1.0/rooms/{binding.room.slug}/live-consent-{action}/",
            {},
            content_type="application/json",
        )
        assert response.status_code == 403
        assert response["Cache-Control"] == "private, no-store"
        assert not calls


@pytest.mark.parametrize("change", ["nonce", "compact", "expired", "revoked_user"])
def test_unbound_or_expired_host_cannot_send_core_request(
    settings, trust, binding, host, change
):
    browser, _ = _browser(host, "host", trust)
    session = browser.session
    if change == "nonce":
        session["mastrao_host_session_nonce"] = "wrong-nonce-" * 4
    elif change == "compact":
        session["mastrao_host_compact_grants"] = {}
    elif change == "expired":
        host.expires_at = timezone.now() - timezone.timedelta(seconds=1)
        host.issued_at = host.expires_at - timezone.timedelta(seconds=300)
        host.save(update_fields=["issued_at", "expires_at", "updated_at"])
    else:
        host.identity.user.is_active = False
        host.identity.user.save(update_fields=["is_active", "updated_at"])
    session.save()
    with _core_peer(settings, trust, host, "host") as calls:
        response = _post(browser, binding, "offer")
        assert response.status_code == 404
        assert not calls


def test_browser_cannot_supply_room_authority_or_change_decision_id(
    settings, trust, binding, host
):
    browser, _ = _browser(host, "host", trust)
    with _core_peer(settings, trust, host, "host") as calls:
        assert (
            _post(
                browser, binding, "offer", {"meeting_ref": "meeting_other_fixture"}
            ).status_code
            == 400
        )
        offer = _post(browser, binding, "offer").json()
        assert (
            _post(
                browser,
                binding,
                "decision",
                {**_choice(offer), "organization_external_id": "other"},
            ).status_code
            == 400
        )
        assert len(calls) == 1
        assert _post(browser, binding, "decision", _choice(offer)).status_code == 200
        conflict = _post(browser, binding, "decision", _choice(offer, "refused"))
        assert conflict.status_code == 409
        assert "private" not in conflict.content.decode()
        assert conflict["Cache-Control"] == "private, no-store"


def test_changed_displayed_snapshot_stays_a_core_conflict(
    settings, trust, binding, host
):
    browser, _ = _browser(host, "host", trust)
    with _core_peer(settings, trust, host, "host") as calls:
        offer = _post(browser, binding, "offer").json()
        response = _post(
            browser,
            binding,
            "decision",
            {
                **_choice(offer),
                "retention_expires_at": offer["retention_expires_at"] + 1,
            },
        )
        assert response.status_code == 409
        assert len(calls) == 2
        assert calls[-1]["retention_expires_at"] == offer["retention_expires_at"] + 1


@pytest.mark.parametrize(
    "mutate",
    [
        {"capture_authorized": True},
        {"organization_external_id": "other_fixture"},
        {"unexpected": True},
    ],
)
def test_malformed_core_success_is_not_returned_to_browser(
    settings, trust, binding, host, mutate
):
    browser, _ = _browser(host, "host", trust)
    with _core_peer(settings, trust, host, "host", mutate=mutate):
        response = _post(browser, binding, "offer")
    assert response.status_code == 503
    assert response.json() == {"message": "Unavailable"}


def test_missing_endpoint_refuses_without_media_effect(settings, trust, binding, host):
    browser, _ = _browser(host, "host", trust)
    settings.MASTRAO_CORE_LIVE_CONSENT_OFFER_ENDPOINT = ""
    response = _post(browser, binding, "offer")
    assert response.status_code == 503
    assert response["Cache-Control"] == "private, no-store"
