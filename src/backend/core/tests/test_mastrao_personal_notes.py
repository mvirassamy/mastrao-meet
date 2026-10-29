"""Notes bridge proofs with real session projections and Ed25519 credentials."""

import base64
import hashlib
import json
import time
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

from django.conf import settings as django_settings
from django.test import Client, RequestFactory
from django.utils import timezone

import pytest
import requests
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core import models
from core.factories import RoomFactory, UserFactory
from core.mastrao_guest_grant import (
    SESSION_COMPACT_GRANT_KEY,
    SESSION_GRANT_REF_KEY,
    SESSION_NONCE_KEY,
)
from core.mastrao_host_grant import (
    SESSION_COMPACT_GRANTS_KEY,
    SESSION_OIDC_SUBJECT_KEY,
    SESSION_PLATFORM_REF_KEY,
)
from core.mastrao_host_grant import (
    SESSION_NONCE_KEY as HOST_NONCE_KEY,
)
from core.mastrao_identity import mastrao_host_subject
from core.mastrao_personal_notes import (
    MAX_RESPONSE_BYTES,
    MAX_WRITE_BODY_BYTES,
    room_personal_note,
)
from core.mastrao_room_contract import _canonical_json

pytestmark = pytest.mark.django_db

CORE = "http://cabinet-core:3000/internal/v1/meetings/personal-notes"
TOKEN = "test-oidc-access-token-123456"
NONCE = "notes-session-nonce-" * 3
EMPTY_NOTE = {
    "id": None,
    "content": "",
    "revision": 0,
    "created_at": None,
    "updated_at": None,
}
SAVED_NOTE = {
    "id": "note_0123456789abcdef",
    "content": "Private note 📝\nSecond paragraph",
    "revision": 1,
    "created_at": 1_800_000_000,
    "updated_at": 1_800_000_000,
}


@pytest.fixture(autouse=True)
def notes_settings(settings):
    """Keep tests independent from recording, Redis, and upstream services."""

    settings.CACHES = {
        "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}
    }
    settings.ALLOWED_HOSTS = ["testserver"]
    settings.MASTRAO_MEETING_RECORDING_ENABLED = False
    settings.MASTRAO_CORE_PERSONAL_NOTES_READ_ENDPOINT = f"{CORE}/read"
    settings.MASTRAO_CORE_PERSONAL_NOTES_WRITE_ENDPOINT = f"{CORE}/write"
    settings.MASTRAO_CORE_PERSONAL_NOTES_TIMEOUT_SECONDS = 5
    settings.MASTRAO_PLATFORM_API_BASE_URL = "https://platform.example.com"


def _b64(value):
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


@pytest.fixture(name="signing_key")
def notes_signing_key(settings):
    """Use the existing production verification functions with a real key."""

    key = Ed25519PrivateKey.generate()
    settings.MASTRAO_ROOM_EFFECT_KEY_ID = "notes-test-key"
    settings.MASTRAO_ROOM_EFFECT_ISSUER = "cabinet-core-local"
    settings.MASTRAO_ROOM_EFFECT_AUDIENCE = "mastrao-meet-local"
    settings.MASTRAO_ROOM_EFFECT_PUBLIC_JWK = json.dumps(
        {
            "kty": "OKP",
            "crv": "Ed25519",
            "x": _b64(key.public_key().public_bytes_raw()),
        }
    )
    return key


@pytest.fixture(name="binding")
def room_binding():
    """A technical binding has no recording policy or content storage."""

    return models.MastraoRoomBinding.objects.create(
        effect_key="effect_0123456789abcdef",
        arguments_digest="a" * 64,
        meeting_ref="meeting_0123456789abcdef",
        room_ref="room_0123456789abcdef",
        owner_ref="owner_0123456789abcdef",
        room=RoomFactory(slug="room_0123456789abcdef"),
        owner=UserFactory(),
        provider_binding_digest="b" * 64,
    )


def _sign(key, claims, kind):
    header = {
        "alg": "EdDSA",
        "kid": "notes-test-key",
        "typ": f"mastrao-meeting-{kind}-grant+jws",
    }
    parts = [_b64(_canonical_json(value)) for value in (header, claims)]
    message = ".".join(parts)
    return f"{message}.{_b64(key.sign(message.encode()))}"


