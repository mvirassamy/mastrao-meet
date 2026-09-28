"""Bounded same-origin bridge from Meet to the Platform meeting facade."""

import json
import re
from urllib.parse import urlencode, urlparse

from django.conf import settings

import requests

MAX_PLATFORM_RESPONSE_BYTES = 2 * 1024 * 1024
MEETING_REF = re.compile(r"^[A-Za-z0-9_-]{16,160}$")
CURSOR = re.compile(r"^[A-Za-z0-9_-]{1,512}$")
DEFAULT_TIMEOUT_SECONDS = 5
# Platform waits up to ~7.75 s for the new meeting projection before it seals
# the recording mode and mints the host handoff. Reads must outlast that.
MEETING_CREATION_TIMEOUT_SECONDS = (5, 20)


class PlatformFacadeError(Exception):
    """Safe public failure returned by the Platform facade bridge."""

    def __init__(self, status=503):
        self.status = status
        super().__init__("Platform meeting facade unavailable")


def _platform_origin():
    configured = settings.MASTRAO_PLATFORM_API_BASE_URL
    if not configured:
        raise PlatformFacadeError()
    parsed = urlparse(configured)
    loopback = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    if (
        parsed.scheme not in ({"https"} if not loopback else {"http", "https"})
        or not parsed.hostname
        or parsed.path not in {"", "/"}
        or any((parsed.username, parsed.password, parsed.query, parsed.fragment))
    ):
        raise PlatformFacadeError()
    return configured.rstrip("/")


def _access_token(request):
    token = request.session.get("oidc_access_token")
    if (
        not isinstance(token, str)
        or not 16 <= len(token) <= 4096
        or any(character.isspace() for character in token)
    ):
        raise PlatformFacadeError(status=401)
    return token


def _read_json(response):
    declared = response.headers.get("content-length")
    if declared is not None and (
        not declared.isdecimal() or int(declared) > MAX_PLATFORM_RESPONSE_BYTES
    ):
        raise PlatformFacadeError()
    chunks = []
    size = 0
    for chunk in response.iter_content(chunk_size=16_384):
        size += len(chunk)
        if size > MAX_PLATFORM_RESPONSE_BYTES:
            raise PlatformFacadeError()
        chunks.append(chunk)
    try:
        body = json.loads(b"".join(chunks))
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as error:
        raise PlatformFacadeError() from error
    if not isinstance(body, dict):
        raise PlatformFacadeError()
    return body


def request_platform(request, method, path, *, accepted_statuses, options=None):
    """Call one allowlisted Platform meeting endpoint without token disclosure."""

    token = _access_token(request)
    options = options or {}
    cursor = options.get("cursor")
    idempotency_key = options.get("idempotency_key")
    timeout = options.get("timeout", DEFAULT_TIMEOUT_SECONDS)
    target = f"{_platform_origin()}{path}"
    if cursor is not None:
        if not CURSOR.fullmatch(cursor):
            raise PlatformFacadeError(status=422)
        target = f"{target}?{urlencode({'cursor': cursor})}"
    try:
        with requests.Session() as session:
            session.trust_env = False
            headers = {"authorization": f"Bearer {token}"}
            if idempotency_key is not None:
                headers["x-idempotency-key"] = idempotency_key
            response = session.request(
                method,
                target,
                headers=headers,
                timeout=timeout,
                allow_redirects=False,
                stream=True,
            )
            try:
                if response.status_code not in accepted_statuses:
                    if response.status_code == 401:
                        request.session.pop("oidc_access_token", None)
                    status = (
                        response.status_code
                        if response.status_code in {401, 404, 409, 422}
                        else 503
                    )
                    raise PlatformFacadeError(status=status)
                return _read_json(response), response.status_code
            finally:
                response.close()
    except requests.RequestException as error:
        raise PlatformFacadeError() from error


def meeting_path(meeting_ref, suffix=""):
    """Build a canonical Platform history path for one meeting."""

    if not MEETING_REF.fullmatch(meeting_ref):
        raise PlatformFacadeError(status=404)
    return f"/api/meet/meetings/history/{meeting_ref}/{suffix}"


def guest_invitation_path(meeting_ref):
    """Build the allowlisted Platform guest-invitation path for one meeting."""

    if not MEETING_REF.fullmatch(meeting_ref):
        raise PlatformFacadeError(status=404)
    return f"/api/meet/meetings/{meeting_ref}/guest-invitation"
