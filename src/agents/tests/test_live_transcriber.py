"""Contract tests for the opt-in live transcription worker."""

import asyncio
import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from livekit.agents import LanguageCode, stt
from livekit.plugins import openai

from live_transcriber import (
    GAP_TOPIC,
    MODEL,
    TRANSCRIPTION_TOPIC,
    LiveTranscriber,
    TrackTranscriber,
    TranscriptionPublisher,
    create_openai_live_stt,
)


class FakeLocalParticipant:
    """Capture text streams without claiming provider transcription."""

    def __init__(self):
        """Initialize an empty message capture."""
        self.messages: list[tuple[str, dict[str, str], str]] = []

    async def send_text(self, text, *, topic, attributes=None):
        """Record the same fields used by LiveKit's local participant API."""
        self.messages.append((topic, attributes or {}, text))


class FakeRoom:
    """Minimal room surface required by the publisher contract."""

    def __init__(self):
        """Initialize a fake local participant."""
        self.local_participant = FakeLocalParticipant()


def speech_event(event_type: stt.SpeechEventType, text: str, item_id: str):
    """Build a provider-shaped event for protocol-only tests."""
    return stt.SpeechEvent(
        type=event_type,
        request_id=item_id,
        alternatives=[
            stt.SpeechData(
                language=LanguageCode("fr"),
                text=text,
            )
        ],
    )


