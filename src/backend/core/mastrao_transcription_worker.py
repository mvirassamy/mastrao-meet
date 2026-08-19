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

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

import requests

from core.mastrao_transcription_contract import TranscriptionContractRefused

FAKE_ENGINE_REF = "fake-asr-deterministic-v1"
ASR_MODES = {"fake", "real"}
TRANSCRIPT_SCHEMA_VERSION = 1
MAX_WORKER_RESPONSE_BYTES = 5_000_000
MAX_SEGMENTS = 10_000
SEGMENT_DURATION_MS = 4_000

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
        or not isinstance(transcript.get("segments"), list)
        or len(transcript["segments"]) > MAX_SEGMENTS
    ):
        raise TranscriptionContractRefused(status=503)
    for segment in transcript["segments"]:
        speaker = segment.get("speaker") if isinstance(segment, dict) else None
        if (
            not isinstance(segment, dict)
            or not isinstance(segment.get("segment_id"), str)
            or not isinstance(segment.get("start_ms"), int)
            or not isinstance(segment.get("end_ms"), int)
            or segment["start_ms"] < 0
            or segment["end_ms"] <= segment["start_ms"]
            or not isinstance(segment.get("text"), str)
            or not isinstance(segment.get("confidence"), (int, float))
            or not isinstance(speaker, dict)
            or not isinstance(speaker.get("kind"), str)
            or not isinstance(speaker.get("ref"), str)
        ):
            raise TranscriptionContractRefused(status=503)
    return transcript


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
        return _validated_transcript(_real_transcribe(audio_bytes))
    return _validated_transcript(_fake_transcribe(audio_bytes))
