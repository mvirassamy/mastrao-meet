"""Verified audio extraction and transcript artifact persistence."""

import hashlib
import json
import subprocess
import tempfile
import wave
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage

from botocore.exceptions import BotoCoreError, ClientError

from core.mastrao_recording_access import _open_verified_stream
from core.mastrao_transcription_contract import TranscriptionContractRefused

TRANSCRIPT_PREFIX = "mastrao-transcripts"
RESULT_RECOVERY_PREFIX = "mastrao-transcript-results"
FFMPEG_TIMEOUT_SECONDS = 900
MAX_AUDIO_BYTES = 2_000_000_000
ARTIFACT_SCHEMA_VERSION = 1


@dataclass
class ExtractedAudio:
    """Temporary extracted audio plus content fingerprint. Caller must close()."""

    path: Path
    sha256: str
    byte_size: int
    duration_ms: int
    codec: str
    workdir: tempfile.TemporaryDirectory | None = None

    def close(self):
        """Delete the temporary WAV directory if this extraction still owns it."""

        if self.workdir is not None:
            self.workdir.cleanup()
            self.workdir = None

    def read_bounded(self):
        """Read the extracted WAV when it is small enough to load in memory."""

        if self.byte_size > MAX_AUDIO_BYTES:
            raise TranscriptionContractRefused(status=503)
        return self.path.read_bytes()


def _hash_file(path):
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_AUDIO_BYTES:
                raise TranscriptionContractRefused(status=503)
            digest.update(chunk)
    if size < 1:
        raise TranscriptionContractRefused(status=503)
    return digest.hexdigest(), size


def _wav_duration_ms(path):
    try:
        with wave.open(str(path), "rb") as handle:
            frames = handle.getnframes()
            rate = handle.getframerate()
    except (wave.Error, OSError) as error:
        raise TranscriptionContractRefused(status=503) from error
    if rate < 1:
        raise TranscriptionContractRefused(status=503)
    return int(frames * 1000 / rate)


def extract_verified_audio_file(object_ref, expected_size, expected_checksum):
    """Extract mono 16 kHz WAV to a temp file without buffering the WAV in RAM."""

    # The WAV must outlive this function; ExtractedAudio.close() owns cleanup.
    workdir = tempfile.TemporaryDirectory(  # pylint: disable=consider-using-with
        prefix="mastrao_transcribe_"
    )
    created = False
    try:
        source_path = Path(workdir.name) / "verified-source.mp4"
        audio_path = Path(workdir.name) / "audio-16k-mono.wav"
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
        digest, byte_size = _hash_file(audio_path)
        duration_ms = max(1, _wav_duration_ms(audio_path))
        created = True
        return ExtractedAudio(
            path=audio_path,
            sha256=digest,
            byte_size=byte_size,
            duration_ms=duration_ms,
            codec="wav_pcm_s16le",
            workdir=workdir,
        )
    finally:
        if not created:
            workdir.cleanup()


def extract_verified_audio(object_ref, expected_size, expected_checksum):
    """Extract mono 16 kHz WAV bytes from the checksum-verified MP4.

    The MP4 is re-verified byte-for-byte through the existing recording
    access stream; no new storage path or credential is introduced.
    Qualification helpers may still load the bounded WAV; the Celery
    pipeline uses extract_verified_audio_file instead.
    """

    extracted = extract_verified_audio_file(
        object_ref, expected_size, expected_checksum
    )
    try:
        return extracted.read_bounded()
    finally:
        extracted.close()


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
                **(
                    {"confidence": segment["confidence"]}
                    if segment.get("confidence") is not None
                    else {}
                ),
            }
            for segment in transcript["segments"]
        ],
    }
    if isinstance(transcript.get("language"), str):
        artifact["language"] = transcript["language"]
    return artifact


def persist_transcript(transcription_ref, transcript, object_ref=None):
    """Store the canonical transcript JSON and return its exact artifact facts."""

    artifact = _canonical_artifact(transcription_ref, transcript)
    payload = json.dumps(
        artifact,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode()
    checksum = hashlib.sha256(payload).hexdigest()
    object_ref = object_ref or canonical_transcript_object_ref(transcription_ref)
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


def persist_result_recovery(attempt_ref, transcript):
    """Store a bounded normalized result so redelivery does not recall ASR."""

    payload = json.dumps(
        transcript,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode()
    object_ref = f"{RESULT_RECOVERY_PREFIX}/{attempt_ref}.json"
    try:
        if not default_storage.exists(object_ref):
            default_storage.save(object_ref, ContentFile(payload))
    except (BotoCoreError, ClientError, OSError, ValueError) as error:
        raise TranscriptionContractRefused(status=503) from error
    return object_ref, hashlib.sha256(payload).hexdigest()


def recover_persisted_transcript(object_ref, transcription_ref, engine_ref):
    """Resume from a predeclared object without another ASR call."""

    try:
        if not default_storage.exists(object_ref):
            return None
        with default_storage.open(object_ref, "rb") as stream:
            stored = stream.read(MAX_AUDIO_BYTES)
    except (BotoCoreError, ClientError, OSError, ValueError) as error:
        raise TranscriptionContractRefused(status=503) from error
    try:
        payload = json.loads(stored)
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as error:
        raise TranscriptionContractRefused(status=409) from error
    if (
        not isinstance(payload, dict)
        or payload.get("transcription_ref") != transcription_ref
        or payload.get("version") != ARTIFACT_SCHEMA_VERSION
    ):
        raise TranscriptionContractRefused(status=409)
    return {
        "transcript_artifact_ref": f"transcript_{uuid4().hex}",
        "object_ref": object_ref,
        "byte_size": len(stored),
        "checksum_digest": hashlib.sha256(stored).hexdigest(),
        "segment_count": len(payload.get("segments") or []),
        "engine_ref": engine_ref,
    }


def load_result_recovery(object_ref):
    """Load a previously persisted provider result, or None if it is missing."""

    try:
        if not default_storage.exists(object_ref):
            return None
        with default_storage.open(object_ref, "rb") as stream:
            stored = stream.read(MAX_AUDIO_BYTES)
        return json.loads(stored)
    except (BotoCoreError, ClientError, OSError, ValueError, json.JSONDecodeError):
        return None


def canonical_transcript_object_ref(transcription_ref):
    """Return the exact object key derived from one authorized transcription."""

    return f"{TRANSCRIPT_PREFIX}/{transcription_ref}.json"


def delete_transcript_object(object_ref):
    """Remove one Meet-written transcript object after a definitive Core refusal."""

    expected_prefix = f"{TRANSCRIPT_PREFIX}/"
    if not isinstance(object_ref, str) or not object_ref.startswith(expected_prefix):
        return
    try:
        default_storage.delete(object_ref)
    except (BotoCoreError, ClientError, OSError, ValueError) as error:
        raise TranscriptionContractRefused(status=503) from error
