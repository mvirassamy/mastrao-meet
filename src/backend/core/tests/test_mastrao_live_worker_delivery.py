"""Exact durable finals from authenticated RTC provenance, without providers."""

# Persisted token and webhook fixtures are intentionally shared.
# pylint: disable=unused-import,redefined-outer-name,unused-argument
# pylint: disable=missing-function-docstring

import hashlib
import json
from unittest import mock

import jwt
import pytest

from core.mastrao_live_delivery_contract import (
    DELIVERY_JOSE,
    DELIVERY_PATH,
    DELIVERY_TYPE,
    canonical_delivery_event,
)
from core.tests.test_mastrao_live_worker_control import (
    gateway,
    invoke,
    send_body,
)
from core.tests.test_mastrao_media_token_binding import (
    binding,
    guest,
    host,
    isolated_binding_settings,
)
from core.tests.test_mastrao_rtc_correlation import receipt

from meet.settings import scrub_mastrao_handoff_credentials

pytestmark = pytest.mark.django_db
POST = "core.mastrao_live_worker_delivery.post_core_json"


def delivery(gateway, **changes):
    body = send_body(gateway, "deliver")
    body["event"] = {
        "version": 2,
        "source_ref": body["source_ref"],
        "leg_ref": body["leg_ref"],
        "sequence": 1,
        "kind": "final",
        "item_ref": "provider_item_1",
        "revision": 1,
        "text": "  De\u0301cision : 250 € 🧪.\n",
        "language": "fr",
        "timing": {"state": "unavailable"},
        **changes,
    }
    return body


def receipt_for(body):
    event, digest = canonical_delivery_event(body["event"], body)
    return {
        "version": 2,
        "source_ref": body["source_ref"],
        "leg_ref": body["leg_ref"],
        "sequence": event["sequence"],
        "event_digest": digest,
        "state": "collecting",
        "confirmed_sequence": event["sequence"],
        "final_event_count": 1,
        "incomplete": True,
        "meeting_coverage": "unverified",
    }


def test_signs_owner_and_exact_non_normalized_event_bytes(client, gateway, host):
    body = delivery(gateway)
    result = receipt_for(body)
    # Input key order cannot change the canonical digest or forwarded bytes.
    body["event"] = dict(reversed(list(body["event"].items())))
    with mock.patch(POST, return_value=result) as post:
        response = invoke(client, gateway, body)
    assert response.status_code == 200
    assert response.json() == result
    sent = post.call_args.kwargs
    assert sent["expected_path"] == DELIVERY_PATH
    token = sent["body"]["request"]
    assert jwt.get_unverified_header(token)["typ"] == DELIVERY_JOSE
    claims = jwt.decode(
        token,
        gateway["key"],
        algorithms=["EdDSA"],
        options={"verify_aud": False},
    )
    assert claims["version"] == 2
    assert claims["type"] == DELIVERY_TYPE
    assert claims["organization_external_id"] == host.organization_external_id
    assert all(
        claims[name] == body[name]
        for name in (
            "room_sid",
            "participant_sid",
            "track_sid",
            "source_ref",
            "admission_ref",
            "attempt_ref",
            "worker_instance_ref",
            "leg_ref",
        )
    )
    assert "text" not in claims and "event" not in claims and "action" not in claims
    event = sent["body"]["event"]
    raw = json.dumps(event, ensure_ascii=False, separators=(",", ":")).encode()
    assert claims["event_digest"] == hashlib.sha256(raw).hexdigest()
    assert event["text"] == body["event"]["text"]
    assert list(event) == [
        "version",
        "source_ref",
        "leg_ref",
        "sequence",
        "kind",
        "item_ref",
        "revision",
        "text",
        "language",
        "timing",
    ]


