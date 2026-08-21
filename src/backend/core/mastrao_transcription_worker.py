"""Minimal internal ASR worker boundary: audio in, transcript JSON out.

Two engines share one strict output contract:

- a deterministic fake engine for provider-free qualification (default);
- a real HTTP engine skeleton behind explicit settings, never active by
  default, mirroring the summary-service transport patterns.

The worker never receives storage credentials and never logs content.
"""

# Strict schema validation keeps all transcript predicates visible in one place.
# pylint: disable=too-many-boolean-expressions

import hashlib
import json
import logging
import math
import re

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

import requests

from core.mastrao_transcription_contract import TranscriptionContractRefused

logger = logging.getLogger(__name__)
FAKE_ENGINE_REF = "fake-asr-deterministic-v1"
ASR_MODES = {"fake", "real"}
TRANSCRIPT_SCHEMA_VERSION = 1
MAX_WORKER_RESPONSE_BYTES = 5_000_000
MAX_SEGMENTS = 10_000
SEGMENT_DURATION_MS = 4_000
SEGMENT_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,160}$")
LANGUAGE_PATTERN = re.compile(r"^[a-z]{2}(?:-[A-Z]{2})?$")

_FAKE_LEXICON = (
    "audience",
    "cabinet",
    "client",
    "conclusion",
    "contrat",
    "dossier",
    "expertise",
    "juridiction",
    "mediation",
    "procedure",
    "preuve",
    "requete",
    "signature",
    "tribunal",
    "verdict",
    "delibere",
)


def _fake_segments(audio_digest):
    """Derive stable synthetic segments from the exact audio digest."""

    seed = bytes.fromhex(audio_digest)
    segment_count = 3 + seed[0] % 3
    segments = []
    for index in range(segment_count):
        start_ms = index * SEGMENT_DURATION_MS
        word_seed = seed[(index * 4) % len(seed) :] + seed
        words = [
            _FAKE_LEXICON[word_seed[position] % len(_FAKE_LEXICON)]
            for position in range(4 + word_seed[0] % 4)
        ]
        segments.append(
            {
                "segment_id": f"segment_{audio_digest[:16]}_{index:04d}",
                "start_ms": start_ms,
                "end_ms": start_ms + SEGMENT_DURATION_MS,
                "speaker": {"kind": "acoustic", "ref": f"SPEAKER_{seed[index] % 3}"},
                "text": " ".join(words),
                "confidence": round(0.80 + (word_seed[1] % 20) / 100, 2),
            }
        )
    return segments


def _fake_transcribe(audio_bytes):
    audio_digest = hashlib.sha256(audio_bytes).hexdigest()
    return {
        "version": TRANSCRIPT_SCHEMA_VERSION,
        "engine_ref": FAKE_ENGINE_REF,
        "language": "fr",
        "audio_digest": audio_digest,
        "segments": _fake_segments(audio_digest),
    }


def _real_transcribe(audio_bytes):
    """Skeleton for the sovereign ASR container; requires explicit settings.

    The endpoint is a private-network service that accepts raw WAV bytes and
    returns the same transcript JSON contract. Engine qualification (model,
    VAD, diarization) happens behind this boundary, not in Meet.
    """

    endpoint = settings.MASTRAO_TRANSCRIPTION_ASR_ENDPOINT
    if not endpoint:
        raise TranscriptionContractRefused(status=503)
    try:
        with requests.Session() as session:
            session.trust_env = False
            response = session.post(
                endpoint,
                data=audio_bytes,
                headers={"Content-Type": "audio/wav"},
                timeout=settings.MASTRAO_TRANSCRIPTION_ASR_TIMEOUT_SECONDS,
                allow_redirects=False,
                stream=True,
            )
            if response.status_code != 200:
                response.close()
                raise TranscriptionContractRefused(status=503)
            chunks = []
            size = 0
            for chunk in response.iter_content(chunk_size=65_536):
                size += len(chunk)
                if size > MAX_WORKER_RESPONSE_BYTES:
                    response.close()
                    raise TranscriptionContractRefused(status=503)
                chunks.append(chunk)
            response.close()
            return json.loads(b"".join(chunks))
    except (requests.RequestException, ValueError, UnicodeDecodeError) as error:
        raise TranscriptionContractRefused(status=503) from error


