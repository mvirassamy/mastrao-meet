"""Compressed FLAC extraction must cover 30 and 60 minutes without truncation."""

import subprocess
from pathlib import Path

from core.mastrao_transcription_artifact import (
    PROVIDER_EGRESS_BYTES,
    _media_duration_ms,
)


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
    for seconds in (30 * 60, 60 * 60):
        path = tmp_path / f"silence-{seconds}.flac"
        _encode_silence_flac(path, seconds)
        duration_ms = _media_duration_ms(path)
        assert abs(duration_ms - seconds * 1000) < 1000
        assert path.stat().st_size < PROVIDER_EGRESS_BYTES
