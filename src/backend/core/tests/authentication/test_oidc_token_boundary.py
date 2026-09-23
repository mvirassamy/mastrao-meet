"""Security tests for the Meet OIDC token boundary."""

# Pytest fixtures intentionally use their registered names, and the
# authentication test sets La Suite's private token cache to exercise its
# public authenticate method without a provider call.
# pylint: disable=redefined-outer-name,protected-access

import base64
import json
import time
from unittest import mock

from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ImproperlyConfigured, SuspiciousOperation
from django.http import HttpResponseRedirect
from django.test import RequestFactory

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from lasuite.oidc_login.views import OIDCAuthenticationCallbackView

from core.authentication.backends import OIDCAuthenticationBackend

ISSUER = "https://accounts.mastrao.test/api/auth"
CLIENT_ID = "meet"
CLIENT_SECRET = "meet-client-secret-padded-to-32-bytes"
NONCE = "expected-nonce"


@pytest.fixture(scope="module")
def signing_key():
    """Return one ephemeral signing key for boundary tests."""
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def public_jwk(private_key, algorithm):
    """Return the provider JWKS entry shape that upstream turns into a PyJWK."""
    if algorithm.startswith("ES"):
        jwk = jwt.algorithms.ECAlgorithm.to_jwk(private_key.public_key(), as_dict=True)
    else:
        jwk = jwt.algorithms.RSAAlgorithm.to_jwk(private_key.public_key(), as_dict=True)
    return jwt.PyJWK({**jwk, "kid": "s0", "alg": algorithm})


def build_backend(settings, algorithm="RS256", **overrides):
    """Build the real Meet backend after settings it reads at construction."""
    settings.OIDC_OP_URL = ISSUER
    settings.OIDC_RP_CLIENT_ID = CLIENT_ID
    settings.OIDC_RP_CLIENT_SECRET = CLIENT_SECRET
    settings.OIDC_RP_SIGN_ALGO = algorithm
    settings.OIDC_RP_IDP_SIGN_KEY = None
    settings.OIDC_OP_JWKS_ENDPOINT = f"{ISSUER}/jwks"
    settings.OIDC_OP_TOKEN_ENDPOINT = f"{ISSUER}/token"
    settings.OIDC_OP_USER_ENDPOINT = f"{ISSUER}/userinfo"
    settings.OIDC_USE_NONCE = True
    for name, value in overrides.items():
        setattr(settings, name, value)
    return OIDCAuthenticationBackend()


@pytest.fixture
def backend(settings, signing_key, monkeypatch):
    """Build the real Meet backend with a synthetic trusted JWKS key."""
    instance = build_backend(settings)
    monkeypatch.setattr(
        instance,
        "retrieve_matching_jwk",
        mock.Mock(return_value=public_jwk(signing_key, "RS256")),
    )
    return instance


def claims_for(**overrides):
    """Return minimal valid Meet ID token claims with focused changes."""
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
    return claims


def make_token(signing_key, algorithm="RS256", without=(), kid="s0", **overrides):
    """Sign a Meet ID token, allowing focused claim changes or removals."""
    claims = claims_for(**overrides)
    for name in without:
        del claims[name]
    headers = {"kid": kid} if kid is not None else None
    return jwt.encode(claims, signing_key, algorithm=algorithm, headers=headers)


def segment(value):
    """Encode one compact JWS segment."""
    raw = value if isinstance(value, bytes) else json.dumps(value).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def test_accepts_valid_meet_id_token(backend, signing_key):
    """The configured issuer, Meet audience and nonce are accepted."""
    payload = backend.verify_token(make_token(signing_key), nonce=NONCE)

    assert payload["iss"] == ISSUER
    assert payload["aud"] == CLIENT_ID
    assert payload["sub"] == "account-123"
    backend.retrieve_matching_jwk.assert_called_once()


@pytest.mark.parametrize(
    "claims",
    [
        {"iss": "https://another-issuer.test"},
        {"iss": f"{ISSUER}/"},
        {"aud": "another-oauth-client"},
        {"iss": "https://convex.test", "aud": "convex"},
        {"sub": ""},
        {"iat": int(time.time()) + 3600},
    ],
    ids=[
        "wrong-issuer",
        "issuer-trailing-slash",
        "other-client",
        "convex-jwt",
        "empty-subject",
        "future-issued-at",
    ],
)
def test_rejects_token_outside_meet_trust_context(backend, signing_key, claims):
    """A valid signature cannot cross issuer, client or subject boundaries."""
    with pytest.raises(SuspiciousOperation):
        backend.verify_token(make_token(signing_key, **claims), nonce=NONCE)


def test_rejects_expired_token(backend, signing_key):
    """Expired ID tokens fail closed at the Meet boundary."""
    with pytest.raises(SuspiciousOperation):
        backend.verify_token(
            make_token(signing_key, exp=int(time.time()) - 1), nonce=NONCE
        )


