"""Complete one post room-audio run through storage and Core publication."""

from __future__ import annotations

import asyncio
from typing import Protocol

from room_audio_manifest import RoomAudioSourceBundle, build_room_audio_source_bundle
from room_audio_ogg import RoomAudioArtifact
from room_audio_session import RoomAudioCompletion


class _ObjectStore(Protocol):
    """Immutable object operations required by the completion boundary."""

    def object_ref(self, audio_run_ref: str) -> str: ...

    def persist(self, audio_run_ref: str, artifact: RoomAudioArtifact) -> str: ...


class RoomAudioSourcePublisher(Protocol):
    """Publish one exact source bundle to its canonical Core owner."""

    async def publish(self, bundle: RoomAudioSourceBundle) -> None:
        """Publish idempotently using the bundle audio-run reference."""
        ...


class RoomAudioPostCompletion:
    """Validate, persist, then publish one finalized post source exactly."""

    def __init__(
        self,
        *,
        object_store: _ObjectStore,
        publisher: RoomAudioSourcePublisher,
        provider_session_ref: str,
    ):
        """Bind one worker session to explicit storage and Core boundaries."""
        self._object_store = object_store
        self._publisher = publisher
        self._provider_session_ref = provider_session_ref

    async def __call__(self, completion: RoomAudioCompletion) -> None:
        """Finish a post run fail-closed without blocking the room event loop."""
        artifact = completion.artifact
        if completion.effect.get("pipeline") != "post" or artifact is None:
            raise ValueError("post room audio completion with artifact is required")
        audio_run_ref = completion.effect.get("audio_run_ref", "")
        planned_object_ref = self._object_store.object_ref(audio_run_ref)
        bundle = build_room_audio_source_bundle(
            completion,
            provider_session_ref=self._provider_session_ref,
            object_ref=planned_object_ref,
        )
        persisted_object_ref = await asyncio.to_thread(
            self._object_store.persist,
            audio_run_ref,
            artifact,
        )
        if persisted_object_ref != planned_object_ref:
            raise RuntimeError("room audio object reference changed during persistence")
        await self._publisher.publish(bundle)
