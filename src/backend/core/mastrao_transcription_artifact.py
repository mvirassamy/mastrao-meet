"""Verified audio extraction and transcript artifact persistence."""

import hashlib
import json
import subprocess
import tempfile
from pathlib import Path
from uuid import uuid4

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage

from botocore.exceptions import BotoCoreError, ClientError

from core.mastrao_recording_access import _open_verified_stream
from core.mastrao_transcription_contract import TranscriptionContractRefused

TRANSCRIPT_PREFIX = "mastrao-transcripts"
FFMPEG_TIMEOUT_SECONDS = 900
MAX_AUDIO_BYTES = 2_000_000_000
ARTIFACT_SCHEMA_VERSION = 1


def extract_verified_audio(object_ref, expected_size, expected_checksum):
    """Extract mono 16 kHz WAV bytes from the checksum-verified MP4.

    The MP4 is re-verified byte-for-byte through the existing recording
    access stream; no new storage path or credential is introduced.
    """

    with tempfile.TemporaryDirectory(prefix="mastrao_transcribe_") as workdir:
        source_path = Path(workdir) / "verified-source.mp4"
        audio_path = Path(workdir) / "audio-16k-mono.wav"
        stream = _open_verified_stream(object_ref, expected_size, expected_checksum)
        try:
            with source_path.open("wb") as destination:
                while chunk := stream.read(1024 * 1024):
                    destination.write(chunk)
        finally:
            stream.close()
        command = [
            "ffmpeg",
            "-v",
            "quiet",
            "-y",
            "-i",
            str(source_path),
            "-vn",
            "-acodec",
            "pcm_s16le",
            "-ar",
            "16000",
            "-ac",
            "1",
            str(audio_path),
        ]
        try:
            subprocess.run(  # noqa: S603
                command, check=True, timeout=FFMPEG_TIMEOUT_SECONDS
            )
        except (
            subprocess.CalledProcessError,
            subprocess.TimeoutExpired,
            OSError,
        ) as error:
            raise TranscriptionContractRefused(status=503) from error
        try:
            audio_bytes = audio_path.read_bytes()
        except OSError as error:
            raise TranscriptionContractRefused(status=503) from error
    if not 1 <= len(audio_bytes) <= MAX_AUDIO_BYTES:
        raise TranscriptionContractRefused(status=503)
    return audio_bytes


def map_speakers(transcript):
    """Give every acoustic speaker a stable anonymous index.

    The LiveKit webhook stream exposes no active-speaker events today, so
    participant mapping is out of scope; the anonymous fallback keeps the
    citation contract stable until a real timeline producer exists.
    """

    anonymous = {}
    for segment in transcript["segments"]:
        speaker = segment["speaker"]
        index = anonymous.setdefault(speaker["ref"], len(anonymous) + 1)
        segment["speaker"] = {"kind": "anonymous", "index": index}
    return transcript


def _canonical_artifact(transcription_ref, transcript):
    """Shape the exact Core transcript artifact schema v1: the citation
    anchor contract. Engine facts stay out of the artifact bytes; they are
    reported through the receipt, not the canonical JSON."""

    artifact = {
        "version": ARTIFACT_SCHEMA_VERSION,
        "transcription_ref": transcription_ref,
        "segments": [
            {
                "segment_id": segment["segment_id"],
                "start_ms": segment["start_ms"],
                "end_ms": segment["end_ms"],
                "speaker": segment["speaker"],
                "text": segment["text"],
                "confidence": segment["confidence"],
            }
            for segment in transcript["segments"]
        ],
    }
    if isinstance(transcript.get("language"), str):
        artifact["language"] = transcript["language"]
    return artifact


def persist_transcript(transcription_ref, transcript):
    """Store the canonical transcript JSON and return its exact artifact facts."""

    artifact = _canonical_artifact(transcription_ref, transcript)
    payload = json.dumps(
        artifact, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()
    checksum = hashlib.sha256(payload).hexdigest()
    object_ref = f"{TRANSCRIPT_PREFIX}/{transcription_ref}.json"
    try:
        if not default_storage.exists(object_ref):
            default_storage.save(object_ref, ContentFile(payload))
        with default_storage.open(object_ref, "rb") as stream:
            stored = stream.read(len(payload) + 1)
    except (BotoCoreError, ClientError, OSError, ValueError) as error:
        raise TranscriptionContractRefused(status=503) from error
    if hashlib.sha256(stored).hexdigest() != checksum:
        raise TranscriptionContractRefused(status=409)
    return {
        "transcript_artifact_ref": f"transcript_{uuid4().hex}",
        "object_ref": object_ref,
        "byte_size": len(payload),
        "checksum_digest": checksum,
        "segment_count": len(artifact["segments"]),
        "engine_ref": transcript["engine_ref"],
    }
