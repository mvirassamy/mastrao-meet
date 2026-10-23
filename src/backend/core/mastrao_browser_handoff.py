"""Shared browser boundary rules for public Mastrao handoff endpoints."""

from urllib.parse import urlparse

from django.conf import settings


def browser_handoff_headers():
    """Return headers that prevent browser handoff responses from leaking."""

    return {
        "Cache-Control": "private, no-store",
        "Referrer-Policy": "no-referrer",
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "frame-ancestors 'none'",
    }


def is_same_origin_handoff(request):
    """Accept only browser submissions from the configured Meet origin."""

    origin = request.headers.get("origin")
    if (
        request.headers.get("sec-fetch-site") not in {"same-origin", "same-site"}
        or not origin
    ):
        return False
    if not isinstance(settings.APPLICATION_BASE_URL, str):
        return False
    parsed = urlparse(origin)
    expected = urlparse(settings.APPLICATION_BASE_URL)
    return (
        parsed.scheme in {"http", "https"}
        and not parsed.username
        and not parsed.password
        and parsed.path in {"", "/"}
        and not parsed.query
        and not parsed.fragment
        and parsed.scheme == expected.scheme
        and parsed.netloc == expected.netloc
    )
