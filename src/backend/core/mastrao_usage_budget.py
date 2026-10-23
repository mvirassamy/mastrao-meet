"""Authenticated settings link to Platform's organization usage-budget page."""

import re

from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.http import require_GET

from core.mastrao_platform_facade import PlatformFacadeError, request_platform

ORGANIZATION_SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
PRIVATE_HEADERS = {
    "Cache-Control": "private, no-store",
    "X-Content-Type-Options": "nosniff",
}


@require_GET
def usage_budget(request):
    """Platform's existing OIDC context owns organization selection and admin access."""
    try:
        if not request.user.is_authenticated:
            raise PlatformFacadeError(status=401)
        if request.GET:
            raise PlatformFacadeError(status=422)
        body, _ = request_platform(
            request, "GET", "/api/meet/usage-budget", accepted_statuses={200}
        )
        slug = body.get("organization_slug")
        if (
            not isinstance(slug, str)
            or len(slug) > 100
            or not ORGANIZATION_SLUG.fullmatch(slug)
        ):
            raise PlatformFacadeError()
        origin = settings.MASTRAO_PLATFORM_API_BASE_URL.rstrip("/")
        url = body.get("usage_budget_url")
        if url != f"{origin}/orgs/{slug}/settings/usage-budget":
            raise PlatformFacadeError()
        return JsonResponse(
            {"organization_slug": slug, "usage_budget_url": url},
            headers=PRIVATE_HEADERS,
        )
    except PlatformFacadeError as error:
        return JsonResponse(
            {"message": "Usage et budget indisponible"},
            status=error.status,
            headers=PRIVATE_HEADERS,
        )