def _save_session(client, values):
    session = client.session
    session.update(values)
    session.save()
    client.cookies[django_settings.SESSION_COOKIE_NAME] = session.session_key


def _participant(client, binding, signing_key, kind="guest", suffix="0123456789abcdef"):
    now = int(time.time())
    claims = {
        "version": 1,
        "type": f"mastrao.core-meeting-{kind}-grant",
        "issuer": "cabinet-core-local",
        "audience": "mastrao-meet-local",
        "purpose": "guest_lobby",
        "grant_ref": f"grant_{suffix}",
        "organization_external_id": "organization_0123456789abcdef",
        "meeting_ref": binding.meeting_ref,
        "room_ref": binding.room_ref,
        "provider_binding_digest": binding.provider_binding_digest,
        "redemption_id": f"redemption_{suffix}",
        "credential_digest": "c" * 64,
        "issued_at": now,
        "expires_at": now + 3600,
    }
    if kind == "host":
        claims.update(
            purpose="media_host",
            host_ref=f"host_{suffix}",
            handoff_ref=f"handoff_{suffix}",
            platform_session_ref=f"platformsession_{suffix}",
        )
    else:
        claims.update(
            guest_ref=f"guest_{suffix}", invitation_ref=f"invitation_{suffix}"
        )
    compact = _sign(signing_key, claims, kind)
    fields = {
        name: claims[name]
        for name in (
            "grant_ref",
            "credential_digest",
            "meeting_ref",
            "room_ref",
            "provider_binding_digest",
        )
    }
    fields.update(
        grant_digest=hashlib.sha256(compact.encode()).hexdigest(),
        session_nonce_digest=hashlib.sha256(NONCE.encode()).hexdigest(),
        issued_at=datetime.fromtimestamp(now, UTC),
        expires_at=datetime.fromtimestamp(now + 3600, UTC),
        room_binding=binding,
    )
    if kind == "host":
        user = UserFactory()
        identity = models.MastraoHostIdentity.objects.create(
            host_ref=claims["host_ref"], user=user
        )
        grant = models.MastraoHostGrant.objects.create(
            **fields,
            identity=identity,
            handoff_ref=claims["handoff_ref"],
            platform_session_ref=claims["platform_session_ref"],
        )
        client.force_login(user)
        values = {
            HOST_NONCE_KEY: NONCE,
            SESSION_PLATFORM_REF_KEY: claims["platform_session_ref"],
            SESSION_COMPACT_GRANTS_KEY: {grant.grant_ref: compact},
            SESSION_OIDC_SUBJECT_KEY: str(user.sub),
            "oidc_access_token": TOKEN,
        }
    else:
        grant = models.MastraoGuestGrant.objects.create(
            **fields,
            guest_ref=claims["guest_ref"],
            invitation_ref=claims["invitation_ref"],
            redemption_id=claims["redemption_id"],
            organization_external_id=claims["organization_external_id"],
        )
        values = {
            SESSION_NONCE_KEY: NONCE,
            SESSION_GRANT_REF_KEY: grant.grant_ref,
            SESSION_COMPACT_GRANT_KEY: compact,
        }
    _save_session(client, values)
    return grant, compact


def _room_url(binding):
    return f"/api/v1.0/rooms/{binding.room_ref}/personal-note/"


def _history_url(binding):
    return f"/api/v1.0/meetings/history/{binding.meeting_ref}/personal-note/"


def _upstream(body, status=200):
    response = MagicMock()
    payload = json.dumps(body, ensure_ascii=False).encode()
    response.status_code = status
    response.headers = {"content-length": str(len(payload))}
    response.iter_content.return_value = [payload]
    return response


@pytest.fixture(name="core_transport")
def notes_core_transport():
    """Capture Core requests without contacting an upstream service."""

    session = MagicMock()
    session.__enter__.return_value = session
    session.post.return_value = _upstream(EMPTY_NOTE)
    with patch("core.mastrao_core_http.requests.Session", return_value=session):
        yield session


