"""Authoritative Deepgram selection through the real controlled track lifecycle."""

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlparse

from live_continuation_fixtures import eventually, numbered_frame
from live_deepgram_fixtures import (
    DeepgramSocket,
    metadata_frame,
    result_frame,
    session_for,
)
from test_live_provider_profiles import DEEPGRAM_SELECTION
from test_live_send_control import Gateway, authority
from test_live_transcriber_stop import AudioSource

from live_deepgram import AuthorizedDeepgram
from live_send_control import CONTROL_VERSION
from live_transcriber import TrackTranscriber


class ProviderWorkerTests(unittest.IsolatedAsyncioTestCase):
    """Keep Core authority, PCM partition, SDK loops, and delivery barrier real."""

    async def asyncSetUp(self):
        """Asyncsetup through the synthetic SDK fixture."""
        self.gateway = Gateway()
        self.gateway.responses["prepare"] = {"live_profile": dict(DEEPGRAM_SELECTION)}
        self.lease = authority(self.gateway)
        self.audio = AudioSource()
        self.enterContext(
            patch(
                "live_transcriber.rtc.AudioStream.from_track", return_value=self.audio
            )
        )
        self.ticks = asyncio.Queue()
        self.timers = asyncio.Queue()

        async def wait_rotation(deadline):
            """Wait rotation through the synthetic SDK fixture."""
            self.timers.put_nowait(deadline)
            await self.ticks.get()

        self.enterContext(patch("live_continuation.wait_rotation", wait_rotation))
        self.sockets = []
        self.providers = []
        self.sessions = []

        def create(**kwargs):
            """Create through the synthetic SDK fixture."""
            if self.sockets:
                self.assertTrue(self.sockets[-1].closed)
            socket = DeepgramSocket()
            session = session_for(socket)
            provider = AuthorizedDeepgram(
                api_key="fixture-unused-key", http_session=session, **kwargs
            )
            self.sockets.append(socket)
            self.providers.append(provider)
            self.sessions.append(session)
            return provider

        self.factory = self.enterContext(
            patch("live_provider_session.AuthorizedDeepgram", side_effect=create)
        )
        self.openai = self.enterContext(patch("live_provider_session.AuthorizedOpenAI"))
        self.publisher = SimpleNamespace(
            publish_gap=AsyncMock(), publish_segment=AsyncMock()
        )
        self.track = TrackTranscriber(
            track=object(),
            participant=SimpleNamespace(identity="fixture"),
            track_sid="TR_track",
            publisher=self.publisher,
            vad=None,
            authority=self.lease,
        )
        self.run = asyncio.create_task(self.track.run())
        await eventually(
            lambda: self.providers and self.providers[0]._socket is not None
        )
        await self.timers.get()

    async def asyncTearDown(self):
        """Asyncteardown through the synthetic SDK fixture."""
        for socket in self.sockets:
            socket.write_release.set()
            socket.control_release.set()
            socket.close_release.set()
        for gate in self.gateway.release.values():
            gate.set()
        self.audio.stop_release.set()
        self.track.fence_audio()
        async with asyncio.timeout(3):
            await asyncio.gather(self.track.aclose(), self.run, return_exceptions=True)
        self.openai.assert_not_called()

    def commands(self, action):
        """Commands through the synthetic SDK fixture."""
        return [body for body in self.gateway.requests if body["action"] == action]

    async def feed(self, first, stop):
        """Feed through the synthetic SDK fixture."""
        for number in range(first, stop):
            self.audio.frames.put_nowait(numbered_frame(number))
        for _ in range(first, stop):
            await self.audio.delivered.get()

    async def test_terminal_only_model_evidence_without_any_transcript_final(self):
        """Retain terminal Metadata when no Results were supplied."""
        terminal = metadata_frame()
        terminal["models"] = ["terminal-build"]
        terminal["model_info"] = {
            "terminal-build": {
                "name": "terminal-native-name",
                "version": "2026.10",
                "arch": "nova-native",
            }
        }
        self.sockets[0].terminal_frames = [terminal]
        old_leg = self.lease.leg_ref
        self.ticks.put_nowait(None)
        await asyncio.wait_for(self.timers.get(), 2)
        events = [body["event"] for body in self.commands("deliver")]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["kind"], "model_evidence")
        self.assertEqual(events[0]["leg_ref"], old_leg)
        self.assertEqual(events[0]["provider"], "deepgram")
        self.assertEqual(
            events[0]["models"],
            [
                {
                    "origin": "terminal",
                    "model_ref": "terminal-build",
                    "name": "terminal-native-name",
                    "version": "2026.10",
                    "architecture": "nova-native",
                }
            ],
        )
        self.assertFalse(events[0]["truncated"])
        self.assertTrue(self.sockets[0].closed)
        self.assertEqual(self.gateway.final_count, 0)
        self.publisher.publish_segment.assert_not_awaited()

    async def test_model_overflow_and_invalid_metadata_keep_every_final_text(self):
        """Missing model details and overflow never discard final text."""
        results = []
        for number in range(10):
            result = result_frame(
                start=number / 100, duration=0.01, text=f"  final {number} e\u0301 😀\n"
            )
            result["metadata"]["model_uuid"] = f"build-{number}"
            result["metadata"]["model_info"] = {"name": "x" * 161}
            results.append(result)
        terminal = metadata_frame()
        terminal.pop("model_info")
        self.sockets[0].terminal_frames = [*results, terminal]
        self.ticks.put_nowait(None)
        await asyncio.wait_for(self.timers.get(), 2)
        events = [body["event"] for body in self.commands("deliver")]
        finals = [event for event in events if event["kind"] == "final"]
        self.assertEqual(
            [event["text"] for event in finals],
            [result["channel"]["alternatives"][0]["transcript"] for result in results],
        )
        self.assertEqual(self.gateway.final_count, len(results))
        self.assertEqual([event["version"] for event in finals], [2] * len(results))
        self.assertEqual(events[-1]["kind"], "model_evidence")
        self.assertTrue(events[-1]["truncated"])
        self.assertEqual(
            [record["model_ref"] for record in events[-1]["models"]],
            [f"build-{number}" for number in range(8)],
        )
        self.assertTrue(all(record["name"] is None for record in events[-1]["models"]))
        self.assertFalse(self.track.incomplete_reasons)

    async def test_two_legs_conserve_pcm_profile_and_distinct_native_final_corrections(
        self,
    ):
        """Two legs conserve pcm profile and distinct native final corrections."""
        first = self.sockets[0]
        correction = result_frame(duration=0.06, text="corrected first")
        word_only = result_frame(duration=0.06, text="corrected first")
        word_only["channel"]["alternatives"][0]["words"][0]["confidence"] = 0.8
        first.terminal_frames = [
            result_frame(),
            result_frame(0.05, 0.07, "second final"),
            correction,
            word_only,
            word_only,
            metadata_frame(),
        ]
        await self.feed(1, 7)
        self.ticks.put_nowait(None)
        await self.timers.get()
        await eventually(
            lambda: len(self.sockets) > 1 and self.providers[1]._socket is not None
        )
        selected = self.lease.live_profile
        await self.feed(7, 12)
        self.ticks.put_nowait(None)
        await self.timers.get()
        self.assertIs(self.lease.live_profile, selected)
        self.assertEqual(len(self.sockets), 3)
        self.assertEqual(
            [len(socket.samples) for socket in self.sockets], [2880, 2400, 0]
        )
        samples = self.sockets[0].samples + self.sockets[1].samples
        # The two provider legs partition all eleven captured frames once.
        self.assertEqual(
            samples, [number for number in range(1, 12) for _ in range(480)]
        )
        self.assertEqual(
            [body["audio_end_sample"] for body in self.commands("rotate")], [2880, 5280]
        )
        finals = [
            body["event"]
            for body in self.commands("deliver")
            if body["event"]["kind"] == "final"
        ]
        old_leg = finals[0]["leg_ref"]
        old_finals = [event for event in finals if event["leg_ref"] == old_leg]
        self.assertEqual(len(old_finals), 4)
        self.assertEqual([event["revision"] for event in old_finals], [1, 1, 2, 3])
        self.assertEqual(old_finals[0]["item_ref"], old_finals[2]["item_ref"])
        self.assertNotEqual(old_finals[0]["item_ref"], old_finals[1]["item_ref"])
        self.assertTrue(
            all(event["timing"] == {"state": "unavailable"} for event in finals)
        )
        self.assertTrue(all(event["version"] == CONTROL_VERSION for event in finals))
        self.assertTrue(
            all(
                provider.terminal_metadata == metadata_frame()
                for provider in self.providers[:2]
            )
        )
        for session in self.sessions[:2]:
            request = session.ws_connect.await_args
            query = parse_qs(urlparse(request.args[0]).query)
            self.assertEqual(
                {
                    key: query[key]
                    for key in (
                        "model",
                        "language",
                        "sample_rate",
                        "channels",
                        "interim_results",
                        "punctuate",
                        "smart_format",
                        "endpointing",
                        "filler_words",
                        "mip_opt_out",
                    )
                },
                {
                    "model": ["nova-3"],
                    "language": ["multi"],
                    "sample_rate": ["24000"],
                    "channels": ["1"],
                    "interim_results": ["true"],
                    "punctuate": ["true"],
                    "smart_format": ["false"],
                    "endpointing": ["25"],
                    "filler_words": ["true"],
                    "mip_opt_out": ["true"],
                },
            )
        self.assertEqual(len(self.publisher.publish_segment.await_args_list), 6)

    async def test_publication_and_physical_close_both_precede_successor(self):
        """Publication and physical close both precede successor."""
        socket = self.sockets[0]
        publishing = asyncio.Event()
        release = asyncio.Event()

        async def publish(event):
            """Publish through the synthetic SDK fixture."""
            publishing.set()
            await release.wait()

        self.publisher.publish_segment.side_effect = publish
        socket.close_release.clear()
        try:
            await self.feed(1, 7)
            self.ticks.put_nowait(None)
            await publishing.wait()
            await socket.close_entered.wait()
            await self.feed(7, 10)
            self.assertEqual(len(self.sockets), 1)
            self.assertEqual(self.commands("rotate"), [])
            socket.close_release.set()
            await eventually(lambda: self.providers[0]._terminal_complete)
            self.assertEqual(self.commands("rotate"), [])
            release.set()
            await self.timers.get()
            self.assertEqual(len(self.sockets), 2)
            self.assertTrue(socket.closed)
            self.assertEqual(self.commands("rotate")[0]["audio_end_sample"], 2880)
        finally:
            release.set()
            socket.close_release.set()

    async def test_missing_terminal_metadata_never_rotates_or_opens_fallback(self):
        """Missing terminal metadata never rotates or opens fallback."""
        self.sockets[0].terminal_frames = [result_frame()]
        await self.feed(1, 4)
        self.ticks.put_nowait(None)
        async with asyncio.timeout(2):
            result = (await asyncio.gather(self.run, return_exceptions=True))[0]
        self.assertIsInstance(result, BaseExceptionGroup)
        self.assertEqual(self.commands("rotate"), [])
        self.assertEqual(len(self.sockets), 1)
        self.assertFalse(self.lease.can_send())
        self.assertTrue(self.track.incomplete_reasons)

    async def test_revocation_cancels_real_sender_before_held_pcm_resumes(self):
        """Revocation cancels real sender before held pcm resumes."""
        socket = self.sockets[0]
        socket.write_release.clear()
        await self.feed(1, 9)
        await socket.write_entered.wait()
        self.track.fence_audio()
        socket.write_release.set()
        await asyncio.gather(self.track.aclose(), self.run, return_exceptions=True)
        self.assertEqual(socket.samples, [])
        self.assertNotIn("Finalize", socket.controls)
        self.assertNotIn("CloseStream", socket.controls)
        self.assertEqual(self.commands("rotate"), [])

    async def test_interrupted_event_consumer_prevents_successor(self):
        """Cancelling the actual SDK iterator consumer cannot publish a handoff."""
        self.track._reader_task.cancel()
        async with asyncio.timeout(2):
            await asyncio.gather(self.run, return_exceptions=True)
        self.assertEqual(self.commands("rotate"), [])
        self.assertEqual(len(self.sockets), 1)
        self.assertFalse(self.lease.can_send())
        self.assertTrue(self.track.incomplete_reasons)
        self.assertNotIn("Finalize", self.sockets[0].controls)

    async def test_withdrawal_during_sealed_tail_cancels_drain_without_flush(self):
        """Revocation interrupts a planned tail already blocked in real send_bytes."""
        socket = self.sockets[0]
        socket.write_release.clear()
        await self.feed(1, 4)
        await socket.write_entered.wait()
        self.ticks.put_nowait(None)
        await eventually(lambda: self.providers[0].ledger.expected_samples is not None)
        self.track.fence_audio()
        socket.write_release.set()
        async with asyncio.timeout(2):
            await asyncio.gather(self.track.aclose(), self.run, return_exceptions=True)
        self.assertEqual(socket.samples, [])
        self.assertNotIn("Finalize", socket.controls)
        self.assertNotIn("CloseStream", socket.controls)
        self.assertEqual(self.commands("rotate"), [])
        self.assertFalse(self.lease.can_send())

    async def test_interrupted_consumer_during_terminal_join_prevents_handoff(self):
        """Metadata and physical close cannot compensate for a cancelled reader."""
        socket = self.sockets[0]
        socket.close_release.clear()
        self.ticks.put_nowait(None)
        await socket.close_entered.wait()
        self.track._reader_task.cancel()
        await asyncio.gather(self.track._reader_task, return_exceptions=True)
        socket.close_release.set()
        async with asyncio.timeout(2):
            await asyncio.gather(self.run, return_exceptions=True)
        self.assertEqual(self.commands("rotate"), [])
        self.assertEqual(len(self.sockets), 1)
        self.assertFalse(self.lease.can_send())
        self.assertTrue(self.track.incomplete_reasons)
