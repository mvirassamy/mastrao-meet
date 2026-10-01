"""Compressed FLAC extraction must cover 30 and 60 minutes without truncation."""

import subprocess
from io import BytesIO
from pathlib import Path
from unittest import mock

import pytest

from core.mastrao_transcription_artifact import (
    PROVIDER_EGRESS_BYTES,
    _media_duration_ms,
    extract_verified_audio_file,
)
from core.mastrao_transcription_contract import TranscriptionContractRefused


def _encode_silence_flac(destination: Path, seconds: int):
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "quiet",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=16000:cl=mono",
            "-t",
            str(seconds),
            "-c:a",
            "flac",
            "-compression_level",
            "8",
            str(destination),
        ],
        check=True,
        timeout=120,
    )


def test_thirty_and_sixty_minute_flac_fit_the_provider_cap(tmp_path):
    """Keep representative long FLAC inputs within the transcription provider cap."""

    for seconds in (30 * 60, 60 * 60):
        path = tmp_path / f"silence-{seconds}.flac"
        _encode_silence_flac(path, seconds)
        duration_ms = _media_duration_ms(path)
        assert abs(duration_ms - seconds * 1000) < 1000
        assert path.stat().st_size < PROVIDER_EGRESS_BYTES


def test_verified_source_disk_exhaustion_is_retryable():
    """A full temporary volume must ask Celery to retry instead of wedging the effect."""

    stream = BytesIO(b"verified recording")
    with (
        mock.patch(
            "core.mastrao_transcription_artifact._open_verified_stream",
            return_value=stream,
        ),
        mock.patch.object(
            Path,
            "open",
            side_effect=OSError(28, "No space left on device"),
        ),
        pytest.raises(TranscriptionContractRefused) as refusal,
    ):
        extract_verified_audio_file("recording.mp4", 18, "a" * 64)

    assert refusal.value.status == 503
    assert stream.closed
