"""Real cross-language authority contract; synthetic consent and Core SQL peer.

Meet issuance, signed RTC intake, HTTP native Start and PostgreSQL are real.
Core service/signatures are real; its ledger is an explicit memory seam here,
separately tested with PostgreSQL/RLS in native-capture-runtime.test.ts.
No browser, SFU, recorded media or actual consent UI is claimed.
"""

import json
import os
import subprocess
import time
from uuid import uuid4

from django.contrib.auth.models import AnonymousUser
from django.db import connection

import psycopg
import pytest
import requests

from core import models
from core.mastrao_media_token_binding import generate_guest_media_config
from core.mastrao_native_capture_contract import arguments_digest, verify_native_start
from core.mastrao_recording_contract import _sign, compact_digest
from core.tests.test_mastrao_media_token_binding import (
    _claims,
    _host_config,
    binding,
    guest,
    host,
    isolated_binding_settings,
)
from core.tests.test_mastrao_native_capture import URL, _twirp_fixture, signer
from core.tests.test_mastrao_rtc_correlation import _assert_post, _event, _join

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.skipif(
        os.environ.get("NATIVE_GRANT_INTEROP") != "synthetic-only",
        reason="explicit local cross-repo fixture only",
    ),
]
ROOT = "/Users/matthias/Programming/mastrao/mastrao-platform-transcript-review"


def _node(settings, payload):
    result = subprocess.run(
        [
            "/opt/homebrew/bin/pnpm",
            "exec",
            "tsx",
            "--conditions=development",
            ".agents/tmp/verify-native-grant-interop-20260910/interop.ts",
        ],
        cwd=ROOT,
        input=json.dumps(
            {
                **payload,
                "privateKey": json.loads(
                    settings.MASTRAO_RECORDING_RECEIPT_PRIVATE_JWK
                ),
                "publicKey": json.loads(settings.MASTRAO_RECORDING_EFFECT_PUBLIC_JWK),
            }
        ),
        text=True,
        capture_output=True,
        timeout=15,
        env={
            "PATH": "/opt/homebrew/bin:/usr/bin:/bin",
            "NATIVE_GRANT_INTEROP": "synthetic-only",
        },
        check=True,
    )
    return json.loads(result.stdout)


def _grant_claims(grant, kind):
    now = int(time.time())
    common = {
        "version": 1,
        "issuer": "core-fixture",
        "audience": "meet-fixture",
        "organization_external_id": "organization_media_fixture",
        "meeting_ref": grant.meeting_ref,
        "room_ref": grant.room_ref,
        "provider_binding_digest": grant.provider_binding_digest,
        "credential_digest": grant.credential_digest,
        "grant_ref": grant.grant_ref,
        "issued_at": now - 10,
        "expires_at": now + 300,
    }
    if kind == "host":
        return {
            **common,
            "type": "mastrao.core-meeting-host-grant",
            "purpose": "media_host",
            "handoff_ref": grant.handoff_ref,
            "host_ref": grant.identity.host_ref,
            "platform_session_ref": grant.platform_session_ref,
            "redemption_id": "redemption_native_fixture",
        }
    return {
        **common,
        "type": "mastrao.core-meeting-guest-grant",
        "purpose": "guest_lobby",
        "invitation_ref": grant.invitation_ref,
        "guest_ref": grant.guest_ref,
        "redemption_id": grant.redemption_id,
    }


