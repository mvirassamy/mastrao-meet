"""Recoverable Meet OIDC callback responses."""

from django.core.exceptions import SuspiciousOperation
from django.shortcuts import render

from lasuite.oidc_login.views import (
    OIDCAuthenticationCallbackView as LaSuiteOIDCAuthenticationCallbackView,
)

from core.authentication.backends import OIDCUserInfoRateLimited

MISSING_CALLBACK_STATE = "OIDC callback state not found in session `oidc_states`!"


def _no_store_page(request, template, status):
    """Render a callback failure without letting the browser cache its URL."""

    response = render(request, template, status=status)
    response["Cache-Control"] = "no-store"
    return response


class OIDCAuthenticationCallbackView(LaSuiteOIDCAuthenticationCallbackView):
    """Keep the inherited OIDC checks and make rejected callbacks recoverable."""

    def get(self, request):
        """Never replay consumed state or authenticate without verified UserInfo."""
        try:
            return super().get(request)
        except OIDCUserInfoRateLimited as error:
            response = _no_store_page(
                request, "authentication/provider_rate_limited.html", status=503
            )
            response["Retry-After"] = error.retry_after
            return response
        except SuspiciousOperation as error:
            if str(error) != MISSING_CALLBACK_STATE:
                raise
            return _no_store_page(
                request, "authentication/callback_state_expired.html", status=400
            )
