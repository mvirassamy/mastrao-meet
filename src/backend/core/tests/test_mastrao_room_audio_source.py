"""Durable exactly-once publication tests for room-audio sources."""

# Test fixtures deliberately expose every signed contract field.
# pylint: disable=missing-function-docstring,redefined-outer-name

import base64
import json
import time
from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

from django.test import Client
from django.utils import timezone

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from jwt.algorithms import OKPAlgorithm
from livekit import api

from core import models
from core.factories import RoomFactory, UserFactory
from core.mastrao_core_http import read_bounded_core_json
from core.mastrao_recording_contract import RecordingContractRefused
from core.mastrao_room_audio_run import (
    POST_SOURCE_PROFILE,
    persist_room_audio_run_effect,
    verify_room_audio_run_effect,
)
from core.mastrao_room_audio_source import (
    MANIFEST_V2_METRIC_FIELDS,
    MAX_ATTEMPTS,
    SOURCE_JOSE_TYPE,
    _mark_delivered,
    claim_room_audio_source,
    schedule_room_audio_source_delivery,
)
from core.mastrao_room_contract import _canonical_json, _sha256_canonical
from core.tasks.native_capture import process_room_audio_sources

pytestmark = pytest.mark.django_db
ENDPOINT = "/internal/mastrao/room-audio/sources/complete/"


