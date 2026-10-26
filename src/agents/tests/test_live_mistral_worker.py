"""Real controlled-track rotation/publication using Mistral's synthetic socket."""

import asyncio
import unittest
from dataclasses import asdict
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from live_mistral_fixtures import MistralSocket, eventually, frame, session_for
from test_live_provider_profiles import MISTRAL_SELECTION
from test_live_send_control import Gateway, authority
from test_live_transcriber_stop import AudioSource

from live_mistral import MODEL, AuthorizedMistral
from live_transcriber import TrackTranscriber

FRAME_SAMPLES = 480


class CountedAudioSource(AudioSource):
    """Count actual RTC reader acquisitions across physical provider rotations."""

    def __init__(self):
        """Start one synthetic RTC source with explicit iterator accounting."""
        super().__init__()
        self.readers = 0

    def __aiter__(self):
        """Keep the underlying cancellation behavior and count each consumer."""
        self.readers += 1
        return super().__aiter__()


class MistralWorkerTests(unittest.IsolatedAsyncioTestCase):
    """Keep factory, authority, PCM partition, publication and durable codec real."""

    async def asyncSetUp(self):
        """Replace only physical I/O, RTC source and rotation timer triggers."""
        self.gateway = Gateway()
        self.profile_snapshot = dict(MISTRAL_SELECTION)
        self.gateway.responses["prepare"] = {"live_profile": self.profile_snapshot}
        self.lease = authority(self.gateway)
        self.audio = CountedAudioSource()
        self.rtc_factory = self.enterContext(
            patch(
                "live_transcriber.rtc.AudioStream.from_track", return_value=self.audio
            )
        )
        self.ticks = asyncio.Queue()
        self.timers = asyncio.Queue()

        async def wait_rotation(deadline):
            self.timers.put_nowait(deadline)
            await self.ticks.get()

        self.enterContext(patch("live_continuation.wait_rotation", wait_rotation))
        self.sockets = []
        self.sessions = []
        self.providers = []

        def create(**kwargs):
            if self.sockets:
                self.assertTrue(self.sockets[-1].closed)
            socket = MistralSocket(
                request_id=f"native-mistral-request-{len(self.sockets)}"
            )
            session = session_for(socket)
            provider = AuthorizedMistral(
                api_key="synthetic-unused-key", http_session=session, **kwargs
            )
            self.sockets.append(socket)
            self.sessions.append(session)
            self.providers.append(provider)
            return provider

        self.factory = self.enterContext(
            patch("live_provider_session.AuthorizedMistral", side_effect=create)
        )
        self.openai = self.enterContext(patch("live_provider_session.AuthorizedOpenAI"))
        self.deepgram = self.enterContext(
            patch("live_provider_session.AuthorizedDeepgram")
        )
        self.publisher = SimpleNamespace(
            publish_gap=AsyncMock(), publish_segment=AsyncMock()
        )
        self.track = TrackTranscriber(
            track=object(),
            participant=SimpleNamespace(identity="synthetic-speaker"),
            track_sid="TR_track",
            publisher=self.publisher,
            vad=None,
            authority=self.lease,
        )
        self.releases = []
        self.run = asyncio.create_task(self.track.run())
        await eventually(
            lambda: self.providers and self.providers[0]._socket is not None
        )
        async with asyncio.timeout(2):
            await self.timers.get()

    async def asyncTearDown(self):
        """Unblock every physical/publication barrier before joining the source."""
        for socket in self.sockets:
            for gate in socket.release.values():
                gate.set()
        for gate in (*self.gateway.release.values(), *self.releases):
            gate.set()
        self.audio.stop_release.set()
        self.track.fence_audio()
        async with asyncio.timeout(3):
            await asyncio.gather(self.track.aclose(), self.run, return_exceptions=True)
        self.openai.assert_not_called()
        self.deepgram.assert_not_called()
        self.rtc_factory.assert_called_once()
        self.assertEqual(self.audio.readers, 1)
        for session in self.sessions:
            session.ws_connect.assert_awaited_once()

    def commands(self, action):
        """Read actual worker request envelopes at the synthetic Core boundary."""
        return [body for body in self.gateway.requests if body["action"] == action]

    def finals(self, leg_ref=None):
        """Preserve durable revision order while allowing a per-leg assertion."""
        events = [
            body["event"]
            for body in self.commands("deliver")
            if body["event"]["kind"] == "final"
        ]
        if leg_ref is None:
            return events
        return [event for event in events if event["leg_ref"] == leg_ref]

    async def feed(self, frames):
        """Drive the single real audio pump with deterministic PCM identities."""
        for item in frames:
            self.audio.frames.put_nowait(item)
        for _ in frames:
            await self.audio.delivered.get()

    async def rotate(self):
        """Trigger the real seal/drain/close/Core-rotate/successor sequence."""
        self.ticks.put_nowait(None)
        async with asyncio.timeout(2):
            await self.timers.get()
        await eventually(lambda: self.providers[-1]._socket is not None)

    async def test_native_session_and_done_survive_empty_text_rotation(self):
        """Deduplicate session facts and retain terminal facts without text."""
        old_leg = self.lease.leg_ref
        await self.rotate()
        events = [body["event"] for body in self.commands("deliver")]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["kind"], "model_evidence")
        self.assertEqual(events[0]["provider"], "mistral")
        self.assertEqual(events[0]["leg_ref"], old_leg)
        self.assertEqual(
            events[0]["models"],
            [
                {
                    "origin": origin,
                    "model_ref": MODEL,
                    "name": None,
                    "version": None,
                    "architecture": None,
                }
                for origin in ("session", "terminal")
            ],
        )
        self.assertFalse(events[0]["truncated"])
        self.assertTrue(self.sockets[0].closed)
        self.assertEqual(self.gateway.final_count, 0)
        self.publisher.publish_segment.assert_not_awaited()
        self.assertFalse(self.track.incomplete_reasons)

    async def test_two_rotations_partition_pcm_and_publish_bounded_native_shards(self):
        """One RTC reader, immutable Mistral selection and exact latest text survive."""
        selected = self.lease.live_profile
        old_leg = self.lease.leg_ref
        self.profile_snapshot["revision"] = 999
        self.profile_snapshot["profile_ref"] = "openai-live-fr-en-v1"
        pieces = ["a" * 3999, "😀e\u0301", " \n", "界" * 4100, " fin."]
        for piece in pieces:
            self.sockets[0].delta(piece)
            await asyncio.sleep(0)
        await self.feed([*(frame(n) for n in range(1, 6)), frame(6, 7)])
        await self.rotate()
        second_leg = self.lease.leg_ref
        self.sockets[1].delta("second leg")
        self.sockets[1].done_text = "second leg with a terminal suffix."
        await self.feed([frame(n) for n in range(7, 12)])
        await self.rotate()
        self.assertIs(self.lease.live_profile, selected)
        self.assertEqual(selected.revision, 7)
        self.assertEqual(selected.profile_ref, MISTRAL_SELECTION["profile_ref"])
        self.assertEqual([len(s.samples) for s in self.sockets], [2407, 2400, 0])
        self.assertEqual(
            self.sockets[0].samples + self.sockets[1].samples,
            [n for n in range(1, 6) for _ in range(480)]
            + [6] * 7
            + [n for n in range(7, 12) for _ in range(480)],
        )
        self.assertEqual(
            [body["audio_end_sample"] for body in self.commands("rotate")], [2407, 4807]
        )
        for leg, text, provider in (
            (old_leg, "".join(pieces), self.providers[0]),
            (second_leg, self.sockets[1].done_text, self.providers[1]),
        ):
            finals = self.finals(leg)
            latest = {event["item_ref"]: event for event in finals}
            self.assertEqual("".join(event["text"] for event in latest.values()), text)
            self.assertEqual(frozenset(latest), provider._final_refs)
            self.assertTrue(provider._terminal_complete)
            self.assertTrue(provider._socket.raw.closed)
            self.assertEqual(provider.terminal_metadata["text"], text)
            self.assertTrue(
                all(event["timing"] == {"state": "unavailable"} for event in finals)
            )
            for event in finals:
                self.assertLessEqual(len(event["text"].encode("utf-16-le")) // 2, 4000)
                same_item = [
                    entry["revision"]
                    for entry in finals
                    if entry["item_ref"] == event["item_ref"]
                ]
                self.assertEqual(same_item, sorted(set(same_item)))
        self.assertGreater(
            len({event["item_ref"] for event in self.finals(old_leg)}), 2
        )
        for provider, socket in zip(self.providers[:2], self.sockets[:2], strict=True):
            self.assertEqual(provider.appended_samples, len(socket.samples))
            self.assertEqual(
                [e["type"] for e in socket.sent].count("input_audio.end"), 1
            )
        self.assertFalse(self.track.incomplete_reasons)
        self.evidence = {
            "selection": asdict(selected),
            "rtc_readers": self.audio.readers,
            "pcm_samples_per_leg": [len(s.samples) for s in self.sockets],
            "rotations": self.commands("rotate"),
            "durable_finals": self.finals(),
            "native_done": [p.terminal_metadata for p in self.providers[:2]],
        }

    async def test_same_ref_last_revision_publication_must_join_before_rotation(self):
        """An already-published ref cannot bypass a later blocked final revision."""
        socket = self.sockets[0]
        socket.delta("first")
        await eventually(lambda: bool(self.track._published_items))
        already_published = set(self.track._published_items)
        entered = asyncio.Event()
        release = asyncio.Event()
        self.releases.append(release)

        async def publish(event):
            if event.data.text == "first suffix":
                entered.set()
                await release.wait()

        self.publisher.publish_segment.side_effect = publish
        socket.delta(" suffix")
        await entered.wait()
        await self.feed([frame(1), frame(2)])
        self.ticks.put_nowait(None)
        await socket.entered["close"].wait()
        await eventually(lambda: self.providers[0]._terminal_complete)
        self.assertTrue(already_published <= self.track._published_items)
        self.assertEqual(self.commands("rotate"), [])
        self.assertEqual(len(self.providers), 1)
        await self.feed([frame(3)])
        release.set()
        async with asyncio.timeout(2):
            await self.timers.get()
        await eventually(lambda: len(self.sockets[-1].samples) == FRAME_SAMPLES)
        self.assertEqual(self.commands("rotate")[0]["audio_end_sample"], 960)
        self.assertEqual(self.sockets[0].samples, [1] * 480 + [2] * 480)
        self.assertEqual(self.sockets[1].samples, [3] * 480)
        old_finals = self.finals(self.commands("rotate")[0]["leg_ref"])
        self.assertEqual({event["item_ref"] for event in old_finals}, already_published)
        self.assertEqual(old_finals[-1]["text"], "first suffix")

    async def test_physical_close_must_join_before_successor(self):
        """Done and published refs do not permit Core rotation during close."""
        socket = self.sockets[0]
        socket.delta("text")
        socket.release["close"].clear()
        await self.feed([frame(1)])
        self.ticks.put_nowait(None)
        await socket.entered["close"].wait()
        self.assertEqual(self.commands("rotate"), [])
        self.assertEqual(len(self.providers), 1)
        socket.release["close"].set()
        async with asyncio.timeout(2):
            await self.timers.get()
        self.assertEqual(len(self.providers), 2)

    async def test_revoked_blocked_append_never_flushes_or_opens_successor(self):
        """Hard stop keeps actual adapter egress fenced, including queued PCM."""
        socket = self.sockets[0]
        socket.release["input_audio.append"].clear()
        await self.feed([frame(n) for n in range(1, 9)])
        await socket.entered["input_audio.append"].wait()
        self.track.fence_audio()
        socket.release["input_audio.append"].set()
        async with asyncio.timeout(2):
            await asyncio.gather(self.track.aclose(), self.run, return_exceptions=True)
        self.assertEqual(socket.samples, [])
        self.assertEqual(self.providers[0].appended_samples, 0)
        self.assertNotIn("input_audio.end", [event["type"] for event in socket.sent])
        self.assertEqual(self.commands("rotate"), [])
        self.assertEqual(len(self.providers), 1)

    async def test_withdrawal_during_sealed_tail_prevents_end(self):
        """Planned drain cannot clear a fence to finish its blocked PCM tail."""
        socket = self.sockets[0]
        socket.release["input_audio.append"].clear()
        await self.feed([frame(1)])
        await socket.entered["input_audio.append"].wait()
        self.ticks.put_nowait(None)
        await eventually(lambda: self.providers[0]._expected_samples == FRAME_SAMPLES)
        self.track.fence_audio()
        socket.release["input_audio.append"].set()
        async with asyncio.timeout(2):
            await asyncio.gather(self.track.aclose(), self.run, return_exceptions=True)
        self.assertEqual(socket.samples, [])
        self.assertNotIn("input_audio.end", [event["type"] for event in socket.sent])
        self.assertEqual(self.commands("rotate"), [])

    async def test_incompatible_done_retains_finals_but_never_rotates(self):
        """No silent quality substitution or stale-text certification on correction."""
        socket = self.sockets[0]
        socket.delta("received provider-final text")
        await eventually(lambda: bool(self.track._published_items))
        socket.done_text = "corrected full text"
        self.ticks.put_nowait(None)
        async with asyncio.timeout(2):
            result = (await asyncio.gather(self.run, return_exceptions=True))[0]
        self.assertIsInstance(result, BaseExceptionGroup)
        self.assertEqual(self.commands("rotate"), [])
        self.assertEqual(len(self.providers), 1)
        self.assertTrue(self.track.incomplete_reasons)
        self.assertEqual(self.providers[0].terminal_metadata["text"], socket.done_text)
        self.assertEqual(self.finals()[-1]["text"], "received provider-final text")

    async def test_uncertain_durable_receipt_never_rotates(self):
        """A provider proof cannot replace the real durable receipt barrier."""
        self.gateway.responses["deliver"] = {"event_digest": "0" * 64}
        self.sockets[0].delta("received")
        async with asyncio.timeout(2):
            await asyncio.gather(self.run, return_exceptions=True)
        self.assertEqual(self.commands("rotate"), [])
        self.assertEqual(len(self.providers), 1)
        self.assertFalse(self.lease.can_send())
        self.assertTrue(self.track.incomplete_reasons)