@pytest.mark.parametrize(
    "changes",
    [
        {"kind": "interim"},
        {"version": 1},
        {"version": True},
        {"source_ref": "live_source_other0123456789"},
        {"leg_ref": "live_leg_other0123456789"},
        {"sequence": True},
        {"sequence": 0},
        {"sequence": 100001},
        {"revision": True},
        {"revision": 2**53},
        {"revision": 1.0},
        {"item_ref": "unsafe/id"},
        {"text": "  \n"},
        {"text": "a" * 4001},
        {"text": "🧪" * 2001},
        {"text": "\ud800"},
        {"language": "French"},
        {
            "timing": {
                "state": "audio_relative",
                "stream_origin_ms": 0,
                "start_ms": 0,
                "end_ms": 1000,
            }
        },
        {"timing": {"state": "unavailable", "receivedAt": 1234}},
        {"provider_credential": "synthetic-forbidden"},
    ],
)
def test_invalid_or_invented_event_never_reaches_core(client, gateway, changes):
    with mock.patch(POST) as post:
        assert invoke(client, gateway, delivery(gateway, **changes)).status_code == 400
        post.assert_not_called()


@pytest.mark.parametrize(
    "changes",
    [
        {"version": 1},
        {"version": True},
        {"sequence": True},
        {"sequence": 2},
        {"event_digest": "0" * 64},
        {"confirmed_sequence": 0},
        {"confirmed_sequence": True},
        {"final_event_count": True},
        {"final_event_count": 0},
        {"final_event_count": 2},
        {"incomplete": False},
        {"meeting_coverage": "complete"},
        {"state": "ended"},
        {"leg_ref": "live_leg_other0123456789"},
        {"send_authorized": True},
    ],
)
def test_wrong_receipt_is_opaque_and_never_reaches_worker(client, gateway, changes):
    body = delivery(gateway)
    with mock.patch(POST, return_value={**receipt_for(body), **changes}):
        response = invoke(client, gateway, body)
    assert response.status_code == 503
    assert response.json() == {"message": "Unavailable"}
    assert response.headers["Cache-Control"] == "no-store"


def test_closed_rtc_epoch_can_deliver_captured_finals_without_new_egress(
    client, gateway
):
    epoch = gateway["epoch"]
    epoch.ended = True
    epoch.save(update_fields=["ended"])
    body = delivery(gateway)
    with mock.patch(POST, return_value=receipt_for(body)):
        assert invoke(client, gateway, body).status_code == 200


def test_multibyte_final_above_old_control_body_limit_is_delivered(client, gateway):
    body = delivery(gateway, text="🧪" * 2000)
    assert len(json.dumps(body).encode()) > 8192
    with mock.patch(POST, return_value=receipt_for(body)):
        assert invoke(client, gateway, body).status_code == 200


@pytest.mark.parametrize("kind", ["gap", "end"])
def test_gap_and_terminal_count_receive_exact_durable_ack(client, gateway, kind):
    body = delivery(gateway)
    event = {
        name: body["event"][name]
        for name in (
            "version",
            "source_ref",
            "leg_ref",
            "sequence",
        )
    }
    event.update(kind=kind, reason="capture_incomplete")
    if kind == "end":
        event["final_event_count"] = 0
    body["event"] = event
    result = receipt_for(body)
    result["final_event_count"] = 0
    result["state"] = "ended" if kind == "end" else "collecting"
    with mock.patch(POST, return_value=result):
        assert invoke(client, gateway, body).status_code == 200


@pytest.mark.parametrize("reason", ["track_unpublished", "meeting_ended"])
def test_normal_end_cannot_clear_a_prior_final(client, gateway, reason):
    body = delivery(gateway)
    body["event"] = {
        "version": 2,
        "source_ref": body["source_ref"],
        "leg_ref": body["leg_ref"],
        "sequence": 2,
        "kind": "end",
        "reason": reason,
        "final_event_count": 1,
    }
    result = receipt_for(body)
    result.update(state="ended", final_event_count=1, incomplete=False)
    with mock.patch(POST, return_value=result):
        assert invoke(client, gateway, body).status_code == 503