def _b64(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _private_jwk(key):
    private = key.private_bytes(
        serialization.Encoding.Raw,
        serialization.PrivateFormat.Raw,
        serialization.NoEncryption(),
    )
    public = key.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    return {"kty": "OKP", "crv": "Ed25519", "d": _b64(private), "x": _b64(public)}


def _signed_start(key, payload):
    header = {
        "alg": "EdDSA",
        "kid": "room-audio-run-test-key",
        "typ": "mastrao-meeting-room-audio-start-effect+jws",
    }
    protected = _b64(_canonical_json(header))
    body = _b64(_canonical_json(payload))
    signature = key.sign(f"{protected}.{body}".encode())
    return f"{protected}.{body}.{_b64(signature)}"


@pytest.fixture
def source_run(settings):
    start_key = Ed25519PrivateKey.generate()
    start_public = start_key.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    receipt_key = Ed25519PrivateKey.generate()
    settings.MASTRAO_RECORDING_EFFECT_ISSUER = "tenant-core-test"
    settings.MASTRAO_RECORDING_EFFECT_AUDIENCE = "mastrao-meet-test"
    settings.MASTRAO_RECORDING_EFFECT_KEY_ID = "room-audio-run-test-key"
    settings.MASTRAO_RECORDING_EFFECT_PUBLIC_JWK = json.dumps(
        {"kty": "OKP", "crv": "Ed25519", "x": _b64(start_public)}
    )
    settings.MASTRAO_RECORDING_RECEIPT_ISSUER = "mastrao-meet-test"
    settings.MASTRAO_RECORDING_RECEIPT_AUDIENCE = "tenant-core-test"
    settings.MASTRAO_RECORDING_RECEIPT_KEY_ID = "room-audio-receipt-test-key"
    settings.MASTRAO_RECORDING_RECEIPT_PRIVATE_JWK = json.dumps(
        _private_jwk(receipt_key)
    )
    settings.ROOM_AUDIO_SOURCE_TRANSFER_ENABLED = True
    settings.MASTRAO_CORE_ROOM_AUDIO_SOURCE_ENDPOINT = (
        "http://127.0.0.1:9000/internal/v1/meetings/room-audio/source"
    )
    settings.MASTRAO_CORE_ROOM_AUDIO_SOURCE_TIMEOUT_SECONDS = 2
    binding = models.MastraoRoomBinding.objects.create(
        effect_key="room_audio_source_fixture",
        arguments_digest="a" * 64,
        meeting_ref="meeting_audio_source_fixture",
        room_ref="room_audio_source_fixture",
        owner_ref="owner_audio_source_fixture",
        provider_binding_digest="b" * 64,
        room=RoomFactory(name="room_audio_source_authority_fixture"),
        owner=UserFactory(),
    )
    now = int(time.time())
    effect = {
        "version": 2,
        "type": "mastrao.core-meeting-room-audio-start-effect",
        "issuer": "tenant-core-test",
        "audience": "mastrao-meet-test",
        "operation": "start_meeting_room_audio_run",
        "operation_version": 1,
        "organization_external_id": "organization_audio_source_fixture",
        "meeting_ref": binding.meeting_ref,
        "room_ref": binding.room_ref,
        "audio_run_ref": "audio_run_source_fixture_12345678",
        "policy_ref": "policy_audio_source_fixture_12345678",
        "policy_digest": "c" * 64,
        "notice_digest": "d" * 64,
        "provider_binding_digest": binding.provider_binding_digest,
        "provider_room_sid": "RM_roomaudio1234",
        "retention_expires_at": now + 3_600,
        "pipeline": "post",
        "source_profile": POST_SOURCE_PROFILE,
        "maximum_duration_seconds": 600,
        "empty_room_grace_seconds": 30,
        "issued_at": now,
        "expires_at": now + 20,
        "jti": "effect_audio_source_fixture_12345678",
    }
    compact = _signed_start(start_key, effect)
    row = persist_room_audio_run_effect(verify_room_audio_run_effect(compact), compact)
    row.state = models.MastraoRoomAudioRunEffect.State.DELIVERED
    row.dispatch_id = "dispatch_source_fixture_12345678"
    row.delivered_at = timezone.now()
    row.save(update_fields=["state", "dispatch_id", "delivered_at"])
    worker_token = (
        api.AccessToken(
            settings.LIVEKIT_CONFIGURATION["api_key"],
            settings.LIVEKIT_CONFIGURATION["api_secret"],
        )
        .with_identity(row.destination_identity)
        .with_grants(
            api.VideoGrants(
                room=str(binding.room_id),
                room_join=True,
                can_publish=False,
                can_publish_data=False,
                can_subscribe=False,
            )
        )
        .to_jwt()
    )
    return {
        "compact": compact,
        "effect": effect,
        "worker_token": worker_token,
        "receipt_public_jwk": {
            key: value for key, value in _private_jwk(receipt_key).items() if key != "d"
        },
    }


def _manifest(source_run, **changes):
    effect = source_run["effect"]
    manifest = {
        "version": 2,
        "type": "mastrao.meeting-room-audio-source",
        **{
            name: effect[name]
            for name in (
                "organization_external_id",
                "meeting_ref",
                "room_ref",
                "audio_run_ref",
                "policy_ref",
                "policy_digest",
                "notice_digest",
                "provider_binding_digest",
                "retention_expires_at",
            )
        },
        "pipeline": "post",
        "provider_session_ref": "dispatch_source_fixture_12345678",
        "object_ref": f"room-audio/v1/{'e' * 64}.ogg",
        "content_type": "audio/ogg",
        "codec": "opus",
        "channel_count": 1,
        "started_at_ms": 1_000_000,
        "ended_at_ms": 1_060_000,
        "duration_ms": 60_000,
        "byte_size": 64_000,
        "checksum_algorithm": "sha256",
        "checksum_digest": "f" * 64,
        "generation_count": 1,
        "gap_count": 0,
        "total_gap_ms": 0,
        "mixer_output_frame_count": 3_000,
        "mixer_dropped_frame_count": 0,
        "mixer_underrun_frame_count": 0,
        "mixer_queue_peak_frames": 3,
        "rtc_sample_count": 61,
        "rtc_collection_error_count": 0,
        "rtc_packets_received": 3_000,
        "rtc_packets_lost": 0,
        "rtc_concealed_samples": 0,
        "rtc_maximum_jitter_ms": 5,
        "rtc_maximum_round_trip_time_ms": 20,
    }
    manifest.update(changes)
    return manifest


def _post(source_run, manifest=None, gaps=None, worker_token=None):
    return Client().post(
        ENDPOINT,
        data=json.dumps(
            {
                "room_audio_start_effect": source_run["compact"],
                "source_manifest": manifest or _manifest(source_run),
                "source_gaps": gaps or [],
            }
        ),
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {worker_token or source_run['worker_token']}",
    )


def _core_success(**kwargs):
    compact = kwargs["body"]["source_receipt"]
    return {
        "sourceId": str(uuid4()),
        "replayed": False,
        "compact": compact,
    }


def test_source_is_signed_persisted_and_delivered_once(source_run):
    calls = []

    def core(**kwargs):
        result = _core_success(**kwargs)
        compact = result.pop("compact")
        claims = jwt.decode(
            compact,
            OKPAlgorithm.from_jwk(json.dumps(source_run["receipt_public_jwk"])),
            algorithms=["EdDSA"],
            options={"verify_aud": False},
        )
        assert jwt.get_unverified_header(compact) == {
            "alg": "EdDSA",
            "kid": "room-audio-receipt-test-key",
            "typ": SOURCE_JOSE_TYPE,
        }
        assert claims["manifest"] == _manifest(source_run)
        assert claims["issuer"] == "mastrao-meet-test"
        assert claims["audience"] == "tenant-core-test"
        assert claims["operation"] == "confirm_meeting_room_audio_source"
        calls.append(compact)
        return result

    with patch("core.mastrao_room_audio_source.post_core_json", side_effect=core):
        first = _post(source_run)
        second = _post(source_run)

    assert first.status_code == 200
    assert second.status_code == 200
    assert len(calls) == 1
    row = models.MastraoRoomAudioSourceOutbox.objects.get()
    assert row.state == models.MastraoRoomAudioSourceOutbox.State.DELIVERED
    assert row.manifest_digest == _sha256_canonical(_manifest(source_run))
    assert row.attempt_count == 1


def test_delivered_source_schedules_room_asr(source_run, settings):
    settings.ROOM_AUDIO_ASR_ENABLED = True
    delivered = _core_success(body={"source_receipt": "unused"})
    delivered.pop("compact")

    with (
        patch(
            "core.mastrao_room_audio_source.post_core_json",
            return_value=delivered,
        ),
        patch(
            "core.mastrao_room_audio_source.transaction.on_commit",
            side_effect=lambda callback: callback(),
        ),
        patch(
            "core.mastrao_room_audio_asr_worker.schedule_room_audio_asr",
            return_value=1,
        ) as schedule,
    ):
        assert _post(source_run).status_code == 200

    schedule.assert_called_once_with(limit=1)


def test_v1_manifest_remains_accepted_during_worker_rollout(source_run):
    manifest = _manifest(source_run)
    manifest["version"] = 1
    for field in MANIFEST_V2_METRIC_FIELDS:
        manifest.pop(field)
    delivered = _core_success(body={"source_receipt": "unused"})
    delivered.pop("compact")

    with patch("core.mastrao_room_audio_source.post_core_json", return_value=delivered):
        response = _post(source_run, manifest)

    assert response.status_code == 200
    assert models.MastraoRoomAudioSourceOutbox.objects.get().manifest == manifest


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("rtc_sample_count", 0),
        ("rtc_packets_lost", -1),
        ("mixer_dropped_frame_count", True),
    ],
)
def test_v2_metrics_are_non_negative_integers(source_run, field, value):
    response = _post(source_run, _manifest(source_run, **{field: value}))

    assert response.status_code == 404
    assert models.MastraoRoomAudioSourceOutbox.objects.count() == 0


