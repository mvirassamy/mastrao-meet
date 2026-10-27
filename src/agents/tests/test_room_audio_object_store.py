"""Tests for immutable and idempotent room-audio object persistence."""

import unittest
from dataclasses import dataclass
from hashlib import sha256

from minio.error import S3Error

from room_audio_object_store import RoomAudioObjectConflict, RoomAudioObjectStore
from room_audio_ogg import RoomAudioArtifact


@dataclass
class _Stat:
    size: int
    metadata: dict[str, str]


class _FakeObjectStore:
    def __init__(self):
        self.objects: dict[tuple[str, str], tuple[bytes, dict[str, str]]] = {}
        self.put_count = 0
        self.timeout_after_store = False

    def stat_object(self, bucket_name, object_name):
        try:
            content, metadata = self.objects[(bucket_name, object_name)]
        except KeyError as error:
            raise S3Error(
                None,
                "NoSuchKey",
                "missing",
                object_name,
                "request",
                "host",
                bucket_name,
                object_name,
            ) from error
        return _Stat(size=len(content), metadata=metadata)

    def put_object(  # noqa: PLR0913 - mirrors the MinIO client boundary
        self,
        bucket_name,
        object_name,
        data,
        length,
        *,
        content_type,
        metadata,
    ):
        self.put_count += 1
        content = data.read()
        if len(content) != length or content_type != "audio/ogg":
            raise AssertionError("unexpected upload")
        self.objects[(bucket_name, object_name)] = (
            content,
            {"x-amz-meta-sha256": metadata["sha256"]},
        )
        if self.timeout_after_store:
            raise TimeoutError("response lost after durable write")


def _artifact(content=b"room-audio"):
    return RoomAudioArtifact(
        content=content,
        byte_size=len(content),
        sha256=sha256(content).hexdigest(),
        duration_ms=1_000,
        frame_count=50,
    )


class RoomAudioObjectStoreTests(unittest.TestCase):
    """Prove create-once storage and authoritative readback."""

    def setUp(self):
        """Create an empty private object store for each test."""
        self.client = _FakeObjectStore()
        self.store = RoomAudioObjectStore(self.client, bucket_name="private-recordings")

    def test_uploads_once_and_verifies_size_and_checksum(self):
        """A new artifact becomes readable with exact immutable metadata."""
        object_ref = self.store.persist("audio_run_12345678", _artifact())

        self.assertEqual(self.client.put_count, 1)
        self.assertTrue(object_ref.startswith("room-audio/v1/"))
        self.assertNotIn("audio_run_12345678", object_ref)

    def test_exact_replay_does_not_upload_again(self):
        """Retrying a completed run resolves to the already verified object."""
        first_ref = self.store.persist("audio_run_12345678", _artifact())
        second_ref = self.store.persist("audio_run_12345678", _artifact())

        self.assertEqual(second_ref, first_ref)
        self.assertEqual(self.client.put_count, 1)

    def test_refuses_to_overwrite_different_existing_content(self):
        """A deterministic key collision fails closed without another PUT."""
        object_ref = self.store.object_ref("audio_run_12345678")
        self.client.objects[("private-recordings", object_ref)] = (
            b"different",
            {"x-amz-meta-sha256": sha256(b"different").hexdigest()},
        )

        with self.assertRaisesRegex(RoomAudioObjectConflict, "already differs"):
            self.store.persist("audio_run_12345678", _artifact())

        self.assertEqual(self.client.put_count, 0)

    def test_recovers_timeout_after_durable_upload(self):
        """A lost response succeeds only when stat proves the expected object."""
        self.client.timeout_after_store = True

        object_ref = self.store.persist("audio_run_12345678", _artifact())

        self.assertEqual(object_ref, self.store.object_ref("audio_run_12345678"))
        self.assertEqual(self.client.put_count, 1)

    def test_rejects_artifact_with_contradictory_checksum(self):
        """Invalid in-memory evidence cannot reach private object storage."""
        artifact = _artifact()
        artifact = RoomAudioArtifact(
            content=artifact.content,
            byte_size=artifact.byte_size,
            sha256="0" * 64,
            duration_ms=artifact.duration_ms,
            frame_count=artifact.frame_count,
        )

        with self.assertRaisesRegex(ValueError, "checksum"):
            self.store.persist("audio_run_12345678", artifact)

        self.assertEqual(self.client.put_count, 0)


if __name__ == "__main__":
    unittest.main()
