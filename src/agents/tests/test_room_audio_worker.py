"""Tests for executable post room-audio worker composition."""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from livekit.agents import AutoSubscribe

from room_audio_session import RoomAudioSessionHandlers
from room_audio_worker import (
    ROOM_AUDIO_AGENT_NAME,
    ROOM_AUDIO_WORKER_PERMISSIONS,
    RoomAudioPostConfig,
    RoomAudioWorkerConfig,
    create_live_room_audio_session,
    create_post_room_audio_session,
    create_room_audio_session,
    entrypoint,
)


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


def _config(pipeline="post"):
    post = None
    if pipeline == "post":
        post = RoomAudioPostConfig(
            callback_endpoint=(
                "http://meet-backend/internal/mastrao/room-audio/sources/complete/"
            ),
            callback_timeout_seconds=5.0,
            livekit_url="wss://livekit.internal",
            livekit_api_key="livekit-key",
            livekit_api_secret="livekit-secret",  # noqa: S106
            object_store_endpoint="minio.internal:9000",
            object_store_access_key="storage-key",
            object_store_secret_key="storage-secret",  # noqa: S106
            object_store_secure=True,
            object_store_bucket="room-audio-private",
        )
    return RoomAudioWorkerConfig(
        pipeline=pipeline,
        effect_issuer="tenant-core",
        effect_audience="meeting-room-audio-worker",
        effect_key_id="room-audio-key-v1",
        effect_public_jwk='{"kty":"OKP"}',
        post=post,
    )


class AwaitableRoomSid:
    """Model LiveKit's awaitable room SID property."""

    def __init__(self, value: str):
        """Store the provider SID and its await count."""
        self.value = value
        self.await_count = 0

    def __await__(self):
        """Resolve the provider SID like LiveKit's property."""

        async def resolve():
            self.await_count += 1
            return self.value

        return resolve().__await__()


def _context(dispatch_id="AD_room_audio_12345678"):
    return SimpleNamespace(
        job=SimpleNamespace(
            id="AJ_room_audio_12345678",
            dispatch_id=dispatch_id,
            metadata="compact.start.effect",
            room=SimpleNamespace(sid="RM_providerfixture"),
        ),
        room=SimpleNamespace(
            name="room_fixture_12345678",
            sid=AwaitableRoomSid("RM_providerfixture"),
        ),
        connect=AsyncMock(),
        add_shutdown_callback=Mock(),
        shutdown=Mock(),
    )


