"""Materialize a private, bounded native epoch; never publish an unverified path."""

import hashlib
import json
import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from core.mastrao_native_spool import (
    PLAYLIST_LIMIT,
    SEGMENT_LIMIT,
    TOTAL_LIMIT,
    SpoolRefused,
    _open_directory,
    _parse_playlist,
    _read_file,
    _signature,
)
from core.mastrao_room_contract import _canonical_json, _sha256_canonical

MAX_AUDIO_BYTES = 16 * 1024**2  # Existing Core private-object bound.
MAX_AUDIO_SECONDS = 10_800
DECODE_TIMEOUT_SECONDS = 90


def native_audio_manifest_digest(manifest):
    """Bind ordered fragments without exceeding the shared 64 KiB JWS bound."""
    metadata = {key: value for key, value in manifest.items() if key != "segments"}
    digest = hashlib.sha256()
    for segment in manifest["segments"]:
        digest.update(_canonical_json(segment))
    return _sha256_canonical(
        {
            **metadata,
            "segment_count": len(manifest["segments"]),
            "segments_sha256": digest.hexdigest(),
        }
    )


@dataclass
class NativeAudio:
    """Caller closes scratch only after upload/verification, including failures."""

    path: Path
    manifest: dict
    workdir: tempfile.TemporaryDirectory

    def close(self):
        self.workdir.cleanup()


def _snapshot(directory, destination):
    descriptor = _open_directory(directory)
    try:
        playlist, signature = _read_file(descriptor, "index.m3u8", PLAYLIST_LIMIT)
        entries, ended = _parse_playlist(playlist)
        if not ended:
            raise SpoolRefused("native_epoch_not_final")
        duration = sum(seconds for _, seconds in entries)
        if duration > MAX_AUDIO_SECONDS:
            raise SpoolRefused("native_duration_limit")
        segments, signatures, total = [], {}, len(playlist)
        with destination.open("xb") as output:
            for filename, seconds in entries:
                payload, info = _read_file(
                    descriptor, filename, min(SEGMENT_LIMIT, TOTAL_LIMIT - total)
                )
                total += len(payload)
                signatures[filename] = info
                output.write(payload)
                segments.append(
                    {
                        "sequence": len(segments),
                        "byte_size": len(payload),
                        "sha256": hashlib.sha256(payload).hexdigest(),
                        "duration_ms": round(seconds * 1000),
                    }
                )
        reread, current = _read_file(descriptor, "index.m3u8", PLAYLIST_LIMIT)
        if reread != playlist or signature != current:
            raise SpoolRefused("native_playlist_changed")
        for filename, expected in signatures.items():
            current = os.stat(filename, dir_fd=descriptor, follow_symlinks=False)
            if _signature(current) != expected:
                raise SpoolRefused("native_segment_changed")
        return {
            "version": 1,
            "format": "native_epoch_flac_v1",
            "playlist_sha256": hashlib.sha256(playlist).hexdigest(),
            "segments": segments,
            "declared_duration_ms": round(duration * 1000),
            "coverage": "epoch_only",
        }
    finally:
        os.close(descriptor)


def _run(command, timeout):
    try:
        return subprocess.run(  # noqa: S603 - fixed binaries/options, private generated paths only.
            command,
            check=True,
            timeout=timeout,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        ).stdout
    except (subprocess.SubprocessError, OSError):
        # Decoder stderr can include paths/content; don't expose it in the API.
        raise SpoolRefused("native_media_decode_failed") from None


def _decode(source, audio):
    _run(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-xerror",
            "-protocol_whitelist",
            "file",
            "-threads",
            "1",
            "-f",
            "mpegts",
            "-i",
            str(source),
            "-map",
            "0:a:0",
            "-vn",
            "-sn",
            "-dn",
            "-threads",
            "1",
            "-filter_threads",
            "1",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "flac",
            "-fs",
            str(MAX_AUDIO_BYTES),
            "-n",
            str(audio),
        ],
        DECODE_TIMEOUT_SECONDS,
    )
    raw = _run(
        [
            "ffprobe",
            "-v",
            "error",
            "-protocol_whitelist",
            "file",
            "-f",
            "flac",
            "-show_entries",
            "stream=codec_name,sample_rate,channels,duration_ts,time_base",
            "-of",
            "json",
            str(audio),
        ],
        10,
    )
    try:
        streams = json.loads(raw)["streams"]
        stream = streams[0]
        if (
            len(streams) != 1
            or stream["codec_name"] != "flac"
            or stream["sample_rate"] != "16000"
            or stream["channels"] != 1
            or stream["time_base"] != "1/16000"
        ):
            raise ValueError
        samples = int(stream["duration_ts"])
        if not 0 < samples <= MAX_AUDIO_SECONDS * 16000:
            raise ValueError
        return samples
    except (KeyError, ValueError, IndexError, TypeError):
        raise SpoolRefused("native_audio_profile_invalid") from None


def materialize_native_audio(directory):
    """Only validated local TS bytes reach ffmpeg, never an HLS URI/playlist."""
    scratch = tempfile.TemporaryDirectory(prefix="mastrao_native_audio_")
    try:
        source, audio = (
            Path(scratch.name) / "source.ts",
            Path(scratch.name) / "source.flac",
        )
        manifest = _snapshot(directory, source)
        samples = _decode(source, audio)
        size = audio.stat().st_size
        if not 0 < size < MAX_AUDIO_BYTES:
            raise SpoolRefused("native_audio_size_limit")
        duration_ms = round(samples * 1000 / 16000)
        # AAC padding is normal. This is truncation detection, not alignment proof.
        if abs(duration_ms - manifest["declared_duration_ms"]) > 1000:
            raise SpoolRefused("native_audio_duration_mismatch")
        digest = hashlib.sha256()
        with audio.open("rb") as stream:
            while payload := stream.read(65536):
                digest.update(payload)
        manifest["audio"] = {
            "sha256": digest.hexdigest(),
            "byte_size": size,
            "sample_count": samples,
            "sample_rate": 16000,
            "channels": 1,
        }
        return NativeAudio(audio, manifest, scratch)
    except BaseException:
        scratch.cleanup()
        raise
