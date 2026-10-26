"""Malformed signed submissions must stop at the transcription boundary."""

import json
from unittest.mock import patch

import pytest

from core.mastrao_transcription_adapter import transcribe_mastrao_recording
from core.mastrao_transcription_contract import (
    TranscriptionContractRefused,
    _verified_submit_payload,
)


@pytest.mark.parametrize("compact", ["not-jws", "a.b.c", "e30.e30.invalid"])
def test_unverifiable_schemas_raise_the_transcription_refusal(compact):
    """Map malformed signed submissions to the opaque transcription refusal."""
    with pytest.raises(TranscriptionContractRefused):
        _verified_submit_payload(compact)


@pytest.mark.parametrize("compact", ["not-jws", "a.b.c", "e30.e30.invalid"])
def test_endpoint_refuses_unverifiable_signatures_before_dispatch(rf, compact):
    """Refuse malformed signatures before any transcription dispatch."""
    request = rf.post(
        "/internal/mastrao/transcriptions/transcribe/",
        data=json.dumps({"transcription_submit_effect": compact}),
        content_type="application/json",
    )
    with patch("core.mastrao_transcription_adapter._apply_transcription") as apply:
        response = transcribe_mastrao_recording(request)

    apply.assert_not_called()
    assert response.status_code == 404
    assert json.loads(response.content) == {"message": "Not found"}
    assert response.headers["Cache-Control"] == "private, no-store"
    assert response.headers["X-Content-Type-Options"] == "nosniff"


def test_endpoint_refuses_expired_verified_payload_before_dispatch(rf):
    """Refuse an expired verified payload before any transcription dispatch."""
    request = rf.post(
        "/internal/mastrao/transcriptions/transcribe/",
        data=json.dumps({"transcription_submit_effect": "fixture-verified"}),
        content_type="application/json",
    )
    with (
        patch(
            "core.mastrao_transcription_contract._verified_submit_payload",
            return_value=({"issued_at": 0, "expires_at": 0}, 4),
        ),
        patch("core.mastrao_transcription_adapter._apply_transcription") as apply,
    ):
        response = transcribe_mastrao_recording(request)

    apply.assert_not_called()
    assert response.status_code == 404
    assert json.loads(response.content) == {"message": "Not found"}