def test_network_retry_reuses_the_same_signed_receipt(source_run):
    unavailable = RecordingContractRefused(status=503)
    delivered = _core_success(body={"source_receipt": "unused"})
    delivered.pop("compact")
    observed = []

    def core(**kwargs):
        observed.append(kwargs["body"]["source_receipt"])
        if len(observed) == 1:
            raise unavailable
        return delivered

    with patch("core.mastrao_room_audio_source.post_core_json", side_effect=core):
        assert _post(source_run).status_code == 503
        row = models.MastraoRoomAudioSourceOutbox.objects.get()
        row.next_attempt_at = timezone.now()
        row.save(update_fields=["next_attempt_at"])
        assert _post(source_run).status_code == 200

    assert observed[0] == observed[1]
    assert models.MastraoRoomAudioSourceOutbox.objects.count() == 1


def test_explicit_expiry_refusal_remints_before_retry(source_run):
    expired = RecordingContractRefused(status=401)
    expired.core_error_code = "invalid_assertion"
    expired.core_reason_codes = ("meeting_room_audio_source_receipt_expired",)
    delivered = _core_success(body={"source_receipt": "unused"})
    delivered.pop("compact")
    observed = []

    def core(**kwargs):
        observed.append(kwargs["body"]["source_receipt"])
        if len(observed) == 1:
            raise expired
        return delivered

    with patch("core.mastrao_room_audio_source.post_core_json", side_effect=core):
        assert _post(source_run).status_code == 200

    assert observed[0] != observed[1]
    row = models.MastraoRoomAudioSourceOutbox.objects.get()
    assert row.compact_receipt == observed[1]
    assert row.attempt_count == 1


def test_locally_expired_receipt_remints_before_network_call(source_run):
    unavailable = RecordingContractRefused(status=503)
    delivered = _core_success(body={"source_receipt": "unused"})
    delivered.pop("compact")
    observed = []

    def core(**kwargs):
        observed.append(kwargs["body"]["source_receipt"])
        if len(observed) == 1:
            raise unavailable
        return delivered

    with patch("core.mastrao_room_audio_source.post_core_json", side_effect=core):
        assert _post(source_run).status_code == 503
        row = models.MastraoRoomAudioSourceOutbox.objects.get()
        original_receipt = row.compact_receipt
        row.receipt_expires_at = timezone.now() - timedelta(seconds=1)
        row.next_attempt_at = timezone.now()
        row.save(update_fields=["receipt_expires_at", "next_attempt_at"])

        assert _post(source_run).status_code == 200

    assert observed == [
        original_receipt,
        models.MastraoRoomAudioSourceOutbox.objects.get().compact_receipt,
    ]
    assert observed[0] != observed[1]


