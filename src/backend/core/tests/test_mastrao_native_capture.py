"""Signed HTTP + actual PostgreSQL intent/receipt; provider/media are a fixture.

No application activation, no VM, no ASR. The provider double inspects committed
SQL from an independent connection before acknowledging or losing its response.
"""

import copy
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import StringIO
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

from django.contrib.auth.models import AnonymousUser
from django.core.management import call_command
from django.db import DatabaseError, close_old_connections, connection
from django.test import Client
from django.utils import timezone

import jwt
import psycopg
import pytest
import requests
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from livekit import api

from core import models
from core.mastrao_media_token_binding import generate_guest_media_config
from core.mastrao_native_capture_adapter import native_request
from core.mastrao_native_capture_contract import (
    EFFECT_TYPE,
    JOSE_TYPE,
    PROFILE_DIGEST,
    RECEIPT_JOSE_TYPE,
    arguments_digest,
)
from core.mastrao_room_contract import _base64url_decode, _canonical_json
from core.tests.test_mastrao_media_token_binding import (
    _claims,
    binding,
    guest,
    host,
    isolated_binding_settings,
)
from core.tests.test_mastrao_room_adapter import _b64, _jwk_pair
from core.tests.test_mastrao_rtc_correlation import _assert_post, _event, _join, receipt

pytestmark = pytest.mark.django_db(transaction=True)
URL = "/internal/mastrao/captures/native/start/"


