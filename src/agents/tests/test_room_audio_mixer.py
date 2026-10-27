"""Consent and scaling tests for the room-owned PCM mixer."""

import unittest
from array import array

from room_audio_mixer import RoomAudioMixer, TrackAuthorizationError

AUTHORIZATION_DIGEST = "a" * 64


def pcm(*samples: int) -> bytes:
    """Encode signed 16-bit samples using the worker's little-endian format."""
    values = array("h", samples)
    return values.tobytes()


def samples(frame: bytes) -> list[int]:
    """Decode one test frame into signed samples."""
    values = array("h")
    values.frombytes(frame)
    return values.tolist()


class RoomAudioMixerTests(unittest.TestCase):
    """Prove one bounded mix excludes every non-authorized microphone."""

    def setUp(self):
        """Create a four-sample mixer for compact deterministic fixtures."""
        self.mixer = RoomAudioMixer(frame_samples=4, maximum_buffered_frames=2)

    def test_audio_is_fail_closed_until_exact_track_is_authorized(self):
        """A published track contributes no samples before Core admission."""
        self.mixer.observe_track("TR_pending")

        self.assertFalse(self.mixer.push_frame("TR_pending", pcm(500, 500, 500, 500)))
        self.assertEqual(samples(self.mixer.mix_next_frame()), [0, 0, 0, 0])
        self.assertEqual(self.mixer.snapshot().pending_track_count, 1)

    def test_stale_authorization_cannot_reopen_a_revoked_track(self):
        """Authorization generations are monotonic for each exact track."""
        self.mixer.observe_track("TR_one")
        self.mixer.admit_track(
            "TR_one",
            generation=4,
            expires_at_ms=2_000,
            authorization_digest=AUTHORIZATION_DIGEST,
        )
        self.mixer.revoke_track("TR_one", generation=5, authorization_digest="b" * 64)

        with self.assertRaises(TrackAuthorizationError):
            self.mixer.admit_track(
                "TR_one",
                generation=4,
                expires_at_ms=3_000,
                authorization_digest=AUTHORIZATION_DIGEST,
            )

        self.assertFalse(
            self.mixer.push_frame(
                "TR_one", pcm(1_000, 1_000, 1_000, 1_000), observed_at_ms=1_000
            )
        )
        self.assertEqual(self.mixer.snapshot().excluded_track_count, 1)

    def test_expired_authorization_contributes_silence(self):
        """Media cannot outlive its short-lived Core track authority."""
        self.mixer.observe_track("TR_expiring")
        self.mixer.admit_track(
            "TR_expiring",
            generation=1,
            expires_at_ms=1_000,
            authorization_digest=AUTHORIZATION_DIGEST,
        )

        self.assertFalse(
            self.mixer.push_frame(
                "TR_expiring",
                pcm(2_000, 2_000, 2_000, 2_000),
                observed_at_ms=1_001,
            )
        )
        self.assertEqual(samples(self.mixer.mix_next_frame()), [0, 0, 0, 0])

    def test_forty_tracks_produce_one_clipped_safe_room_frame(self):
        """Mixer output count scales with the room, not participant count."""
        for index in range(40):
            track_ref = f"TR_{index:02d}"
            self.mixer.observe_track(track_ref)
            self.mixer.admit_track(
                track_ref,
                generation=1,
                expires_at_ms=2_000,
                authorization_digest=AUTHORIZATION_DIGEST,
            )
            accepted = self.mixer.push_frame(
                track_ref,
                pcm(20_000, -20_000, 10_000, -10_000),
                observed_at_ms=1_000,
            )
            self.assertTrue(accepted)

        mixed = self.mixer.mix_next_frame(observed_at_ms=1_001)

        self.assertEqual(samples(mixed), [20_000, -20_000, 10_000, -10_000])
        snapshot = self.mixer.snapshot()
        self.assertEqual(snapshot.authorized_track_count, 40)
        self.assertEqual(snapshot.speaking_track_count, 40)
        self.assertEqual(snapshot.output_frame_count, 1)

    def test_only_speaking_tracks_are_averaged(self):
        """Silent authorized microphones do not attenuate a speaker."""
        for track_ref in ("TR_speaker", "TR_silent"):
            self.mixer.observe_track(track_ref)
            self.mixer.admit_track(
                track_ref,
                generation=1,
                expires_at_ms=2_000,
                authorization_digest=AUTHORIZATION_DIGEST,
            )
        self.mixer.push_frame(
            "TR_speaker", pcm(4_000, 4_000, 4_000, 4_000), observed_at_ms=1_000
        )
        self.mixer.push_frame("TR_silent", pcm(0, 0, 0, 0), observed_at_ms=1_000)

        self.assertEqual(
            samples(self.mixer.mix_next_frame(observed_at_ms=1_001)),
            [4_000, 4_000, 4_000, 4_000],
        )
        self.assertEqual(self.mixer.snapshot().speaking_track_count, 1)

    def test_missing_track_frame_is_an_underrun_not_a_room_gap(self):
        """A quiet authorized microphone does not claim lost room-clock audio."""
        self.mixer.observe_track("TR_quiet")
        self.mixer.admit_track(
            "TR_quiet",
            generation=1,
            expires_at_ms=2_000,
            authorization_digest=AUTHORIZATION_DIGEST,
        )

        self.mixer.mix_next_frame(observed_at_ms=1_000)

        self.assertEqual(self.mixer.snapshot().track_underrun_frame_count, 1)

    def test_buffer_is_bounded_and_reports_dropped_frames(self):
        """A slow consumer cannot grow one participant queue without limit."""
        self.mixer.observe_track("TR_fast")
        self.mixer.admit_track(
            "TR_fast",
            generation=1,
            expires_at_ms=2_000,
            authorization_digest=AUTHORIZATION_DIGEST,
        )
        for value in (1_000, 2_000, 3_000):
            self.mixer.push_frame(
                "TR_fast", pcm(value, value, value, value), observed_at_ms=1_000
            )

        self.assertEqual(
            samples(self.mixer.mix_next_frame(observed_at_ms=1_001)),
            [2_000, 2_000, 2_000, 2_000],
        )
        self.assertEqual(self.mixer.snapshot().dropped_frame_count, 1)

    def test_same_semantic_authority_can_renew_its_short_lease(self):
        """A fresh envelope extends expiry without changing Core authority."""
        self.mixer.observe_track("TR_renewed")
        self.mixer.admit_track(
            "TR_renewed",
            generation=1,
            expires_at_ms=1_000,
            authorization_digest=AUTHORIZATION_DIGEST,
        )

        self.mixer.admit_track(
            "TR_renewed",
            generation=1,
            expires_at_ms=2_000,
            authorization_digest=AUTHORIZATION_DIGEST,
        )

        self.assertTrue(
            self.mixer.push_frame(
                "TR_renewed",
                pcm(1_000, 1_000, 1_000, 1_000),
                observed_at_ms=1_500,
            )
        )

    def test_same_generation_with_a_different_digest_is_rejected(self):
        """A renewal cannot alter the authority sealed by Core."""
        self.mixer.observe_track("TR_conflict")
        self.mixer.admit_track(
            "TR_conflict",
            generation=1,
            expires_at_ms=1_000,
            authorization_digest=AUTHORIZATION_DIGEST,
        )

        with self.assertRaises(TrackAuthorizationError):
            self.mixer.admit_track(
                "TR_conflict",
                generation=1,
                expires_at_ms=2_000,
                authorization_digest="c" * 64,
            )

    def test_unobserved_track_is_removed_from_metrics_and_queues(self):
        """A departed track keeps its generation but leaves live metrics."""
        self.mixer.observe_track("TR_departed")
        self.mixer.admit_track(
            "TR_departed",
            generation=1,
            expires_at_ms=2_000,
            authorization_digest=AUTHORIZATION_DIGEST,
        )
        self.mixer.push_frame(
            "TR_departed",
            pcm(1_000, 1_000, 1_000, 1_000),
            observed_at_ms=1_000,
        )

        self.mixer.unobserve_track("TR_departed")

        self.assertEqual(
            samples(self.mixer.mix_next_frame(observed_at_ms=1_001)),
            [0, 0, 0, 0],
        )
        self.assertEqual(self.mixer.snapshot().published_track_count, 0)

        self.mixer.observe_track("TR_departed")
        with self.assertRaises(TrackAuthorizationError):
            self.mixer.admit_track(
                "TR_departed",
                generation=1,
                expires_at_ms=3_000,
                authorization_digest=AUTHORIZATION_DIGEST,
            )
        self.assertFalse(
            self.mixer.push_frame(
                "TR_departed",
                pcm(1_000, 1_000, 1_000, 1_000),
                observed_at_ms=1_500,
            )
        )

        self.mixer.admit_track(
            "TR_departed",
            generation=2,
            expires_at_ms=3_000,
            authorization_digest="b" * 64,
        )
        self.assertTrue(
            self.mixer.push_frame(
                "TR_departed",
                pcm(1_000, 1_000, 1_000, 1_000),
                observed_at_ms=1_500,
            )
        )


if __name__ == "__main__":
    unittest.main()
