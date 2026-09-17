"""Native Core/gateway protocol. No direct provider, SQL or object-store access."""

import base64
import hashlib
import json
from urllib.parse import urlparse

from django.conf import settings

import requests

from core.mastrao_core_http import post_core_json, read_bounded_core_json
from core.mastrao_native_notice import native_envelope
from core.mastrao_recording_contract import RecordingContractRefused, _sign

PREPARE_PATH = "/internal/v1/meetings/capture/native/asr/prepare"
RESULT_PATH = "/internal/v1/meetings/capture/native/asr/result"
PREPARE_JOSE = "mastrao-native-source-asr-prepare+jws"
RESULT_JOSE = "mastrao-native-source-asr-result+jws"
GATEWAY_PATH = "/v1/native/transcribe"
MAX_AUDIO_BYTES = 16 * 1024**2
MAX_REPLY_BYTES = 24 * 1024**2


def prepare_native_asr(intent):
    """Core selects the profile/attempt and reads its own verified private object."""
    source_ref = intent.source_receipt["source_ref"]
    assertion = {
        **native_envelope("mastrao.meet-native-source-asr-prepare"),
        "organization_external_id": intent.organization_external_id,
        "source_ref": source_ref,
    }
    prepared = post_core_json(
        endpoint=settings.MASTRAO_CORE_NATIVE_ASR_PREPARE_ENDPOINT,
        expected_path=PREPARE_PATH,
        body={"request": _sign(assertion, PREPARE_JOSE)},
        timeout=20,
        refusal=RecordingContractRefused,
        maximum_response_bytes=MAX_REPLY_BYTES,
    )
    if (
        set(prepared)
        != {
            "version",
            "source_ref",
            "capture_ref",
            "epoch_ref",
            "metadata",
            "native_source_egress_grant",
            "audio_base64",
        }
        or type(prepared["version"]) is not int
        or prepared["version"] != 1
        or prepared["source_ref"] != source_ref
        or prepared["capture_ref"] != str(intent.capture_ref)
        or prepared["epoch_ref"] != str(intent.epoch_id)
        or not isinstance(prepared["metadata"], dict)
        or not isinstance(prepared["native_source_egress_grant"], str)
        or not 16 <= len(prepared["native_source_egress_grant"]) <= 32000
    ):
        raise RecordingContractRefused(status=503)
    audio = base64.b64decode(prepared["audio_base64"], validate=True)
    metadata = prepared["metadata"]
    if (
        not 1 <= len(audio) <= MAX_AUDIO_BYTES
        or hashlib.sha256(audio).hexdigest() != metadata.get("audio_sha256")
        or metadata.get("provider") != "mistral"
        or metadata.get("audio_codec") != "flac"
    ):
        raise RecordingContractRefused(status=503)
    return prepared, audio


def _gateway_target():
    endpoint = settings.MASTRAO_NATIVE_ASR_GATEWAY_ENDPOINT
    parsed = urlparse(endpoint)
    token = settings.MASTRAO_NATIVE_ASR_GATEWAY_AUTH_TOKEN
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "localhost", "::1", "asr-gateway"}
        or parsed.path != GATEWAY_PATH
        or any((parsed.username, parsed.password, parsed.query, parsed.fragment))
        or not isinstance(token, str)
        or not 32 <= len(token) <= 512
    ):
        raise RecordingContractRefused(status=503)
    return endpoint, token


def transcribe_native_asr(prepared, audio):
    """Only the native gateway can consume the source's one-send capability."""
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
                "audio": ("source.flac", audio, "audio/flac"),
            },
            headers={
                "Authorization": f"Bearer {token}",
                "X-Mastrao-Native-Source-Egress-Grant": prepared[
                    "native_source_egress_grant"
                ],
            },
            timeout=(5, 75),
            allow_redirects=False,
            stream=True,
        )
        result = read_bounded_core_json(
            response, RecordingContractRefused, maximum_bytes=MAX_REPLY_BYTES
        )
    if result.get("outcome") != "succeeded" or not isinstance(
        result.get("transcript"), dict
    ):
        raise RecordingContractRefused(status=503)
    if result["transcript"].get("audio_digest") != prepared["metadata"]["audio_sha256"]:
        raise RecordingContractRefused(status=503)
    return result


def upload_native_asr_result(intent, prepared, result):
    """No acoustic identity or invented meeting-global origin enters the artifact."""
    transcript = result["transcript"]
    artifact = {
        "version": 1,
        "source_ref": prepared["source_ref"],
        "epoch_ref": prepared["epoch_ref"],
        "attempt_ref": prepared["metadata"]["attempt_ref"],
        "audio_sha256": transcript["audio_digest"],
        "time_basis": "source_relative_ms",
        "coverage": "epoch_only",
        "segments": [
            {
                **{
                    key: segment[key]
                    for key in ("segment_id", "start_ms", "end_ms", "text")
                },
                **(
                    {"confidence": segment["confidence"]}
                    if "confidence" in segment
                    else {}
                ),
            }
            for segment in transcript["segments"]
        ],
        "provenance": result["provenance"],
        **({"language": transcript["language"]} if "language" in transcript else {}),
    }
    raw = json.dumps(
        artifact,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(raw) > MAX_AUDIO_BYTES:
        raise RecordingContractRefused(status=503)
    digest = hashlib.sha256(raw).hexdigest()
    assertion = {
        **native_envelope("mastrao.meet-native-source-asr-result"),
        "organization_external_id": intent.organization_external_id,
        "source_ref": prepared["source_ref"],
        "attempt_ref": prepared["metadata"]["attempt_ref"],
        "result_sha256": digest,
        "result_bytes": len(raw),
    }
    signed = _sign(assertion, RESULT_JOSE)
    receipt = post_core_json(
        endpoint=settings.MASTRAO_CORE_NATIVE_ASR_RESULT_ENDPOINT,
        expected_path=RESULT_PATH,
        body={
            "request": signed,
            "result_base64": base64.b64encode(raw).decode("ascii"),
        },
        headers={"Authorization": f"Bearer {signed}"},
        timeout=20,
        refusal=RecordingContractRefused,
    )
    if (
        set(receipt)
        != {
            "version",
            "state",
            "result_ref",
            "source_ref",
            "epoch_ref",
            "attempt_ref",
            "result_sha256",
            "coverage",
            "time_basis",
        }
        or type(receipt["version"]) is not int
        or receipt["version"] != 1
        or receipt["state"] != "verified"
        or receipt["source_ref"] != prepared["source_ref"]
        or receipt["epoch_ref"] != prepared["epoch_ref"]
        or receipt["attempt_ref"] != prepared["metadata"]["attempt_ref"]
        or receipt["result_sha256"] != digest
        or receipt["coverage"] != "epoch_only"
        or receipt["time_basis"] != "source_relative_ms"
    ):
        raise RecordingContractRefused(status=503)
    return {"core": receipt, "fingerprint": prepared["metadata"]["fingerprint"]}


def ack_native_asr(receipt):
    """Called only after the verified Core receipt has been persisted locally."""
    endpoint, token = _gateway_target()
    with requests.Session() as session:
        session.trust_env = False
        response = session.post(
            endpoint[: -len(GATEWAY_PATH)] + "/v1/native/attempts/ack",
            json={
                "attempt_ref": receipt["core"]["attempt_ref"],
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
        "attempt_ref": receipt["core"]["attempt_ref"],
    }:
        raise RecordingContractRefused(status=503)
