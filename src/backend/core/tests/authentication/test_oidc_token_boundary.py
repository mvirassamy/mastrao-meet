"""Security tests for the Meet OIDC token boundary."""

# Pytest fixtures intentionally use their registered names, and the PKCE
# regression test sets La Suite's private token cache to exercise its public
# authenticate method without a provider call.
# pylint: disable=redefined-outer-name,protected-access

import time
from unittest import mock

from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ImproperlyConfigured, SuspiciousOperation
from django.http import HttpResponseRedirect
from django.test import RequestFactory

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from lasuite.oidc_login.views import OIDCAuthenticationCallbackView

from core.authentication.backends import OIDCAuthenticationBackend

ISSUER = "https://accounts.mastrao.test/api/auth"
CLIENT_ID = "meet"
NONCE = "expected-nonce"


@pytest.fixture(scope="module")
def signing_key():
    """Return one ephemeral signing key for boundary tests."""
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def backend(settings, signing_key, monkeypatch):
    """Build the real Meet backend with a synthetic trusted signing key."""
    settings.OIDC_OP_URL = ISSUER
    settings.OIDC_RP_CLIENT_ID = CLIENT_ID
    settings.OIDC_RP_SIGN_ALGO = "RS256"
    settings.OIDC_OP_JWKS_ENDPOINT = f"{ISSUER}/jwks"
    settings.OIDC_OP_TOKEN_ENDPOINT = f"{ISSUER}/token"
    settings.OIDC_OP_USER_ENDPOINT = f"{ISSUER}/userinfo"
    settings.OIDC_USE_NONCE = True

    instance = OIDCAuthenticationBackend()
    monkeypatch.setattr(
        instance,
        "retrieve_matching_jwk",
        lambda _token: signing_key.public_key(),
    )
    return instance


def make_token(signing_key, **overrides):
    """Sign a minimal valid Meet ID token, allowing focused claim changes."""
    now = int(time.time())
    claims = {
        "iss": ISSUER,
        "sub": "account-123",
        "aud": CLIENT_ID,
        "exp": now + 300,
        "iat": now,
        "nonce": NONCE,
    }
    claims.update(overrides)
    return jwt.encode(claims, signing_key, algorithm="RS256", headers={"kid": "s0"})


def test_accepts_valid_meet_id_token(backend, signing_key):
    """The configured issuer, Meet audience and nonce are accepted."""
    payload = backend.verify_token(make_token(signing_key), nonce=NONCE)

    assert payload["iss"] == ISSUER
    assert payload["aud"] == CLIENT_ID
    assert payload["sub"] == "account-123"


@pytest.mark.parametrize(
    "claims",
    [
        {"iss": "https://another-issuer.test"},
        {"aud": "another-oauth-client"},
        {"iss": "https://convex.test", "aud": "convex"},
    ],
    ids=["wrong-issuer", "other-client", "convex-jwt"],
)
def test_rejects_token_outside_meet_trust_context(backend, signing_key, claims):
    """A valid signature cannot cross issuer or client boundaries."""
    with pytest.raises(SuspiciousOperation):
        backend.verify_token(make_token(signing_key, **claims), nonce=NONCE)


def test_rejects_expired_token(backend, signing_key):
    """Expired ID tokens fail closed at the Meet boundary."""
    with pytest.raises(SuspiciousOperation):
        backend.verify_token(
            make_token(signing_key, exp=int(time.time()) - 1), nonce=NONCE
        )


def test_rejects_missing_required_expiration(backend, signing_key):
    """An ID token must explicitly carry its expiry."""
    token = make_token(signing_key)
    claims = jwt.decode(token, options={"verify_signature": False})
    del claims["exp"]
    token = jwt.encode(claims, signing_key, algorithm="RS256", headers={"kid": "s0"})

    with pytest.raises(SuspiciousOperation):
        backend.verify_token(token, nonce=NONCE)


