"""Bounded response handling for the private Room Audio ASR gateway."""

from core.mastrao_core_http import _read_core_body, read_bounded_core_json
from core.mastrao_recording_contract import RecordingContractRefused

MAX_REPLY_BYTES = 24 * 1024**2


def read_asr_gateway_response(response):
    """Return one successful bounded response or fail closed."""
    if response.status_code == 200:
        return read_bounded_core_json(
            response,
            RecordingContractRefused,
            maximum_bytes=MAX_REPLY_BYTES,
        )
    try:
        body = _read_core_body(response, RecordingContractRefused)
        if not isinstance(body, dict) or body.get("outcome") not in {
            "unknown",
            "rejected",
            "retry",
            "failed_pre_egress",
        }:
            raise RecordingContractRefused(status=503)
        raise RecordingContractRefused(
            status=response.status_code,
            outcome=body["outcome"],
        )
    except (ValueError, TypeError, UnicodeDecodeError) as error:
        raise RecordingContractRefused(status=503) from error
    finally:
        response.close()
