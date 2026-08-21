"""Focused tests for the shared private Core HTTP boundary."""

from unittest import mock

import pytest

from core.mastrao_core_http import read_bounded_core_json, validate_core_endpoint
from core.mastrao_guest_contract import GuestHandoffRefused
from core.mastrao_room_close_contract import RoomCloseRefused
from core.mastrao_transcription_contract import TranscriptionContractRefused


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://localhost:3911/internal/v1/meetings/close",
        "http://host.docker.internal:3911/internal/v1/meetings/close",
        "http://cabinet-core:3911/internal/v1/meetings/close",
    ],
)
def test_private_core_endpoint_accepts_only_qualified_hosts(endpoint):
    """Local and deployed private Core names remain supported."""

    assert (
        validate_core_endpoint(
            endpoint, "/internal/v1/meetings/close", RoomCloseRefused
        )
        == endpoint
    )


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://cabinet-core:3911/internal/v1/meetings/close",
        "http://example.test/internal/v1/meetings/close",
        "http://cabinet-core:3911/internal/v1/meetings/other",
        "http://user@cabinet-core:3911/internal/v1/meetings/close",
        "http://cabinet-core:3911/internal/v1/meetings/close?redirect=true",
    ],
)
def test_private_core_endpoint_rejects_unqualified_targets(endpoint):
    """Configuration cannot turn the signed client into an arbitrary HTTP client."""

    with pytest.raises(RoomCloseRefused) as error:
        validate_core_endpoint(
            endpoint, "/internal/v1/meetings/close", RoomCloseRefused
        )
    assert error.value.status == 503


def test_bounded_json_preserves_close_conflict_status():
    """The close client keeps its intentional opaque 409 mapping."""

    response = mock.Mock(
        headers={}, status_code=409, iter_content=mock.Mock(return_value=[])
    )
    with pytest.raises(RoomCloseRefused) as error:
        read_bounded_core_json(
            response, RoomCloseRefused, passthrough_statuses={404, 409}
        )
    assert error.value.status == 409
    response.close.assert_called_once_with()


def test_bounded_json_keeps_unexpected_close_status_unavailable():
    """Only the close boundary's explicit 404/409 statuses pass through."""

    response = mock.Mock(
        headers={}, status_code=403, iter_content=mock.Mock(return_value=[])
    )
    with pytest.raises(RoomCloseRefused) as error:
        read_bounded_core_json(
            response,
            RoomCloseRefused,
            passthrough_statuses={404, 409},
            client_error_status=None,
        )
    assert error.value.status == 503
    response.close.assert_called_once_with()


def test_bounded_json_requires_the_exact_response_envelope():
    """Host and guest clients reject extra response fields."""

    response = mock.Mock(
        headers={},
        status_code=200,
        iter_content=mock.Mock(
            return_value=[b'{"guest_grant":"a.b.c","unexpected":true}']
        ),
    )
    with pytest.raises(GuestHandoffRefused) as error:
        read_bounded_core_json(
            response, GuestHandoffRefused, expected_fields={"guest_grant"}
        )
    assert error.value.status == 503
    response.close.assert_called_once_with()


def test_bounded_json_preserves_transcription_callback_outcome():
    """Terminal Core transcription callbacks carry an explicit outcome."""

    response = mock.Mock(
        headers={},
        status_code=409,
        iter_content=mock.Mock(
            return_value=[
                b'{"code":"runtime_busy","request_id":"r1",'
                b'"message":"Runtime is busy","outcome":"failed"}'
            ]
        ),
    )
    with pytest.raises(TranscriptionContractRefused) as error:
        read_bounded_core_json(
            response,
            TranscriptionContractRefused,
            passthrough_statuses={404, 409, 503},
        )
    assert error.value.status == 409
    assert error.value.outcome == "failed"
    response.close.assert_called_once_with()
