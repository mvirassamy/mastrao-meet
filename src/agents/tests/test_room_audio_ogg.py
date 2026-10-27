"""Tests for the single post-meeting room audio artifact."""

import io
import unittest

import av

from room_audio_ogg import RoomAudioOggEncoder


class RoomAudioOggEncoderTests(unittest.TestCase):
    """Verify a deterministic mono Opus object without a browser compositor."""

    def test_fifty_room_frames_produce_one_decodable_second(self):
        """One room clock becomes one Ogg/Opus source with exact accounting."""
        encoder = RoomAudioOggEncoder(sample_rate_hz=24_000, frame_samples=480)
        frame = b"\x00\x00" * 480
        for _ in range(50):
            encoder.write_frame(frame)

        artifact = encoder.finish()

        self.assertTrue(artifact.content.startswith(b"OggS"))
        self.assertEqual(artifact.frame_count, 50)
        self.assertEqual(artifact.duration_ms, 1_000)
        self.assertEqual(artifact.byte_size, len(artifact.content))
        self.assertRegex(artifact.sha256, r"^[a-f0-9]{64}$")
        with av.open(io.BytesIO(artifact.content), format="ogg", mode="r") as source:
            decoded_samples = sum(frame.samples for frame in source.decode(audio=0))
        self.assertGreaterEqual(decoded_samples, 24_000)

    def test_encoder_is_single_use_after_finalization(self):
        """A finalized source cannot accept late participant audio."""
        encoder = RoomAudioOggEncoder(sample_rate_hz=24_000, frame_samples=480)
        encoder.write_frame(b"\x00\x00" * 480)
        first = encoder.finish()

        self.assertIs(first, encoder.finish())
        with self.assertRaises(RuntimeError):
            encoder.write_frame(b"\x00\x00" * 480)

    def test_frame_geometry_is_exact(self):
        """Misaligned PCM is refused instead of shifting the room clock."""
        encoder = RoomAudioOggEncoder(sample_rate_hz=24_000, frame_samples=480)

        with self.assertRaises(ValueError):
            encoder.write_frame(b"\x00\x00" * 479)


if __name__ == "__main__":
    unittest.main()
