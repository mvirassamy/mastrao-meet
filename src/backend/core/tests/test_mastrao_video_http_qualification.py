"""Opt-in Meet/Core HTTP qualification against the existing isolated Core fixture.

The fixture precreates the meeting, grants and ACL. SMTP is captured locally;
SFU observations and the provider are doubles. Activation/effect setup below is
explicit SQL, not a claim of Platform bootstrap or actual capture qualification.
"""

# One ordered HTTP scenario keeps the decision fence chronology reviewable.
# pylint: disable=too-many-locals,too-many-statements
# Shared fixtures expose the existing provider boundary without a second harness.
# pylint: disable=unused-import,missing-function-docstring,redefined-outer-name,protected-access,no-member

import json
import os
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

from django.core import mail

import psycopg
import pytest
import requests

from core import models
from core.mastrao_recording_adapter import _apply_start
from core.mastrao_recording_contract import RecordingContractRefused, compact_digest
from core.mastrao_video_invitations import deliver_meeting_invitations
from core.mastrao_video_participant import bind_video_invitation
from core.tests.test_mastrao_video_roster import authority, provider_boundary


@pytest.fixture
def core_http(settings):
    path = os.environ.get("MASTRAO_VIDEO_QUALIFICATION_CONFIG")
    if not path:
        pytest.skip("Requires the existing isolated Core HTTP fixture")
    config = json.loads(Path(path).read_text(encoding="utf-8"))
    for suffix, key in (
        ("PRIVATE_JWK", "meet_receipt_private_jwk"),
        ("KEY_ID", "meet_receipt_key_id"),
        ("ISSUER", "meet_receipt_issuer"),
        ("AUDIENCE", "meet_receipt_audience"),
    ):
        value = config[key]
        if isinstance(value, dict):
            value = json.dumps(value)
        setattr(settings, f"MASTRAO_RECORDING_RECEIPT_{suffix}", value)
    base = config["core_base_url"]
    settings.MASTRAO_CORE_RECORDING_SESSION_STATUS_ENDPOINT = (
        f"{base}/internal/v1/meetings/recording/session-status"
    )
    settings.MASTRAO_CORE_VIDEO_DELIVERY_ENDPOINT = (
        f"{base}/internal/v1/meetings/video-invitations/delivery"
    )
    settings.MASTRAO_CORE_VIDEO_PARTICIPANT_ENDPOINT = (
        f"{base}/internal/v1/meetings/video-invitations/participant"
    )
    settings.MASTRAO_CORE_RECORDING_TIMEOUT_SECONDS = 3
    settings.MASTRAO_PLATFORM_API_BASE_URL = "https://app.example.test"
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    settings.EMAIL_FROM = "host@example.test"
    return config


def _choice(config, token, decision=None):
    body = {"token": token}
    if decision:
        body["decision"] = decision
    with requests.Session() as session:
        session.trust_env = False
        return session.post(
            f"{config['core_base_url']}/internal/v1/meetings/video-choice",
            json=body,
            timeout=3,
        )


def _roster(config):
    participants = []
    for kind in ("host", "guest"):
        grant = config[kind]
        participants.append(
            {
                "participant_kind": kind,
                "participant_ref": grant[f"{kind}_ref"],
                "grant_ref": grant["grant_ref"],
                "participant_session_digest": config["participant_session_digest"],
                "participant_grant_digest": compact_digest(config[f"{kind}_grant"]),
            }
        )
    return participants


def _prepare_core_start(config, participants, effect):
    # This setup mirrors prepareVideoStart in the existing Core fixture. The
    # subsequent fence and invitation changes go through real signed HTTP.
    with psycopg.connect(
        host=config["postgres_socket"],
        port=config["postgres_port"],
        dbname=config["postgres_database"],
        user=config["postgres_fixture_owner"],
    ) as connection:
        connection.execute(
            "UPDATE native_fixture.meeting_recordings SET state='starting',"
            "video_activation_roster=%s::jsonb",
            (json.dumps({"participants": participants}),),
        )
        connection.execute("DELETE FROM native_fixture.meeting_recording_effects")
        connection.execute(
            "INSERT INTO native_fixture.meeting_recording_effects "
            "VALUES(%s,%s,%s,%s,%s,'start','claimed',1,"
            "clock_timestamp()+interval '1 minute')",
            (
                config["cabinet_id"],
                config["meeting_id"],
                str(uuid4()),
                effect["effect_key"],
                effect["claim_id"],
            ),
        )