@pytest.mark.parametrize("kind", ["host", "guest"])
@pytest.mark.parametrize("closed", [False, True])
def test_room_get_put_use_only_exact_authority(
    binding, signing_key, core_transport, kind, closed
):
    """Read and write still work after a durable room closure, without recording."""

    client = Client(enforce_csrf_checks=True)
    grant, compact = _participant(client, binding, signing_key, kind)
    if closed:
        models.MastraoRoomClosure.objects.create(
            room_binding=binding,
            organization_external_id="organization_0123456789abcdef",
            meeting_ref=binding.meeting_ref,
            room_ref=binding.room_ref,
            provider_binding_digest=binding.provider_binding_digest,
            close_ref="close_0123456789abcdef",
            effect_key="closeeffect_0123456789abcdef",
            arguments_digest="d" * 64,
            requested_at=timezone.now(),
        )
        binding.closing_at = timezone.now()
        binding.save(update_fields=["closing_at"])
    response = client.get(_room_url(binding))
    assert response.status_code == 200
    assert response.json() == EMPTY_NOTE
    assert response.headers["Cache-Control"] == "private, no-store"
    assert "csrftoken" in client.cookies
    core_transport.post.assert_called_once_with(
        f"{CORE}/read",
        json={
            "participant_grant": compact,
            "participant_session_digest": grant.session_nonce_digest,
        },
        timeout=5,
        allow_redirects=False,
        stream=True,
    )
    assert core_transport.trust_env is False
    core_transport.post.return_value = _upstream(SAVED_NOTE)
    body = {"content": SAVED_NOTE["content"], "expected_revision": 0}
    response = client.put(
        _room_url(binding),
        data=json.dumps(body),
        content_type="application/json",
        HTTP_X_CSRFTOKEN=client.cookies["csrftoken"].value,
    )
    assert response.status_code == 200
    assert response.json() == SAVED_NOTE
    assert core_transport.post.call_args.args == (f"{CORE}/write",)
    assert core_transport.post.call_args.kwargs["json"] == {
        "participant_grant": compact,
        "participant_session_digest": grant.session_nonce_digest,
        **body,
    }
    assert compact not in response.content.decode()
    assert not models.MastraoRecordingBinding.objects.exists()


@pytest.mark.parametrize("kind", ["host", "guest"])
@pytest.mark.parametrize("mutation", ["nonce", "compact", "binding", "expiry", "owner"])
def test_room_rejects_unbound_or_tampered_authority(
    binding, signing_key, core_transport, kind, mutation
):
    """Neither a stored bearer nor a local ACL can bypass author/room binding."""

    client = Client()
    grant, _compact = _participant(client, binding, signing_key, kind)
    if mutation == "nonce":
        key = HOST_NONCE_KEY if kind == "host" else SESSION_NONCE_KEY
        _save_session(client, {key: "different-nonce-" * 3})
    elif mutation == "compact":
        if kind == "host":
            values = {
                SESSION_COMPACT_GRANTS_KEY: {grant.grant_ref: "forged.bearer.sig"}
            }
        else:
            values = {SESSION_COMPACT_GRANT_KEY: "forged.bearer.sig"}
        _save_session(client, values)
    elif mutation == "binding":
        binding.provider_binding_digest = "e" * 64
        binding.save(update_fields=["provider_binding_digest"])
    elif mutation == "expiry":
        grant.expires_at = grant.issued_at.replace(microsecond=1)
        grant.save(update_fields=["expires_at"])
    elif kind == "host":
        grant.identity.host_ref = "host_someoneelsesref"
        grant.identity.save(update_fields=["host_ref"])
    else:
        grant.guest_ref = "guest_someoneelsesref"
        grant.save(update_fields=["guest_ref"])
    assert client.get(_room_url(binding)).status_code == 404
    core_transport.post.assert_not_called()


def test_room_never_uses_other_participant_or_technical_owner(
    binding, signing_key, core_transport
):
    """A technical room owner cannot read notes without participant authority."""

    client = Client()
    _participant(Client(), binding, signing_key)
    client.force_login(binding.owner)
    assert client.get(_room_url(binding)).status_code == 404
    assert Client().get(_room_url(binding)).status_code == 404
    core_transport.post.assert_not_called()