def _validated_transcript(transcript):
    """Fail closed on any transcript that violates the v1 schema."""

    if (
        not isinstance(transcript, dict)
        or transcript.get("version") != TRANSCRIPT_SCHEMA_VERSION
        or not isinstance(transcript.get("engine_ref"), str)
        or not isinstance(transcript.get("language"), str)
        or not LANGUAGE_PATTERN.fullmatch(transcript["language"])
        or not isinstance(transcript.get("segments"), list)
        or len(transcript["segments"]) > MAX_SEGMENTS
    ):
        raise TranscriptionContractRefused(status=503)
    segment_ids = set()
    for segment in transcript["segments"]:
        speaker = segment.get("speaker") if isinstance(segment, dict) else None
        confidence = segment.get("confidence") if isinstance(segment, dict) else None
        segment_id = segment.get("segment_id") if isinstance(segment, dict) else None
        if (
            not isinstance(segment, dict)
            or not isinstance(segment_id, str)
            or not SEGMENT_ID_PATTERN.fullmatch(segment_id)
            or segment_id in segment_ids
            or not isinstance(segment.get("start_ms"), int)
            or not isinstance(segment.get("end_ms"), int)
            or segment["start_ms"] < 0
            or segment["end_ms"] <= segment["start_ms"]
            or not isinstance(segment.get("text"), str)
            or not 1 <= len(segment["text"]) <= 4_000
            or (
                confidence is not None
                and (
                    not isinstance(confidence, (int, float))
                    or isinstance(confidence, bool)
                    or not math.isfinite(confidence)
                    or not 0 <= confidence <= 1
                )
            )
            or not isinstance(speaker, dict)
            or set(speaker) != {"kind", "ref"}
            or speaker.get("kind") != "acoustic"
            or not isinstance(speaker.get("ref"), str)
            or not 1 <= len(speaker["ref"]) <= 160
        ):
            raise TranscriptionContractRefused(status=503)
        segment_ids.add(segment_id)
    return transcript


def _gateway_fingerprint(extracted, provider, model, language="", context_bias=""):
    return hashlib.sha256(
        "|".join(
            [
                extracted.sha256,
                str(extracted.duration_ms),
                extracted.codec,
                provider,
                model,
                "asr-gateway-v1",
                "1",
                language or "",
                context_bias or "",
                "0",
            ]
        ).encode()
    ).hexdigest()


def _gateway_transcribe(extracted, attempt):
    """Call the private ASR Gateway. Never inherit proxy env or follow redirects."""

    endpoint = settings.MASTRAO_TRANSCRIPTION_ASR_ENDPOINT
    if not endpoint:
        raise TranscriptionContractRefused(status=503)
    if extracted.byte_size > 25_000_000:
        raise TranscriptionContractRefused(status=503)
    token = getattr(settings, "MASTRAO_ASR_GATEWAY_AUTH_TOKEN", "") or ""
    if not token:
        raise TranscriptionContractRefused(status=503)
    language = "fr"
    metadata = {
        "attempt_ref": attempt.attempt_ref,
        "fingerprint": _gateway_fingerprint(
            extracted,
            attempt.provider_ref,
            attempt.requested_model_ref,
            language=language,
        ),
        "provider": attempt.provider_ref,
        "model": attempt.requested_model_ref,
        "audio_sha256": extracted.sha256,
        "audio_duration_ms": extracted.duration_ms,
        "audio_codec": extracted.codec,
        "adapter_version": "asr-gateway-v1",
        "normalization_schema_version": "1",
        "language": language,
    }
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    response = None
    try:
        with extracted.path.open("rb") as audio, requests.Session() as session:
            session.trust_env = False
            response = session.post(
                endpoint,
                files={
                    "metadata": (
                        "metadata.json",
                        json.dumps(metadata),
                        "application/json",
                    ),
                    "audio": (extracted.path.name, audio, "audio/flac"),
                },
                headers=headers,
                timeout=settings.MASTRAO_TRANSCRIPTION_ASR_TIMEOUT_SECONDS,
                allow_redirects=False,
                stream=True,
            )
            if response.status_code in {401, 403}:
                raise TranscriptionContractRefused(status=503)
            payload = json.loads(_read_bounded_http_body(response))
    except TranscriptionContractRefused:
        raise
    except requests.RequestException as error:
        raise TranscriptionContractRefused(status=503, outcome="unknown") from error
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise TranscriptionContractRefused(status=503) from error
    finally:
        if response is not None:
            response.close()
    return _accepted_gateway_transcript(payload, extracted, attempt)