def test_refuses_id_token_validation_without_configured_issuer(
    backend, signing_key, settings
):
    """Strict validation cannot silently continue without an issuer."""
    settings.OIDC_OP_URL = None

    with pytest.raises(ImproperlyConfigured, match="OIDC_OP_URL"):
        backend.verify_token(make_token(signing_key), nonce=NONCE)


def test_rejects_wrong_nonce(backend, signing_key):
    """The inherited callback nonce check remains enforced."""
    with pytest.raises(SuspiciousOperation, match="Nonce"):
        backend.verify_token(make_token(signing_key), nonce="wrong-nonce")


def test_rejects_missing_expected_and_token_nonce(backend, signing_key):
    """Two missing nonce values never constitute a valid nonce check."""
    token = make_token(signing_key)
    claims = jwt.decode(token, options={"verify_signature": False})
    del claims["nonce"]
    token = jwt.encode(claims, signing_key, algorithm="RS256", headers={"kid": "s0"})

    with pytest.raises(SuspiciousOperation, match="Nonce"):
        backend.verify_token(token, nonce=None)


def test_rejects_missing_token_nonce_with_expected_nonce(backend, signing_key):
    """An expected nonce must be present in the ID token."""
    token = make_token(signing_key)
    claims = jwt.decode(token, options={"verify_signature": False})
    del claims["nonce"]
    token = jwt.encode(claims, signing_key, algorithm="RS256", headers={"kid": "s0"})

    with pytest.raises(SuspiciousOperation, match="Nonce"):
        backend.verify_token(token, nonce=NONCE)


def test_rejects_none_expected_nonce_with_token_nonce(backend, signing_key):
    """An incomplete callback context cannot validate an ID token nonce."""
    with pytest.raises(SuspiciousOperation, match="Nonce"):
        backend.verify_token(make_token(signing_key), nonce=None)


@pytest.mark.parametrize(
    "token",
    [
        "not-a-jwt",
        "eyJhbGciOiJSUzI1NiIsImtpZCI6InMwIn0.not-json.signature",
    ],
    ids=["malformed-header", "malformed-payload"],
)
def test_malformed_token_fails_before_jwks_lookup(backend, monkeypatch, token):
    """Malformed compact JWT input produces a bounded refusal before key lookup."""
    retrieve_matching_jwk = mock.Mock()
    monkeypatch.setattr(backend, "retrieve_matching_jwk", retrieve_matching_jwk)

    with pytest.raises(SuspiciousOperation, match="algorithm"):
        backend.verify_token(token, nonce=NONCE)

    retrieve_matching_jwk.assert_not_called()


def test_rejects_wrong_signing_algorithm(backend, monkeypatch):
    """The token algorithm must equal the configured algorithm."""
    token = jwt.encode(
        {
            "iss": ISSUER,
            "sub": "account-123",
            "aud": CLIENT_ID,
            "exp": int(time.time()) + 300,
            "iat": int(time.time()),
            "nonce": NONCE,
        },
        "test-secret-padded-to-thirty-two-bytes",
        algorithm="HS256",
        headers={"kid": "s0"},
    )
    monkeypatch.setattr(
        backend,
        "retrieve_matching_jwk",
        lambda _token: "test-secret-padded-to-thirty-two-bytes",
    )

    with pytest.raises(SuspiciousOperation, match="algorithm"):
        backend.verify_token(token, nonce=NONCE)


@pytest.mark.parametrize("azp", [None, "another-oauth-client"])
def test_rejects_multi_audience_without_exact_authorized_party(
    backend, signing_key, azp
):
    """A multi-audience token must explicitly authorize the Meet client."""
    claims = {"aud": [CLIENT_ID, "another-oauth-client"]}
    if azp is not None:
        claims["azp"] = azp

    with pytest.raises(SuspiciousOperation):
        backend.verify_token(make_token(signing_key, **claims), nonce=NONCE)


def test_accepts_multi_audience_with_meet_as_authorized_party(backend, signing_key):
    """A multi-audience token is accepted when Meet is the authorized party."""
    payload = backend.verify_token(
        make_token(
            signing_key,
            aud=[CLIENT_ID, "another-oauth-client"],
            azp=CLIENT_ID,
        ),
        nonce=NONCE,
    )

    assert payload["azp"] == CLIENT_ID


