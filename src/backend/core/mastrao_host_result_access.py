"""Relay one host result link to Core for the visitor result page.

The page needs no Meet account. The link credential is read from the page
fragment and sent here in a POST body; Meet never stores it and Core revalidates
the link and the host authority on every read.
"""

import hashlib
import json
import time

from django.conf import settings
from django.core.cache import cache
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from core.mastrao_browser_handoff import (
    browser_handoff_headers,
    is_same_origin_handoff,
)
from core.mastrao_core_http import post_core_json

CORE_READ_PATH = "/internal/v1/meeting-host-results/read"
MAX_BODY_BYTES = 20_000
MIN_ACCESS_BYTES = 32
MAX_ACCESS_BYTES = 16_384
MAX_CORE_RESPONSE_BYTES = 10 * 1024 * 1024
MAX_ATTEMPTS_PER_ACCESS_PER_MINUTE = 10
RESPONSE_FIELDS = {
    "version",
    "meeting_ref",
    "title",
    "access_expires_at",
    "started_at",
    "ended_at",
    "summary",
    "transcript",
}


class HostResultRefused(Exception):
    """Opaque refusal: every unavailable link looks the same to the browser."""

    def __init__(self, status=404):
        self.status = status
        super().__init__("host_result_refused")


def _response_headers():
    return {**browser_handoff_headers(), "X-Robots-Tag": "noindex, nofollow"}


def _read_access(request):
    declared = request.headers.get("content-length")
    if (
        request.content_type != "application/json"
        or declared is None
        or not declared.isdecimal()
        or int(declared) > MAX_BODY_BYTES
        or len(request.body) > MAX_BODY_BYTES
    ):
        raise HostResultRefused()
    try:
        body = json.loads(request.body)
    except (UnicodeDecodeError, ValueError) as error:
        raise HostResultRefused() from error
    if not isinstance(body, dict) or set(body) != {"access"}:
        raise HostResultRefused()
    access = body["access"]
    if not isinstance(access, str) or not access.isascii():
        raise HostResultRefused()
    if not MIN_ACCESS_BYTES <= len(access) <= MAX_ACCESS_BYTES or any(
        character.isspace() for character in access
    ):
        raise HostResultRefused()
    return access


def _count_attempt(key):
    if cache.add(key, 1, timeout=70):
        return 1
    try:
        return cache.incr(key)
    except ValueError as error:
        raise HostResultRefused(status=503) from error


def _admit_attempt(access):
    bucket = int(time.time()) // 60
    digest = hashlib.sha256(access.encode("ascii")).hexdigest()
    if (
        _count_attempt(f"mastrao-host-result:{digest}:{bucket}")
        > MAX_ATTEMPTS_PER_ACCESS_PER_MINUTE
    ):
        raise HostResultRefused(status=429)
    if (
        _count_attempt(f"mastrao-host-result:global:{bucket}")
        > settings.MASTRAO_HOST_HANDOFF_GLOBAL_ATTEMPTS_PER_MINUTE
    ):
        raise HostResultRefused(status=503)


@csrf_exempt
@require_POST
def read_mastrao_host_result(request):
    """Return the host's current result, or one opaque refusal."""

    headers = _response_headers()
    if not settings.MASTRAO_CORE_HOST_RESULT_READ_ENDPOINT:
        return JsonResponse({"message": "Not found"}, status=404, headers=headers)
    try:
        if not is_same_origin_handoff(request):
            raise HostResultRefused()
        access = _read_access(request)
        _admit_attempt(access)
        result = post_core_json(
            endpoint=settings.MASTRAO_CORE_HOST_RESULT_READ_ENDPOINT,
            expected_path=CORE_READ_PATH,
            body={"credential": access},
            timeout=settings.MASTRAO_CORE_REDEMPTION_TIMEOUT_SECONDS,
            refusal=HostResultRefused,
            expected_fields=RESPONSE_FIELDS,
            maximum_response_bytes=MAX_CORE_RESPONSE_BYTES,
        )
    except HostResultRefused as error:
        return JsonResponse(
            {"message": "Not found" if error.status == 404 else "Unavailable"},
            status=error.status,
            headers=headers,
        )
    return JsonResponse(result, status=200, headers=headers)
