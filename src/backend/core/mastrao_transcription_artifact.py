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
ANONYMOUS_SPEAKER_LABEL = "Locuteur"


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


def _overlap_ms(sample, segment):
    ended = sample.speaking_ended_at_ms
    if ended is None:
        ended = segment["end_ms"]
    return min(ended, segment["end_ms"]) - max(
        sample.speaking_started_at_ms, segment["start_ms"]
    )


def map_speakers(transcript, samples):
    """Map acoustic speakers to participants via the active-speaker timeline.

    Fallback is deliberate: without a timeline (the LiveKit webhook stream
    exposes no active-speaker events today), every acoustic speaker keeps a
    stable anonymous label.
    """

    anonymous = {}
    for segment in transcript["segments"]:
        speaker = segment["speaker"]
        best_ref, best_overlap = None, 0
        for sample in samples:
            overlap = _overlap_ms(sample, segment)
            if overlap > best_overlap:
                best_ref, best_overlap = sample.participant_ref, overlap
        if best_ref is not None and best_overlap * 2 > (
            segment["end_ms"] - segment["start_ms"]
        ):
            segment["speaker"] = {"kind": "participant", "ref": best_ref}
            continue
        label = anonymous.setdefault(
            speaker["ref"], f"{ANONYMOUS_SPEAKER_LABEL} {len(anonymous) + 1}"
        )
        segment["speaker"] = {"kind": "anonymous", "ref": label}
    return transcript


def persist_transcript(transcription_ref, transcript):
    """Store the canonical transcript JSON and return its exact artifact facts."""

    payload = json.dumps(
        transcript, sort_keys=True, separators=(",", ":"), ensure_ascii=False
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
        "segment_count": len(transcript["segments"]),
        "engine_ref": transcript["engine_ref"],
    }