def test_transport_retains_only_validated_expiry_reason():
    response = type(
        "Response",
        (),
        {
            "status_code": 401,
            "headers": {},
            "iter_content": lambda self, chunk_size: iter(
                [
                    json.dumps(
                        {
                            "code": "invalid_assertion",
                            "request_id": "request-room-audio-expired",
                            "message": "opaque",
                            "reason_codes": [
                                "meeting_room_audio_source_receipt_expired"
                            ],
                        }
                    ).encode()
                ]
            ),
            "close": lambda self: None,
        },
    )()

    with pytest.raises(RecordingContractRefused) as caught:
        read_bounded_core_json(
            response,
            RecordingContractRefused,
            client_error_status=None,
        )

    assert caught.value.core_error_code == "invalid_assertion"
    assert caught.value.core_reason_codes == (
        "meeting_room_audio_source_receipt_expired",
    )


def test_conflicting_manifest_is_rejected_without_second_core_call(source_run):
    delivered = _core_success(body={"source_receipt": "unused"})
    delivered.pop("compact")
    with patch(
        "core.mastrao_room_audio_source.post_core_json", return_value=delivered
    ) as core:
        assert _post(source_run).status_code == 200
        assert (
            _post(source_run, _manifest(source_run, byte_size=64_001)).status_code
            == 409
        )

    assert core.call_count == 1


@pytest.mark.parametrize("field", ["version", "channel_count"])
def test_manifest_integer_fields_refuse_booleans(source_run, field):
    response = _post(source_run, _manifest(source_run, **{field: True}))

    assert response.status_code == 404
    assert models.MastraoRoomAudioSourceOutbox.objects.count() == 0


def test_gap_sequence_refuses_boolean(source_run):
    manifest = _manifest(source_run, gap_count=1, total_gap_ms=1_000)
    gap = {
        "sequence": False,
        "generation": 1,
        "started_at_ms": 1_001_000,
        "ended_at_ms": 1_002_000,
        "reason": "source_reconnect",
    }

    response = _post(source_run, manifest, [gap])

    assert response.status_code == 404
    assert models.MastraoRoomAudioSourceOutbox.objects.count() == 0


def test_worker_token_must_match_the_persisted_destination(source_run, settings):
    wrong_worker_token = (
        api.AccessToken(
            settings.LIVEKIT_CONFIGURATION["api_key"],
            settings.LIVEKIT_CONFIGURATION["api_secret"],
        )
        .with_identity(f"room-audio-worker-{'0' * 32}")
        .with_grants(
            api.VideoGrants(
                room=str(
                    models.MastraoRoomAudioRunEffect.objects.get().room_binding.room_id
                ),
                room_join=True,
                can_publish=False,
                can_publish_data=False,
                can_subscribe=False,
            )
        )
        .to_jwt()
    )

    with patch("core.mastrao_room_audio_source.post_core_json") as core:
        response = _post(source_run, worker_token=wrong_worker_token)

    assert response.status_code == 404
    assert models.MastraoRoomAudioSourceOutbox.objects.count() == 0
    core.assert_not_called()


def test_expired_claim_cannot_finish_after_another_worker_reclaims(source_run):
    unavailable = RecordingContractRefused(status=503)
    with patch(
        "core.mastrao_room_audio_source.post_core_json", side_effect=unavailable
    ):
        assert _post(source_run).status_code == 503
    row = models.MastraoRoomAudioSourceOutbox.objects.get()
    row.next_attempt_at = timezone.now()
    row.save(update_fields=["next_attempt_at"])

    first_claim, claimed = claim_room_audio_source(row.pk)
    assert claimed is True
    models.MastraoRoomAudioSourceOutbox.objects.filter(pk=row.pk).update(
        claim_until=timezone.now() - timedelta(seconds=1),
        next_attempt_at=timezone.now(),
    )
    second_claim, reclaimed = claim_room_audio_source(row.pk)
    assert reclaimed is True
    assert second_claim.claim_id != first_claim.claim_id

    assert (
        _mark_delivered(row.pk, first_claim.claim_id, uuid4(), replayed=False) is False
    )
    assert (
        _mark_delivered(row.pk, second_claim.claim_id, uuid4(), replayed=False) is True
    )


