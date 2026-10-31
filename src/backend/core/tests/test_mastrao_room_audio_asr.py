"""Durable ASR delivery for a verified mixed room source."""

# Fixtures are imported from the source-publication suite to preserve one setup.
# pylint: disable=missing-function-docstring,redefined-outer-name,unused-import

import base64
import hashlib
import io
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from core import models
from core.mastrao_recording_contract import RecordingContractRefused
from core.mastrao_room_audio_asr_client import (
    _gateway_target,
    prepare_room_audio_asr,
    upload_room_audio_asr_result,
)
from core.mastrao_room_audio_asr_worker import (
    process_room_audio_asr_source,
    schedule_room_audio_asr,
)
from core.tests.test_mastrao_room_audio_source import _core_success, _post, source_run

pytestmark = pytest.mark.django_db


def test_room_audio_uses_its_dedicated_gateway(settings):
    settings.MASTRAO_ROOM_AUDIO_ASR_GATEWAY_ENDPOINT = (
        "http://asr-gateway-room-audio:8787/v1/room/transcribe"
    )
    settings.MASTRAO_ROOM_AUDIO_ASR_GATEWAY_AUTH_TOKEN = "t" * 32

    assert _gateway_target() == (
        "http://asr-gateway-room-audio:8787/v1/room/transcribe",
        "t" * 32,
    )


def test_room_audio_refuses_the_native_gateway(settings):
    settings.MASTRAO_ROOM_AUDIO_ASR_GATEWAY_ENDPOINT = (
        "http://asr-gateway:8787/v1/room/transcribe"
    )
    settings.MASTRAO_ROOM_AUDIO_ASR_GATEWAY_AUTH_TOKEN = "t" * 32

    with pytest.raises(RecordingContractRefused):
        _gateway_target()


def _local_room_audio_prepare_fixture(audio=b"verified-room-audio"):
    audio_digest = hashlib.sha256(audio).hexdigest()
    object_key = hashlib.sha256(b"room-audio-run-fixture").hexdigest()
    source_id = "11111111-1111-4111-8111-111111111111"
    source = SimpleNamespace(
        source_id=source_id,
        manifest={
            "organization_external_id": "organization_room_audio_fixture",
            "object_ref": f"room-audio/v1/{object_key}.ogg",
            "byte_size": len(audio),
            "checksum_digest": audio_digest,
        },
    )
    prepared = {
        "version": 1,
        "source_id": source_id,
        "run_id": "22222222-2222-4222-8222-222222222222",
        "coverage": "room_mix",
        "speaker_attribution": "none",
        "metadata": {
            "audio_sha256": audio_digest,
            "provider": "mistral",
            "model": "voxtral-mini-2602",
            "audio_codec": "ogg",
        },
        "room_audio_source_egress_grant": "signed-room-source-grant",
    }
    return source, prepared, audio


def test_prepare_reads_and_verifies_the_meet_owned_room_audio_object():
    source, prepared, audio = _local_room_audio_prepare_fixture()

    with (
        patch(
            "core.mastrao_room_audio_asr_client._sign",
            return_value="signed-prepare-request",
        ),
        patch(
            "core.mastrao_room_audio_asr_client.post_core_json",
            return_value=prepared,
        ),
        patch(
            "core.mastrao_room_audio_asr_client.default_storage.open",
            return_value=io.BytesIO(audio),
        ) as storage,
    ):
        response, verified_audio = prepare_room_audio_asr(source)

    assert response == prepared
    assert verified_audio == audio
    storage.assert_called_once_with(source.manifest["object_ref"], "rb")


def test_prepare_refuses_changed_room_audio_bytes():
    source, prepared, _audio = _local_room_audio_prepare_fixture()

    with (
        patch(
            "core.mastrao_room_audio_asr_client._sign",
            return_value="signed-prepare-request",
        ),
        patch(
            "core.mastrao_room_audio_asr_client.post_core_json",
            return_value=prepared,
        ),
        patch(
            "core.mastrao_room_audio_asr_client.default_storage.open",
            return_value=io.BytesIO(b"changed-room-audio"),
        ),
        pytest.raises(RecordingContractRefused),
    ):
        prepare_room_audio_asr(source)


