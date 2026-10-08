"""Proofs for safe platform return projections and credential scrubbing."""

import json
from datetime import timedelta
from unittest import mock

from django.test import override_settings
from django.utils import timezone

from core.mastrao_host_grant import host_platform_return_projection
from core.tests import test_mastrao_host_handoff

from meet.settings import scrub_mastrao_handoff_credentials

# Register the same per-test verifier isolation as the handoff proofs.
fixture_local_handoff_verification = (
    test_mastrao_host_handoff.fixture_local_handoff_verification
)


def test_sentry_scrubs_host_handoff_credentials():
    """Filter host and room-close credentials while preserving safe request data."""

    event = {
        "request": {
            "data": {
                "host_handoff": "header.payload.signature",
                "host_grant": "grant.payload.signature",
                "close_assertion": "close.payload.signature",
                "room_close_effect": "effect.payload.signature",
                "room_close_receipt": "receipt.payload.signature",
                "safe": "kept",
            }
        }
    }

    scrubbed = scrub_mastrao_handoff_credentials(event, {})

    assert scrubbed["request"]["data"] == {
        "host_handoff": "[Filtered]",
        "host_grant": "[Filtered]",
        "close_assertion": "[Filtered]",
        "room_close_effect": "[Filtered]",
        "room_close_receipt": "[Filtered]",
        "safe": "kept",
    }


def test_sentry_scrubs_raw_guest_confirmation_credentials():
    """Filter raw JSON bodies containing guest confirmation credentials."""

    event = {
        "request": {
            "data": json.dumps(
                {
                    "decision_grant": "decision.payload.signature",
                    "receipt_assertion": "receipt.payload.signature",
                }
            )
        }
    }

    scrubbed = scrub_mastrao_handoff_credentials(event, {})

    assert scrubbed["request"]["data"] == "[Filtered]"


def test_sentry_scrubs_transcription_effects_and_receipts():
    """Filter transcription credentials in structured data, raw bodies, and headers."""

    event = {
        "request": {
            "data": {
                "transcription_submit_effect": "effect.payload.signature",
                "transcription_artifact_receipt": "receipt.payload.signature",
                "transcription_egress_request": "request.payload.signature",
                "transcription_egress_grant": "grant.payload.signature",
                "transcription_terminal_receipt": "terminal.payload.signature",
                "safe": "kept",
            }
        }
    }
    scrubbed = scrub_mastrao_handoff_credentials(event, {})
    assert scrubbed["request"]["data"] == {
        "transcription_submit_effect": "[Filtered]",
        "transcription_artifact_receipt": "[Filtered]",
        "transcription_egress_request": "[Filtered]",
        "transcription_egress_grant": "[Filtered]",
        "transcription_terminal_receipt": "[Filtered]",
        "safe": "kept",
    }

    raw = {
        "request": {
            "data": json.dumps(
                {"transcription_failure_receipt": "receipt.payload.signature"}
            )
        }
    }
    assert scrub_mastrao_handoff_credentials(raw, {})["request"]["data"] == (
        "[Filtered]"
    )

    headers = {
        "request": {
            "headers": {
                "X-Mastrao-Transcription-Egress-Grant": "grant.payload.signature",
                "Accept": "application/json",
            }
        }
    }
    assert scrub_mastrao_handoff_credentials(headers, {})["request"]["headers"] == {
        "X-Mastrao-Transcription-Egress-Grant": "[Filtered]",
        "Accept": "application/json",
    }


@override_settings(MASTRAO_PLATFORM_ORIGIN="https://attacker.test/path")
def test_host_platform_return_rejects_non_origin_configuration():
    """Refuse platform return URLs when configuration includes an origin path."""

    grant = mock.Mock()
    with mock.patch(
        "core.mastrao_host_grant.active_host_close_grant", return_value=grant
    ):
        assert host_platform_return_projection(mock.Mock(), mock.Mock()) is None


@override_settings(MASTRAO_PLATFORM_ORIGIN="https://platform.mastrao.test")
def test_host_platform_return_rejects_a_grant_binding_mismatch():
    """Refuse return projections when signed claims differ from the stored grant."""

    expires_at = timezone.now() + timedelta(minutes=5)
    stored = mock.Mock(
        grant_ref="grant_0123456789abcdef",
        meeting_ref="meeting_0123456789abcdef",
        room_ref="room_0123456789abcdef",
        platform_session_ref="platformsession_0123456789abcdef",
        provider_binding_digest="b" * 64,
        expires_at=expires_at,
    )
    claims = {
        "grant_ref": stored.grant_ref,
        "organization_external_id": "organization_0123456789",
        "meeting_ref": "meeting_different_01234567",
        "room_ref": stored.room_ref,
        "platform_session_ref": stored.platform_session_ref,
        "provider_binding_digest": stored.provider_binding_digest,
        "expires_at": int(expires_at.timestamp()),
    }
    with (
        mock.patch(
            "core.mastrao_host_grant.active_host_close_grant",
            return_value=stored,
        ),
        mock.patch(
            "core.mastrao_host_grant.active_host_compact_grant",
            return_value="grant.payload.signature",
        ),
        mock.patch("core.mastrao_host_grant.verify_host_grant", return_value=claims),
    ):
        assert host_platform_return_projection(mock.Mock(), mock.Mock()) is None