def test_temporary_host_can_read_after_close_without_oidc(
    binding, signing_key, core_transport
):
    """The handoff-only host principal also keeps its own valid notes authority."""

    client = Client()
    grant, _compact = _participant(client, binding, signing_key, "host")
    user = grant.identity.user
    user.sub = mastrao_host_subject(grant.identity.host_ref)
    user.save(update_fields=["sub"])
    session = client.session
    session.pop("oidc_access_token")
    session.save()
    binding.closing_at = timezone.now()
    binding.save(update_fields=["closing_at"])
    assert client.get(_room_url(binding)).status_code == 200
    core_transport.post.assert_called_once()


@pytest.mark.parametrize("kind", ["host", "guest"])
def test_matching_digest_cannot_replace_signature_verification(
    binding, signing_key, core_transport, kind
):
    """A matching stored digest cannot authorize a forged cryptographic signature."""

    client = Client()
    grant, compact = _participant(client, binding, signing_key, kind)
    header, payload, _signature = compact.split(".")
    compact = f"{header}.{payload}.{_b64(bytes(64))}"
    grant.grant_digest = hashlib.sha256(compact.encode()).hexdigest()
    grant.save(update_fields=["grant_digest"])
    if kind == "host":
        values = {SESSION_COMPACT_GRANTS_KEY: {grant.grant_ref: compact}}
    else:
        values = {SESSION_COMPACT_GRANT_KEY: compact}
    _save_session(client, values)
    assert client.get(_room_url(binding)).status_code == 404
    core_transport.post.assert_not_called()


def test_guest_history_never_shares_another_guests_document(
    binding, signing_key, core_transport
):
    """Two session guests of the same room forward distinct owner capabilities."""

    first = Client()
    second = Client()
    first_grant, first_compact = _participant(first, binding, signing_key)
    _second_grant, second_compact = _participant(
        second, binding, signing_key, suffix="fedcba9876543210"
    )
    assert first.get(_history_url(binding)).status_code == 200
    assert (
        core_transport.post.call_args.kwargs["json"]["participant_grant"]
        == first_compact
    )
    assert second.get(_history_url(binding)).status_code == 200
    assert (
        core_transport.post.call_args.kwargs["json"]["participant_grant"]
        == second_compact
    )
    _save_session(second, {SESSION_COMPACT_GRANT_KEY: first_compact})
    assert second.get(_history_url(binding)).status_code == 404
    assert core_transport.post.call_count == 2
    _save_session(second, {SESSION_GRANT_REF_KEY: first_grant.grant_ref})
    _save_session(second, {SESSION_NONCE_KEY: "other-browser-session-" * 3})
    assert second.get(_history_url(binding)).status_code == 404
    assert core_transport.post.call_count == 2


@pytest.mark.parametrize(
    "body",
    [
        {},
        [],
        {"content": "text"},
        {"content": 1, "expected_revision": 0},
        {"content": "text", "expected_revision": True},
        {"content": "text", "expected_revision": -1},
        {"content": "text", "expected_revision": 1.0},
        {"content": "text", "expected_revision": 9_007_199_254_740_992},
        {"content": "text", "expected_revision": 0, "owner_ref": "someone_else"},
        {"content": "x" * 100_001, "expected_revision": 0},
        {"content": "\ud800", "expected_revision": 0},
        {"content": "not\x00storable", "expected_revision": 0},
    ],
)
def test_invalid_write_is_rejected_before_core(binding, core_transport, body):
    """Reject invalid note fields before forwarding to Core."""

    response = Client().put(
        _room_url(binding), data=json.dumps(body), content_type="application/json"
    )
    assert response.status_code == 422
    core_transport.post.assert_not_called()


@pytest.mark.parametrize(
    "body,content_type",
    [
        (
            b'{"content":"one","content":"two","expected_revision":0}',
            "application/json",
        ),
        (b'{"content":"\xff","expected_revision":0}', "application/json"),
        (b'{"content":"text","expected_revision":NaN}', "application/json"),
        (b"[" * 1500, "application/json"),
        (b"{" + b" " * MAX_WRITE_BODY_BYTES, "application/json"),
        (b'{"content":"text","expected_revision":0}', "text/plain"),
    ],
    ids=[
        "duplicate-json-key",
        "invalid-utf8",
        "non-finite-revision",
        "excessive-json-depth",
        "oversized-body",
        "unsupported-content-type",
    ],
)
def test_malformed_or_oversized_envelope_is_rejected(
    binding, core_transport, body, content_type
):
    """Reject ambiguous, malformed or oversized write envelopes."""

    response = Client().put(_room_url(binding), data=body, content_type=content_type)
    assert response.status_code == 422
    core_transport.post.assert_not_called()