def test_prepare_refuses_room_audio_outside_the_private_namespace():
    source, prepared, audio = _local_room_audio_prepare_fixture()
    source.manifest["object_ref"] = "recordings/private-room-audio.ogg"

    with (
        patch(
            "core.mastrao_room_audio_asr_client._sign",
            return_value="signed-prepare-request",
        ),
        patch(
            "core.mastrao_room_audio_asr_client.post_core_json",
            return_value=prepared,
        ),
        patch(
            "core.mastrao_room_audio_asr_client.default_storage.open",
            return_value=io.BytesIO(audio),
        ) as storage,
        pytest.raises(RecordingContractRefused),
    ):
        prepare_room_audio_asr(source)

    storage.assert_not_called()


def test_prepare_refuses_missing_room_audio_object():
    source, prepared, _audio = _local_room_audio_prepare_fixture()

    with (
        patch(
            "core.mastrao_room_audio_asr_client._sign",
            return_value="signed-prepare-request",
        ),
        patch(
            "core.mastrao_room_audio_asr_client.post_core_json",
            return_value=prepared,
        ),
        patch(
            "core.mastrao_room_audio_asr_client.default_storage.open",
            side_effect=OSError("object unavailable"),
        ),
        pytest.raises(RecordingContractRefused),
    ):
        prepare_room_audio_asr(source)


@pytest.fixture
def delivered_room_source(source_run, settings):
    settings.ROOM_AUDIO_ASR_ENABLED = False
    delivered = _core_success(body={"source_receipt": "unused"})
    delivered.pop("compact")
    with patch("core.mastrao_room_audio_source.post_core_json", return_value=delivered):
        assert _post(source_run).status_code == 200
    return models.MastraoRoomAudioSourceOutbox.objects.get()


def test_room_mix_is_transcribed_and_acknowledged(
    delivered_room_source,
    settings,
):
    settings.ROOM_AUDIO_ASR_ENABLED = True
    prepared = {
        "source_id": str(delivered_room_source.source_id),
        "metadata": {"attempt_ref": "room_asr_attempt_123", "fingerprint": "a" * 64},
    }
    receipt = {
        "core": {"result_ref": "room_asr_result_123"},
        "attempt_ref": "room_asr_attempt_123",
        "fingerprint": "a" * 64,
    }

    with (
        patch(
            "core.mastrao_room_audio_asr_worker.prepare_room_audio_asr",
            return_value=(prepared, b"ogg"),
        ),
        patch(
            "core.mastrao_room_audio_asr_worker.transcribe_room_audio",
            return_value={"transcript": {}},
        ),
        patch(
            "core.mastrao_room_audio_asr_worker.upload_room_audio_asr_result",
            return_value=receipt,
        ),
        patch("core.mastrao_room_audio_asr_worker.ack_room_audio_asr") as ack,
    ):
        assert process_room_audio_asr_source(delivered_room_source.pk) is True

    delivered_room_source.refresh_from_db()
    assert delivered_room_source.asr_acknowledged is True
    assert delivered_room_source.asr_receipt == receipt
    assert delivered_room_source.asr_error == ""
    ack.assert_called_once_with(receipt)


def test_room_mix_failure_keeps_source_for_retry(delivered_room_source, settings):
    settings.ROOM_AUDIO_ASR_ENABLED = True
    with patch(
        "core.mastrao_room_audio_asr_worker.prepare_room_audio_asr",
        side_effect=RecordingContractRefused(status=503),
    ):
        assert process_room_audio_asr_source(delivered_room_source.pk) is False

    delivered_room_source.refresh_from_db()
    assert delivered_room_source.asr_acknowledged is False
    assert delivered_room_source.asr_claim is None
    assert delivered_room_source.asr_error == "room_audio_asr_delivery_failed"


def test_room_asr_scheduler_dispatches_the_requested_source(
    delivered_room_source,
    settings,
):
    settings.ROOM_AUDIO_ASR_ENABLED = True
    settings.CELERY_ENABLED = True

    with patch("core.mastrao_room_audio_asr_worker.current_app.send_task") as send_task:
        assert schedule_room_audio_asr(delivered_room_source.pk, limit=1) == 1

    send_task.assert_called_once_with(
        "core.process_room_audio_asr",
        args=[str(delivered_room_source.pk)],
        queue="mastrao-transcription",
    )


