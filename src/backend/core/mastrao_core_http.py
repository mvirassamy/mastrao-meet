"""Strict, bounded HTTP transport for private Cabinet Core calls."""

import json
from urllib.parse import urlparse

import requests

MAX_CORE_RESPONSE_BYTES = 20_000
TRANSCRIPTION_CALLBACK_OUTCOMES = {
    "available",
    "failed",
    "deleted",
    "conflict",
    "retry",
}
ALLOWED_CORE_HOSTS = {
    "127.0.0.1",
    "localhost",
    "::1",
    "host.docker.internal",
    "127.0.0.1.nip.io",
    "cabinet-core",
}


def validate_core_endpoint(value, expected_path, refusal):
    """Return one exact private Core endpoint or fail closed."""

    endpoint = urlparse(value)
    if (
        endpoint.scheme != "http"
        or endpoint.hostname not in ALLOWED_CORE_HOSTS
        or endpoint.path != expected_path
        or any(
            (endpoint.username, endpoint.password, endpoint.query, endpoint.fragment)
        )
    ):
        raise refusal(status=503)
    return value


def _read_core_body(response, refusal):
    chunks = []
    size = 0
    for chunk in response.iter_content(chunk_size=4_096):
        size += len(chunk)
        if size > MAX_CORE_RESPONSE_BYTES:
            raise refusal(status=503)
        chunks.append(chunk)
    return json.loads(b"".join(chunks))


def _raise_refusal(refusal, status, outcome=None):
    try:
        raise refusal(status=status, outcome=outcome)
    except TypeError:
        raise refusal(status=status) from None


def read_bounded_core_json(
    response,
    refusal,
    *,
    expected_fields=None,
    passthrough_statuses=frozenset(),
    client_error_status=404,
):
    """Read one bounded JSON object and always close its streamed response."""

    declared = response.headers.get("content-length")
    if declared is not None and (
        not declared.isdecimal() or int(declared) > MAX_CORE_RESPONSE_BYTES
    ):
        response.close()
        raise refusal(status=503)
    if response.status_code != 200:
        status = (
            response.status_code
            if response.status_code in passthrough_statuses
            else 503
            if response.status_code >= 500 or client_error_status is None
            else client_error_status
        )
        outcome = None
        try:
            body = _read_core_body(response, refusal)
            if (
                isinstance(body, dict)
                and body.get("outcome") in TRANSCRIPTION_CALLBACK_OUTCOMES
            ):
                outcome = body["outcome"]
        except (UnicodeDecodeError, ValueError, json.JSONDecodeError, TypeError):
            outcome = None
        finally:
            response.close()
        _raise_refusal(refusal, status, outcome)
    try:
        body = _read_core_body(response, refusal)
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as error:
        raise refusal(status=503) from error
    finally:
        response.close()
    if not isinstance(body, dict):
        raise refusal(status=503)
    if expected_fields is not None and set(body) != set(expected_fields):
        raise refusal(status=503)
    return body


def post_core_json(  # noqa: PLR0913  # pylint: disable=too-many-arguments
    *,
    endpoint,
    expected_path,
    body,
    timeout,
    refusal,
    expected_fields=None,
    passthrough_statuses=frozenset(),
    client_error_status=404,
):
    """POST one JSON object to an allowlisted Core endpoint."""

    target = validate_core_endpoint(endpoint, expected_path, refusal)
    try:
        with requests.Session() as session:
            session.trust_env = False
            response = session.post(
                target,
                json=body,
                timeout=timeout,
                allow_redirects=False,
                stream=True,
            )
            return read_bounded_core_json(
                response,
                refusal,
                expected_fields=expected_fields,
                passthrough_statuses=passthrough_statuses,
                client_error_status=client_error_status,
            )
    except requests.RequestException as error:
        raise refusal(status=503) from error