def _candidate(epoch, issued, grant, kind):
    now = int(time.time())
    return {
        "version": 1,
        "type": "mastrao.meet-native-microphone-candidate",
        "issuer": "meet-fixture",
        "audience": "core-fixture",
        "organization_external_id": "organization_media_fixture",
        "meeting_ref": grant.meeting_ref,
        "room_ref": grant.room_ref,
        "provider_binding_digest": grant.provider_binding_digest,
        "epoch_ref": str(epoch.pk),
        "media_token_binding_ref": str(issued.pk),
        "room_sid": epoch.connection.room_sid,
        "participant_sid": epoch.connection.participant_sid,
        "track_sid": epoch.track_sid,
        "grant_ref": grant.grant_ref,
        "grant_digest": issued.grant_digest,
        "session_nonce_digest": issued.session_nonce_digest,
        "participant_kind": kind,
        "participant_ref": grant.identity.host_ref
        if kind == "host"
        else grant.guest_ref,
        "consent": {
            "decision_ref": "native_decision_fixture",
            "decision": "accepted",
            "decided_at": now,
            "policy_ref": "native_policy_fixture",
            "notice_version": "native_notice_fixture",
            "notice_digest": "e" * 64,
            "purpose": "meeting_transcription_source_audio",
            "scope": "consented_microphone_track_epoch",
            "retention_expires_at": now + 3600,
        },
        "issued_at": now,
        "expires_at": now + 30,
        "jti": "nativecandidate_" + uuid4().hex,
    }


@pytest.mark.parametrize("kind", ["host", "guest"])
def test_exact_issued_token_survives_core_and_meet_start(  # noqa: PLR0913,PLR0917
    kind, client, live_server, settings, signer, host, guest
):
    grant = host if kind == "host" else guest
    signed = _node(settings, {"mode": "grant", "grant": _grant_claims(grant, kind)})
    # Synthetic handoff persistence, using the production exact-token hash.
    # The following issuance and RTC observations are actual Meet consumers.
    grant.grant_digest = compact_digest(signed["compact"])
    grant.save(update_fields=["grant_digest", "updated_at"])
    config = (
        _host_config(grant)
        if kind == "host"
        else generate_guest_media_config(
            grant,
            "f" * 64,
            room_id=str(grant.room_binding.room_id),
            user=AnonymousUser(),
            username="Same name",
            participant_id=grant.guest_ref,
            expires_at=grant.expires_at,
        )
    )
    issued = models.MastraoMediaTokenBinding.objects.get(
        pk=_claims(config)["attributes"]["mastrao.media_token_binding_ref"],
    )
    assert issued.grant_digest != signed["payloadDigest"]
    joined = _join(issued, participant_sid="PA_" + kind)
    _assert_post(client, settings, joined)
    _assert_post(client, settings, _event(joined, track_sid="TR_" + kind))
    epoch = models.MastraoRtcTrackEpoch.objects.get(
        connection__media_token_binding=issued
    )
    candidate = _candidate(epoch, issued, grant, kind)
    result = _node(
        settings,
        {
            "mode": "admit",
            "candidate": _sign(candidate, "mastrao-native-microphone-candidate+jws"),
            "compactGrant": signed["compact"],
            "organization": candidate["organization_external_id"],
        },
    )
    effect = verify_native_start(result["effect"])
    assert effect["grant_digest"] == issued.grant_digest
    assert effect["epoch_ref"] == str(epoch.pk)

    wrong = {**effect, "grant_digest": signed["payloadDigest"]}
    wrong["arguments_digest"] = arguments_digest(wrong)
    assert (
        client.post(
            URL, {"native_start_effect": signer(wrong)}, content_type="application/json"
        ).status_code
        == 404
    )
    assert not models.MastraoNativeCaptureStart.objects.exists()

    with _twirp_fixture(effect, False) as (configuration, calls, errors):
        settings.LIVEKIT_CONFIGURATION = configuration
        response = requests.post(
            f"{live_server.url}{URL}",
            json={"native_start_effect": result["effect"]},
            timeout=8,
        )
        assert response.status_code == 200
        assert len(calls) == 1 and not errors
    with psycopg.connect(**connection.get_connection_params()) as independent:
        row = independent.execute(
            "SELECT receipt_claims::text FROM meet_mastrao_native_capture_start "
            "WHERE capture_ref=%s",
            [effect["capture_ref"]],
        ).fetchone()
    receipt = json.loads(row[0])
    assert receipt["epoch_ref"] == effect["epoch_ref"]
    assert receipt["provider_job_ref"] == "EG_nativehttp"
    assert receipt["media_durability_proven"] is False
