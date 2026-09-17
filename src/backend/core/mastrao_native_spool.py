"""Bounded, no-follow snapshot of native HLS output. No remote durability claim.

Only the Egress EVENT playlist is accepted. Never scan temporary TS files or
infer a speaker from a filename. This diagnostic does not upload or delete data.
An immutable Core receipt is still required before source cleanup or ASR.
"""

import hashlib
import math
import os
import re
import stat
from pathlib import Path

PLAYLIST_LIMIT = 2 * 1024**2
SEGMENT_LIMIT = 4 * 1024**2
TOTAL_LIMIT = 256 * 1024**2
SEGMENT_COUNT_LIMIT = 5000


class SpoolRefused(RuntimeError):
    """Fixed diagnostic code, without local paths or file contents."""


def _open_directory(directory):
    path = Path(directory)
    if not path.is_absolute() or ".." in path.parts:
        raise SpoolRefused("spool_absolute_directory_required")
    descriptor = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for component in path.parts[1:]:
            child = os.open(
                component,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=descriptor,
            )
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _signature(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _read_file(directory, filename, limit):
    descriptor = os.open(
        filename, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory
    )
    with os.fdopen(descriptor, "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= limit:
            raise SpoolRefused("spool_file_type_or_budget")
        payload = stream.read(limit + 1)
        after = os.fstat(stream.fileno())
        current = os.stat(filename, dir_fd=directory, follow_symlinks=False)
        if (
            len(payload) != before.st_size
            or _signature(before) != _signature(after)
            or _signature(after) != _signature(current)
        ):
            raise SpoolRefused("spool_file_changed")
    return payload, _signature(after)


def _parse_playlist(payload):  # noqa: PLR0912 - explicit fail-closed grammar.
    if not payload.endswith(b"\n"):
        raise SpoolRefused("spool_playlist_incomplete")
    lines = payload.decode("utf-8").splitlines()
    required = {"#EXT-X-PLAYLIST-TYPE:EVENT", "#EXT-X-MEDIA-SEQUENCE:0"}
    if not lines or lines[0] != "#EXTM3U" or not required.issubset(lines):
        raise SpoolRefused("spool_event_playlist_required")
    segments, pending, ended = [], None, False
    headers = (
        "#EXT-X-VERSION:3",
        "#EXT-X-VERSION:4",
        "#EXT-X-PLAYLIST-TYPE:EVENT",
        "#EXT-X-MEDIA-SEQUENCE:0",
        "#EXT-X-ALLOW-CACHE:NO",
    )
    for line in lines[1:]:
        if ended:
            raise SpoolRefused("spool_playlist_after_end")
        if line == "#EXT-X-ENDLIST":
            ended = True
        elif line.startswith("#EXTINF:"):
            if pending is not None:
                raise SpoolRefused("spool_playlist_order")
            pending = float(line[8:].removesuffix(","))
            if not math.isfinite(pending) or not 0 < pending <= 10:
                raise SpoolRefused("spool_segment_duration")
        elif line.startswith("#EXT-X-PROGRAM-DATE-TIME:") and pending is None:
            continue
        elif (
            not segments
            and pending is None
            and (
                line in headers
                or re.fullmatch(r"#EXT-X-TARGETDURATION:[1-9][0-9]?", line)
            )
        ):
            continue
        elif line == f"audio_{len(segments):05d}.ts" and pending is not None:
            segments.append((line, pending))
            pending = None
            if len(segments) > SEGMENT_COUNT_LIMIT:
                raise SpoolRefused("spool_segment_count_budget")
        else:
            raise SpoolRefused("spool_playlist_order")
    if pending is not None or not segments:
        raise SpoolRefused("spool_playlist_incomplete")
    return segments, ended


def inspect_spool(directory):
    """Hash one bounded snapshot; refuse changing, missing or unsafe files."""
    descriptor = _open_directory(directory)
    try:
        playlist, playlist_signature = _read_file(
            descriptor, "index.m3u8", PLAYLIST_LIMIT
        )
        entries, ended = _parse_playlist(playlist)
        segments, signatures, total = [], {}, len(playlist)
        for filename, seconds in entries:
            payload, signature = _read_file(
                descriptor, filename, min(SEGMENT_LIMIT, TOTAL_LIMIT - total)
            )
            total += len(payload)
            signatures[filename] = signature
            segments.append(
                {
                    "filename": filename,
                    "bytes": len(payload),
                    "sha256": hashlib.sha256(payload).hexdigest(),
                    "declared_seconds": seconds,
                }
            )
        reread, signature = _read_file(descriptor, "index.m3u8", PLAYLIST_LIMIT)
        if reread != playlist or signature != playlist_signature:
            raise SpoolRefused("spool_playlist_changed")
        for filename, signature in signatures.items():
            if (
                _signature(os.stat(filename, dir_fd=descriptor, follow_symlinks=False))
                != signature
            ):
                raise SpoolRefused("spool_file_changed")
        return {
            "version": 1,
            "status": "LOCAL_SNAPSHOT_ONLY",
            "endlist": ended,
            "playlist_sha256": hashlib.sha256(playlist).hexdigest(),
            "bytes": total,
            "segments": segments,
            "remote_durability_proven": False,
            "capture_coverage_proven": False,
            "core_authority_verified": False,
            "asr_ready": False,
        }
    finally:
        os.close(descriptor)
