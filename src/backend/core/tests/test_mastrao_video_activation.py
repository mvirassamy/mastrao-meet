"""Server roster proofs at the explicit host activation boundary."""

# Explicit pytest dependencies and private boundary assertions keep these tests local.
# pylint: disable=missing-function-docstring,redefined-outer-name,protected-access,no-member,too-many-arguments,too-many-positional-arguments

import json
import time
from types import SimpleNamespace
from unittest import mock

import pytest

from core import mastrao_recording_session as session
from core import mastrao_video_roster as roster
from core.mastrao_recording_contract import (
    ACTIVATION_JOSE_TYPE,
    ACTIVATION_TYPE,
    RecordingContractRefused,
    compact_digest,
    sign_activation_assertion,
)

# Pytest discovers imported fixtures; common provider-free setup is shared.
from core.tests.test_mastrao_video_roster import (  # pylint: disable=unused-import
    _claims,
    authority,
    core_reply,
    sfu,
    signer,
)


@pytest.fixture
def activation(authority, sfu, core_reply, settings):
    settings.MASTRAO_MEETING_RECORDING_ENABLED = True
    settings.MASTRAO_MEETING_RECORDING_START_ENABLED = True
    settings.MASTRAO_CORE_RECORDING_ACTIVATION_ENDPOINT = (
        "http://cabinet-core:8081/internal/v1/meetings/recording/activate"
    )
    issued = sfu[3].media_token_binding
    participant = {
        "kind": "host",
        "ref": issued.host_grant.identity.host_ref,
        "session_digest": issued.session_nonce_digest,
        "compact": "host.compact.grant",
    }
    status = {
        "organization_external_id": authority.organization_external_id,
        "meeting_ref": authority.meeting_ref,
        "room_ref": authority.room_ref,
        "recording_ref": authority.recording_ref,
        "mode": "recorded",
        "policy_ref": authority.policy_ref,
        "notice_version": authority.notice_version,
        "notice_digest": authority.notice_digest,
        "purpose": authority.purpose,
        "scope": authority.scope,
        "retention_expires_at": int(authority.retention_expires_at.timestamp()),
        "video": {"start_available": True},
    }
    response = {
        "version": 1,
        "matter_ref": "matter_0123456789abcdef",
        "state_version": 1,
        "state": "authorized",
        **{
            name: value
            for name, value in status.items()
            if name not in {"organization_external_id", "video"}
        },
    }
    core_reply[1].iter_content.return_value = [json.dumps(response).encode()]
    room = SimpleNamespace(
        id=authority.room_binding.room_id, mastrao_binding=authority.room_binding
    )
    with (
        mock.patch.object(session, "_participant", return_value=participant),
        mock.patch.object(session, "recording_session_status", return_value=status),
    ):
        yield room, participant, status, response


def test_activation_uses_server_snapshot_before_core_and_ignores_browser_roster(
    activation, signer, sfu, core_reply, settings
):
    room, host, status, response = activation
    calls = []
    sfu[0].room.list_participants.side_effect = lambda _: (
        calls.append("server_roster") or sfu[0].room.list_participants.return_value
    )
    core_reply[0].post.side_effect = lambda *_args, **_kwargs: (
        calls.append("core") or core_reply[1]
    )
    request = SimpleNamespace(
        data={
            "observed_at": int(time.time()),
            "participants": [
                {"participant_kind": "host", "participant_ref": "browser_forged"}
            ],
        }
    )
    assert (
        session.activate_recording(request, room, "activation_0123456789abcdef")
        == response
    )
    assert calls == ["server_roster", "core"]
    kwargs = core_reply[0].post.call_args.kwargs
    assert (
        core_reply[0].post.call_args.args[0]
        == settings.MASTRAO_CORE_RECORDING_ACTIVATION_ENDPOINT
    )
    assert set(kwargs["json"]) == {"host_grant", "activation_assertion"}
    assert kwargs["json"]["host_grant"] == host["compact"]
    header, payload = _claims(kwargs["json"]["activation_assertion"], signer)
    assert header["typ"] == ACTIVATION_JOSE_TYPE
    assert payload["type"] == ACTIVATION_TYPE
    assert set(payload) == {
        "version",
        "type",
        "issuer",
        "audience",
        "operation",
        "operation_version",
        "activation_request_id",
        "organization_external_id",
        "meeting_ref",
        "room_ref",
        "recording_ref",
        "provider_binding_digest",
        "host_ref",
        "host_session_digest",
        "host_grant_digest",
        "issued_at",
        "expires_at",
        "jti",
        "observed_at",
        "participants",
    }
    assert payload["meeting_ref"] == status["meeting_ref"]
    assert payload["host_ref"] == host["ref"]
    assert payload["host_grant_digest"] == compact_digest(host["compact"])
    assert 0 <= payload["issued_at"] - payload["observed_at"] <= 10
    assert payload["participants"] == [
        {
            "participant_kind": "host",
            "participant_ref": host["ref"],
            "grant_ref": sfu[3].media_token_binding.host_grant.grant_ref,
            "participant_session_digest": host["session_digest"],
            "participant_grant_digest": sfu[3].media_token_binding.grant_digest,
        }
    ]
    sfu[0].room.list_participants.assert_awaited_once()


