"""Tests for the ordered post room-audio completion boundary."""

import unittest
from hashlib import sha256
from unittest.mock import AsyncMock, Mock, call

from room_audio_mixer import RoomAudioMixerSnapshot
from room_audio_ogg import RoomAudioArtifact
from room_audio_post_completion import RoomAudioPostCompletion
from room_audio_rtc import RoomAudioRtcSnapshot
from room_audio_session import RoomAudioCompletion


def _effect(pipeline="post"):
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
        "pipeline": pipeline,
    }


def _completion(pipeline="post"):
    content = b"room-audio"
    artifact = None
    if pipeline == "post":
        artifact = RoomAudioArtifact(
            content=content,
            byte_size=len(content),
            sha256=sha256(content).hexdigest(),
            duration_ms=1_000,
            frame_count=50,
        )
    return RoomAudioCompletion(
        effect=_effect(pipeline),
        artifact=artifact,
        reason="explicit-stop",
        started_at_ms=1_000_000,
        ended_at_ms=1_001_000,
        generation_count=1,
        gaps=(),
        mixer=RoomAudioMixerSnapshot(
            published_track_count=3,
            authorized_track_count=3,
            pending_track_count=0,
            excluded_track_count=0,
            speaking_track_count=1,
            output_frame_count=50,
            audio_bytes=48_000,
            dropped_frame_count=0,
            track_underrun_frame_count=0,
            mixer_queue_peak_frames=3,
        ),
        rtc=RoomAudioRtcSnapshot(
            sample_count=2,
            collection_error_count=0,
            packets_received=100,
            packets_lost=0,
            concealed_samples=0,
            maximum_jitter_ms=2,
            maximum_round_trip_time_ms=10,
        ),
    )


class RoomAudioPostCompletionTests(unittest.IsolatedAsyncioTestCase):
    """Keep validation, durable storage and canonical publication ordered."""

    def setUp(self):
        """Create explicit in-memory completion collaborators."""
        self.object_store = Mock()
        self.object_store.object_ref.return_value = f"room-audio/v1/{'a' * 64}.ogg"
        self.object_store.persist.return_value = (
            self.object_store.object_ref.return_value
        )
        self.publisher = AsyncMock()
        self.handler = RoomAudioPostCompletion(
            object_store=self.object_store,
            publisher=self.publisher,
            provider_session_ref="dispatch_12345678",
        )

    async def test_persists_before_publishing_the_exact_bundle(self):
        """Core sees a source only after its object has exact durable readback."""
        completion = _completion()

        await self.handler(completion)

        self.object_store.object_ref.assert_called_once_with(
            "audio_run_fixture_12345678"
        )
        self.object_store.persist.assert_called_once_with(
            "audio_run_fixture_12345678", completion.artifact
        )
        bundle = self.publisher.publish.await_args.args[0]
        self.assertEqual(
            bundle.manifest["object_ref"], self.object_store.object_ref.return_value
        )
        self.assertEqual(bundle.manifest["checksum_digest"], completion.artifact.sha256)

    async def test_refuses_live_completion_before_touching_storage(self):
        """A live-only completion cannot create an orphan post object."""
        with self.assertRaisesRegex(ValueError, "post room audio"):
            await self.handler(_completion(pipeline="live"))

        self.object_store.object_ref.assert_not_called()
        self.object_store.persist.assert_not_called()
        self.publisher.publish.assert_not_awaited()

    async def test_refuses_storage_reference_changed_during_persistence(self):
        """A contradictory persistence result cannot be published to Core."""
        self.object_store.persist.return_value = "room-audio/v1/different.ogg"

        with self.assertRaisesRegex(RuntimeError, "reference changed"):
            await self.handler(_completion())

        self.publisher.publish.assert_not_awaited()

    async def test_publication_retry_reuses_the_same_storage_identity(self):
        """A failed callback may retry without selecting another object."""
        self.publisher.publish.side_effect = [RuntimeError("Core unavailable"), None]
        completion = _completion()

        with self.assertRaisesRegex(RuntimeError, "Core unavailable"):
            await self.handler(completion)
        await self.handler(completion)

        self.assertEqual(
            self.object_store.persist.call_args_list,
            [
                call("audio_run_fixture_12345678", completion.artifact),
                call("audio_run_fixture_12345678", completion.artifact),
            ],
        )
        self.assertEqual(self.publisher.publish.await_count, 2)


if __name__ == "__main__":
    unittest.main()