def test_explicit_csrf_protection_is_present_even_without_middleware(core_transport):
    """Protect note writes even when CSRF middleware is absent."""

    request = RequestFactory().put(
        "/api/v1.0/rooms/room_0123456789abcdef/personal-note/",
        data='{"content":"private","expected_revision":0}',
        content_type="application/json",
    )
    request.session = {}
    response = room_personal_note(request, "room_0123456789abcdef")
    assert response.status_code == 403
    core_transport.post.assert_not_called()


@pytest.mark.parametrize("method", ["post", "delete", "patch", "head"])
@pytest.mark.parametrize("route", ["room", "history"])
def test_only_get_put_are_allowed(binding, core_transport, method, route):
    """Expose only reads and revision-checked writes on both routes."""

    url = _room_url(binding) if route == "room" else _history_url(binding)
    assert getattr(Client(), method)(url).status_code == 405
    core_transport.post.assert_not_called()


@pytest.mark.parametrize("character", ["📝", "\x01"])
def test_utf8_full_size_note_uses_larger_bounded_response(
    binding, signing_key, core_transport, character
):
    """Preserve full-size Unicode notes within the bounded response limit."""

    client = Client()
    _participant(client, binding, signing_key)
    note = {**SAVED_NOTE, "content": character * 100_000}
    core_transport.post.return_value = _upstream(note)
    response = client.put(
        _room_url(binding),
        data=json.dumps(
            {"content": note["content"], "expected_revision": 0}, ensure_ascii=False
        ),
        content_type="application/json",
    )
    assert response.status_code == 200
    assert response.json() == note


@pytest.mark.parametrize(
    "status,expected", [(409, 409), (422, 422), (403, 404), (500, 503)]
)
def test_core_errors_never_expose_upstream_note_or_grant(
    binding, signing_key, core_transport, status, expected
):
    """Return opaque errors without exposing note content or credentials."""

    client = Client()
    _grant, compact = _participant(client, binding, signing_key)
    upstream = _upstream({**SAVED_NOTE, "participant_grant": compact}, status)
    core_transport.post.return_value = upstream
    response = client.put(
        _room_url(binding),
        data='{"content":"private","expected_revision":0}',
        content_type="application/json",
    )
    assert response.status_code == expected
    assert set(response.json()) == {"message"}
    assert compact not in response.content.decode()
    upstream.close.assert_called_once()


@pytest.mark.parametrize(
    "failure", ["timeout", "oversized", "stream", "invalid", "fields"]
)
def test_core_temporary_failures_are_retryable_and_bounded(
    binding, signing_key, core_transport, failure
):
    """Keep transport failures retryable and enforce response limits."""

    client = Client()
    _participant(client, binding, signing_key)
    upstream = _upstream(EMPTY_NOTE)
    core_transport.post.return_value = upstream
    if failure == "timeout":
        core_transport.post.side_effect = requests.Timeout()
    elif failure == "oversized":
        upstream.headers["content-length"] = str(MAX_RESPONSE_BYTES + 1)
    elif failure == "stream":
        upstream.headers = {}
        upstream.iter_content.return_value = [b"x" * (MAX_RESPONSE_BYTES + 1)]
    elif failure == "invalid":
        upstream.iter_content.return_value = [b"not json"]
    else:
        core_transport.post.return_value = _upstream(
            {**EMPTY_NOTE, "participant_grant": "secret"}
        )
    response = client.get(_room_url(binding))
    assert response.status_code == 503
    assert set(response.json()) == {"message"}
    if failure != "timeout":
        core_transport.post.return_value.close.assert_called_once()


