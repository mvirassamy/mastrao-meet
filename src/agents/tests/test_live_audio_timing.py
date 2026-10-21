"""Offline evidence for timing left visible by the installed realtime plugin."""

import asyncio
import json
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import aiohttp
from livekit.agents import APIConnectOptions, stt
from livekit.plugins import openai
from openai import AsyncOpenAI

from live_transcriber import MODEL, TrackTranscriber, TranscriptionPublisher


class ProtocolSocket:
    """Feed raw protocol messages through the real SDK receive loop."""

    def __init__(self, *messages: dict):
        """Queue synthetic server messages; never create a network connection."""
        self.messages = asyncio.Queue()
        self.feed(*messages)
        self.send_json = AsyncMock()
        self.close = AsyncMock()
        self.close_code = 1006

    def feed(self, *messages: dict):
        """Deliver messages on an already established synthetic connection."""
        for message in messages:
            self.messages.put_nowait(
                aiohttp.WSMessage(aiohttp.WSMsgType.TEXT, json.dumps(message), "")
            )

    async def receive(self):
        """Block when fixtures are exhausted, like an idle realtime socket."""
        return await self.messages.get()


def completed(item_id: str, text: str) -> dict:
    """Build a transcription completion without fabricating audio bounds."""
    return {
        "type": "conversation.item.input_audio_transcription.completed",
        "item_id": item_id,
        "transcript": text,
        "languages": [{"code": "fr"}, {"code": "en"}],
    }