@pytest.mark.parametrize(
    "gate", ["guest", "disabled", "start_disabled", "start_unavailable"]
)
def test_activation_preserves_host_and_rollout_gates(
    activation, sfu, core_reply, settings, gate
):
    room, participant, status, _ = activation
    if gate == "guest":
        participant["kind"] = "guest"
    elif gate == "disabled":
        settings.MASTRAO_MEETING_RECORDING_ENABLED = False
    elif gate == "start_disabled":
        settings.MASTRAO_MEETING_RECORDING_START_ENABLED = False
    else:
        status["video"]["start_available"] = False
    with pytest.raises(RecordingContractRefused):
        session.activate_recording(mock.Mock(), room, "activation_0123456789abcdef")
    sfu[0].room.list_rooms.assert_not_awaited()
    core_reply[0].post.assert_not_called()


def test_activation_unknown_participant_never_reaches_core(activation, sfu, core_reply):
    sfu[1].return_value.select_related.return_value = []
    with pytest.raises(RecordingContractRefused):
        session.activate_recording(
            mock.Mock(), activation[0], "activation_0123456789abcdef"
        )
    core_reply[0].post.assert_not_called()


@pytest.fixture
def activation_payload(activation):
    room, host, status, _ = activation
    return {
        **session._base_assertion(ACTIVATION_TYPE, "activate_meeting_recording"),
        **roster.snapshot_video_roster(room.mastrao_binding),
        "activation_request_id": "activation_0123456789abcdef",
        **{
            name: status[name]
            for name in (
                "organization_external_id",
                "meeting_ref",
                "room_ref",
                "recording_ref",
            )
        },
        "provider_binding_digest": room.mastrao_binding.provider_binding_digest,
        "host_ref": host["ref"],
        "host_session_digest": host["session_digest"],
        "host_grant_digest": compact_digest(host["compact"]),
    }


@pytest.mark.parametrize(
    "field,value",
    [
        ("observed_at", None),
        ("observed_at", True),
        ("observed_at", 0),
        ("observed_at", "123"),
        ("participants", None),
        ("participants", {}),
        ("participants", []),
    ],
)
@pytest.mark.usefixtures("signer")
def test_activation_signer_requires_snapshot_fields(activation_payload, field, value):
    activation_payload[field] = value
    with pytest.raises(RecordingContractRefused):
        sign_activation_assertion(activation_payload)


@pytest.mark.parametrize("field", ["observed_at", "participants"])
@pytest.mark.usefixtures("signer")
def test_activation_signer_rejects_missing_snapshot_field(activation_payload, field):
    activation_payload.pop(field)
    with pytest.raises(RecordingContractRefused):
        sign_activation_assertion(activation_payload)


@pytest.mark.parametrize("age", [-1, 11])
@pytest.mark.usefixtures("signer")
def test_activation_signer_rejects_stale_or_future_snapshot(activation_payload, age):
    activation_payload["observed_at"] = activation_payload["issued_at"] - age
    with pytest.raises(RecordingContractRefused):
        sign_activation_assertion(activation_payload)


@pytest.mark.parametrize(
    "change", ["duplicate", "over100", "extra", "wrong_kind", "bad_ref", "bad_digest"]
)
@pytest.mark.usefixtures("signer")
def test_activation_signer_rejects_ambiguous_or_noncontract_participants(
    activation_payload, change
):
    participant = activation_payload["participants"][0]
    if change == "duplicate":
        activation_payload["participants"].append(dict(participant))
    elif change == "over100":
        activation_payload["participants"] = [dict(participant)] * 101
    elif change == "extra":
        participant["grant_payload_digest"] = "a" * 64
    elif change == "wrong_kind":
        participant["participant_kind"] = "recorder"
    elif change == "bad_ref":
        participant["grant_ref"] = "invalid reference"
    else:
        participant["participant_grant_digest"] = "not-a-sha256"
    with pytest.raises(RecordingContractRefused):
        sign_activation_assertion(activation_payload)
