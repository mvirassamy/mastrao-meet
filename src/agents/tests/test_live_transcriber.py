"""Contract tests for the opt-in live transcription worker."""

import asyncio
import json
import os
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

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


def track_transcriber(room: FakeRoom, track_sid: str = "TR_A"):
    """Create an isolated track using the real publisher and synthetic identity."""
    return TrackTranscriber(
        track=object(),
        participant=SimpleNamespace(identity="alice"),
        track_sid=track_sid,
        publisher=TranscriptionPublisher(room),
        vad=object(),
    )


class LiveTranscriptionContractTests(unittest.IsolatedAsyncioTestCase):
    """Verify identity, replacement and gap semantics without network I/O."""

    async def test_final_replays_do_not_publish_or_reset_revisions(self):
        """Receipt time cannot turn a final replay into a correction."""
        room = FakeRoom()
        track = track_transcriber(room)
        interim = speech_event(stt.SpeechEventType.INTERIM_TRANSCRIPT, "bon", "a")
        final = speech_event(stt.SpeechEventType.FINAL_TRANSCRIPT, "bonjour", "a")
        correction = speech_event(
            stt.SpeechEventType.FINAL_TRANSCRIPT, "bonjour à tous", "a"
        )

        with patch("live_transcriber.time.time", return_value=10) as clock:
            await track._handle_event(interim)
            clock.return_value = 20
            await track._handle_event(final)
            clock.return_value = 30
            await track._handle_event(final)
            await track._handle_event(interim)
            await track._handle_event(correction)
            clock.return_value = 40
            await track._handle_event(correction)

        envelopes = [
            json.loads(message[2]) for message in room.local_participant.messages
        ]
        self.assertEqual(
            [item["text"] for item in envelopes], ["bon", "bonjour", "bonjour à tous"]
        )
        self.assertEqual([item["revision"] for item in envelopes], [1, 2, 3])
        self.assertEqual([item["sequence"] for item in envelopes], [1, 2, 3])
        self.assertEqual([item["startTime"] for item in envelopes], [10000] * 3)
        self.assertEqual([item["endTime"] for item in envelopes], [None, 20000, 20000])

    async def test_late_interim_cannot_replace_a_final(self):
        """A final-first item remains final despite a delayed interim."""
        room = FakeRoom()
        track = track_transcriber(room)
        await track._handle_event(
            speech_event(stt.SpeechEventType.FINAL_TRANSCRIPT, "bonjour", "a")
        )
        await track._handle_event(
            speech_event(stt.SpeechEventType.INTERIM_TRANSCRIPT, "bon", "a")
        )
        await track._handle_event(
            speech_event(stt.SpeechEventType.FINAL_TRANSCRIPT, "bonjour !", "a")
        )
        envelopes = [
            json.loads(message[2]) for message in room.local_participant.messages
        ]
        self.assertEqual([item["state"] for item in envelopes], ["final", "final"])
        self.assertEqual([item["revision"] for item in envelopes], [1, 2])

    async def test_each_semantic_final_change_is_a_correction(self):
        """Raw text, language and explicit SDK clocks distinguish final revisions."""
        changes = [
            ({"text": " bonjour \n"}, {}),
            ({"language": LanguageCode("en")}, {}),
            ({"start_time": 1.5}, {}),
            ({"end_time": 2.5}, {}),
            ({}, {"speech_start_time": 0.0}),
            ({}, {"speech_end_time": 2.5}),
            ({"start_time": 1.0001}, {}),
        ]
        for data_changes, event_changes in changes:
            with self.subTest(data=data_changes, event=event_changes):
                room = FakeRoom()
                track = track_transcriber(room)
                original = speech_event(
                    stt.SpeechEventType.FINAL_TRANSCRIPT, "bonjour", "a"
                )
                original = replace(
                    original,
                    alternatives=[
                        replace(original.alternatives[0], start_time=1, end_time=2)
                    ],
                )
                await track._handle_event(original)
                corrected_data = replace(original.alternatives[0], **data_changes)
                corrected = replace(
                    original, alternatives=[corrected_data], **event_changes
                )
                await track._handle_event(corrected)
                await track._handle_event(replace(corrected, created_at=999))
                envelopes = [
                    json.loads(message[2])
                    for message in room.local_participant.messages
                ]
                self.assertEqual([item["revision"] for item in envelopes], [1, 2])
                self.assertEqual(envelopes[-1]["text"], corrected_data.text)
                self.assertEqual(
                    envelopes[-1]["language"], str(corrected_data.language)
                )

    async def test_explicit_provider_times_are_published_and_preserved_on_replay(self):
        """Both SDK timing surfaces take precedence over receipt fallbacks."""
        room = FakeRoom()
        track = track_transcriber(room)
        original = speech_event(stt.SpeechEventType.FINAL_TRANSCRIPT, "bonjour", "a")
        original = replace(
            original,
            alternatives=[replace(original.alternatives[0], start_time=1, end_time=2)],
        )
        for clocks in [{}, {"speech_start_time": 0.0, "speech_end_time": 3}]:
            event = replace(original, **clocks)
            await track._handle_event(event)
            await track._handle_event(event)
        envelopes = [
            json.loads(message[2]) for message in room.local_participant.messages
        ]
        self.assertEqual([item["revision"] for item in envelopes], [1, 2])
        self.assertEqual([item["startTime"] for item in envelopes], [1000, 0])
        self.assertEqual([item["endTime"] for item in envelopes], [2000, 3000])

    async def test_items_tracks_and_reconnected_legs_are_independent(self):
        """Reused provider IDs never merge independent items or stream legs."""
        room = FakeRoom()
        first = track_transcriber(room)
        second = track_transcriber(room, "TR_B")
        reconnected = track_transcriber(room)
        for track, item_id in [
            (first, "a"),
            (first, "b"),
            (second, "a"),
            (reconnected, "a"),
        ]:
            await track._handle_event(
                speech_event(stt.SpeechEventType.FINAL_TRANSCRIPT, "bonjour", item_id)
            )
        await first._handle_event(
            speech_event(stt.SpeechEventType.FINAL_TRANSCRIPT, "corrigé", "a")
        )
        envelopes = [
            json.loads(message[2]) for message in room.local_participant.messages
        ]
        self.assertEqual([item["revision"] for item in envelopes], [1, 1, 1, 1, 2])
        self.assertEqual(envelopes[0]["legId"], envelopes[1]["legId"])
        self.assertNotEqual(envelopes[0]["legId"], envelopes[2]["legId"])
        self.assertNotEqual(envelopes[0]["legId"], envelopes[3]["legId"])
        self.assertEqual(envelopes[0]["trackSid"], envelopes[3]["trackSid"])

    async def test_missing_request_id_does_not_invent_deduplication(self):
        """Unidentified events remain independent even with identical final text."""
        room = FakeRoom()
        track = track_transcriber(room)
        for event_type in [
            stt.SpeechEventType.INTERIM_TRANSCRIPT,
            stt.SpeechEventType.FINAL_TRANSCRIPT,
            stt.SpeechEventType.FINAL_TRANSCRIPT,
        ]:
            await track._handle_event(speech_event(event_type, "bonjour", ""))
        envelopes = [
            json.loads(message[2]) for message in room.local_participant.messages
        ]
        self.assertEqual(len({item["itemId"] for item in envelopes}), 3)
        self.assertEqual([item["revision"] for item in envelopes], [1, 1, 1])

    async def test_item_capacity_preserves_existing_revisions_and_rejects_new_items(
        self,
    ):
        """Capacity includes interim items and never evicts final identity."""
        room = FakeRoom()
        track = track_transcriber(room)
        with patch("live_transcriber.MAX_ITEMS_PER_TRACK", 2):
            for event_type, text, item_id in [
                (stt.SpeechEventType.FINAL_TRANSCRIPT, "un", "a"),
                (stt.SpeechEventType.INTERIM_TRANSCRIPT, "de", "b"),
                (stt.SpeechEventType.FINAL_TRANSCRIPT, "deux", "b"),
                (stt.SpeechEventType.FINAL_TRANSCRIPT, "un corrigé", "a"),
                (stt.SpeechEventType.FINAL_TRANSCRIPT, "un corrigé", "a"),
            ]:
                await track._handle_event(speech_event(event_type, text, item_id))
            with self.assertRaisesRegex(RuntimeError, "item capacity exceeded"):
                await track._handle_event(
                    speech_event(stt.SpeechEventType.FINAL_TRANSCRIPT, "trois", "c")
                )
        envelopes = [
            json.loads(message[2]) for message in room.local_participant.messages
        ]
        self.assertEqual([item["itemId"] for item in envelopes], ["a", "b", "b", "a"])
        self.assertEqual([item["revision"] for item in envelopes], [1, 1, 2, 2])

    async def test_capacity_failure_closes_stream_and_marks_gap_without_retry(self):
        """The existing session failure path reports overflow and stops ASR."""
        room = FakeRoom()
        track = track_transcriber(room)
        speech = MagicMock()
        speech.__aiter__.return_value = [
            speech_event(stt.SpeechEventType.FINAL_TRANSCRIPT, "un", "a"),
            speech_event(stt.SpeechEventType.FINAL_TRANSCRIPT, "deux", "b"),
            speech_event(stt.SpeechEventType.FINAL_TRANSCRIPT, "trois", "c"),
        ]
        speech.aclose = AsyncMock()
        audio = MagicMock()
        audio.__aiter__.return_value = []
        audio.aclose = AsyncMock()
        provider = MagicMock()
        provider.stream.return_value = speech
        provider.aclose = AsyncMock()
        manager = LiveTranscriber.__new__(LiveTranscriber)
        manager._publisher = track._publisher
        manager._closed = False
        key = ("alice", "TR_A")
        manager._sessions = {key: track}
        manager._session_tasks = {key: None}

        with (
            patch("live_transcriber.MAX_ITEMS_PER_TRACK", 2),
            patch(
                "live_transcriber.create_openai_live_stt", return_value=provider
            ) as factory,
            patch("live_transcriber.rtc.AudioStream.from_track", return_value=audio),
            self.assertLogs("transcriber.live", level="ERROR") as logs,
        ):
            with self.assertRaises(ExceptionGroup):
                await manager._run_session(key, track)

        factory.assert_called_once()
        provider.stream.assert_called_once()
        speech.aclose.assert_awaited_once()
        audio.aclose.assert_awaited_once()
        provider.aclose.assert_awaited_once()
        self.assertIn("live transcription failed", "\n".join(logs.output))
        self.assertIn("publication-failed", track.incomplete_reasons)
        self.assertIs(manager._sessions[key], track)
        messages = room.local_participant.messages
        self.assertEqual(
            [message[0] for message in messages],
            [TRANSCRIPTION_TOPIC, TRANSCRIPTION_TOPIC, GAP_TOPIC, GAP_TOPIC],
        )
        self.assertEqual(json.loads(messages[-2][2])["reason"], "publication-failed")
        self.assertEqual(json.loads(messages[-1][2])["reason"], "provider-error")

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
