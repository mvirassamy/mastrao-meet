"""Encode one consent-filtered room mix as a private Ogg/Opus source."""

from __future__ import annotations

import io
from dataclasses import dataclass
from fractions import Fraction
from hashlib import sha256

import av
import numpy as np


@dataclass(frozen=True)
class RoomAudioArtifact:
    """Verified in-memory representation ready for private object storage."""

    content: bytes
    byte_size: int
    sha256: str
    duration_ms: int
    frame_count: int


class RoomAudioOggEncoder:
    """Write one fixed-clock mono PCM room mix to Ogg/Opus exactly once."""

    def __init__(
        self,
        *,
        sample_rate_hz: int = 24_000,
        frame_samples: int = 480,
        bitrate: int = 64_000,
    ):
        """Open one browser-free audio encoder for a room run."""
        if sample_rate_hz <= 0 or frame_samples <= 0 or bitrate <= 0:
            raise ValueError("room audio encoder settings must be positive")
        self._sample_rate_hz = sample_rate_hz
        self._frame_samples = frame_samples
        self._buffer = io.BytesIO()
        self._container = av.open(self._buffer, mode="w", format="ogg")
        self._stream = self._container.add_stream("libopus", rate=sample_rate_hz)
        self._stream.layout = "mono"
        self._stream.bit_rate = bitrate
        self._next_pts = 0
        self._frame_count = 0
        self._artifact: RoomAudioArtifact | None = None

    def write_frame(self, pcm_s16le: bytes) -> None:
        """Encode one exact mono frame on the room clock."""
        if self._artifact is not None:
            raise RuntimeError("room audio artifact is already finalized")
        if len(pcm_s16le) != self._frame_samples * 2:
            raise ValueError("room audio frame has unexpected geometry")
        samples = (
            np.frombuffer(pcm_s16le, dtype="<i2").reshape(1, self._frame_samples).copy()
        )
        frame = av.AudioFrame.from_ndarray(samples, format="s16", layout="mono")
        frame.sample_rate = self._sample_rate_hz
        frame.pts = self._next_pts
        frame.time_base = Fraction(1, self._sample_rate_hz)
        self._next_pts += self._frame_samples
        self._frame_count += 1
        self._mux(self._stream.encode(frame))

    def finish(self) -> RoomAudioArtifact:
        """Flush the codec and return immutable bytes plus exact provenance."""
        if self._artifact is not None:
            return self._artifact
        if self._frame_count == 0:
            raise RuntimeError("room audio artifact contains no frames")
        self._mux(self._stream.encode(None))
        self._container.close()
        content = self._buffer.getvalue()
        self._artifact = RoomAudioArtifact(
            content=content,
            byte_size=len(content),
            sha256=sha256(content).hexdigest(),
            duration_ms=round(self._next_pts * 1_000 / self._sample_rate_hz),
            frame_count=self._frame_count,
        )
        return self._artifact

    def _mux(self, packets) -> None:
        """Write every encoded packet to the single Ogg container."""
        for packet in packets:
            self._container.mux(packet)
