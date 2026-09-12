"""Actual ffmpeg AAC/HLS decoding, not a provider or microphone test."""

import hashlib
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.mastrao_native_audio import materialize_native_audio
from core.mastrao_native_spool import SpoolRefused


@unittest.skipUnless(
    shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg required"
)
class NativeAudioTests(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory(prefix="native-audio-test-")
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name).resolve()
        self.generate(440)

    def generate(self, frequency):
        subprocess.run(
            [
                "ffmpeg",
                "-nostdin",
                "-v",
                "error",
                "-y",
                "-f",
                "lavfi",
                "-i",
                f"sine=frequency={frequency}:duration=2",
                "-threads",
                "1",
                "-ac",
                "1",
                "-c:a",
                "aac",
                "-ar",
                "48000",
                "-f",
                "hls",
                "-hls_time",
                "1",
                "-hls_playlist_type",
                "event",
                "-hls_segment_filename",
                str(self.root / "audio_%05d.ts"),
                str(self.root / "index.m3u8"),
            ],
            check=True,
            timeout=15,
            capture_output=True,
        )

    def change_playlist(self, transform):
        path = self.root / "index.m3u8"
        path.write_text(transform(path.read_text()))

    def test_two_distinct_real_encoded_sources(self):
        first = materialize_native_audio(self.root)
        self.addCleanup(first.close)
        self.assertEqual(first.manifest["coverage"], "epoch_only")
        self.assertEqual(first.manifest["audio"]["sample_rate"], 16000)
        self.assertLess(abs(first.manifest["audio"]["sample_count"] / 16000 - 2), 0.1)
        self.assertEqual(
            first.manifest["audio"]["sha256"],
            hashlib.sha256(first.path.read_bytes()).hexdigest(),
        )
        self.assertTrue(first.path.read_bytes().startswith(b"fLaC"))
        self.generate(880)
        second = materialize_native_audio(self.root)
        self.addCleanup(second.close)
        self.assertNotEqual(
            first.manifest["audio"]["sha256"], second.manifest["audio"]["sha256"]
        )

    def test_no_endlist_does_not_call_decoder(self):
        self.change_playlist(lambda text: text.replace("#EXT-X-ENDLIST\n", ""))
        with patch("core.mastrao_native_audio._decode") as decode:
            with self.assertRaisesRegex(SpoolRefused, "not_final"):
                materialize_native_audio(self.root)
        decode.assert_not_called()

    def test_urls_traversal_key_and_duplicate_are_denied_before_decoder(self):
        path = self.root / "index.m3u8"
        initial = path.read_text()
        for malicious in [
            "https://example.invalid/a.ts",
            "../secret",
            "audio_00001.ts",
            "file:///etc/passwd",
            "crypto:audio_00000.ts",
        ]:
            with self.subTest(malicious=malicious):
                path.write_text(initial.replace("audio_00000.ts", malicious))
                with patch("core.mastrao_native_audio._decode") as decode:
                    with self.assertRaises(SpoolRefused):
                        materialize_native_audio(self.root)
                    decode.assert_not_called()
        path.write_text(
            initial.replace("#EXTM3U\n", '#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI="a"\n')
        )
        with self.assertRaises(SpoolRefused):
            materialize_native_audio(self.root)

    def test_segment_symlink_and_parent_symlink_refused(self):
        original = self.root / "audio_00000.ts"
        moved = self.root / "other.ts"
        original.rename(moved)
        original.symlink_to(moved)
        with self.assertRaises(OSError):
            materialize_native_audio(self.root)
        link = self.root / "alias"
        link.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(OSError):
            materialize_native_audio(link)

    def test_missing_and_corrupt_media_refused(self):
        path = self.root / "audio_00000.ts"
        path.write_bytes(b"not-mpegts")
        # Invalid bytes are never published as a valid FLAC.
        with self.assertRaises(SpoolRefused):
            materialize_native_audio(self.root)
        path.unlink()
        with self.assertRaises(OSError):
            materialize_native_audio(self.root)

    def test_segment_limit_and_duration_limit(self):
        with patch("core.mastrao_native_audio.SEGMENT_LIMIT", 10):
            with self.assertRaises(SpoolRefused):
                materialize_native_audio(self.root)
        with patch("core.mastrao_native_audio.MAX_AUDIO_SECONDS", 1):
            with self.assertRaisesRegex(SpoolRefused, "duration_limit"):
                materialize_native_audio(self.root)

    def test_fifo_is_nonblocking_refusal(self):
        path = self.root / "audio_00000.ts"
        path.unlink()
        os.mkfifo(path)
        with self.assertRaises(SpoolRefused):
            materialize_native_audio(self.root)

    def test_output_is_private_and_close_only_removes_own_scratch(self):
        audio = materialize_native_audio(self.root)
        self.assertEqual(audio.path.parent.stat().st_mode & 0o777, 0o700)
        audio.close()
        self.assertFalse(audio.path.exists())
        self.assertTrue((self.root / "index.m3u8").exists())
