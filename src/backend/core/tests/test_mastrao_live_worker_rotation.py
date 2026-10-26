"""Authenticated connection handoff boundaries; no provider or Core side effect."""

# Imported fixtures provide real persisted RTC/token provenance.
# pylint: disable=unused-import,redefined-outer-name,unused-argument

import json
from unittest import mock

from django.core import signing

import jwt
import pytest
from asgiref.sync import async_to_sync

from core import models
from core.mastrao_live_worker_control import CAPABILITY_SALT
from core.services.subtitle_reconciliation import (
    SubtitleReconciliationAmbiguous,
    _dispatch_metadata_json,
    _reconcile_provider_unlocked,
)
from core.tests.services.test_subtitle_reconciliation import mock_livekit_client
from core.tests.test_mastrao_live_worker_control import (
    command,
    core_send,
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

pytestmark = pytest.mark.django_db


def rotation(gateway, **changes):
    """Close one exact leg at a cumulative PCM boundary and name its successor."""
    return send_body(
        gateway,
        "rotate",
        **{
            "next_leg_ref": "live_leg_successor0123456789",
            "audio_end_sample": 12_960_000,
            **changes,
        },
    )


@pytest.mark.parametrize("replayed", [False, True])
def test_rotation_preserves_signed_boundary_and_single_use_start(
    client, gateway, replayed
):
    """The gateway cannot convert a rotation replay into another opening."""
    body = rotation(gateway)
    result = core_send(body, start=not replayed)
    result["replayed"] = replayed
    with mock.patch(
        "core.mastrao_live_worker_control.post_core_json", return_value=result
    ) as post:
        response = invoke(client, gateway, body)
    assert response.status_code == 200
    assert response.json() == result
    claims = jwt.decode(
        post.call_args.kwargs["body"]["request"],
        gateway["key"],
        algorithms=["EdDSA"],
        options={"verify_aud": False},
    )
    assert all(claims[name] == value for name, value in body.items())
    assert result["leg_ref"] == body["next_leg_ref"]
    assert result["audio_start_sample"] == body["audio_end_sample"]
    assert "consent_ref" not in claims
    assert "participant_grant" not in claims


@pytest.mark.parametrize(
    "changes",
    [
        {"leg_ref": "short"},
        {"leg_ref": True},
        {"next_leg_ref": "short"},
        {"next_leg_ref": []},
        {"audio_end_sample": True},
        {"audio_end_sample": -1},
        {"audio_end_sample": 2**53},
        {"audio_end_sample": 24000.0},
        {"version": 1},
        {"previous_leg_closed": True},
    ],
)
def test_malformed_rotation_never_reaches_core(client, gateway, changes):
    """Do not coerce positions or accept a participant-supplied closure flag."""
    with mock.patch("core.mastrao_live_worker_control.post_core_json") as post:
        assert invoke(client, gateway, rotation(gateway, **changes)).status_code == 400
        post.assert_not_called()


@pytest.mark.parametrize("missing", ["leg_ref", "next_leg_ref", "audio_end_sample"])
def test_rotation_requires_complete_handoff_identity(client, gateway, missing):
    """An underspecified transition cannot authorize another provider connection."""
    body = rotation(gateway)
    del body[missing]
    with mock.patch("core.mastrao_live_worker_control.post_core_json") as post:
        assert invoke(client, gateway, body).status_code == 400
        post.assert_not_called()


def test_rotation_cannot_reuse_the_current_leg_identity(client, gateway):
    """The caller must name a different, separately recorded provider invocation."""
    body = rotation(gateway)
    body["next_leg_ref"] = body["leg_ref"]
    with mock.patch("core.mastrao_live_worker_control.post_core_json") as post:
        assert invoke(client, gateway, body).status_code == 400
        post.assert_not_called()


@pytest.mark.parametrize("action", ["prepare", "claim", "renew", "stopped"])
@pytest.mark.parametrize(
    "extra", [{"next_leg_ref": "live_leg_extra0123456789"}, {"audio_end_sample": 0}]
)
def test_only_rotate_accepts_handoff_fields(client, gateway, action, extra):
    """Claim/renew/cleanup cannot smuggle a new connection through their schema."""
    body = command() if action == "prepare" else send_body(gateway, action)
    with mock.patch("core.mastrao_live_worker_control.post_core_json") as post:
        assert invoke(client, gateway, {**body, **extra}).status_code == 400
        post.assert_not_called()


@pytest.mark.parametrize(
    "changes",
    [
        {"leg_ref": "live_leg_wrong_successor0123456789"},
        {"audio_start_sample": 0},
        {"audio_start_sample": True},
        {"audio_start_sample": 12_960_001},
        {"start_authorized": True, "replayed": True},
        {"version": 1},
    ],
)
def test_incoherent_core_rotation_is_opaque_and_cannot_reach_worker(
    client, gateway, changes
):
    """Accept only the exact successor and PCM boundary requested by this worker."""
    body = rotation(gateway)
    with mock.patch(
        "core.mastrao_live_worker_control.post_core_json",
        return_value={**core_send(body, start=True), **changes},
    ):
        response = invoke(client, gateway, body)
    assert response.status_code == 503
    assert response.json() == {"message": "Unavailable"}


@pytest.mark.parametrize("revocation", ["off", "epoch_ended", "host_inactive"])
def test_rotation_rechecks_local_authority_after_core_io(
    client, gateway, host, revocation
):
    """A valid Core receipt cannot outlive a local RTC/control revocation."""
    body = rotation(gateway)

    def revoke(**_kwargs):
        if revocation == "off":
            models.RoomSubtitleControl.objects.update(desired_state="OFF")
        elif revocation == "epoch_ended":
            models.MastraoRtcTrackEpoch.objects.update(ended=True)
        else:
            models.User.objects.filter(pk=host.identity.user_id).update(is_active=False)
        return core_send(body, start=True)

    with mock.patch(
        "core.mastrao_live_worker_control.post_core_json", side_effect=revoke
    ):
        assert invoke(client, gateway, body).status_code == 404


def test_old_signed_capability_cannot_use_new_control_protocol(client, gateway):
    """A v1 scope is refused even if it was re-signed with the current salt."""
    scope = signing.loads(gateway["metadata"]["capability"], salt=CAPABILITY_SALT)
    scope["version"] = 1
    with mock.patch("core.mastrao_live_worker_control.post_core_json") as post:
        response = invoke(
            client,
            gateway,
            command(),
            capability=signing.dumps(scope, salt=CAPABILITY_SALT),
        )
    assert response.status_code == 404
    post.assert_not_called()


@pytest.mark.django_db(transaction=True)
def test_old_controlled_dispatch_cannot_be_adopted_after_protocol_change(
    gateway, mock_livekit_client
):
    """A future rollout must drain v1 workers instead of treating them as v2."""
    old = {**gateway["metadata"], "version": 1}
    dispatch = mock.Mock(
        id="AD_old_protocol",
        agent_name="multi-user-transcriber",
        metadata=_dispatch_metadata_json("RM_fixture", 3, "controlled-live", old),
    )
    mock_livekit_client.agent_dispatch.list_dispatch.return_value = [dispatch]
    with pytest.raises(SubtitleReconciliationAmbiguous):
        async_to_sync(_reconcile_provider_unlocked)(
            str(gateway["control"].room_id), "RM_fixture", "ON", 3, "controlled-live"
        )
    mock_livekit_client.agent_dispatch.create_dispatch.assert_not_called()