def test_guest_history_is_exact_session_grant_after_close(
    binding, signing_key, core_transport
):
    """Retain the exact guest author when accessing notes after closure."""

    client = Client(enforce_csrf_checks=True)
    grant, compact = _participant(client, binding, signing_key)
    binding.closing_at = timezone.now()
    binding.save(update_fields=["closing_at"])
    with patch("core.mastrao_personal_notes.request_platform") as platform:
        response = client.get(_history_url(binding))
        assert response.status_code == 200
        response = client.put(
            _history_url(binding),
            data='{"content":"","expected_revision":0}',
            content_type="application/json",
            HTTP_X_CSRFTOKEN=client.cookies["csrftoken"].value,
        )
        assert response.status_code == 200
    platform.assert_not_called()
    assert core_transport.post.call_args.kwargs["json"] == {
        "participant_grant": compact,
        "participant_session_digest": grant.session_nonce_digest,
        "content": "",
        "expected_revision": 0,
    }
    assert (
        client.get(
            _history_url(binding).replace(
                binding.meeting_ref, "meeting_other0123456789"
            )
        ).status_code
        == 401
    )
    assert Client().get(_history_url(binding)).status_code == 401


@pytest.mark.parametrize("method", ["get", "put"])
def test_history_oidc_forwards_token_and_method(binding, method):
    """Forward account history requests with server-side OIDC credentials."""

    client = Client(enforce_csrf_checks=True)
    _save_session(client, {"oidc_access_token": TOKEN})
    session = MagicMock()
    session.__enter__.return_value = session
    session.request.return_value = _upstream(SAVED_NOTE)
    body = {"content": "account note", "expected_revision": 1}
    with patch("core.mastrao_platform_facade.requests.Session", return_value=session):
        read = client.get(_history_url(binding))
        assert read.status_code == 200
        session.request.reset_mock()
        options = {}
        if method == "put":
            options = {
                "data": json.dumps(body),
                "content_type": "application/json",
                "HTTP_X_CSRFTOKEN": client.cookies["csrftoken"].value,
            }
        response = getattr(client, method)(_history_url(binding), **options)
    assert response.status_code == 200
    assert response.json() == SAVED_NOTE
    expected_options = {"json": body} if method == "put" else {}
    session.request.assert_called_once_with(
        method.upper(),
        (
            "https://platform.example.com/api/meet/meetings/history/"
            f"{binding.meeting_ref}/personal-note"
        ),
        headers={"authorization": f"Bearer {TOKEN}"},
        timeout=5,
        allow_redirects=False,
        stream=True,
        **expected_options,
    )
    assert TOKEN not in response.content.decode()


@pytest.mark.parametrize("method", ["get", "put"])
def test_signed_in_guest_history_keeps_live_author_after_close(
    binding, signing_key, core_transport, method
):
    """Preserved OIDC state cannot silently switch a guest's document owner."""

    client = Client(enforce_csrf_checks=True)
    client.force_login(UserFactory())
    _save_session(client, {"oidc_access_token": TOKEN})
    grant, compact = _participant(client, binding, signing_key)
    core_transport.post.return_value = _upstream(SAVED_NOTE)
    live = client.get(_room_url(binding))
    assert live.json() == SAVED_NOTE
    binding.closing_at = timezone.now()
    binding.save(update_fields=["closing_at"])
    options = {}
    if method == "put":
        options = {
            "data": json.dumps(
                {"content": SAVED_NOTE["content"], "expected_revision": 1}
            ),
            "content_type": "application/json",
            "HTTP_X_CSRFTOKEN": client.cookies["csrftoken"].value,
        }
    with patch("core.mastrao_personal_notes.request_platform") as platform:
        history = getattr(client, method)(_history_url(binding), **options)
    assert history.status_code == 200
    assert history.json() == live.json()
    forwarded = core_transport.post.call_args.kwargs["json"]
    assert forwarded["participant_grant"] == compact
    assert forwarded["participant_session_digest"] == grant.session_nonce_digest
    platform.assert_not_called()