def test_retry_budget_exhaustion_enters_observable_terminal_state(source_run):
    unavailable = RecordingContractRefused(status=503)
    with patch(
        "core.mastrao_room_audio_source.post_core_json", side_effect=unavailable
    ):
        assert _post(source_run).status_code == 503
    row = models.MastraoRoomAudioSourceOutbox.objects.get()
    row.attempt_count = MAX_ATTEMPTS - 1
    row.next_attempt_at = timezone.now()
    row.save(update_fields=["attempt_count", "next_attempt_at"])

    with patch(
        "core.mastrao_room_audio_source.post_core_json", side_effect=unavailable
    ):
        assert _post(source_run).status_code == 503

    row.refresh_from_db()
    assert row.state == models.MastraoRoomAudioSourceOutbox.State.EXHAUSTED
    assert row.attempt_count == MAX_ATTEMPTS
    assert row.claim_id is None
    assert row.claim_until is None


def test_crashed_final_attempt_becomes_exhausted_after_lease(source_run, settings):
    unavailable = RecordingContractRefused(status=503)
    with patch(
        "core.mastrao_room_audio_source.post_core_json", side_effect=unavailable
    ):
        assert _post(source_run).status_code == 503
    row = models.MastraoRoomAudioSourceOutbox.objects.get()
    row.attempt_count = MAX_ATTEMPTS
    row.claim_id = uuid4()
    row.claim_until = timezone.now() - timedelta(seconds=1)
    row.next_attempt_at = timezone.now()
    row.save(
        update_fields=[
            "attempt_count",
            "claim_id",
            "claim_until",
            "next_attempt_at",
        ]
    )
    settings.CELERY_ENABLED = True

    assert schedule_room_audio_source_delivery(limit=1) == 0

    row.refresh_from_db()
    assert row.state == models.MastraoRoomAudioSourceOutbox.State.EXHAUSTED
    assert row.claim_id is None
    assert row.claim_until is None


def test_core_timeout_must_fit_the_delivery_lease(source_run, settings):
    settings.MASTRAO_CORE_ROOM_AUDIO_SOURCE_TIMEOUT_SECONDS = 31

    with patch("core.mastrao_room_audio_source.post_core_json") as core:
        response = _post(source_run)

    assert response.status_code == 503
    core.assert_not_called()


def test_scheduler_queues_only_pending_rows(source_run, settings):
    unavailable = RecordingContractRefused(status=503)
    with patch(
        "core.mastrao_room_audio_source.post_core_json", side_effect=unavailable
    ):
        assert _post(source_run).status_code == 503
    row = models.MastraoRoomAudioSourceOutbox.objects.get()
    row.next_attempt_at = timezone.now()
    row.save(update_fields=["next_attempt_at"])
    settings.CELERY_ENABLED = True

    with patch(
        "core.tasks.native_capture.process_room_audio_sources.apply_async"
    ) as enqueue:
        assert schedule_room_audio_source_delivery(limit=1) == 1

    args = enqueue.call_args.kwargs["args"]
    assert args[0] == str(row.pk)
    assert args[2] == row.attempt_count
    reserved = models.MastraoRoomAudioSourceOutbox.objects.get(pk=row.pk)
    assert args[1] == str(reserved.claim_id)
    assert reserved.claim_until > timezone.now() + timedelta(seconds=250)
    assert schedule_room_audio_source_delivery(limit=1) == 0
    delivered = _core_success(body={"source_receipt": "unused"})
    delivered.pop("compact")
    with patch("core.mastrao_room_audio_source.post_core_json", return_value=delivered):
        assert process_room_audio_sources(*args) is True
    reserved.refresh_from_db()
    assert reserved.state == models.MastraoRoomAudioSourceOutbox.State.DELIVERED


def test_scheduler_releases_reservation_when_broker_refuses(source_run, settings):
    unavailable = RecordingContractRefused(status=503)
    with patch(
        "core.mastrao_room_audio_source.post_core_json", side_effect=unavailable
    ):
        assert _post(source_run).status_code == 503
    row = models.MastraoRoomAudioSourceOutbox.objects.get()
    row.next_attempt_at = timezone.now()
    row.save(update_fields=["next_attempt_at"])
    settings.CELERY_ENABLED = True

    with patch(
        "core.tasks.native_capture.process_room_audio_sources.apply_async",
        side_effect=RuntimeError("broker unavailable"),
    ):
        assert schedule_room_audio_source_delivery(limit=1) == 0

    row.refresh_from_db()
    assert row.claim_id is None
    assert row.claim_until is None
