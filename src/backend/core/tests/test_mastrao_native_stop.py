"""Core stop envelope to durable Meet snapshot, no real media or cloud."""

import json
import os
import subprocess

from django.db import DatabaseError, connection
from django.utils import timezone

import psycopg
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from livekit import api

from core import models
from core.mastrao_native_capture_drain import reconcile_native_captures
from core.mastrao_native_capture_stop import STOP_FIELDS, STOP_JOSE, STOP_RECEIPT_JOSE
from core.mastrao_room_contract import _base64url_decode
from core.tests.test_mastrao_native_capture import (
    _post,
    binding,
    effect,
    host,
    isolated_binding_settings,
    provider,
    receipt,
    signer,
)

pytestmark = pytest.mark.django_db(transaction=True)
URL = "/internal/mastrao/captures/native/stop/"


def _stop_effect(effect):
    payload = {key: value for key, value in effect.items() if key in STOP_FIELDS}
    payload.update(
        type="mastrao.core-native-microphone-stop-effect", provider_job_ref=None
    )
    return payload


def _stop(client, signer, payload):
    return client.post(
        URL,
        {"native_stop_effect": signer(payload, typ=STOP_JOSE)},
        content_type="application/json",
    )


def _claims(response, settings):
    header, payload, signature = response.json()["native_stop_receipt"].split(".")
    assert json.loads(_base64url_decode(header))["typ"] == STOP_RECEIPT_JOSE
    public = json.loads(settings.MASTRAO_RECORDING_EFFECT_PUBLIC_JWK)
    Ed25519PublicKey.from_public_bytes(_base64url_decode(public["x"])).verify(
        _base64url_decode(signature), f"{header}.{payload}".encode()
    )
    return json.loads(_base64url_decode(payload))


def test_stop_snapshot_requires_separate_terminal_observation(
    client, signer, effect, provider, settings
):
    assert _post(client, signer, effect).status_code == 200
    settings.MASTRAO_NATIVE_CAPTURE_START_ENABLED = False
    payload = _stop_effect(effect)
    response = _stop(client, signer, payload)
    assert response.status_code == 200
    assert _claims(response, settings)["state"] == "requested"
    with psycopg.connect(**connection.get_connection_params()) as independent:
        row = independent.execute(
            "SELECT stop_requested_at,drained_at FROM meet_mastrao_native_capture_start"
        ).fetchone()
    assert row[0] and row[1] is None
    provider.egress.list_egress.assert_not_called()
    provider.jobs[0].status = api.EgressStatus.EGRESS_COMPLETE
    assert reconcile_native_captures() == 1
    snapshot = _claims(_stop(client, signer, payload), settings)
    assert snapshot["state"] == "drained" and snapshot["observed_status"] == 3
    assert snapshot["media_durability_proven"] is False
    assert provider.egress.start_track_composite_egress.await_count == 1


@pytest.mark.parametrize(
    "field,value",
    [
        ("capture_ref", "00000000-0000-4000-8000-000000000001"),
        ("epoch_ref", "00000000-0000-4000-8000-000000000002"),
        ("organization_external_id", "other-cabinet"),
        ("arguments_digest", "a" * 64),
        ("effect_key", "effect_other_fixture"),
        ("room_ref", "room_other_fixture"),
        ("meeting_ref", "meeting_other_fixture"),
        ("provider_binding_digest", "a" * 64),
        ("provider_job_ref", "EG_other"),
        ("epoch_ref", True),
        ("extra", "denied"),
    ],
)
def test_stop_refuses_crossed_or_malformed_authority(  # noqa: PLR0913,PLR0917
    client, signer, effect, provider, field, value
):
    assert _post(client, signer, effect).status_code == 200
    payload = _stop_effect(effect)
    payload[field] = value
    assert _stop(client, signer, payload).status_code in {404, 409}
    assert models.MastraoNativeCaptureStart.objects.get().stop_requested_at is None


def test_unknown_stop_does_not_create_intent_or_start(client, signer, effect, provider):
    assert _stop(client, signer, _stop_effect(effect)).status_code == 404
    assert not models.MastraoNativeCaptureStart.objects.exists()
    provider.egress.start_track_composite_egress.assert_not_called()


def test_start_signature_is_not_a_stop_capability(client, signer, effect, provider):
    assert _post(client, signer, effect).status_code == 200
    response = client.post(
        URL, {"native_stop_effect": signer(effect)}, content_type="application/json"
    )
    assert response.status_code == 404
    assert models.MastraoNativeCaptureStart.objects.get().stop_requested_at is None


def test_stop_sql_failure_rolls_back_latch_and_retry_commits(
    client, signer, effect, provider, monkeypatch
):
    assert _post(client, signer, effect).status_code == 200
    original = models.MastraoNativeCaptureStart.save

    def failing(self, *args, **kwargs):
        original(self, *args, **kwargs)
        if self.stop_requested_at:
            raise DatabaseError("synthetic stop commit failure")

    monkeypatch.setattr(models.MastraoNativeCaptureStart, "save", failing)
    assert _stop(client, signer, _stop_effect(effect)).status_code == 503
    assert models.MastraoNativeCaptureStart.objects.get().stop_requested_at is None
    monkeypatch.setattr(models.MastraoNativeCaptureStart, "save", original)
    assert _stop(client, signer, _stop_effect(effect)).status_code == 200


@pytest.mark.skipif(
    os.environ.get("NATIVE_STOP_INTEROP") != "synthetic-only",
    reason="explicit local cross-repo fixture only",
)
def test_core_typescript_signing_and_receipt_verification_interoperate(
    client, signer, effect, provider, settings
):
    assert _post(client, signer, effect).status_code == 200
    root = "/Users/matthias/Programming/mastrao/mastrao-platform-transcript-review"

    def node(payload):
        result = subprocess.run(
            [
                "/opt/homebrew/bin/pnpm",
                "exec",
                "tsx",
                "--conditions=development",
                ".agents/tmp/verify-native-core-stop-20260910/interop.ts",
            ],
            cwd=root,
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            timeout=15,
            env={
                "PATH": "/opt/homebrew/bin:/usr/bin:/bin",
                "NATIVE_STOP_INTEROP": "synthetic-only",
            },
            check=True,
        )
        return result.stdout

    compact = node(
        {
            "mode": "sign",
            "effect": _stop_effect(effect),
            "key": json.loads(settings.MASTRAO_RECORDING_RECEIPT_PRIVATE_JWK),
        }
    )
    response = client.post(
        URL, {"native_stop_effect": compact}, content_type="application/json"
    )
    assert response.status_code == 200
    for terminal in (False, True):
        if terminal:
            provider.jobs[0].status = api.EgressStatus.EGRESS_COMPLETE
            models.MastraoNativeCaptureStart.objects.update(
                next_check_at=timezone.now()
            )
            assert reconcile_native_captures() == 1
            response = client.post(
                URL, {"native_stop_effect": compact}, content_type="application/json"
            )
        snapshot = json.loads(
            node(
                {
                    "mode": "verify",
                    "receipt": response.json()["native_stop_receipt"],
                    "key": json.loads(settings.MASTRAO_RECORDING_EFFECT_PUBLIC_JWK),
                }
            )
        )
        assert snapshot["state"] == ("drained" if terminal else "requested")
        assert snapshot["capture_ref"] == effect["capture_ref"]