class LiveTranscriptionContractTests(unittest.IsolatedAsyncioTestCase):
    """Verify identity, replacement and gap semantics without network I/O."""

    async def test_two_tracks_keep_identity_and_replace_interim_with_final(self):
        """Two tracks publish independent keys and revisions."""
        room = FakeRoom()
        publisher = TranscriptionPublisher(room)
        first = TrackTranscriber(
            track=object(),
            participant=SimpleNamespace(identity="alice"),
            track_sid="TR_A",
            publisher=publisher,
            vad=object(),
        )
        second = TrackTranscriber(
            track=object(),
            participant=SimpleNamespace(identity="bob"),
            track_sid="TR_B",
            publisher=publisher,
            vad=object(),
        )

        await first._handle_event(
            speech_event(stt.SpeechEventType.INTERIM_TRANSCRIPT, "bonjour", "item-a")
        )
        await first._handle_event(
            speech_event(
                stt.SpeechEventType.FINAL_TRANSCRIPT,
                "bonjour tout le monde",
                "item-a",
            )
        )
        await second._handle_event(
            speech_event(stt.SpeechEventType.FINAL_TRANSCRIPT, "hello", "item-b")
        )

        messages = room.local_participant.messages
        self.assertEqual(
            [message[0] for message in messages],
            [
                TRANSCRIPTION_TOPIC,
                TRANSCRIPTION_TOPIC,
                TRANSCRIPTION_TOPIC,
            ],
        )
        envelopes = [json.loads(message[2]) for message in messages]
        self.assertEqual(envelopes[0]["state"], "interim")
        self.assertEqual(envelopes[1]["state"], "final")
        self.assertEqual(envelopes[0]["itemId"], envelopes[1]["itemId"])
        self.assertEqual(envelopes[0]["revision"], 1)
        self.assertEqual(envelopes[1]["revision"], 2)
        self.assertEqual(envelopes[0]["participantIdentity"], "alice")
        self.assertEqual(envelopes[2]["participantIdentity"], "bob")
        self.assertEqual(envelopes[0]["trackSid"], "TR_A")
        self.assertEqual(envelopes[2]["trackSid"], "TR_B")
        self.assertEqual([envelope["sequence"] for envelope in envelopes], [1, 2, 3])

    async def test_gap_is_versioned_and_separate_from_text(self):
        """Reconnect markers use the dedicated gap topic."""
        room = FakeRoom()
        publisher = TranscriptionPublisher(room)

        await publisher.publish_gap("room-reconnecting")

        topic, attributes, payload = room.local_participant.messages[0]
        self.assertEqual(topic, GAP_TOPIC)
        self.assertEqual(attributes, {})
        self.assertEqual(json.loads(payload)["reason"], "room-reconnecting")

    async def test_close_is_idempotent_for_an_active_track(self):
        """Repeated shutdown calls do not touch the stream twice."""
        track = TrackTranscriber(
            track=object(),
            participant=SimpleNamespace(identity="alice"),
            track_sid="TR_A",
            publisher=TranscriptionPublisher(FakeRoom()),
            vad=object(),
        )

        class CloseCounter:
            """Count close calls for a fake SDK stream."""

            def __init__(self):
                self.calls = 0

            async def aclose(self):
                self.calls += 1

        speech = CloseCounter()
        audio = CloseCounter()
        track._speech_stream = speech
        track._audio_stream = audio

        await track.aclose()
        await track.aclose()

        self.assertEqual(speech.calls, 1)
        self.assertEqual(audio.calls, 1)

    async def test_stale_unsubscribe_does_not_close_replacement_track(self):
        """A delayed SDK event cannot stop a newer track instance."""
        room = FakeRoom()
        manager = LiveTranscriber.__new__(LiveTranscriber)
        manager._publisher = TranscriptionPublisher(room)
        manager._sessions = {}
        manager._session_tasks = {}
        manager._session_task_set = set()
        manager._tasks = set()
        manager._lifecycle_lock = asyncio.Lock()
        manager._closed = False
        manager._reconnecting = False
        current_track = object()
        replacement = TrackTranscriber(
            track=current_track,
            participant=SimpleNamespace(identity="alice"),
            track_sid="TR_A",
            publisher=manager._publisher,
            vad=object(),
        )
        key = ("alice", "TR_A")
        manager._sessions[key] = replacement

        await manager._remove_session(key, "track-unsubscribed", object())

        self.assertIs(manager._sessions[key], replacement)
        self.assertEqual(room.local_participant.messages, [])

    async def test_provider_is_closed_if_audio_stream_creation_fails(self):
        """Initialization failures still release the OpenAI client."""

        class Provider:
            """Track provider cleanup in the acquisition failure path."""

            def __init__(self):
                self.close_calls = 0

            async def aclose(self):
                self.close_calls += 1

        provider = Provider()
        track = TrackTranscriber(
            track=object(),
            participant=SimpleNamespace(identity="alice"),
            track_sid="TR_A",
            publisher=TranscriptionPublisher(FakeRoom()),
            vad=object(),
        )
        with patch("live_transcriber.create_openai_live_stt", return_value=provider):
            with patch(
                "live_transcriber.rtc.AudioStream.from_track",
                side_effect=RuntimeError("audio init failed"),
            ):
                with self.assertRaisesRegex(RuntimeError, "audio init failed"):
                    await track.run()

        self.assertEqual(provider.close_calls, 1)

    async def test_real_livekit_openai_sdk_surface_closes_without_network(self):
        """Exercise the installed SDK class without claiming real transcription."""
        provider = openai.STT(
            api_key="test-key",
            language=["fr", "en"],
            model=MODEL,
            use_realtime=True,
            vad=None,
        )
        try:
            self.assertEqual(provider.model, MODEL)
            self.assertTrue(provider.capabilities.streaming)
        finally:
            await provider.aclose()

    async def test_subscription_failure_gap_does_not_stop_active_session(self):
        """A failed old subscription only marks a gap for the current session."""
        room = FakeRoom()
        manager = LiveTranscriber.__new__(LiveTranscriber)
        manager._publisher = TranscriptionPublisher(room)
        manager._sessions = {}
        manager._session_tasks = {}
        manager._session_task_set = set()
        manager._tasks = set()
        manager._lifecycle_lock = asyncio.Lock()
        manager._closed = False
        manager._reconnecting = False
        session = TrackTranscriber(
            track=object(),
            participant=SimpleNamespace(identity="alice"),
            track_sid="TR_A",
            publisher=manager._publisher,
            vad=object(),
        )
        manager._sessions[("alice", "TR_A")] = session

        manager.on_track_subscription_failed(
            SimpleNamespace(identity="alice"), "TR_A", "subscription failed"
        )
        await asyncio.gather(*list(manager._tasks))

        self.assertIs(manager._sessions[("alice", "TR_A")], session)
        self.assertEqual(len(room.local_participant.messages), 1)

    def test_provider_uses_configured_model_and_realtime_languages(self):
        """Pass the provider configuration to the realtime STT factory."""
        with patch.dict(
            os.environ,
            {"OPENAI_API_KEY": "test-key", "OPENAI_STT_LANGUAGES": "fr,en"},
            clear=False,
        ):
            with patch("live_transcriber.openai.STT") as stt_factory:
                create_openai_live_stt(object())

        self.assertEqual(stt_factory.call_args.kwargs["model"], MODEL)
        self.assertEqual(stt_factory.call_args.kwargs["language"], ["fr", "en"])
        self.assertTrue(stt_factory.call_args.kwargs["use_realtime"])


if __name__ == "__main__":
    asyncio.run(unittest.main())
