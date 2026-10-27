"""Persist completed room audio without overwriting an existing object."""

from __future__ import annotations

from hashlib import sha256
from io import BytesIO
from typing import Protocol

from minio.error import S3Error

from room_audio_ogg import RoomAudioArtifact

ROOM_AUDIO_PREFIX = "room-audio/v1"
MISSING_OBJECT_CODES = frozenset({"NoSuchKey", "NoSuchObject", "NotFound"})


class RoomAudioObjectConflict(RuntimeError):
    """Refuse to replace storage content that differs from the completed run."""


class RoomAudioObjectVerificationError(RuntimeError):
    """Report that durable storage did not prove the expected bytes."""


class _ObjectStat(Protocol):
    """Minimal object metadata returned by the storage client."""

    size: int
    metadata: dict[str, str]


class _ObjectStoreClient(Protocol):
    """Storage operations required by the room-audio persistence boundary."""

    def stat_object(self, bucket_name: str, object_name: str) -> _ObjectStat: ...

    def put_object(  # noqa: PLR0913 - mirrors the MinIO client boundary
        self,
        bucket_name: str,
        object_name: str,
        data,
        length: int,
        *,
        content_type: str,
        metadata: dict[str, str],
    ): ...


class RoomAudioObjectStore:
    """Store one immutable Ogg artifact under an opaque run-derived key."""

    def __init__(self, client: _ObjectStoreClient, *, bucket_name: str):
        """Bind storage explicitly instead of reading runtime configuration."""
        if not bucket_name or bucket_name.strip() != bucket_name:
            raise ValueError("room audio bucket_name is invalid")
        self._client = client
        self._bucket_name = bucket_name

    def persist(self, audio_run_ref: str, artifact: RoomAudioArtifact) -> str:
        """Create once, recover ambiguous responses, and verify durable bytes."""
        object_ref = self.object_ref(audio_run_ref)
        expected = _ExpectedObject.from_artifact(artifact)
        existing = self._read_expected_state(object_ref, expected)
        if existing == "exact":
            return object_ref
        if existing == "conflict":
            raise RoomAudioObjectConflict("room audio object already differs")

        try:
            self._client.put_object(
                self._bucket_name,
                object_ref,
                BytesIO(artifact.content),
                artifact.byte_size,
                content_type="audio/ogg",
                metadata={"sha256": artifact.sha256},
            )
        except (OSError, S3Error) as error:
            recovered = self._read_expected_state(object_ref, expected)
            if recovered == "exact":
                return object_ref
            if recovered == "conflict":
                raise RoomAudioObjectConflict(
                    "room audio object differs after ambiguous upload"
                ) from error
            raise

        verified = self._read_expected_state(object_ref, expected)
        if verified == "exact":
            return object_ref
        if verified == "conflict":
            raise RoomAudioObjectConflict("room audio upload readback differs")
        raise RoomAudioObjectVerificationError("room audio upload is not readable")

    @staticmethod
    def object_ref(audio_run_ref: str) -> str:
        """Derive a stable storage key without exposing the Core run reference."""
        if not audio_run_ref or audio_run_ref.strip() != audio_run_ref:
            raise ValueError("audio_run_ref is invalid")
        digest = sha256(audio_run_ref.encode("utf-8")).hexdigest()
        return f"{ROOM_AUDIO_PREFIX}/{digest}.ogg"

    def _read_expected_state(self, object_ref: str, expected: _ExpectedObject) -> str:
        """Classify storage state as missing, exact, or conflicting."""
        try:
            result = self._client.stat_object(self._bucket_name, object_ref)
        except S3Error as error:
            if error.code in MISSING_OBJECT_CODES:
                return "missing"
            raise
        metadata = {key.lower(): value for key, value in result.metadata.items()}
        stored_sha256 = metadata.get("x-amz-meta-sha256") or metadata.get("sha256")
        if result.size == expected.byte_size and stored_sha256 == expected.sha256:
            return "exact"
        return "conflict"


class _ExpectedObject:
    """Validated immutable fields used for storage comparison."""

    def __init__(self, *, byte_size: int, checksum: str):
        self.byte_size = byte_size
        self.sha256 = checksum

    @classmethod
    def from_artifact(cls, artifact: RoomAudioArtifact) -> _ExpectedObject:
        """Reject contradictory artifact metadata before touching storage."""
        checksum = sha256(artifact.content).hexdigest()
        if artifact.byte_size != len(artifact.content):
            raise ValueError("room audio artifact size does not match content")
        if artifact.sha256 != checksum:
            raise ValueError("room audio artifact checksum does not match content")
        return cls(byte_size=artifact.byte_size, checksum=checksum)
