"""Tests for the canonical post-room-audio source bundle."""

import unittest
from hashlib import sha256

from room_audio_manifest import build_room_audio_source_bundle
from room_audio_mixer import RoomAudioMixerSnapshot
from room_audio_ogg import RoomAudioArtifact
from room_audio_rtc import RoomAudioRtcSnapshot
from room_audio_session import RoomAudioCompletion, RoomAudioGap


def _effect():
    return {
        "organization_external_id": "organization_fixture",
        "meeting_ref": "meeting_fixture_12345678",
        "room_ref": "room_fixture_12345678",
        "audio_run_ref": "audio_run_fixture_12345678",
        "policy_ref": "policy_fixture_12345678",
        "policy_digest": "a" * 64,
        "notice_digest": "b" * 64,
        "provider_binding_digest": "c" * 64,
        "retention_expires_at": 2_000_000_000,
        "pipeline": "post",
    }


def _snapshot():
    return RoomAudioMixerSnapshot(
        published_track_count=3,
        authorized_track_count=2,
        pending_track_count=1,
        excluded_track_count=0,
        speaking_track_count=1,
        output_frame_count=500,
        audio_bytes=480_000,
        dropped_frame_count=0,
        track_underrun_frame_count=2,
        mixer_queue_peak_frames=3,
    )


def _completion(*, pipeline="post", duration_ms=10_000, gaps=()):
    effect = _effect()
    effect["pipeline"] = pipeline
    artifact = None
    if pipeline == "post":
        content = b"ogg"
        artifact = RoomAudioArtifact(
            content=content,
            byte_size=len(content),
            sha256=sha256(content).hexdigest(),
            duration_ms=duration_ms,
            frame_count=max(1, duration_ms // 20),
        )
    return RoomAudioCompletion(
        effect=effect,
        artifact=artifact,
        reason="explicit-stop",
        started_at_ms=1_000_000,
        ended_at_ms=1_000_000 + duration_ms,
        generation_count=2 if gaps else 1,
        gaps=tuple(gaps),
        mixer=_snapshot(),
        rtc=RoomAudioRtcSnapshot(
            sample_count=3,
            collection_error_count=0,
            packets_received=1_000,
            packets_lost=2,
            concealed_samples=40,
            maximum_jitter_ms=7,
            maximum_round_trip_time_ms=30,
        ),
    )


class RoomAudioManifestTests(unittest.TestCase):
    """Keep source metadata exact, bounded and free of participant content."""

    def test_builds_exact_manifest_and_ordered_gap_records(self):
        """Manifest duration, checksum, generation and gaps remain exact."""
        gaps = (
            RoomAudioGap(
                generation=1,
                started_at_ms=1_002_000,
                ended_at_ms=1_002_120,
                reason="source_reconnect",
            ),
        )

        bundle = build_room_audio_source_bundle(
            _completion(gaps=gaps),
            provider_session_ref="dispatch_12345678",
            object_ref="room-audio/audio_run_fixture_12345678.ogg",
        )

        self.assertEqual(bundle.manifest["pipeline"], "post")
        self.assertEqual(bundle.manifest["duration_ms"], 10_000)
        self.assertEqual(bundle.manifest["ended_at_ms"], 1_010_000)
        self.assertEqual(bundle.manifest["generation_count"], 2)
        self.assertEqual(bundle.manifest["gap_count"], 1)
        self.assertEqual(bundle.manifest["total_gap_ms"], 120)
        self.assertEqual(bundle.manifest["version"], 2)
        self.assertEqual(bundle.manifest["mixer_underrun_frame_count"], 2)
        self.assertEqual(bundle.manifest["rtc_packets_received"], 1_000)
        self.assertEqual(bundle.manifest["rtc_maximum_round_trip_time_ms"], 30)
        self.assertEqual(bundle.manifest["checksum_digest"], sha256(b"ogg").hexdigest())
        self.assertEqual(
            bundle.gaps,
            (
                {
                    "sequence": 0,
                    "generation": 1,
                    "started_at_ms": 1_002_000,
                    "ended_at_ms": 1_002_120,
                    "reason": "source_reconnect",
                },
            ),
        )

    def test_refuses_live_completion_without_post_artifact(self):
        """A live-only run cannot masquerade as an immutable post source."""
        with self.assertRaisesRegex(ValueError, "post room audio"):
            build_room_audio_source_bundle(
                _completion(pipeline="live"),
                provider_session_ref="dispatch_12345678",
                object_ref="room-audio/audio_run_fixture_12345678.ogg",
            )

    def test_refuses_gap_outside_completed_run(self):
        """A source gap must remain inside the observed lifecycle interval."""
        gap = RoomAudioGap(
            generation=1,
            started_at_ms=1_009_900,
            ended_at_ms=1_010_100,
            reason="source_reconnect",
        )

        with self.assertRaisesRegex(ValueError, "outside the completed run"):
            build_room_audio_source_bundle(
                _completion(gaps=(gap,)),
                provider_session_ref="dispatch_12345678",
                object_ref="room-audio/audio_run_fixture_12345678.ogg",
            )

    def test_refuses_malformed_storage_reference(self):
        """Object references cannot escape the private room-audio prefix."""
        with self.assertRaisesRegex(ValueError, "object_ref"):
            build_room_audio_source_bundle(
                _completion(),
                provider_session_ref="dispatch_12345678",
                object_ref="room-audio/../../private-secret",
            )


if __name__ == "__main__":
    unittest.main()