def test_real_http_delivery_choices_binding_and_provider_fence(
    authority, provider_boundary, core_http
):
    config = core_http
    recording, local_effect, worker, _ = provider_boundary
    for name in (
        "organization_external_id",
        "meeting_ref",
        "room_ref",
        "recording_ref",
    ):
        setattr(authority, name, config[name])
    binding = authority.room_binding
    binding.meeting_ref = config["meeting_ref"]
    binding.room_ref = config["room_ref"]
    binding.room_id = recording.room_id
    binding._state.fields_cache["room"] = SimpleNamespace(id=recording.room_id)
    binding.provider_binding_digest = config["host"]["provider_binding_digest"]
    host = {"kind": "host", "claims": config["host"]}
    invitation = {**config["invitations"][0], "delivery_state": "pending"}
    request = SimpleNamespace(
        user=SimpleNamespace(email="host@example.test"), session={}
    )
    schedule = {
        "title": "Local HTTP qualification",
        "scheduled_start_at": int(time.time()) + 3600,
        "scheduled_end_at": int(time.time()) + 7200,
        "timezone": "Europe/Paris",
    }
    room_url = "https://meet.example.test/guest#organization=fixture&share=fixture"
    with (
        patch("core.mastrao_video_invitations._participant", return_value=host),
        patch(
            "core.mastrao_video_invitations.guest_invitation_url", return_value=room_url
        ),
    ):
        delivered = deliver_meeting_invitations(
            request, binding, [invitation], schedule
        )
        replay = deliver_meeting_invitations(request, binding, [invitation], schedule)
    assert delivered == replay == [{**invitation, "delivery_state": "sent"}]
    assert len(mail.outbox) == 1
    link = next(
        line.split(": ", 1)[1]
        for line in mail.outbox[0].body.splitlines()
        if "/meet/video-choice?" in line
    )
    token = parse_qs(urlparse(link).query)["token"][0]
    before = _choice(config, token)
    assert before.status_code == 200
    assert before.json()["decision"] == "absent"
    assert before.json()["decision_basis"] == "no_opposition"
    assert _choice(config, token, "accepted").status_code == 200
    assert _choice(config, token, "refused").status_code == 200
    request.session = {"mastrao_video_choices": {config["meeting_ref"]: token}}
    guest = {
        "kind": "guest",
        "compact": config["guest_grant"],
        "session_digest": config["participant_session_digest"],
    }
    admitted = SimpleNamespace(
        admission_state=models.MastraoGuestGrant.AdmissionState.ALLOWED,
        decision_confirmed_at=object(),
    )
    with patch(
        "core.mastrao_video_participant.active_guest_grant", return_value=admitted
    ):
        assert bind_video_invitation(
            request,
            binding.room,
            guest,
            {
                "meeting_ref": config["meeting_ref"],
                "mode": "recorded",
                "video": {"decision_lock": "open"},
            },
        )
    assert request.session["mastrao_video_choices"] == {}
    participants = _roster(config)
    effect = {
        **{
            name: config[name]
            for name in (
                "organization_external_id",
                "meeting_ref",
                "room_ref",
                "recording_ref",
            )
        },
        "provider_binding_digest": binding.provider_binding_digest,
        "effect_key": "videoeffect_meet_http_fixture",
        "claim_id": "videoclaim_meet_http_fixture",
        "resolve_only": False,
    }
    _prepare_core_start(config, participants, effect)
    with patch(
        "core.mastrao_video_roster.snapshot_video_roster",
        side_effect=lambda _: {
            "observed_at": int(time.time()),
            "participants": participants,
        },
    ):
        with pytest.raises(RecordingContractRefused) as refused:
            _apply_start(effect)
        assert refused.value.status == 409
        worker.start.assert_not_called()
        assert _choice(config, token).json()["decision_lock"] == "open"
        assert _choice(config, token, "accepted").status_code == 200
        _prepare_core_start(config, participants, effect)
        # Local persistence is doubled here, including rearming after a definite
        # refusal. Its real PostgreSQL behavior has separate focused tests.
        local_effect.state = models.MastraoRecordingEffect.State.APPLYING
        _apply_start(effect)
    worker.start.assert_called_once_with(recording)
    assert _choice(config, token).json()["decision_lock"] == "start_in_progress"
    assert _choice(config, token, "refused").status_code == 409
    assert _choice(config, token).json()["decision"] == "accepted"
