"""Authentication boundaries for Mastrao technical owners."""

from types import SimpleNamespace
from unittest import mock

from django.core.exceptions import SuspiciousOperation

import pytest
from rest_framework.exceptions import AuthenticationFailed

from core.authentication.livekit import LiveKitTokenAuthentication
from core.external_api.authentication import (
    BaseJWTAuthentication,
    ResourceServerBackend,
)
from core.factories import UserFactory


@pytest.mark.django_db
def test_application_jwt_cannot_load_mastrao_owner():
    """Application JWT authentication cannot assume a technical owner."""

    owner = UserFactory(sub="mastrao_owner_0123456789", is_device=True)
    backend = BaseJWTAuthentication(None, None, None, None, None, None, False)

    with pytest.raises(AuthenticationFailed, match="non-interactive"):
        backend.get_user({"user_id": str(owner.id)})


@pytest.mark.django_db
def test_resource_server_refuses_mastrao_subject(django_assert_num_queries):
    """The resource server rejects reserved subjects before database access."""

    backend = ResourceServerBackend()

    with django_assert_num_queries(0), pytest.raises(SuspiciousOperation):
        backend.get_or_create_user(None, None, {"sub": "mastrao_owner_0123456789"})


@pytest.mark.django_db
def test_livekit_refuses_mastrao_subject(settings, django_assert_num_queries):
    """LiveKit authentication cannot load a technical owner subject."""

    settings.LIVEKIT_CONFIGURATION = {
        "api_key": "livekit-test-key",
        "api_secret": "livekit-test-secret",
    }
    verifier = mock.Mock()
    verifier.verify.return_value = SimpleNamespace(identity="mastrao_owner_0123456789")
    request = SimpleNamespace(headers={"Authorization": "Bearer signed-token"})

    with (
        mock.patch("core.authentication.livekit.TokenVerifier", return_value=verifier),
        django_assert_num_queries(0),
        pytest.raises(AuthenticationFailed),
    ):
        LiveKitTokenAuthentication().authenticate(request)