def test_ack_failure_keeps_core_receipt_for_retry(delivered_room_source, settings):
    settings.ROOM_AUDIO_ASR_ENABLED = True
    prepared = {
        "source_id": str(delivered_room_source.source_id),
        "metadata": {"attempt_ref": "room_asr_attempt_123", "fingerprint": "a" * 64},
    }
    receipt = {
        "core": {"result_ref": "room_asr_result_123"},
        "attempt_ref": "room_asr_attempt_123",
        "fingerprint": "a" * 64,
    }

    with (
        patch(
            "core.mastrao_room_audio_asr_worker.prepare_room_audio_asr",
            return_value=(prepared, b"ogg"),
        ),
        patch(
            "core.mastrao_room_audio_asr_worker.transcribe_room_audio",
            return_value={"transcript": {}},
        ),
        patch(
            "core.mastrao_room_audio_asr_worker.upload_room_audio_asr_result",
            return_value=receipt,
        ),
        patch(
            "core.mastrao_room_audio_asr_worker.ack_room_audio_asr",
            side_effect=RecordingContractRefused(status=503),
        ),
    ):
        assert process_room_audio_asr_source(delivered_room_source.pk) is False

    delivered_room_source.refresh_from_db()
    assert delivered_room_source.asr_acknowledged is False
    assert delivered_room_source.asr_receipt == receipt
    assert delivered_room_source.asr_error == "room_audio_asr_delivery_failed"


def test_result_upload_preserves_gateway_cost_and_provenance():
    prepared = {
        "source_id": "11111111-1111-4111-8111-111111111111",
        "run_id": "22222222-2222-4222-8222-222222222222",
        "metadata": {
            "attempt_ref": "room_asr_attempt_123",
            "fingerprint": "f" * 64,
        },
    }
    result = {
        "transcript": {
            "audio_digest": "a" * 64,
            "language": "fr",
            "segments": [
                {
                    "segment_id": "segment_1",
                    "start_ms": 0,
                    "end_ms": 1_000,
                    "text": "Bonjour depuis la salle.",
                }
            ],
        },
        "usage": {
            "usage_audio_seconds": 90,
            "estimated_cost_micros": 4_950,
            "currency": "USD",
            "tariff_version": "asr-tariff-v2",
        },
        "provenance": {
            "grant_semantic_digest": "c" * 64,
            "provider_egress_opened_at": 1_000,
            "provider_completed_at": 1_001,
        },
    }
    source = SimpleNamespace(
        manifest={"organization_external_id": "organization_room_audio_fixture"}
    )

    def accept_result(**kwargs):
        uploaded = json.loads(
            base64.b64decode(kwargs["body"]["result_base64"], validate=True)
        )
        return {
            "result_ref": uploaded["result_ref"],
            "source_id": prepared["source_id"],
            "coverage": "room_mix",
            "speaker_attribution": "none",
            "outcome": "verified",
        }

    with (
        patch(
            "core.mastrao_room_audio_asr_client._sign",
            return_value="signed-room-audio-result",
        ),
        patch(
            "core.mastrao_room_audio_asr_client.post_core_json",
            side_effect=accept_result,
        ) as post_core,
    ):
        receipt = upload_room_audio_asr_result(source, prepared, result)
        retry_receipt = upload_room_audio_asr_result(source, prepared, result)

    body = post_core.call_args.kwargs["body"]
    artifact = json.loads(base64.b64decode(body["result_base64"], validate=True))
    first_body = post_core.call_args_list[0].kwargs["body"]
    retry_body = post_core.call_args_list[1].kwargs["body"]
    assert artifact["estimated_cost_micros"] == 4_950
    assert artifact["grant_semantic_digest"] == "c" * 64
    assert artifact["provider_egress_opened_at"] == 1_000
    assert artifact["provider_completed_at"] == 1_001
    assert receipt["attempt_ref"] == "room_asr_attempt_123"
    assert receipt["fingerprint"] == "f" * 64
    assert receipt["core"]["result_ref"] == artifact["result_ref"]
    assert retry_receipt == receipt
    assert retry_body == first_body
