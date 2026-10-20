"""Bounded same-origin bridge from Meet to the Platform meeting facade."""

import json
import logging
import re
import time
from urllib.parse import urlencode, urlparse

from django.conf import settings

import requests
from urllib3.exceptions import ReadTimeoutError

logger = logging.getLogger(__name__)

MAX_PLATFORM_RESPONSE_BYTES = 2 * 1024 * 1024
MEETING_REF = re.compile(r"^[A-Za-z0-9_-]{16,160}$")
CURSOR = re.compile(r"^[A-Za-z0-9_-]{1,512}$")
DEFAULT_TIMEOUT_SECONDS = 5
# Platform waits up to ~7.75 s for the new meeting projection before it seals
# the recording mode and mints the host handoff. Reads must outlast that.
MEETING_CREATION_TIMEOUT_SECONDS = (5, 20)


class PlatformFacadeError(Exception):
    """Safe public failure returned by the Platform facade bridge."""

    def __init__(self, status=503, *, cause_code=None, stage=None):
        self.status = status
        self.cause_code = cause_code
        self.stage = stage
        super().__init__("Platform meeting facade unavailable")


def _transport_cause(error):
    if isinstance(error, requests.ConnectTimeout):
        return "connect_timeout"
    # Requests wraps urllib3's body-read timeout in ConnectionError.
    if isinstance(error, requests.ReadTimeout) or (
        isinstance(error, requests.ConnectionError)
        and error.args
        and isinstance(error.args[0], ReadTimeoutError)
    ):
        return "read_timeout"
    if isinstance(error, requests.Timeout):
        return "timeout"
    return "transport_error"


def _report_refusal(cause_code, stage, started_at, upstream_status):
    """Emit only closed diagnostic fields; logging must not affect the refusal."""
    try:
        logger.warning(
            "meet_platform_facade_refused cause_code=%s stage=%s "
            "elapsed_ms=%d upstream_status=%s",
            cause_code,
            stage,
            int((time.monotonic() - started_at) * 1000),
            upstream_status,
        )
    except Exception:  # noqa: BLE001 - a broken log handler must preserve the refusal
        return


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


def _read_json(response, maximum_bytes=MAX_PLATFORM_RESPONSE_BYTES):
    declared = response.headers.get("content-length")
    if declared is not None and (len(declared) > 20 or not declared.isdecimal()):
        raise PlatformFacadeError(
            cause_code="invalid_content_length", stage="check_response_size"
        )
    if declared is not None and int(declared) > maximum_bytes:
        raise PlatformFacadeError(
            cause_code="response_too_large", stage="check_response_size"
        )
    chunks = []
    size = 0
    for chunk in response.iter_content(chunk_size=16_384):
        size += len(chunk)
        if size > maximum_bytes:
            raise PlatformFacadeError(
                cause_code="response_too_large", stage="read_response_body"
            )
        chunks.append(chunk)
    try:
        body = json.loads(b"".join(chunks))
    except (UnicodeDecodeError, ValueError, RecursionError) as error:
        raise PlatformFacadeError(
            cause_code="invalid_json", stage="decode_json"
        ) from error
    if not isinstance(body, dict):
        raise PlatformFacadeError(
            cause_code="invalid_object", stage="check_response_shape"
        )
    return body


def _query_string(options):
    """Encode day bounds and the existing bounded opaque history cursor."""

    query = {
        field: options[field] for field in ("day_start", "day_end") if field in options
    }
    cursor = options.get("cursor")
    if cursor is not None:
        if not CURSOR.fullmatch(cursor):
            raise PlatformFacadeError(status=422)
        query["cursor"] = cursor
    return urlencode(query)


def request_platform(request, method, path, *, accepted_statuses, options=None):
    """Call one allowlisted Platform meeting endpoint without token disclosure."""

    token = _access_token(request)
    options = options or {}
    error_statuses = options.get("error_statuses", {401, 404, 409, 422})
    idempotency_key = options.get("idempotency_key")
    timeout = options.get("timeout", DEFAULT_TIMEOUT_SECONDS)
    body_options = {}
    if "json" in options:
        body_options["json"] = options["json"]
    target = f"{_platform_origin()}{path}"
    query = _query_string(options)
    if query:
        target = f"{target}?{query}"
    started_at = time.monotonic()
    stage = "await_response_headers"
    upstream_status = None
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
                **body_options,
            )
            try:
                upstream_status = response.status_code
                if response.status_code not in accepted_statuses:
                    if response.status_code == 401:
                        request.session.pop("oidc_access_token", None)
                    status = (
                        response.status_code
                        if response.status_code in error_statuses
                        else 503
                    )
                    raise PlatformFacadeError(
                        status=status,
                        cause_code="upstream_http_refused",
                        stage="check_http_status",
                    )
                stage = "read_response_body"
                return (
                    _read_json(
                        response,
                        options.get("max_response_bytes", MAX_PLATFORM_RESPONSE_BYTES),
                    ),
                    response.status_code,
                )
            finally:
                response.close()
    except PlatformFacadeError as error:
        _report_refusal(error.cause_code, error.stage, started_at, upstream_status)
        raise
    except requests.RequestException as error:
        _report_refusal(_transport_cause(error), stage, started_at, upstream_status)
        raise PlatformFacadeError() from error


def meeting_path(meeting_ref, suffix=""):
    """Build a canonical Platform history path for one meeting."""

    if not MEETING_REF.fullmatch(meeting_ref):
        raise PlatformFacadeError(status=404)
    return f"/api/meet/meetings/history/{meeting_ref}/{suffix}"


def share_link_path(meeting_ref):
    """Build the allowlisted Platform durable-share path for one meeting."""

    if not MEETING_REF.fullmatch(meeting_ref):
        raise PlatformFacadeError(status=404)
    return f"/api/meet/meetings/{meeting_ref}/share-link"