class RoomAudioWorkerConfigTests(unittest.TestCase):
    """Reject partial or ambiguous worker process configuration."""

    def test_loads_exact_environment(self):
        """Every live boundary is explicit before a worker accepts work."""
        environment = {
            "ROOM_AUDIO_PIPELINE": "post",
            "ROOM_AUDIO_EFFECT_ISSUER": "tenant-core",
            "ROOM_AUDIO_EFFECT_AUDIENCE": "meeting-room-audio-worker",
            "ROOM_AUDIO_EFFECT_KEY_ID": "room-audio-key-v1",
            "ROOM_AUDIO_EFFECT_PUBLIC_JWK": '{"kty":"OKP"}',
            "ROOM_AUDIO_SOURCE_CALLBACK_ENDPOINT": (
                "http://meet-backend/internal/mastrao/room-audio/sources/complete/"
            ),
            "ROOM_AUDIO_SOURCE_CALLBACK_TIMEOUT_SECONDS": "5",
            "LIVEKIT_URL": "wss://livekit.internal",
            "LIVEKIT_API_KEY": "livekit-key",
            "LIVEKIT_API_SECRET": "livekit-secret",
            "AWS_S3_ENDPOINT_URL": "minio.internal:9000",
            "AWS_S3_ACCESS_KEY_ID": "storage-key",
            "AWS_S3_SECRET_ACCESS_KEY": "storage-secret",
            "AWS_S3_SECURE_ACCESS": "true",
            "AWS_STORAGE_BUCKET_NAME": "room-audio-private",
        }

        with patch.dict("os.environ", environment, clear=True):
            config = RoomAudioWorkerConfig.from_environment()

        self.assertEqual(config, _config())

    def test_rejects_missing_and_ambiguous_values(self):
        """Missing timeout and non-boolean TLS values fail before connection."""
        environment = {
            "ROOM_AUDIO_PIPELINE": "post",
            "ROOM_AUDIO_EFFECT_ISSUER": "tenant-core",
            "ROOM_AUDIO_EFFECT_AUDIENCE": "meeting-room-audio-worker",
            "ROOM_AUDIO_EFFECT_KEY_ID": "room-audio-key-v1",
            "ROOM_AUDIO_EFFECT_PUBLIC_JWK": '{"kty":"OKP"}',
            "ROOM_AUDIO_SOURCE_CALLBACK_ENDPOINT": "http://meet-backend/callback",
            "ROOM_AUDIO_SOURCE_CALLBACK_TIMEOUT_SECONDS": "5",
            "LIVEKIT_URL": "wss://livekit.internal",
            "LIVEKIT_API_KEY": "livekit-key",
            "LIVEKIT_API_SECRET": "livekit-secret",
            "AWS_S3_ENDPOINT_URL": "minio.internal:9000",
            "AWS_S3_ACCESS_KEY_ID": "storage-key",
            "AWS_S3_SECRET_ACCESS_KEY": "storage-secret",
            "AWS_S3_SECURE_ACCESS": "sometimes",
            "AWS_STORAGE_BUCKET_NAME": "room-audio-private",
        }

        with patch.dict("os.environ", environment, clear=True):
            with self.assertRaisesRegex(ValueError, "AWS_S3_SECURE_ACCESS"):
                RoomAudioWorkerConfig.from_environment()

    def test_live_worker_loads_without_post_pipeline_secrets(self):
        """Live ASR never receives callback or object-store credentials."""
        environment = {
            "ROOM_AUDIO_PIPELINE": "live",
            "ROOM_AUDIO_EFFECT_ISSUER": "tenant-core",
            "ROOM_AUDIO_EFFECT_AUDIENCE": "meeting-room-audio-worker",
            "ROOM_AUDIO_EFFECT_KEY_ID": "room-audio-key-v1",
            "ROOM_AUDIO_EFFECT_PUBLIC_JWK": '{"kty":"OKP"}',
        }

        with patch.dict("os.environ", environment, clear=True):
            config = RoomAudioWorkerConfig.from_environment()

        self.assertEqual(config, _config("live"))
        self.assertIsNone(config.post)