class LiveAudioTimingTests(unittest.IsolatedAsyncioTestCase):
    """Prove uncertainty and UI compatibility with SDK and raw event fixtures."""

    def setUp(self):
        """Capture actual published JSON envelopes for each isolated test."""
        self.sender = AsyncMock()
        room = SimpleNamespace(local_participant=SimpleNamespace(send_text=self.sender))
        self.publisher = TranscriptionPublisher(room)

    def track(self, participant="alice", track_sid="TR_A"):
        """Create the production publisher path for a synthetic speaker."""
        return TrackTranscriber(
            track=object(),
            participant=SimpleNamespace(identity=participant),
            track_sid=track_sid,
            publisher=self.publisher,
            vad=object(),
        )

    def envelopes(self):
        """Decode emitted transport envelopes without inspecting timing helpers."""
        return [json.loads(call.args[0]) for call in self.sender.await_args_list]

    async def sdk_stream(self, *sockets):
        """Replace only the test connection boundary; execute plugin code intact.

        _connect_ws is private: this test seam is deliberately not a production
        adaptation. The plugin has no public realtime transport injection API.
        """
        client = await self.enterAsyncContext(AsyncOpenAI(api_key="test-key"))
        connector = AsyncMock(side_effect=sockets)
        with patch.object(openai.STT, "_connect_ws", connector):
            provider = openai.STT(
                client=client,
                model=MODEL,
                language=["fr", "en"],
                use_realtime=True,
                vad=None,
            )
        self.addAsyncCleanup(provider.aclose)
        stream = provider.stream(
            conn_options=APIConnectOptions(max_retry=1, retry_interval=0)
        )
        self.addAsyncCleanup(stream.aclose)
        return stream, connector

    async def publish_item(self, stream, track):
        """Consume one real SDK completion and its corresponding usage event."""
        events = []
        async with asyncio.timeout(2):
            async for event in stream:
                events.append(event)
                await track._handle_event(event)
                if event.type == stt.SpeechEventType.RECOGNITION_USAGE:
                    return events
        self.fail("SDK stream ended before recognition usage")

    async def test_raw_audio_bounds_are_lost_by_sdk_not_replaced_with_receipt(self):
        """Even when raw bounds exist, the installed SDK exposes only duration."""
        socket = ProtocolSocket(
            {"type": "session.created", "session": {"id": "session-a"}},
            {
                "type": "input_audio_buffer.speech_started",
                "item_id": "a",
                "audio_start_ms": 0,
            },
            {
                "type": "input_audio_buffer.speech_stopped",
                "item_id": "a",
                "audio_end_ms": 1500,
            },
            completed("a", " Bonjour, hello ! \n"),
        )
        stream, connector = await self.sdk_stream(socket)
        track = self.track()
        events = await self.publish_item(stream, track)
        final = next(
            event
            for event in events
            if event.type == stt.SpeechEventType.FINAL_TRANSCRIPT
        )
        self.assertEqual(final.alternatives[0].start_time, 0)
        self.assertEqual(final.alternatives[0].end_time, 0)
        self.assertIsNone(final.speech_start_time)
        self.assertIsNone(final.speech_end_time)
        self.assertEqual(events[-1].recognition_usage.audio_duration, 1.5)
        envelope = self.envelopes()[0]
        self.assertEqual(envelope["text"], " Bonjour, hello ! \n")
        self.assertEqual(envelope["language"], "fr")
        self.assertEqual(envelope["participantIdentity"], "alice")
        self.assertEqual(envelope["trackSid"], "TR_A")
        self.assertGreater(envelope["startTime"], 0)
        self.assertGreater(envelope["endTime"], 0)
        self.assertEqual(
            envelope["audioTiming"],
            {
                "state": "uncertain",
                "reason": "sdk-item-timing-unavailable",
                "clock": "sdk-speech-data",
                "unit": "ms",
                "startMs": None,
                "endMs": None,
                "streamOrigin": {
                    "type": "sdk-stream",
                    "id": track.leg_id,
                    "providerSessionId": None,
                },
                "meetingOffsetMs": None,
            },
        )
        connector.assert_awaited_once()

    async def test_raw_missing_bounds_replays_and_text_correction_stay_uncertain(self):
        """Missing timing neither drops finals nor turns receipt into evidence."""
        stream, _ = await self.sdk_stream(
            ProtocolSocket(
                completed("a", "bonjour"),
                completed("a", "bonjour"),
                completed("a", "bonjour corrigé"),
            )
        )
        track = self.track()
        for _ in range(3):
            await self.publish_item(stream, track)
        envelopes = self.envelopes()
        self.assertEqual([item["revision"] for item in envelopes], [1, 2])
        self.assertEqual(
            [item["text"] for item in envelopes], ["bonjour", "bonjour corrigé"]
        )
        self.assertEqual(envelopes[0]["startTime"], envelopes[1]["startTime"])
        self.assertEqual(envelopes[0]["endTime"], envelopes[1]["endTime"])
        self.assertEqual(envelopes[0]["audioTiming"], envelopes[1]["audioTiming"])

    async def test_sdk_socket_reset_does_not_certify_a_new_audio_origin(self):
        """An SDK timeout rotates sockets within the same logical track leg."""
        second = ProtocolSocket()
        with patch("livekit.plugins.openai.stt._max_session_duration", -1):
            stream, connector = await self.sdk_stream(
                ProtocolSocket(completed("a", "avant reset")),
                second,
            )
            track = self.track()
            await self.publish_item(stream, track)
        second.feed(completed("a", "après reset"))
        await self.publish_item(stream, track)
        self.assertEqual(connector.await_count, 2)
        envelopes = self.envelopes()
        self.assertEqual([item["revision"] for item in envelopes], [1, 2])
        self.assertEqual(envelopes[0]["legId"], envelopes[1]["legId"])
        for envelope in envelopes:
            self.assertEqual(envelope["audioTiming"]["state"], "uncertain")
            origin = envelope["audioTiming"]["streamOrigin"]
            self.assertIsNone(origin["providerSessionId"])
            self.assertIsNone(envelope["audioTiming"]["meetingOffsetMs"])

    async def test_sdk_socket_failure_retries_without_a_proven_audio_origin(self):
        """A real SDK retry must not acquire an invented meeting offset."""
        first = ProtocolSocket(completed("a", "avant coupure"))
        first.messages.put_nowait(aiohttp.WSMessage(aiohttp.WSMsgType.CLOSED, None, ""))
        stream, connector = await self.sdk_stream(
            first, ProtocolSocket(completed("b", "après coupure"))
        )
        track = self.track()
        with (
            self.assertLogs("livekit.agents", level="WARNING"),
            self.assertLogs("livekit.plugins.openai", level="ERROR"),
        ):
            await self.publish_item(stream, track)
            await self.publish_item(stream, track)
        self.assertEqual(connector.await_count, 2)
        for envelope in self.envelopes():
            self.assertEqual(envelope["audioTiming"]["state"], "uncertain")
            self.assertIsNone(envelope["audioTiming"]["startMs"])
            self.assertIsNone(envelope["audioTiming"]["meetingOffsetMs"])

    async def test_sdk_bounds_and_timing_correction_remain_unanchored(self):
        """Explicit SDK evidence survives corrections without assuming alignment."""
        track = self.track()
        data = stt.SpeechData(language="fr", text="bonjour", start_time=1, end_time=2)
        event = stt.SpeechEvent(
            type=stt.SpeechEventType.FINAL_TRANSCRIPT,
            request_id="a",
            alternatives=[data],
        )
        await track._handle_event(event)
        correction = replace(event, alternatives=[replace(data, end_time=2.5)])
        await track._handle_event(correction)
        await track._handle_event(replace(correction, created_at=999))
        envelopes = self.envelopes()
        self.assertEqual([item["revision"] for item in envelopes], [1, 2])
        self.assertEqual(
            [item["audioTiming"]["startMs"] for item in envelopes], [1000, 1000]
        )
        self.assertEqual(
            [item["audioTiming"]["endMs"] for item in envelopes], [2000, 2500]
        )
        for envelope in envelopes:
            self.assertEqual(
                envelope["audioTiming"]["reason"], "provider-stream-origin-unavailable"
            )
            self.assertEqual(envelope["audioTiming"]["state"], "uncertain")

    async def test_wall_clocks_defaults_and_invalid_bounds_are_not_audio_proof(self):
        """Zero defaults, partial and malformed intervals cannot certify audio."""
        cases = [
            (0, 0, None, None, "sdk-item-timing-unavailable"),
            (0, 2, None, 2000, "sdk-item-timing-incomplete"),
            (1, 0, 1000, None, "sdk-item-timing-incomplete"),
            (2, 1, 2000, 1000, "sdk-item-timing-invalid"),
            (1, 1, 1000, 1000, "sdk-item-timing-invalid"),
            (-1, float("nan"), None, None, "sdk-item-timing-unavailable"),
            (float("inf"), 0, None, None, "sdk-item-timing-unavailable"),
        ]
        for start, end, expected_start, expected_end, reason in cases:
            with self.subTest(start=start, end=end):
                await self.track()._handle_event(
                    stt.SpeechEvent(
                        type=stt.SpeechEventType.FINAL_TRANSCRIPT,
                        request_id="a",
                        alternatives=[
                            stt.SpeechData(
                                language="fr",
                                text="bonjour",
                                start_time=start,
                                end_time=end,
                            )
                        ],
                        speech_start_time=100,
                        speech_end_time=101,
                    )
                )
                envelope = self.envelopes()[-1]
                self.assertEqual(envelope["startTime"], 100000)
                self.assertEqual(envelope["endTime"], 101000)
                self.assertEqual(envelope["audioTiming"]["startMs"], expected_start)
                self.assertEqual(envelope["audioTiming"]["endMs"], expected_end)
                self.assertEqual(envelope["audioTiming"]["reason"], reason)
                self.assertEqual(envelope["audioTiming"]["state"], "uncertain")

    async def test_parallel_tracks_and_new_legs_have_separate_logical_origins(self):
        """Origin IDs scope logical SDK streams, never a shared meeting clock."""
        stream, _ = await self.sdk_stream(ProtocolSocket(completed("a", "bonjour")))
        first = self.track()
        await self.publish_item(stream, first)
        event = stt.SpeechEvent(
            type=stt.SpeechEventType.FINAL_TRANSCRIPT,
            request_id="a",
            alternatives=[stt.SpeechData(language="en", text="hello")],
        )
        for track in [self.track("bob", "TR_B"), self.track()]:
            await track._handle_event(event)
        envelopes = self.envelopes()
        origins = [item["audioTiming"]["streamOrigin"]["id"] for item in envelopes]
        self.assertEqual(len(set(origins)), 3)
        self.assertEqual(
            [item["participantIdentity"] for item in envelopes],
            ["alice", "bob", "alice"],
        )
        self.assertEqual(
            [item["trackSid"] for item in envelopes], ["TR_A", "TR_B", "TR_A"]
        )
        self.assertEqual([item["revision"] for item in envelopes], [1, 1, 1])
