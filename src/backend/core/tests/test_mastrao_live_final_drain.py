"""Stopped control generations can save captured results, never send audio."""

# Imported fixtures exercise real token issuance, RTC signatures and PostgreSQL.
# pylint: disable=missing-function-docstring,redefined-outer-name,unused-import

import json
import time
from unittest import mock

from django.core import signing

import jwt
import pytest

from core.mastrao_live_worker_control import CAPABILITY_SALT, live_control_metadata
from core.mastrao_recording_contract import RecordingContractRefused
from core.services.subtitle_control import compare_and_set_subtitle_control
from core.services.subtitle_reconciliation import request_subtitle_stop
from core.tests.test_mastrao_live_room_finished import verified_room_finished
from core.tests.test_mastrao_live_worker_control import (
    binding,
    command,
    gateway,
    host,
    invoke,
    isolated_binding_settings,
    receipt,
    send_body,
)
from core.tests.test_mastrao_live_worker_delivery import delivery, receipt_for
from core.tests.test_mastrao_rtc_observations import _post

pytestmark = pytest.mark.django_db
POST = "core.mastrao_live_worker_delivery.post_core_json"


def stop_control(client, settings, gateway, verified_room_finished, reason):
    control = gateway["control"]
    if reason in {"manual", "manual_then_finished"}:
        request_subtitle_stop(control.room, room_sid=control.room_sid)
    if reason in {"finished", "manual_then_finished"}:
        settings.ROOM_TELEPHONY_ENABLED = False
        settings.ROOMKIT_ENABLED = False
        _, raw = verified_room_finished
        with (
            mock.patch("core.services.livekit_events.LobbyService.clear_room_cache"),
            mock.patch("core.services.livekit_events.schedule_subtitle_reconciliation"),
        ):
            response = _post(client, settings, json.loads(raw))
        assert response.status_code == 200, response.content
    control.refresh_from_db()
    assert control.desired_state == "OFF"
    assert control.control_generation > 3


@pytest.fixture
def stopped_control(client, settings, gateway, verified_room_finished):
    def stop(reason):
        stop_control(client, settings, gateway, verified_room_finished, reason)

    return stop


@pytest.mark.parametrize("reason", ["manual", "finished", "manual_then_finished"])
@pytest.mark.parametrize("kind", ["final", "gap", "end"])
def test_stopped_generation_saves_exact_captured_event(
    client, gateway, stopped_control, reason, kind
):
    body = delivery(gateway)
    if kind != "final":
        body["event"] = {
            key: body["event"][key]
            for key in ("version", "source_ref", "leg_ref", "sequence")
        }
        body["event"].update(kind=kind, reason="capture_incomplete")
        if kind == "end":
            body["event"]["final_event_count"] = 0
    result = receipt_for(body)
    if kind != "final":
        result["final_event_count"] = 0
    if kind == "end":
        result["state"] = "ended"
    stopped_control(reason)
    with mock.patch(POST, return_value=result) as post:
        response = invoke(client, gateway, body)
    assert response.status_code == 200
    assert response.json() == result
    sent = post.call_args.kwargs["body"]
    assert sent["event"] == body["event"]
    claims = jwt.decode(
        sent["request"],
        gateway["key"],
        algorithms=["EdDSA"],
        options={"verify_aud": False},
    )
    for key in (
        "source_ref",
        "attempt_ref",
        "worker_instance_ref",
        "leg_ref",
        "admission_ref",
        "room_sid",
        "participant_sid",
        "track_sid",
    ):
        assert claims[key] == body[key]
    assert "send_authorized" not in claims
    assert result["meeting_coverage"] == "unverified"
    assert result["incomplete"] is True


@pytest.mark.parametrize("action", ["prepare", "claim", "renew", "rotate"])
def test_draining_generation_never_reopens_audio(client, gateway, action):
    control = gateway["control"]
    request_subtitle_stop(control.room, room_sid=control.room_sid)
    body = command() if action == "prepare" else send_body(gateway, action)
    if action == "rotate":
        body.update(next_leg_ref="live_leg_successor0123456789", audio_end_sample=24000)
    with mock.patch("core.mastrao_live_worker_control.post_core_json") as post:
        assert invoke(client, gateway, body).status_code == 404
    post.assert_not_called()


def test_new_on_invalidates_old_drain_even_after_another_stop(client, gateway):
    control = gateway["control"]
    request_subtitle_stop(control.room, room_sid=control.room_sid)
    control.refresh_from_db()
    control = compare_and_set_subtitle_control(
        room_sid=control.room_sid,
        expected_control_generation=control.control_generation,
        expected_state_version=control.state_version,
        desired_state="ON",
        new_intent=True,
    )
    metadata = live_control_metadata(
        control.room_sid, control.control_generation, "openai-live"
    )
    body = delivery(gateway)
    with mock.patch(POST, return_value=receipt_for(body)) as post:
        assert invoke(client, gateway, body).status_code == 404
        post.assert_not_called()
        assert (
            invoke(client, gateway, body, capability=metadata["capability"]).status_code
            == 200
        )
    request_subtitle_stop(control.room, room_sid=control.room_sid)
    with mock.patch(POST, return_value=receipt_for(body)) as post:
        assert invoke(client, gateway, body).status_code == 404
        post.assert_not_called()
        assert (
            invoke(client, gateway, body, capability=metadata["capability"]).status_code
            == 200
        )


@pytest.mark.parametrize(
    "fault", ["expired", "tampered", "generation", "room_sid", "binding_id"]
)
def test_drain_does_not_expand_machine_capability(client, gateway, fault):
    control = gateway["control"]
    request_subtitle_stop(control.room, room_sid=control.room_sid)
    token = gateway["metadata"]["capability"]
    scope = signing.loads(token, salt=CAPABILITY_SALT)
    if fault == "expired":
        scope.update(
            issued_at=int(time.time()) - 7200, expires_at=int(time.time()) - 3600
        )
    elif fault == "generation":
        scope["generation"] = 1
    elif fault == "room_sid":
        scope["room_sid"] = "RM_other"
    elif fault == "binding_id":
        scope["binding_id"] = "00000000-0000-0000-0000-000000000001"
    token = signing.dumps(scope, salt=CAPABILITY_SALT)
    if fault == "tampered":
        token += "invalid"
    with mock.patch(POST) as post:
        assert (
            invoke(client, gateway, delivery(gateway), capability=token).status_code
            == 404
        )
    post.assert_not_called()


@pytest.mark.parametrize("field", ["admission_ref", "participant_sid", "track_sid"])
def test_drain_still_requires_exact_rtc_epoch(client, gateway, field):
    control = gateway["control"]
    request_subtitle_stop(control.room, room_sid=control.room_sid)
    body = delivery(gateway)
    body[field] = {
        "admission_ref": "live_admission_other",
        "participant_sid": "PA_other",
        "track_sid": "TR_other",
    }[field]
    with mock.patch(POST) as post:
        assert invoke(client, gateway, body).status_code == 404
    post.assert_not_called()


@pytest.mark.parametrize("status", [404, 409, 503])
def test_core_remains_delivery_authority_without_retry(client, gateway, status):
    control = gateway["control"]
    request_subtitle_stop(control.room, room_sid=control.room_sid)
    with mock.patch(POST, side_effect=RecordingContractRefused(status=status)) as post:
        assert invoke(client, gateway, delivery(gateway)).status_code == status
    post.assert_called_once()
