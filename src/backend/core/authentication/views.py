"""Meet OIDC callback responses for temporary provider throttling."""

from django.shortcuts import render

from lasuite.oidc_login.views import (
    OIDCAuthenticationCallbackView as LaSuiteOIDCAuthenticationCallbackView,
)

from core.authentication.backends import OIDCUserInfoRateLimited


class OIDCAuthenticationCallbackView(LaSuiteOIDCAuthenticationCallbackView):
    """Keep the inherited OIDC flow and offer a fresh login after a UserInfo 429."""

    def get(self, request):
        """Never replay consumed state or authenticate without verified UserInfo."""
        try:
            return super().get(request)
        except OIDCUserInfoRateLimited as error:
            response = render(
                request, "authentication/provider_rate_limited.html", status=503
            )
            response["Retry-After"] = error.retry_after
            response["Cache-Control"] = "no-store"
            return response