@pytest.mark.parametrize("failure", ["expired", "revoked", "nonce", "compact"])
def test_matching_signed_in_guest_fails_closed_instead_of_oidc_fallback(
    binding, signing_key, core_transport, failure
):
    """An unusable matching guest must never create an account-owned replacement."""

    client = Client()
    _save_session(client, {"oidc_access_token": TOKEN})
    grant, _compact = _participant(client, binding, signing_key)
    binding.closing_at = timezone.now()
    binding.save(update_fields=["closing_at"])
    if failure == "expired":
        grant.expires_at = grant.issued_at.replace(microsecond=1)
        grant.save(update_fields=["expires_at"])
    elif failure == "revoked":
        core_transport.post.return_value = _upstream({"message": "revoked"}, 403)
    elif failure == "nonce":
        _save_session(client, {SESSION_NONCE_KEY: "another-session-nonce-" * 3})
    else:
        _save_session(client, {SESSION_COMPACT_GRANT_KEY: None})
    with patch("core.mastrao_personal_notes.request_platform") as platform:
        response = client.put(
            _history_url(binding),
            data='{"content":"private","expected_revision":1}',
            content_type="application/json",
        )
    assert response.status_code == 404
    platform.assert_not_called()


def test_history_host_authority_takes_priority_over_guest_and_oidc(
    binding, signing_key, core_transport
):
    """Keep the live host author when other session identities are present."""

    client = Client()
    _host, host_compact = _participant(client, binding, signing_key, "host")
    _participant(client, binding, signing_key, suffix="fedcba9876543210")
    binding.closing_at = timezone.now()
    binding.save(update_fields=["closing_at"])
    with patch("core.mastrao_personal_notes.request_platform") as platform:
        assert client.get(_room_url(binding)).status_code == 200
        assert client.get(_history_url(binding)).status_code == 200
    assert all(
        call.kwargs["json"]["participant_grant"] == host_compact
        for call in core_transport.post.call_args_list
    )
    assert core_transport.post.call_count == 2
    platform.assert_not_called()


def test_unrelated_guest_association_does_not_block_account_history(
    binding, signing_key, core_transport
):
    """Allow account history for meetings unrelated to the guest session."""

    client = Client()
    _participant(client, binding, signing_key)
    _save_session(client, {"oidc_access_token": TOKEN})
    with patch("core.mastrao_personal_notes.request_platform") as platform:
        platform.return_value = (EMPTY_NOTE, 200)
        response = client.get(
            _history_url(binding).replace(
                binding.meeting_ref, "meeting_other0123456789"
            )
        )
    assert response.status_code == 200
    platform.assert_called_once()
    core_transport.post.assert_not_called()


def test_history_platform_conflict_discards_note_body(binding):
    """Hide upstream note content when an account write conflicts."""

    client = Client()
    _save_session(client, {"oidc_access_token": TOKEN})
    session = MagicMock()
    session.__enter__.return_value = session
    session.request.return_value = _upstream(SAVED_NOTE, 409)
    with patch("core.mastrao_platform_facade.requests.Session", return_value=session):
        response = client.put(
            _history_url(binding),
            data='{"content":"account note","expected_revision":0}',
            content_type="application/json",
        )
    assert response.status_code == 409
    assert set(response.json()) == {"message"}


def test_history_put_rejects_csrf_and_invalid_payload_before_platform(binding):
    """Reject unsafe history writes before contacting Platform."""

    client = Client(enforce_csrf_checks=True)
    _save_session(client, {"oidc_access_token": TOKEN})
    with patch("core.mastrao_personal_notes.request_platform") as platform:
        response = client.put(
            _history_url(binding),
            data='{"content":"private","expected_revision":0}',
            content_type="application/json",
        )
        assert response.status_code == 403
        response = Client().put(
            _history_url(binding), data="{}", content_type="application/json"
        )
        assert response.status_code == 422
    platform.assert_not_called()


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://cabinet-core:3000/internal/v1/meetings/personal-notes/read",
        "http://public.example.com/internal/v1/meetings/personal-notes/read",
        f"{CORE}/write",
        f"{CORE}/read?owner=someone_else",
    ],
)
def test_invalid_core_endpoint_fails_before_network(
    binding, signing_key, core_transport, settings, endpoint
):
    """Refuse untrusted Core endpoints before making a network request."""

    client = Client()
    _participant(client, binding, signing_key)
    settings.MASTRAO_CORE_PERSONAL_NOTES_READ_ENDPOINT = endpoint
    assert client.get(_room_url(binding)).status_code == 503
    core_transport.post.assert_not_called()