def _read_bounded_http_body(response):
    length = response.headers.get("Content-Length")
    if length is not None and int(length) > MAX_WORKER_RESPONSE_BYTES:
        raise TranscriptionContractRefused(status=503)
    chunks = []
    size = 0
    for chunk in response.iter_content(chunk_size=65_536):
        size += len(chunk)
        if size > MAX_WORKER_RESPONSE_BYTES:
            raise TranscriptionContractRefused(status=503)
        chunks.append(chunk)
    return b"".join(chunks)


def ack_gateway_attempt(extracted, attempt):
    """Tell the Gateway Meet persisted the result so it can drop the transcript."""

    endpoint = settings.MASTRAO_TRANSCRIPTION_ASR_ENDPOINT
    token = getattr(settings, "MASTRAO_ASR_GATEWAY_AUTH_TOKEN", "") or ""
    if not endpoint or not token:
        return
    ack_url = (
        endpoint[: -len("/v1/transcribe")] + "/v1/attempts/ack"
        if endpoint.endswith("/v1/transcribe")
        else f"{endpoint.rstrip('/')}/v1/attempts/ack"
    )
    try:
        with requests.Session() as session:
            session.trust_env = False
            response = session.post(
                ack_url,
                json={
                    "attempt_ref": attempt.attempt_ref,
                    "fingerprint": _gateway_fingerprint(
                        extracted,
                        attempt.provider_ref,
                        attempt.requested_model_ref,
                        language="fr",
                    ),
                },
                headers={"Authorization": f"Bearer {token}"},
                timeout=5,
                allow_redirects=False,
            )
    except requests.RequestException as error:
        raise TranscriptionContractRefused(status=503, outcome="retry") from error
    if response.status_code != 200:
        raise TranscriptionContractRefused(status=503, outcome="retry")


def _accepted_gateway_transcript(payload, extracted, attempt):
    if not isinstance(payload, dict):
        raise TranscriptionContractRefused(status=503)
    if payload.get("outcome") == "unknown" or payload.get("error") == (
        "PROVIDER_OUTCOME_UNKNOWN"
    ):
        raise TranscriptionContractRefused(status=409, outcome="unknown")
    transcript = payload.get("transcript")
    usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
    engine = transcript.get("engine_ref") if isinstance(transcript, dict) else None
    matched = (
        payload.get("outcome") == "succeeded"
        and isinstance(transcript, dict)
        and transcript.get("audio_digest") == extracted.sha256
        and isinstance(engine, str)
        and engine.startswith(f"{attempt.provider_ref}:{attempt.requested_model_ref}")
        and usage.get("provider") in {None, attempt.provider_ref}
        and usage.get("requested_model") in {None, attempt.requested_model_ref}
    )
    if not matched:
        raise TranscriptionContractRefused(status=503)
    transcript["_usage"] = usage
    return transcript


def transcribe_extracted(extracted, attempt):
    """Transcribe one extracted audio file through fake ASR or the Gateway."""

    mode = settings.MASTRAO_TRANSCRIPTION_ASR_MODE
    if mode not in ASR_MODES:
        raise ImproperlyConfigured(
            f"MASTRAO_TRANSCRIPTION_ASR_MODE must be one of {sorted(ASR_MODES)}, "
            f"got {mode!r}"
        )
    logger.info("mastrao transcription asr invoked")
    if mode == "real":
        return _validated_transcript(_gateway_transcribe(extracted, attempt))
    return _validated_transcript(_fake_transcribe(extracted.read_bounded()))


def transcribe_audio(audio_bytes):
    """Transcribe one bounded WAV payload with the configured engine."""

    if not isinstance(audio_bytes, bytes) or len(audio_bytes) < 1:
        raise TranscriptionContractRefused(status=503)
    mode = settings.MASTRAO_TRANSCRIPTION_ASR_MODE
    if mode not in ASR_MODES:
        raise ImproperlyConfigured(
            f"MASTRAO_TRANSCRIPTION_ASR_MODE must be one of {sorted(ASR_MODES)}, "
            f"got {mode!r}"
        )
    if mode == "real":
        logger.info("mastrao transcription asr invoked")
        return _validated_transcript(_real_transcribe(audio_bytes))
    logger.info("mastrao transcription asr invoked")
    return _validated_transcript(_fake_transcribe(audio_bytes))
