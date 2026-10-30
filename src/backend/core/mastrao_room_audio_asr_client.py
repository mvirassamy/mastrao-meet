"""Room-mix ASR protocol. Tenant Core owns grants and canonical results."""

import base64
import hashlib
import json
from urllib.parse import urlparse
from uuid import uuid4

from django.conf import settings
from django.core.files.storage import default_storage

import requests
from botocore.exceptions import BotoCoreError, ClientError

from core.mastrao_core_http import post_core_json, read_bounded_core_json
from core.mastrao_native_asr_client import _read_native_gateway_response
from core.mastrao_native_authorization import native_envelope
from core.mastrao_recording_contract import RecordingContractRefused, _sign

PREPARE_PATH = "/internal/v1/meetings/room-audio/asr/prepare"
RESULT_PATH = "/internal/v1/meetings/room-audio/asr/result"
PREPARE_JOSE = "mastrao-room-audio-asr-prepare+jws"
RESULT_JOSE = "mastrao-room-audio-asr-result+jws"
GATEWAY_PATH = "/v1/room/transcribe"
MAX_AUDIO_BYTES = 16 * 1024**2
MAX_REPLY_BYTES = 24 * 1024**2


def _room_audio_object_identity(source, metadata):
    manifest = source.manifest
    object_ref = manifest.get("object_ref")
    byte_size = manifest.get("byte_size")
    checksum_digest = manifest.get("checksum_digest")
    if not isinstance(checksum_digest, str) or len(checksum_digest) != 64:
        raise RecordingContractRefused(status=503)
    if object_ref != f"room-audio/v1/{checksum_digest}.ogg":
        raise RecordingContractRefused(status=503)
    if isinstance(byte_size, bool) or not isinstance(byte_size, int):
        raise RecordingContractRefused(status=503)
    if not 1 <= byte_size <= MAX_AUDIO_BYTES:
        raise RecordingContractRefused(status=503)
    if metadata.get("audio_sha256") != checksum_digest:
        raise RecordingContractRefused(status=503)
    return object_ref, byte_size, checksum_digest


def _read_verified_room_audio(source, metadata):
    """Read one Meet-owned room mix and prove its published byte identity."""
    object_ref, byte_size, checksum_digest = _room_audio_object_identity(
        source, metadata
    )
    try:
        with default_storage.open(object_ref, "rb") as stored_audio:
            audio = stored_audio.read(MAX_AUDIO_BYTES + 1)
    except (OSError, BotoCoreError, ClientError) as error:
        raise RecordingContractRefused(status=503) from error
    if (
        not isinstance(audio, bytes)
        or len(audio) != byte_size
        or hashlib.sha256(audio).hexdigest() != checksum_digest
    ):
        raise RecordingContractRefused(status=503)
    return audio


def prepare_room_audio_asr(source):
    """Request one Core-authorized room-mix source and verify its binding."""
    assertion = {
        **native_envelope("mastrao.meet-room-audio-asr-prepare"),
        "organization_external_id": source.manifest["organization_external_id"],
        "source_id": str(source.source_id),
    }
    prepared = post_core_json(
        endpoint=settings.MASTRAO_CORE_ROOM_AUDIO_ASR_PREPARE_ENDPOINT,
        expected_path=PREPARE_PATH,
        body={"request": _sign(assertion, PREPARE_JOSE)},
        timeout=20,
        refusal=RecordingContractRefused,
        maximum_response_bytes=MAX_REPLY_BYTES,
    )
    required = {
        "version",
        "source_id",
        "run_id",
        "coverage",
        "speaker_attribution",
        "metadata",
        "room_audio_source_egress_grant",
    }
    if set(prepared) != required:
        raise RecordingContractRefused(status=503)
    if not isinstance(prepared["version"], int) or isinstance(
        prepared["version"], bool
    ):
        raise RecordingContractRefused(status=503)
    if prepared["version"] != 1 or prepared["source_id"] != str(source.source_id):
        raise RecordingContractRefused(status=503)
    if prepared["coverage"] != "room_mix":
        raise RecordingContractRefused(status=503)
    if prepared["speaker_attribution"] != "none":
        raise RecordingContractRefused(status=503)
    if not isinstance(prepared["metadata"], dict):
        raise RecordingContractRefused(status=503)
    if not isinstance(prepared["room_audio_source_egress_grant"], str):
        raise RecordingContractRefused(status=503)
    metadata = prepared["metadata"]
    if (
        metadata.get("provider") != "mistral"
        or metadata.get("model") != "voxtral-mini-2602"
        or metadata.get("audio_codec") != "ogg"
    ):
        raise RecordingContractRefused(status=503)
    audio = _read_verified_room_audio(source, metadata)
    return prepared, audio


def _gateway_target():
    endpoint = settings.MASTRAO_ROOM_AUDIO_ASR_GATEWAY_ENDPOINT
    parsed = urlparse(endpoint)
    token = settings.MASTRAO_ROOM_AUDIO_ASR_GATEWAY_AUTH_TOKEN
    if parsed.scheme != "http":
        raise RecordingContractRefused(status=503)
    if parsed.hostname not in {
        "127.0.0.1",
        "localhost",
        "::1",
        "asr-gateway-room-audio",
    }:
        raise RecordingContractRefused(status=503)
    if parsed.path != GATEWAY_PATH:
        raise RecordingContractRefused(status=503)
    if any((parsed.username, parsed.password, parsed.query, parsed.fragment)):
        raise RecordingContractRefused(status=503)
    if not isinstance(token, str) or not 32 <= len(token) <= 512:
        raise RecordingContractRefused(status=503)
    return endpoint, token