@pytest.mark.parametrize("sequence", [1, 2, 9])
def test_gap_receipt_cannot_count_the_gap_as_a_final(client, gateway, sequence):
    body = delivery(gateway)
    body["event"] = {
        "version": 2,
        "source_ref": body["source_ref"],
        "leg_ref": body["leg_ref"],
        "sequence": sequence,
        "kind": "gap",
        "reason": "capture_incomplete",
    }
    result = receipt_for(body)
    result["final_event_count"] = sequence
    with mock.patch(POST, return_value=result):
        assert invoke(client, gateway, body).status_code == 503


@pytest.mark.parametrize("reason", ["track_unpublished", "meeting_ended"])
@pytest.mark.parametrize("sequence", [1, 2, 9])
def test_empty_normal_end_preserves_valid_non_incomplete_receipt(
    client, gateway, reason, sequence
):
    body = delivery(gateway)
    body["event"] = {
        "version": 2,
        "source_ref": body["source_ref"],
        "leg_ref": body["leg_ref"],
        "sequence": sequence,
        "kind": "end",
        "reason": reason,
        "final_event_count": 0,
    }
    result = receipt_for(body)
    result.update(state="ended", final_event_count=0, incomplete=False)
    with mock.patch(POST, return_value=result):
        assert invoke(client, gateway, body).status_code == 200


def test_delivery_still_requires_room_scoped_machine_capability(client, gateway):
    with mock.patch(POST) as post:
        assert (
            invoke(client, gateway, delivery(gateway), capability="invalid").status_code
            == 404
        )
        post.assert_not_called()


def test_stale_control_generation_cannot_sign_delivery(client, gateway):
    control = gateway["control"]
    control.control_generation += 1
    control.save(update_fields=["control_generation"])
    with mock.patch(POST) as post:
        assert invoke(client, gateway, delivery(gateway)).status_code == 404
        post.assert_not_called()


def test_durable_text_is_filtered_from_captured_request(settings):
    event = {"request": {"data": {"action": "deliver", "event": {"text": "synthetic"}}}}
    assert (
        scrub_mastrao_handoff_credentials(event, None)["request"]["data"]["event"]
        == "[Filtered]"
    )


def test_signs_source_buffer_in_shared_field_order_without_claiming_meeting_time(
    client, gateway
):
    timing = {
        "state": "source_buffer",
        "sample_rate": 24000,
        "start_sample": 12960000,
        "end_sample": 12962400,
    }
    body = delivery(gateway, timing=dict(reversed(list(timing.items()))))
    result = receipt_for(body)
    with mock.patch(POST, return_value=result) as post:
        response = invoke(client, gateway, body)
    assert response.status_code == 200
    event = post.call_args.kwargs["body"]["event"]
    assert event["timing"] == timing
    assert list(event["timing"]) == list(timing)
    assert response.json()["incomplete"] is True
    assert response.json()["meeting_coverage"] == "unverified"
    assert canonical_delivery_event(event, body)[1] == result["event_digest"]


@pytest.mark.parametrize(
    "changes",
    [
        {"sample_rate": 48000},
        {"sample_rate": True},
        {"start_sample": -1},
        {"start_sample": True},
        {"end_sample": 0},
        {"end_sample": 1.5},
        {"end_sample": 2**53},
        {"end_sample": 540 * 24000 + 1},
        {"meeting_origin_ms": 100},
        {"speech_start_ms": 0},
        {"state": "meeting_relative_ms"},
    ],
)
def test_invalid_source_buffer_never_reaches_core(client, gateway, changes):
    timing = {
        "state": "source_buffer",
        "sample_rate": 24000,
        "start_sample": 0,
        "end_sample": 2400,
        **changes,
    }
    with mock.patch(POST) as post:
        response = invoke(client, gateway, delivery(gateway, timing=timing))
    assert response.status_code == 400
    post.assert_not_called()