class RoomAudioWorkerCompositionTests(unittest.IsolatedAsyncioTestCase):
    """Bind the source to exact dispatch, storage and publication boundaries."""

    def test_uses_backend_dispatch_agent_name(self):
        """The executable registers under the backend's exact dispatch name."""
        self.assertEqual(ROOM_AUDIO_AGENT_NAME, "room-audio-worker")

    def test_worker_has_minimal_live_permissions(self):
        """The worker publishes text data but never media or room metadata."""
        permissions = ROOM_AUDIO_WORKER_PERMISSIONS
        self.assertFalse(permissions.can_publish)
        self.assertTrue(permissions.can_subscribe)
        self.assertTrue(permissions.can_publish_data)
        self.assertFalse(permissions.can_update_metadata)
        self.assertTrue(permissions.hidden)

    def test_composes_post_session_with_exact_dispatch_id(self):
        """The final manifest uses the provider dispatch, never the job id."""
        ctx = _context()
        effect = _effect()
        with (
            patch("room_audio_worker.RoomAudioSession") as create_session,
            patch("room_audio_worker.RoomAudioPostCompletion") as create_completion,
            patch(
                "room_audio_worker.DurableRoomAudioSourcePublisher"
            ) as create_publisher,
            patch("room_audio_worker.RoomAudioObjectStore") as create_store,
            patch("room_audio_worker.Minio") as minio,
            patch("room_audio_worker.RoomAudioAuthority") as create_authority,
            patch(
                "room_audio_worker.create_room_audio_run_for_effect"
            ) as create_runtime,
        ):
            runtime = create_runtime.return_value
            authority = create_authority.return_value
            store = create_store.return_value
            publisher = create_publisher.return_value
            completion = create_completion.return_value

            result = create_post_room_audio_session(
                ctx,
                effect=effect,
                compact_effect=ctx.job.metadata,
                config=_config(),
            )

        self.assertIs(result, create_session.return_value)
        create_runtime.assert_called_once()
        minio.assert_called_once_with(
            endpoint="minio.internal:9000",
            access_key="storage-key",
            secret_key="storage-secret",  # noqa: S106
            secure=True,
        )
        create_store.assert_called_once_with(
            minio.return_value,
            bucket_name="room-audio-private",
        )
        create_completion.assert_called_once_with(
            object_store=store,
            publisher=publisher,
            provider_session_ref="AD_room_audio_12345678",
        )
        create_session.assert_called_once_with(
            room=ctx.room,
            effect=effect,
            runtime=runtime,
            authority=authority,
            handlers=RoomAudioSessionHandlers(complete=completion),
        )

    def test_refuses_live_and_missing_dispatch_before_side_effects(self):
        """Unsupported output and unbound provider sessions fail closed."""
        with self.assertRaisesRegex(ValueError, "only post"):
            create_post_room_audio_session(
                _context(),
                effect=_effect("live"),
                compact_effect="compact",
                config=_config(),
            )
        with self.assertRaisesRegex(ValueError, "dispatch_id"):
            create_post_room_audio_session(
                _context(dispatch_id=""),
                effect=_effect(),
                compact_effect="compact",
                config=_config(),
            )

    def test_composes_one_live_provider_with_room_output(self):
        """One live effect opens one provider factory for the entire room."""
        ctx = _context()
        effect = _effect("live")
        with (
            patch("room_audio_worker.RoomAudioSession") as create_session,
            patch("room_audio_worker.RoomLiveTranscriptionOutput") as create_output,
            patch("room_audio_worker.RoomAudioAuthority") as create_authority,
            patch(
                "room_audio_worker.create_room_audio_run_for_effect"
            ) as create_runtime,
        ):
            output = create_output.return_value
            runtime = create_runtime.return_value

            result = create_live_room_audio_session(
                ctx,
                effect=effect,
                config=_config("live"),
            )

        self.assertIs(result, create_session.return_value)
        live_output = create_runtime.call_args.kwargs["live_output"]
        self.assertIs(
            live_output.speech_event_handler_factory,
            output.create_event_handler,
        )
        self.assertIs(live_output.failure_handler, output.handle_failure)
        create_session.assert_called_once_with(
            room=ctx.room,
            effect=effect,
            runtime=runtime,
            authority=create_authority.return_value,
            handlers=RoomAudioSessionHandlers(
                complete=output.complete,
                publish_gap=output.publish_gap,
            ),
        )

    def test_selects_only_the_signed_pipeline(self):
        """Verified post and live effects cannot open each other's outputs."""
        with (
            patch("room_audio_worker.create_post_room_audio_session") as create_post,
            patch("room_audio_worker.create_live_room_audio_session") as create_live,
        ):
            create_room_audio_session(
                _context(),
                effect=_effect("post"),
                compact_effect="compact",
                config=_config(),
            )
            create_room_audio_session(
                _context(),
                effect=_effect("live"),
                compact_effect="compact",
                config=_config("live"),
            )

        create_post.assert_called_once()
        create_live.assert_called_once()

    async def test_entrypoint_connects_audio_only_and_closes_the_job(self):
        """A completed session exits its dedicated LiveKit job cleanly."""
        ctx = _context()
        verify_job = Mock(
            return_value=(_effect(), "room-audio-worker-identity"),
        )
        session = SimpleNamespace(
            start=AsyncMock(),
            wait_closed=AsyncMock(),
            aclose=AsyncMock(),
        )
        with (
            patch(
                "room_audio_worker.verify_room_audio_job",
                verify_job,
            ),
            patch(
                "room_audio_worker.RoomAudioWorkerConfig.from_environment",
                return_value=_config(),
            ),
            patch(
                "room_audio_worker.create_room_audio_session",
                return_value=session,
            ),
        ):
            await entrypoint(ctx)

        verify_job.assert_called_once_with(
            "RM_providerfixture",
            "compact.start.effect",
        )
        self.assertEqual(ctx.room.sid.await_count, 0)
        ctx.connect.assert_awaited_once_with(auto_subscribe=AutoSubscribe.AUDIO_ONLY)
        session.start.assert_awaited_once_with()
        session.wait_closed.assert_awaited_once_with()
        ctx.shutdown.assert_called_once_with("room-audio-post-completed")


if __name__ == "__main__":
    unittest.main()