def test_rejects_single_audience_with_another_authorized_party(backend, signing_key):
    """An explicit authorized party can never name another client."""
    with pytest.raises(SuspiciousOperation):
        backend.verify_token(
            make_token(signing_key, azp="another-oauth-client"), nonce=NONCE
        )


def test_signed_userinfo_preserves_existing_validation_contract(backend, signing_key):
    """Signed UserInfo responses remain on the inherited validation path."""
    token = jwt.encode(
        {"sub": "account-123", "email": "person@example.test"},
        signing_key,
        algorithm="RS256",
        headers={"kid": "s0"},
    )

    payload = backend.verify_token(token)

    assert payload["sub"] == "account-123"


def test_backend_sends_exact_pkce_verifier_to_token_endpoint(backend, monkeypatch):
    """The backend keeps the callback's verifier in the authorization-code exchange."""
    request = RequestFactory().get("/callback", {"code": "code", "state": "state"})
    request.session = {}
    captured_payload = {}
    authenticated_user = object()

    def get_token(payload):
        captured_payload.update(payload)
        backend._token_info = {
            "id_token": "id-token",
            "access_token": "access-token",
            "refresh_token": None,
            "sid": None,
        }
        return backend._token_info

    monkeypatch.setattr(backend, "get_token", get_token)
    monkeypatch.setattr(backend, "verify_token", lambda *_args, **_kwargs: {"sub": "1"})
    monkeypatch.setattr(
        backend,
        "get_or_create_user",
        lambda *_args, **_kwargs: authenticated_user,
    )

    user = backend.authenticate(
        request,
        nonce=NONCE,
        code_verifier="v" * 64,
    )

    assert user is authenticated_user
    assert captured_payload["code_verifier"] == "v" * 64


class CallbackSession(dict):
    """Small session double preserving the callback's consume-and-reload contract."""

    persisted = {}

    def __init__(self, session_key="callback-session"):
        super().__init__(self.persisted)
        self.session_key = session_key

    def save(self):
        """Persist the current dictionary for the callback reload."""
        self.__class__.persisted = dict(self)


def callback_request(state="known-state"):
    """Build a callback request with stored state, nonce and PKCE verifier."""
    CallbackSession.persisted = {
        "oidc_states": {
            "known-state": {
                "nonce": NONCE,
                "code_verifier": "v" * 64,
            }
        }
    }
    request = RequestFactory().get("/callback", {"code": "code", "state": state})
    request.session = CallbackSession()
    request.user = AnonymousUser()
    return request


def test_callback_consumes_state_and_forwards_nonce_and_pkce(monkeypatch):
    """The inherited callback keeps state one-time and forwards nonce and PKCE."""
    request = callback_request()
    authenticated_user = mock.Mock(is_active=True)
    authenticate = mock.Mock(return_value=authenticated_user)
    monkeypatch.setattr("mozilla_django_oidc.views.auth.authenticate", authenticate)
    monkeypatch.setattr(
        OIDCAuthenticationCallbackView,
        "login_success",
        lambda _self: HttpResponseRedirect("/success"),
    )

    response = OIDCAuthenticationCallbackView.as_view()(request)

    assert response.status_code == 302
    assert "known-state" not in CallbackSession.persisted["oidc_states"]
    authenticate.assert_called_once()
    assert authenticate.call_args.kwargs["nonce"] == NONCE
    assert authenticate.call_args.kwargs["code_verifier"] == "v" * 64


def test_callback_rejects_unknown_or_replayed_state():
    """Unknown state and a second use of consumed state both fail closed."""
    with pytest.raises(SuspiciousOperation, match="state not found"):
        OIDCAuthenticationCallbackView.as_view()(callback_request("unknown-state"))

    request = callback_request()
    del request.session["oidc_states"]["known-state"]
    request.session.save()
    with pytest.raises(SuspiciousOperation, match="state not found"):
        OIDCAuthenticationCallbackView.as_view()(request)