def transcribe_room_audio(prepared, audio):
    """Submit one verified room mix to the private ASR Gateway."""
    endpoint, token = _gateway_target()
    with requests.Session() as session:
        session.trust_env = False
        response = session.post(
            endpoint,
            files={
                "metadata": (
                    "metadata.json",
                    json.dumps(prepared["metadata"]),
                    "application/json",
                ),
                "audio": ("room-source.ogg", audio, "audio/ogg"),
            },
            headers={
                "Authorization": f"Bearer {token}",
                "X-Mastrao-Room-Audio-Source-Egress-Grant": prepared[
                    "room_audio_source_egress_grant"
                ],
            },
            timeout=(5, 180),
            allow_redirects=False,
            stream=True,
        )
        result = _read_native_gateway_response(response)
    if result.get("outcome") != "succeeded" or not isinstance(
        result.get("transcript"), dict
    ):
        raise RecordingContractRefused(status=503)
    if result["transcript"].get("audio_digest") != prepared["metadata"]["audio_sha256"]:
        raise RecordingContractRefused(status=503)
    return result


def upload_room_audio_asr_result(source, prepared, result):
    """Upload one canonical room transcript with exact cost and provenance."""
    transcript = result["transcript"]
    usage = result.get("usage")
    provenance = result.get("provenance")
    if (
        not isinstance(usage, dict)
        or not isinstance(provenance, dict)
        or provenance.get("grant_semantic_digest") is None
        or provenance.get("provider_egress_opened_at") is None
        or provenance.get("provider_completed_at") is None
    ):
        raise RecordingContractRefused(status=503)
    artifact = {
        "version": 1,
        "type": "mastrao.room-mix-asr-result",
        "result_ref": str(uuid4()),
        "attempt_ref": prepared["metadata"]["attempt_ref"],
        "run_id": prepared["run_id"],
        "source_id": prepared["source_id"],
        "coverage": "room_mix",
        "time_basis": "source_relative_ms",
        "provider_ref": "mistral",
        "model_ref": "voxtral-mini-2602",
        "grant_semantic_digest": provenance["grant_semantic_digest"],
        "provider_egress_opened_at": provenance["provider_egress_opened_at"],
        "provider_completed_at": provenance["provider_completed_at"],
        "usage_audio_seconds": usage["usage_audio_seconds"],
        "estimated_cost_micros": usage["estimated_cost_micros"],
        "currency": usage["currency"],
        "tariff_catalog_version": usage["tariff_version"],
        "audio_digest": transcript["audio_digest"],
        "segments": [
            {
                **{
                    key: segment[key]
                    for key in ("segment_id", "start_ms", "end_ms", "text")
                },
                "speaker": None,
                **(
                    {"confidence": segment["confidence"]}
                    if "confidence" in segment
                    else {}
                ),
            }
            for segment in transcript["segments"]
        ],
        **({"language": transcript["language"]} if "language" in transcript else {}),
    }
    raw = json.dumps(
        artifact,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()
    assertion = {
        **native_envelope("mastrao.meet-room-audio-asr-result"),
        "organization_external_id": source.manifest["organization_external_id"],
        "source_id": prepared["source_id"],
        "attempt_ref": prepared["metadata"]["attempt_ref"],
        "result_sha256": digest,
        "result_bytes": len(raw),
    }
    signed = _sign(assertion, RESULT_JOSE)
    receipt = post_core_json(
        endpoint=settings.MASTRAO_CORE_ROOM_AUDIO_ASR_RESULT_ENDPOINT,
        expected_path=RESULT_PATH,
        body={
            "request": signed,
            "result_base64": base64.b64encode(raw).decode("ascii"),
        },
        headers={"Authorization": f"Bearer {signed}"},
        timeout=20,
        refusal=RecordingContractRefused,
    )
    expected = {
        "result_ref",
        "source_id",
        "coverage",
        "speaker_attribution",
        "outcome",
    }
    if set(receipt) != expected or receipt["outcome"] != "verified":
        raise RecordingContractRefused(status=503)
    if receipt["source_id"] != prepared["source_id"]:
        raise RecordingContractRefused(status=503)
    if receipt["result_ref"] != artifact["result_ref"]:
        raise RecordingContractRefused(status=503)
    if receipt["coverage"] != "room_mix":
        raise RecordingContractRefused(status=503)
    if receipt["speaker_attribution"] != "none":
        raise RecordingContractRefused(status=503)
    return {
        "core": receipt,
        "attempt_ref": prepared["metadata"]["attempt_ref"],
        "fingerprint": prepared["metadata"]["fingerprint"],
    }


def ack_room_audio_asr(receipt):
    """Acknowledge the Gateway attempt only after Core verification."""
    endpoint, token = _gateway_target()
    with requests.Session() as session:
        session.trust_env = False
        response = session.post(
            endpoint[: -len(GATEWAY_PATH)] + "/v1/room/attempts/ack",
            json={
                "attempt_ref": receipt["attempt_ref"],
                "fingerprint": receipt["fingerprint"],
            },
            headers={"Authorization": f"Bearer {token}"},
            timeout=5,
            allow_redirects=False,
            stream=True,
        )
        acknowledged = read_bounded_core_json(response, RecordingContractRefused)
    if acknowledged != {
        "outcome": "acked",
        "attempt_ref": receipt["attempt_ref"],
    }:
        raise RecordingContractRefused(status=503)
