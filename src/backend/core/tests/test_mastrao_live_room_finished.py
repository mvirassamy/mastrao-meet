"""A verified physical-room close fences audio while preserving exact cleanup."""

# Imported fixtures reuse the private gateway and actual media token issuance.
# pylint: disable=missing-function-docstring,redefined-outer-name,unused-import
# pylint: disable=no-member

import base64
import hashlib
import json
from unittest import mock

import jwt
import pytest
from livekit import api

from core import models
from core.mastrao_rtc_observations import record_verified_rtc_observation
from core.tests.test_mastrao_live_worker_control import (
    binding,
    command,
    core_admission,
    core_send,
    gateway,
    host,
    invoke,
    isolated_binding_settings,
    receipt,
    send_body,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def verified_room_finished(settings, receipt):
    """Verify the signed room-only body without running the HTTP OFF handler."""
    body = json.dumps(
        {
            "id": "EV_worker_room_finished_fixture",
            "event": "room_finished",
            "createdAt": str(int(receipt.issued_at.timestamp()) + 1),
            "room": {
                "sid": "RM_fixture",
                "name": str(receipt.room_binding.room_id),
            },
        }
    )
    configuration = settings.LIVEKIT_CONFIGURATION
    signer = api.AccessToken(configuration["api_key"], configuration["api_secret"])
    signer.claims.sha256 = base64.b64encode(
        hashlib.sha256(body.encode()).digest()
    ).decode()
    receiver = api.WebhookReceiver(
        api.TokenVerifier(configuration["api_key"], configuration["api_secret"])
    )
    event = receiver.receive(body, signer.to_jwt())
    assert isinstance(event, api.WebhookEvent)
    return event, body.encode()


@pytest.mark.parametrize("action", ["prepare", "claim", "renew", "rotate"])
def test_ingested_room_finished_refuses_audio_with_control_still_active(
    client, gateway, verified_room_finished, action
):
    body = command() if action == "prepare" else send_body(gateway, action)
    result = core_admission()
    if action != "prepare":
        if action == "rotate":
            body.update(
                next_leg_ref="live_leg_successor0123456789", audio_end_sample=24000
            )
        result = core_send(body, start=action in {"claim", "rotate"})
    with mock.patch(
        "core.mastrao_live_worker_control.post_core_json", return_value=result
    ) as post:
        assert invoke(client, gateway, body).status_code == 200
        post.assert_called_once()

    event, verified_body = verified_room_finished
    record_verified_rtc_observation(event, verified_body)

    observation = models.MastraoRtcObservation.objects.get(event_id=event.id)
    assert observation.event_type == "room_finished"
    assert observation.payload_digest == hashlib.sha256(verified_body).hexdigest()
    epoch = models.MastraoRtcTrackEpoch.objects.select_related("connection").get(
        pk=gateway["epoch"].pk
    )
    assert epoch.ended
    assert epoch.connection.ended
    assert not epoch.conflict
    assert epoch.connection.correlation == "correlated"
    control = gateway["control"]
    control.refresh_from_db()
    assert control.is_current
    assert control.desired_state == "ON"
    assert control.provider == "openai-live"
    assert control.control_generation == 3
    assert control.room_finished_at is None
    epoch.connection.room_binding.refresh_from_db()
    assert epoch.connection.room_binding.closing_at is None

    with mock.patch("core.mastrao_live_worker_control.post_core_json") as post:
        assert invoke(client, gateway, body).status_code == 404
        post.assert_not_called()


def test_ingested_room_finished_preserves_exact_stopped_cleanup(
    client, gateway, verified_room_finished
):
    event, verified_body = verified_room_finished
    record_verified_rtc_observation(event, verified_body)
    epoch = models.MastraoRtcTrackEpoch.objects.select_related("connection").get(
        pk=gateway["epoch"].pk
    )
    assert epoch.ended
    assert epoch.connection.ended
    body = send_body(gateway, "stopped")
    result = core_send(body, state="stopped")
    with mock.patch(
        "core.mastrao_live_worker_control.post_core_json", return_value=result
    ) as post:
        response = invoke(client, gateway, body)
        post.assert_called_once()
    assert response.status_code == 200
    assert response.json() == result
    assert not result["start_authorized"]
    assert not result["send_authorized"]
    claims = jwt.decode(
        post.call_args.kwargs["body"]["request"],
        gateway["key"],
        algorithms=["EdDSA"],
        options={"verify_aud": False},
    )
    assert all(claims[name] == body[name] for name in body)
    with mock.patch("core.mastrao_live_worker_control.post_core_json") as post:
        wrong_epoch = {**body, "admission_ref": "live_admission_wrong_epoch"}
        assert invoke(client, gateway, wrong_epoch).status_code == 404
        post.assert_not_called()