@contextmanager
def _twirp_fixture(effect, lose_response, *, lose_stop=False):
    """Actual HTTP/protobuf/auth, but no SFU or media engine behind this server."""
    params = connection.get_connection_params()
    calls, jobs, errors = [], [], []
    secret = "synthetic-native-http-secret-only-0123456789"

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass  # Do not log Authorization or private RPC details.

        def do_POST(self):
            try:
                claims = jwt.decode(
                    self.headers["Authorization"].removeprefix("Bearer "),
                    secret,
                    algorithms=["HS256"],
                )
                assert claims["iss"] == "native-http-key"
                assert claims["video"]["roomRecord"] is True
                assert self.headers["Content-Type"] == "application/protobuf"
                length = int(self.headers["Content-Length"])
                assert 0 < length < 4096
                body = self.rfile.read(length)
                calls.append(self.path)
                if self.path == "/twirp/livekit.Egress/StartTrackCompositeEgress":
                    request = api.TrackCompositeEgressRequest.FromString(body)
                    assert request.audio_track_id == effect["track_sid"]
                    assert not request.video_track_id
                    with psycopg.connect(**params) as independent:
                        row = independent.execute(
                            "SELECT receipt_claims::text FROM meet_mastrao_native_capture_start "
                            "WHERE capture_ref = %s",
                            [effect["capture_ref"]],
                        ).fetchone()
                    assert row is not None and json.loads(row[0]) == {}
                    job = api.EgressInfo(
                        egress_id="EG_nativehttp",
                        room_id=effect["room_sid"],
                        room_name=request.room_name,
                        track_composite=request,
                        status=api.EgressStatus.EGRESS_STARTING,
                    )
                    jobs.append(job)
                    payload = job.SerializeToString()
                    if lose_response:
                        self.send_response(503)
                        self.send_header("Content-Type", "application/json")
                        self.end_headers()
                        self.wfile.write(
                            b'{"code":"unavailable","msg":"synthetic lost acknowledgement"}'
                        )
                        return
                elif self.path == "/twirp/livekit.Egress/StopEgress":
                    request = api.StopEgressRequest.FromString(body)
                    assert len(jobs) == 1 and request.egress_id == jobs[0].egress_id
                    with psycopg.connect(**params) as independent:
                        row = independent.execute(
                            "SELECT stop_requested_at, drained_at FROM "
                            "meet_mastrao_native_capture_start WHERE capture_ref=%s",
                            [effect["capture_ref"]],
                        ).fetchone()
                    assert row and row[0] is not None and row[1] is None
                    jobs[0].status = api.EgressStatus.EGRESS_COMPLETE
                    payload = jobs[0].SerializeToString()
                    if lose_stop:
                        self.send_response(503)
                        self.send_header("Content-Type", "application/json")
                        self.end_headers()
                        self.wfile.write(
                            b'{"code":"unavailable","msg":"synthetic lost stop"}'
                        )
                        return
                else:
                    assert self.path == "/twirp/livekit.Egress/ListEgress"
                    query = api.ListEgressRequest.FromString(body)
                    assert query.room_name == jobs[0].room_name
                    payload = api.ListEgressResponse(items=jobs).SerializeToString()
                self.send_response(200)
                self.send_header("Content-Type", "application/protobuf")
                self.end_headers()
                self.wfile.write(payload)
            except Exception as error:  # noqa: BLE001 - expose synthetic fixture failures.
                errors.append(repr(error))
                self.send_error(500, "fixture assertion failed")

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.05))
    thread.start()
    try:
        yield (
            {
                "url": f"http://127.0.0.1:{server.server_port}",
                "api_key": "native-http-key",
                "api_secret": secret,
            },
            calls,
            errors,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        assert not thread.is_alive()


@pytest.mark.parametrize("lose_response", [False, True])
def test_real_http_consumer_and_livekit_transport(
    live_server, signer, effect, settings, lose_response
):
    with _twirp_fixture(effect, lose_response) as (configuration, calls, errors):
        settings.LIVEKIT_CONFIGURATION = configuration
        settings.LIVEKIT_VERIFY_SSL = True
        response = requests.post(
            live_server.url + URL,
            json={"native_start_effect": signer(effect)},
            timeout=8,
        )
        assert response.status_code == (503 if lose_response else 200), errors
        effect["resolve_only"] = True
        response = requests.post(
            live_server.url + URL,
            json={"native_start_effect": signer(effect)},
            timeout=8,
        )
        assert response.status_code == 200, errors
        claims = _receipt(response, settings)
        assert claims["provider_job_ref"] == "EG_nativehttp"
        assert models.MastraoNativeCaptureStart.objects.get().receipt_claims == claims
        assert calls.count("/twirp/livekit.Egress/StartTrackCompositeEgress") == 1
        assert len(calls) == (2 if lose_response else 1)
        assert not errors


@pytest.fixture
def signer(settings):
    private, jwk = _jwk_pair()
    settings.MASTRAO_RECORDING_EFFECT_PUBLIC_JWK = jwk["public"]
    settings.MASTRAO_RECORDING_EFFECT_KEY_ID = "native-core-fixture"
    settings.MASTRAO_RECORDING_EFFECT_ISSUER = "core-fixture"
    settings.MASTRAO_RECORDING_EFFECT_AUDIENCE = "meet-fixture"
    settings.MASTRAO_RECORDING_RECEIPT_PRIVATE_JWK = jwk["private"]
    settings.MASTRAO_RECORDING_RECEIPT_KEY_ID = "native-receipt-fixture"
    settings.MASTRAO_RECORDING_RECEIPT_ISSUER = "meet-fixture"
    settings.MASTRAO_RECORDING_RECEIPT_AUDIENCE = "core-fixture"
    settings.MASTRAO_NATIVE_CAPTURE_START_ENABLED = True
    settings.MASTRAO_NATIVE_CAPTURE_SPOOL_ROOT = "/native-fixture/spool"

    def sign(payload, typ=JOSE_TYPE):
        protected = _b64(
            _canonical_json({"alg": "EdDSA", "kid": "native-core-fixture", "typ": typ})
        )
        encoded = _b64(_canonical_json(payload))
        return f"{protected}.{encoded}.{_b64(private.sign(f'{protected}.{encoded}'.encode()))}"

    return sign


@pytest.fixture
def effect(client, settings, receipt, signer):
    join = _join(receipt)
    _assert_post(client, settings, join)
    _assert_post(client, settings, _event(join))
    epoch = models.MastraoRtcTrackEpoch.objects.get()
    grant = receipt.host_grant
    now = int(time.time())
    payload = {
        "version": 1,
        "type": EFFECT_TYPE,
        "issuer": "core-fixture",
        "audience": "meet-fixture",
        "effect_key": "effect_native_" + uuid4().hex,
        "capture_ref": str(uuid4()),
        "organization_external_id": "org-native-fixture",
        "meeting_ref": grant.meeting_ref,
        "room_ref": grant.room_ref,
        "provider_binding_digest": grant.provider_binding_digest,
        "epoch_ref": str(epoch.pk),
        "media_token_binding_ref": str(receipt.pk),
        "room_sid": epoch.connection.room_sid,
        "participant_sid": epoch.connection.participant_sid,
        "track_sid": epoch.track_sid,
        "grant_ref": grant.grant_ref,
        "grant_digest": grant.grant_digest,
        "session_nonce_digest": grant.session_nonce_digest,
        "participant_kind": "host",
        "participant_ref": grant.identity.host_ref,
        "policy_ref": "policy_native_fixture",
        "notice_version": "notice_native_fixture",
        "notice_digest": "e" * 64,
        "consent_snapshot_digest": "f" * 64,
        "purpose": "meeting_transcription_source_audio",
        "scope": "consented_microphone_track_epoch",
        "retention_expires_at": now + 3600,
        "profile_digest": PROFILE_DIGEST,
        "issued_at": now,
        "expires_at": now + 30,
        "jti": "claim_native_" + uuid4().hex,
        "resolve_only": False,
    }
    payload["arguments_digest"] = arguments_digest(payload)
    return payload


def _post(client, signer, payload):
    return client.post(
        URL, {"native_start_effect": signer(payload)}, content_type="application/json"
    )


def _switch_to_guest(client, settings, effect, guest):
    config = generate_guest_media_config(
        guest,
        "f" * 64,
        room_id=str(guest.room_binding.room_id),
        user=AnonymousUser(),
        username="Same name",
        participant_id=guest.guest_ref,
        expires_at=guest.expires_at,
    )
    issued = models.MastraoMediaTokenBinding.objects.get(
        pk=_claims(config)["attributes"]["mastrao.media_token_binding_ref"],
    )
    join = _join(issued, participant_sid="PA_guest")
    _assert_post(client, settings, join)
    _assert_post(client, settings, _event(join, track_sid="TR_guest"))
    epoch = models.MastraoRtcTrackEpoch.objects.get(
        connection__media_token_binding=issued
    )
    effect.update(
        capture_ref=str(uuid4()),
        effect_key="effect_native_" + uuid4().hex,
        epoch_ref=str(epoch.pk),
        media_token_binding_ref=str(issued.pk),
        participant_sid=epoch.connection.participant_sid,
        track_sid=epoch.track_sid,
        organization_external_id=guest.organization_external_id,
        participant_kind="guest",
        participant_ref=guest.guest_ref,
        grant_ref=guest.grant_ref,
        grant_digest=guest.grant_digest,
        session_nonce_digest=guest.session_nonce_digest,
    )
    effect["arguments_digest"] = arguments_digest(effect)


def test_host_and_guest_get_distinct_jobs_not_a_room_mix(  # noqa: PLR0913,PLR0917
    client, signer, effect, provider, settings, guest
):
    first = _post(client, signer, effect)
    assert first.status_code == 200
    first_claims = _receipt(first, settings)
    _switch_to_guest(client, settings, effect, guest)
    second = _post(client, signer, effect)
    assert second.status_code == 200
    second_claims = _receipt(second, settings)
    assert first_claims["provider_job_ref"] != second_claims["provider_job_ref"]
    assert first_claims["epoch_ref"] != second_claims["epoch_ref"]
    assert {job.track_composite.audio_track_id for job in provider.jobs} == {
        "TR_audio",
        "TR_guest",
    }
    assert models.MastraoNativeCaptureStart.objects.count() == 2


@pytest.mark.parametrize("fault", ["pending", "denied", "wrong_organization"])
def test_guest_requires_current_confirmed_exact_admission(  # noqa: PLR0913,PLR0917
    client, signer, effect, provider, settings, guest, fault
):
    _switch_to_guest(client, settings, effect, guest)
    if fault == "pending":
        models.MastraoGuestGrant.objects.update(decision_confirmed_at=None)
    elif fault == "denied":
        models.MastraoGuestGrant.objects.update(
            admission_state="denied", decision_allow=False
        )
    else:
        effect["organization_external_id"] = "other-organization"
        effect["arguments_digest"] = arguments_digest(effect)
    assert _post(client, signer, effect).status_code == 404
    provider.egress.start_track_composite_egress.assert_not_called()


def test_two_concurrent_deliveries_send_only_once(client, signer, effect, provider):
    entered, release = threading.Event(), threading.Event()
    start = provider.egress.start_track_composite_egress.side_effect

    async def held(request):
        job = await start(request)
        entered.set()
        assert release.wait(8), "concurrent fixture timed out"
        return job

    provider.egress.start_track_composite_egress.side_effect = held

    def deliver():
        close_old_connections()
        try:
            return _post(Client(), signer, effect)
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(deliver)
        try:
            assert entered.wait(8), "first request did not reach provider"
            second = _post(client, signer, effect)
            assert second.status_code == 200
        finally:
            release.set()
        assert first.result(timeout=8).content == second.content
    assert provider.egress.start_track_composite_egress.await_count == 1
    assert models.MastraoNativeCaptureStart.objects.count() == 1


def test_receipt_commit_failure_recovers_provider_without_resend(  # noqa: PLR0913,PLR0917
    client, signer, effect, provider, monkeypatch, settings
):
    original_save = models.MastraoNativeCaptureStart.save

    def fail_receipt(row, *args, **kwargs):
        original_save(row, *args, **kwargs)
        if row.provider_job_ref:
            raise DatabaseError("fixture failure after receipt write")

    monkeypatch.setattr(models.MastraoNativeCaptureStart, "save", fail_receipt)
    assert _post(client, signer, effect).status_code == 503
    intent = models.MastraoNativeCaptureStart.objects.get()
    assert intent.provider_job_ref is None and intent.receipt_claims == {}
    monkeypatch.setattr(models.MastraoNativeCaptureStart, "save", original_save)
    assert _post(client, signer, effect).status_code == 200
    assert provider.egress.start_track_composite_egress.await_count == 1


def test_missing_receipt_signer_prevents_start(
    client, signer, effect, provider, settings
):
    settings.MASTRAO_RECORDING_RECEIPT_PRIVATE_JWK = ""
    assert _post(client, signer, effect).status_code == 503
    provider.egress.start_track_composite_egress.assert_not_called()
    assert not models.MastraoNativeCaptureStart.objects.exists()


def test_model_and_migration_are_in_sync():
    output = StringIO()
    call_command("makemigrations", "core", check=True, dry_run=True, stdout=output)
    assert "No changes detected" in output.getvalue()


def test_resolution_survives_retention_expiry_without_renewing_authority(
    client, signer, effect, provider, monkeypatch
):
    start = provider.egress.start_track_composite_egress.side_effect

    async def lost(request):
        await start(request)
        raise TimeoutError()

    provider.egress.start_track_composite_egress.side_effect = lost
    assert _post(client, signer, effect).status_code == 503
    after_retention = effect["retention_expires_at"] + 1
    monkeypatch.setattr(
        "core.mastrao_recording_contract.time",
        SimpleNamespace(time=lambda: after_retention),
    )
    effect.update(issued_at=after_retention, expires_at=after_retention + 30)
    # Never extend the retained authority to make reconciliation possible.
    assert _post(client, signer, effect).status_code == 404
    effect["resolve_only"] = True
    assert _post(client, signer, effect).status_code == 200
    assert provider.egress.start_track_composite_egress.await_count == 1


@pytest.fixture
def provider(monkeypatch, effect):
    stored = []
    params = connection.get_connection_params()

    async def start(request):
        # This is outside the request's ORM connection: an uncommitted intent fails.
        with psycopg.connect(**params) as independent:
            row = independent.execute(
                "SELECT output_prefix, receipt_claims::text FROM meet_mastrao_native_capture_start "
                "WHERE capture_ref = %s",
                [effect["capture_ref"]],
            ).fetchone()
            assert row is not None and json.loads(row[1]) == {}
        assert not request.video_track_id
        assert len(request.segment_outputs) == 1
        assert not request.file_outputs
        assert request.segment_outputs[0].WhichOneof("output") is None
        assert request.segment_outputs[0].playlist_name == row[0] + "/index.m3u8"
        assert request.audio_track_id == effect["track_sid"]
        job = api.EgressInfo(
            egress_id="EG_nativefixture" if not stored else "EG_nativefixture2",
            room_id=effect["room_sid"],
            room_name=request.room_name,
            status=api.EgressStatus.EGRESS_STARTING,
            track_composite=request,
        )
        stored.append(job)
        return job

    async def listed(_request):
        return api.ListEgressResponse(items=stored)

    fake = SimpleNamespace(
        egress=SimpleNamespace(
            start_track_composite_egress=AsyncMock(side_effect=start),
            list_egress=AsyncMock(side_effect=listed),
        ),
        aclose=AsyncMock(),
        jobs=stored,
    )

    def client_factory(configuration):
        assert configuration["failover"] is False
        return fake

    monkeypatch.setattr(
        "core.mastrao_native_capture_adapter.utils.create_livekit_client",
        client_factory,
    )
    return fake


def _receipt(response, settings):
    compact = response.json()["native_start_receipt"]
    header, payload, signature = compact.split(".")
    assert json.loads(_base64url_decode(header))["typ"] == RECEIPT_JOSE_TYPE
    jwk = json.loads(settings.MASTRAO_RECORDING_EFFECT_PUBLIC_JWK)
    Ed25519PublicKey.from_public_bytes(_base64url_decode(jwk["x"])).verify(
        _base64url_decode(signature),
        f"{header}.{payload}".encode(),
    )
    return json.loads(_base64url_decode(payload))


def test_signed_start_commits_intent_before_send_and_receipt_before_ack(
    client, signer, effect, provider, settings
):
    response = _post(client, signer, effect)
    assert response.status_code == 200
    claims = _receipt(response, settings)
    assert claims["provider_job_ref"] == "EG_nativefixture"
    assert claims["observed_status"] == api.EgressStatus.EGRESS_STARTING
    assert claims["media_durability_proven"] is False
    with psycopg.connect(**connection.get_connection_params()) as independent:
        row = independent.execute(
            "SELECT receipt_claims::text FROM meet_mastrao_native_capture_start WHERE capture_ref = %s",
            [effect["capture_ref"]],
        ).fetchone()
    assert json.loads(row[0]) == claims
    assert "no-store" in response["Cache-Control"]
    # A renewed envelope, not a renewed start permission for this epoch.
    effect["jti"] = "renewed_claim_" + uuid4().hex
    repeated = _post(client, signer, effect)
    assert repeated.content == response.content
    assert provider.egress.start_track_composite_egress.await_count == 1
    provider.egress.list_egress.assert_not_called()


def test_lost_response_resolves_exact_job_without_restarting_after_flag_rollback(
    client, signer, effect, provider, settings
):
    start = provider.egress.start_track_composite_egress.side_effect

    async def lost(request):
        await start(request)
        raise TimeoutError("must-not-leak-provider-secret")

    provider.egress.start_track_composite_egress.side_effect = lost
    response = _post(client, signer, effect)
    assert response.status_code == 503
    assert b"secret" not in response.content
    assert models.MastraoNativeCaptureStart.objects.get().receipt_claims == {}
    settings.MASTRAO_NATIVE_CAPTURE_START_ENABLED = False
    settings.MASTRAO_NATIVE_CAPTURE_SPOOL_ROOT = "/changed/spool"
    models.MastraoRoomBinding.objects.update(closing_at=timezone.now())
    effect["resolve_only"] = True
    assert _post(client, signer, effect).status_code == 200
    assert provider.egress.start_track_composite_egress.await_count == 1
    assert provider.egress.list_egress.await_count == 1


def test_crash_before_send_or_absent_job_never_allows_second_attempt(
    client, signer, effect, provider
):
    provider.egress.start_track_composite_egress.side_effect = TimeoutError()
    assert _post(client, signer, effect).status_code == 503
    assert _post(client, signer, effect).status_code == 503
    assert provider.egress.start_track_composite_egress.await_count == 1
    assert models.MastraoNativeCaptureStart.objects.count() == 1


@pytest.mark.parametrize(
    "mismatch",
    [
        "audience",
        "purpose",
        "scope",
        "profile_digest",
        "grant_ref",
        "grant_digest",
        "session_nonce_digest",
        "participant_ref",
        "participant_kind",
        "track_sid",
        "participant_sid",
        "room_sid",
        "media_token_binding_ref",
        "epoch_ref",
        "provider_binding_digest",
        "expires_at",
        "version",
    ],
)
def test_crossed_or_invalid_signed_effect_never_calls_provider(
    client, signer, effect, provider, mismatch
):
    if mismatch in ("media_token_binding_ref", "epoch_ref"):
        effect[mismatch] = str(uuid4())
    elif mismatch == "expires_at":
        effect[mismatch] = int(time.time()) - 1
    elif mismatch == "version":
        effect[mismatch] = True
    elif mismatch == "participant_kind":
        effect[mismatch] = "guest"
    else:
        effect[mismatch] = "wrong_" + effect[mismatch]
    effect["arguments_digest"] = arguments_digest(effect)
    assert _post(client, signer, effect).status_code == 404
    provider.egress.start_track_composite_egress.assert_not_called()
    assert not models.MastraoNativeCaptureStart.objects.exists()


@pytest.mark.parametrize(
    "fault",
    [
        "ended",
        "conflict",
        "missing_join",
        "closed_room",
        "screen_audio",
        "inactive_host",
        "grant_changed",
        "flag_off",
        "resolve_only",
        "bad_spool",
    ],
)
def test_local_denials_do_not_create_intent(  # noqa: PLR0913,PLR0917 - explicit pytest fixtures.
    client, signer, effect, provider, settings, fault
):
    if fault in ("ended", "conflict"):
        models.MastraoRtcTrackEpoch.objects.update(**{fault: True})
    elif fault == "missing_join":
        models.MastraoRtcConnection.objects.update(correlation="missing_join")
    elif fault == "closed_room":
        models.MastraoRoomBinding.objects.update(closing_at=timezone.now())
    elif fault == "screen_audio":
        models.MastraoRtcObservation.objects.filter(
            event_type="track_published"
        ).update(track_source=api.TrackSource.SCREEN_SHARE_AUDIO)
    elif fault == "inactive_host":
        models.User.objects.update(is_active=False)
    elif fault == "grant_changed":
        models.MastraoHostGrant.objects.update(grant_digest="d" * 64)
    elif fault == "flag_off":
        settings.MASTRAO_NATIVE_CAPTURE_START_ENABLED = False
    elif fault == "resolve_only":
        effect["resolve_only"] = True
    else:
        settings.MASTRAO_NATIVE_CAPTURE_SPOOL_ROOT = "/"
    assert _post(client, signer, effect).status_code == (
        503 if fault == "bad_spool" else 404
    )
    provider.egress.start_track_composite_egress.assert_not_called()
    assert not models.MastraoNativeCaptureStart.objects.exists()


@pytest.mark.parametrize(
    "changed",
    [
        "effect_key",
        "capture_ref",
        "consent_snapshot_digest",
        "organization_external_id",
    ],
)
def test_epoch_cannot_be_restarted_with_another_key_or_authority(
    client, signer, effect, provider, changed
):
    assert _post(client, signer, effect).status_code == 200
    if changed == "capture_ref":
        effect[changed] = str(uuid4())
    elif changed == "consent_snapshot_digest":
        effect[changed] = "a" * 64
    else:
        effect[changed] += "changed"
    effect["arguments_digest"] = arguments_digest(effect)
    assert _post(client, signer, effect).status_code == 409
    assert provider.egress.start_track_composite_egress.await_count == 1


def test_database_insert_failure_prevents_external_send(
    client, signer, effect, provider, monkeypatch
):
    def unavailable(*_args, **_kwargs):
        raise DatabaseError("private-sql-details")

    monkeypatch.setattr(models.MastraoNativeCaptureStart.objects, "create", unavailable)
    assert _post(client, signer, effect).status_code == 503
    provider.egress.start_track_composite_egress.assert_not_called()


def test_wrong_signature_extra_fields_and_legacy_jose_refused(
    client, signer, effect, provider
):
    for compact in (
        signer(effect)[:-12] + "xxxxxxxxxxxx",
        signer(effect, "mastrao-meeting-recording-start-effect+jws"),
        signer({**effect, "path": "/tmp/wrong"}),
    ):
        response = client.post(
            URL, {"native_start_effect": compact}, content_type="application/json"
        )
        assert response.status_code == 404
    provider.egress.start_track_composite_egress.assert_not_called()


@pytest.mark.parametrize("wrong", ["room", "track", "output", "duplicate"])
def test_reconciliation_requires_exact_provider_job(
    client, signer, effect, provider, wrong
):
    provider.egress.start_track_composite_egress.side_effect = TimeoutError()
    assert _post(client, signer, effect).status_code == 503
    intent = models.MastraoNativeCaptureStart.objects.get()
    request = native_request(intent)
    job = api.EgressInfo(
        egress_id="EG_nativefixture",
        room_id=effect["room_sid"],
        room_name=request.room_name,
        track_composite=request,
    )
    if wrong == "room":
        job.room_id = "RM_wrong"
    elif wrong == "track":
        job.track_composite.audio_track_id = "TR_wrong"
    elif wrong == "output":
        job.track_composite.segment_outputs[0].playlist_name = "/other/index.m3u8"
    provider.jobs.append(job)
    if wrong == "duplicate":
        duplicate = copy.deepcopy(job)
        duplicate.egress_id = "EG_second"
        provider.jobs.append(duplicate)
    assert _post(client, signer, effect).status_code == (
        409 if wrong == "duplicate" else 503
    )
    assert models.MastraoNativeCaptureStart.objects.get().receipt_claims == {}
    assert provider.egress.start_track_composite_egress.await_count == 1