@pytest.mark.parametrize("claim", ["iss", "sub", "aud", "exp", "iat"])
def test_rejects_missing_required_claim(backend, signing_key, claim):
    """An ID token must explicitly carry every required claim."""
    with pytest.raises(SuspiciousOperation):
        backend.verify_token(make_token(signing_key, without=[claim]), nonce=NONCE)


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


@pytest.mark.parametrize(
    ("expected", "token_overrides", "without"),
    [
        (None, {}, ["nonce"]),
        (NONCE, {}, ["nonce"]),
        (None, {}, []),
        ("", {"nonce": ""}, []),
        (NONCE, {"nonce": 123}, []),
        (123, {"nonce": 123}, []),
    ],
    ids=[
        "both-missing",
        "token-missing",
        "expected-missing",
        "both-empty",
        "token-not-string",
        "both-not-string",
    ],
)
def test_rejects_incomplete_nonce_context(
    backend, signing_key, expected, token_overrides, without
):
    """Missing, empty or non-string nonce values never validate an ID token."""
    token = make_token(signing_key, without=without, **token_overrides)

    with pytest.raises(SuspiciousOperation, match="Nonce"):
        backend.verify_token(token, nonce=expected)


def test_nonce_can_be_disabled_without_weakening_the_boundary(settings, signing_key):
    """OIDC_USE_NONCE=False skips only the nonce check for ID tokens."""
    instance = build_backend(settings, OIDC_USE_NONCE=False)
    instance.retrieve_matching_jwk = mock.Mock(
        return_value=public_jwk(signing_key, "RS256")
    )

    payload = instance.verify_token(
        make_token(signing_key, without=["nonce"]), nonce=None
    )
    assert payload["sub"] == "account-123"

    with pytest.raises(SuspiciousOperation):
        instance.verify_token(
            make_token(signing_key, aud="another-oauth-client"), nonce=None
        )


@pytest.mark.parametrize(
    "token",
    [
        "not-a-jwt",
        f"{segment({'typ': 'JWT', 'kid': 's0'})}.{segment({})}.{segment(b'sig')}",
        f"{segment({'alg': 'RS256'})}.{segment({})}.{segment(b'sig')}",
        f"{segment({'alg': 'RS256', 'kid': ''})}.{segment({})}.{segment(b'sig')}",
        f"{segment({'alg': 'RS256', 'kid': 's0'})}.{segment(b'not-json')}"
        f".{segment(b'sig')}",
        f"{segment({'alg': 'RS256', 'kid': 's0'})}.{segment([])}.{segment(b'sig')}",
    ],
    ids=[
        "malformed-header",
        "missing-algorithm",
        "missing-key-id",
        "empty-key-id",
        "malformed-payload",
        "non-object-payload",
    ],
)
def test_malformed_token_fails_before_jwks_lookup(backend, token):
    """Malformed compact JWT input produces a bounded refusal before key lookup."""
    with pytest.raises(SuspiciousOperation):
        backend.verify_token(token, nonce=NONCE)

    backend.retrieve_matching_jwk.assert_not_called()


def test_rejects_wrong_signing_algorithm(backend):
    """The token algorithm must equal the configured algorithm."""
    token = jwt.encode(claims_for(), CLIENT_SECRET, algorithm="HS256")

    with pytest.raises(SuspiciousOperation, match="algorithm"):
        backend.verify_token(token, nonce=NONCE)

    backend.retrieve_matching_jwk.assert_not_called()


def test_uses_the_configured_static_provider_key(settings, signing_key):
    """A configured provider key is used instead of a JWKS lookup."""
    instance = build_backend(
        settings, OIDC_RP_IDP_SIGN_KEY=static_public_pem(signing_key)
    )
    instance.retrieve_matching_jwk = mock.Mock()

    payload = instance.verify_token(make_token(signing_key), nonce=NONCE)

    assert payload["sub"] == "account-123"
    instance.retrieve_matching_jwk.assert_not_called()


def test_uses_the_client_secret_for_hmac_id_tokens(settings):
    """HMAC ID tokens are verified with the Meet client secret only."""
    instance = build_backend(settings, algorithm="HS256")
    instance.retrieve_matching_jwk = mock.Mock()

    payload = instance.verify_token(
        make_token(CLIENT_SECRET, algorithm="HS256"), nonce=NONCE
    )
    assert payload["sub"] == "account-123"

    with pytest.raises(SuspiciousOperation):
        instance.verify_token(
            make_token("another-secret-padded-to-32-bytes!", algorithm="HS256"),
            nonce=NONCE,
        )
    instance.retrieve_matching_jwk.assert_not_called()


