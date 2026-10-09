"""Native gateway response handling; the client and bounded parser execute for real."""

# Pytest injects settings fixtures by argument name.
# pylint: disable=missing-function-docstring,redefined-outer-name,unused-argument

import io
import json
from datetime import timedelta
from email.utils import format_datetime
from unittest.mock import patch

from django.utils import timezone

import pytest
import requests

from core.mastrao_native_asr_client import transcribe_native_asr
from core.mastrao_recording_contract import RecordingContractRefused


@pytest.fixture
def native_gateway(settings):
    settings.MASTRAO_NATIVE_ASR_GATEWAY_ENDPOINT = (
        "http://asr-gateway/v1/native/transcribe"
    )
    settings.MASTRAO_NATIVE_ASR_GATEWAY_AUTH_TOKEN = (
        "native-gateway-fixture-only-token-1234"
    )


def gateway_response(status, body, headers=None):
    response = requests.Response()
    response.status_code = status
    response.headers.update(headers or {})
    response.raw = io.BytesIO(json.dumps(body).encode())
    return response


def test_native_client_preserves_rate_limit_for_durable_retry(native_gateway):
    now = timezone.now()
    response = gateway_response(
        429,
        {"outcome": "rejected", "error": "PROVIDER_RATE_LIMITED"},
        {"Retry-After": "120"},
    )
    prepared = {"metadata": {}, "native_source_egress_grant": "fixture-grant"}
    with (
        patch("requests.Session.post", return_value=response),
        pytest.raises(RecordingContractRefused) as refused,
    ):
        transcribe_native_asr(prepared, b"audio")

    assert refused.value.status == 429
    assert refused.value.retry_at >= now + timedelta(seconds=120)


def test_native_client_preserves_unknown_for_recoverable_core_acknowledgement(
    native_gateway,
):
    response = gateway_response(
        503,
        {"outcome": "unknown", "error": "PROVIDER_OUTCOME_UNKNOWN"},
    )
    prepared = {"metadata": {}, "native_source_egress_grant": "fixture-grant"}
    with (
        patch("requests.Session.post", return_value=response),
        pytest.raises(RecordingContractRefused) as refused,
    ):
        transcribe_native_asr(prepared, b"audio")

    assert refused.value.outcome == "unknown"


def test_native_client_still_accepts_a_matching_transcript(native_gateway):
    digest = "a" * 64
    result = {"outcome": "succeeded", "transcript": {"audio_digest": digest}}
    response = gateway_response(200, result)
    prepared = {
        "metadata": {"audio_sha256": digest},
        "native_source_egress_grant": "fixture-grant",
    }
    with patch("requests.Session.post", return_value=response):
        assert transcribe_native_asr(prepared, b"audio") == result


@pytest.mark.parametrize("header_kind", ["seconds", "absolute"])
def test_native_retry_deadline_is_absolute(native_gateway, header_kind):
    now = timezone.now().replace(microsecond=0)
    deadline = now + timedelta(seconds=3600)
    header = "3600"
    if header_kind == "absolute":
        header = format_datetime(deadline, usegmt=True)
    response = gateway_response(
        429,
        {"outcome": "rejected", "error": "PROVIDER_RATE_LIMITED"},
        {"Retry-After": header},
    )
    prepared = {"metadata": {}, "native_source_egress_grant": "fixture-grant"}
    with (
        patch("requests.Session.post", return_value=response),
        patch("core.mastrao_native_asr_client.timezone.now", return_value=now),
        pytest.raises(RecordingContractRefused) as refused,
    ):
        transcribe_native_asr(prepared, b"audio")
    assert refused.value.retry_at == deadline


@pytest.mark.parametrize(
    "body", [[], {"outcome": "unrecognized"}, {"outcome": "x" * 21000}]
)
def test_invalid_gateway_error_is_bounded_and_closed(native_gateway, body):
    response = gateway_response(503, body)
    prepared = {"metadata": {}, "native_source_egress_grant": "fixture-grant"}
    with (
        patch("requests.Session.post", return_value=response),
        patch.object(response, "close", wraps=response.close) as close,
        pytest.raises(RecordingContractRefused) as refused,
    ):
        transcribe_native_asr(prepared, b"audio")
    close.assert_called_once()
    assert refused.value.outcome is None
