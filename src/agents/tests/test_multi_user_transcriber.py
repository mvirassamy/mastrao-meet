"""Provider-selection tests for the multi-user transcription worker."""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from livekit import rtc

import multi_user_transcriber


class LiveProviderSelectionTests(unittest.TestCase):
    """Keep provider selection explicit and fail closed."""

    def test_mistral_live_does_not_require_silero(self):
        """Create the measured Mistral path without an unrelated VAD dependency."""
        ctx = object()
        with patch.object(
            multi_user_transcriber,
            "STT_PROVIDER",
            multi_user_transcriber.MISTRAL_LIVE_PROVIDER,
        ):
            with patch.object(
                multi_user_transcriber,
                "LiveTranscriber",
                return_value=Mock(),
            ) as transcriber:
                multi_user_transcriber.create_live_transcriber(ctx)

        self.assertIs(transcriber.call_args.args[0], ctx)
        self.assertFalse(transcriber.call_args.kwargs["requires_vad"])
        self.assertIs(
            transcriber.call_args.kwargs["provider_factory"],
            multi_user_transcriber.create_mistral_live_stt,
        )

    def test_openai_live_keeps_silero_requirement(self):
        """Preserve the existing rollback provider contract."""
        ctx = object()
        with patch.object(
            multi_user_transcriber,
            "STT_PROVIDER",
            multi_user_transcriber.OPENAI_LIVE_PROVIDER,
        ):
            with patch.object(
                multi_user_transcriber,
                "LiveTranscriber",
                return_value=Mock(),
            ) as transcriber:
                multi_user_transcriber.create_live_transcriber(ctx)

        self.assertIs(transcriber.call_args.args[0], ctx)
        self.assertTrue(transcriber.call_args.kwargs["requires_vad"])


class LegacyAdmissionTests(unittest.IsolatedAsyncioTestCase):
    """Prevent the rollback worker from racing the room-owned mixer."""

    async def test_rejects_when_room_audio_worker_is_present(self):
        """Only one live transcript architecture may own a room."""
        request = SimpleNamespace(
            room=SimpleNamespace(name="room_legal"),
            accept=AsyncMock(),
            reject=AsyncMock(),
        )
        livekit = AsyncMock()
        livekit.__aenter__.return_value = livekit
        livekit.__aexit__.return_value = None
        livekit.room.list_participants.return_value = SimpleNamespace(
            participants=[
                SimpleNamespace(
                    kind=rtc.ParticipantKind.PARTICIPANT_KIND_AGENT,
                    identity=(
                        "room-audio-worker-live-40fd4bef1c3df4daa837a0d37b0dd585"
                    ),
                )
            ]
        )

        with patch.object(
            multi_user_transcriber.api, "LiveKitAPI", return_value=livekit
        ):
            await multi_user_transcriber.handle_transcriber_job_request(request)

        request.reject.assert_awaited_once_with()
        request.accept.assert_not_awaited()

    async def test_allows_post_worker_to_coexist(self):
        """Durable post capture does not compete for the live transcript lane."""
        request = SimpleNamespace(
            room=SimpleNamespace(name="room_legal"),
            accept=AsyncMock(),
            reject=AsyncMock(),
        )
        livekit = AsyncMock()
        livekit.__aenter__.return_value = livekit
        livekit.__aexit__.return_value = None
        livekit.room.list_participants.return_value = SimpleNamespace(
            participants=[
                SimpleNamespace(
                    kind=rtc.ParticipantKind.PARTICIPANT_KIND_AGENT,
                    identity=(
                        "room-audio-worker-post-40fd4bef1c3df4daa837a0d37b0dd585"
                    ),
                )
            ]
        )

        with patch.object(
            multi_user_transcriber.api, "LiveKitAPI", return_value=livekit
        ):
            await multi_user_transcriber.handle_transcriber_job_request(request)

        request.accept.assert_awaited_once_with(
            identity="multi-user-transcriber-room_legal"
        )
        request.reject.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