def test_uses_the_provider_jwks_for_ecdsa_id_tokens(settings):
    """ECDSA ID tokens use the provider JWKS like RSA tokens."""
    ec_key = ec.generate_private_key(ec.SECP256R1())
    instance = build_backend(settings, algorithm="ES256")
    instance.retrieve_matching_jwk = mock.Mock(return_value=public_jwk(ec_key, "ES256"))

    payload = instance.verify_token(make_token(ec_key, algorithm="ES256"), nonce=NONCE)

    assert payload["sub"] == "account-123"
    instance.retrieve_matching_jwk.assert_called_once()


@pytest.mark.parametrize(
    ("audiences", "azp"),
    [
        ([CLIENT_ID, "another-oauth-client"], None),
        ([CLIENT_ID, "another-oauth-client"], "another-oauth-client"),
        (["another-oauth-client", CLIENT_ID], "another-oauth-client"),
        ([CLIENT_ID, "another-oauth-client"], ""),
    ],
    ids=["missing", "other-client", "other-client-first", "empty"],
)
def test_rejects_multi_audience_without_exact_authorized_party(
    backend, signing_key, audiences, azp
):
    """A multi-audience token must explicitly authorize the Meet client."""
    claims = {"aud": audiences}
    if azp is not None:
        claims["azp"] = azp

    with pytest.raises(SuspiciousOperation):
        backend.verify_token(make_token(signing_key, **claims), nonce=NONCE)


def test_accepts_multi_audience_with_meet_as_authorized_party(backend, signing_key):
    """A multi-audience token is accepted when Meet is the authorized party."""
    payload = backend.verify_token(
        make_token(
            signing_key,
            aud=["another-oauth-client", CLIENT_ID],
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


def authenticate_with_id_token(backend, monkeypatch, id_token):
    """Run the real authenticate flow with only provider exchanges replaced."""
    request = RequestFactory().get("/callback", {"code": "code", "state": "state"})
    request.session = {}
    captured_payload = {}
    authenticated_user = object()
    get_or_create_user = mock.Mock(return_value=authenticated_user)

    def get_token(payload):
        captured_payload.update(payload)
        backend._token_info = {
            "id_token": id_token,
            "access_token": "access-token",
            "refresh_token": None,
            "sid": None,
        }
        return backend._token_info

    monkeypatch.setattr(backend, "get_token", get_token)
    monkeypatch.setattr(backend, "get_or_create_user", get_or_create_user)
    user = backend.authenticate(request, nonce=NONCE, code_verifier="v" * 64)
    return user, authenticated_user, captured_payload, get_or_create_user


def test_authenticate_verifies_the_id_token_and_keeps_pkce(
    backend, signing_key, monkeypatch
):
    """The real callback exchange keeps PKCE and routes the ID token strictly."""
    user, authenticated_user, captured_payload, _ = authenticate_with_id_token(
        backend, monkeypatch, make_token(signing_key)
    )

    assert user is authenticated_user
    assert captured_payload["code_verifier"] == "v" * 64


def test_authenticate_rejects_an_id_token_for_another_client(
    backend, signing_key, monkeypatch
):
    """A token for another client never reaches user resolution or UserInfo."""
    with pytest.raises(SuspiciousOperation):
        authenticate_with_id_token(
            backend,
            monkeypatch,
            make_token(signing_key, aud="another-oauth-client"),
        )

    assert backend.get_or_create_user.call_count == 0


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


def test_accepts_single_element_audience_list_without_authorized_party(
    backend, signing_key
):
    """A one-element audience list naming Meet needs no authorized party."""
    payload = backend.verify_token(
        make_token(signing_key, aud=[CLIENT_ID]), nonce=NONCE
    )

    assert payload["aud"] == [CLIENT_ID]


def static_public_pem(signing_key):
    """Return the PEM form used for a configured static provider key."""
    return (
        signing_key.public_key()
        .public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )


@pytest.mark.parametrize(
    "case", ["hmac", "static-key", "jwks-without-kid-verification"]
)
def test_accepts_token_without_key_id_when_no_kid_lookup_applies(
    settings, signing_key, case
):
    """A key identifier is required only for a kid-verified JWKS lookup."""
    if case == "hmac":
        instance = build_backend(settings, algorithm="HS256")
        token = make_token(CLIENT_SECRET, algorithm="HS256", kid=None)
    elif case == "static-key":
        instance = build_backend(
            settings, OIDC_RP_IDP_SIGN_KEY=static_public_pem(signing_key)
        )
        token = make_token(signing_key, kid=None)
    else:
        instance = build_backend(settings, OIDC_VERIFY_KID=False)
        token = make_token(signing_key, kid=None)
    instance.retrieve_matching_jwk = mock.Mock(
        return_value=public_jwk(signing_key, "RS256")
    )

    payload = instance.verify_token(token, nonce=NONCE)

    assert payload["sub"] == "account-123"
    assert instance.retrieve_matching_jwk.call_count == (
        1 if case == "jwks-without-kid-verification" else 0
    )
