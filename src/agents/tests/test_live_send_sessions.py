"""Canonical admission/fencing through the production track and RTC callbacks."""

import asyncio
import json
import os
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from livekit import rtc
from livekit.agents import LanguageCode, stt
from test_live_send_control import Gateway, authority
from test_live_transcriber_stop import AudioSource, CloseGate, SpeechSource, reached

from live_send_control import ControlError, ControlResponseLost, ControlUnavailable
from live_transcriber import LiveTranscriber, TrackTranscriber


class WorkerRoom:
    """Expose actual room SID separately from its display name."""

    def __init__(self):
        """Start with an empty participant set and observable callback wiring."""
        self.on = MagicMock()
        self.off = MagicMock()
        self.remote_participants = {}
        self.local_participant = SimpleNamespace(send_text=AsyncMock())

    @property
    async def sid(self):
        """Match rtc.Room's asynchronous authoritative SID property."""
        return "RM_room"


class ControlledSessionTests(unittest.IsolatedAsyncioTestCase):
    """Prove no audio admission precedes a single successful initial claim."""

    def setUp(self):
        """Replace provider/audio boundaries, retaining production lifecycle code."""
        self.gateway = Gateway()
        self.lease = authority(self.gateway)
        self.audio = AudioSource()
        self.speech = SpeechSource()
        self.provider = CloseGate()
        self.provider.fence_audio = MagicMock()
        self.provider.appended_samples = 0
        self.provider.input_window = AsyncMock(return_value=None)
        self.provider.stream = MagicMock(return_value=self.speech)
        self.factory = self.enterContext(
            patch("live_provider_session.AuthorizedOpenAI", return_value=self.provider)
        )
        self.audio_factory = self.enterContext(
            patch(
                "live_transcriber.rtc.AudioStream.from_track", return_value=self.audio
            )
        )
        self.publisher = SimpleNamespace(
            publish_gap=AsyncMock(), publish_segment=AsyncMock()
        )
        self.track = TrackTranscriber(
            track=object(),
            participant=SimpleNamespace(identity="alice", sid="PA_participant"),
            track_sid="TR_track",
            publisher=self.publisher,
            vad=object(),
            authority=self.lease,
        )
        self.tasks = []
        self.enterContext(patch("live_send_control.RENEW_SECONDS", 0.01))

    async def asyncTearDown(self):
        """Release fixture gates before joining any cancelled local shutdown."""
        for gate in self.gateway.release.values():
            gate.set()
        for resource in (self.audio, self.speech, self.provider):
            resource.close_release.set()
        self.audio.stop_release.set()
        for task in self.tasks:
            if not task.done():
                task.cancel()
        async with asyncio.timeout(2):
            await asyncio.gather(*self.tasks, return_exceptions=True)

    def spawn(self, coroutine):
        """Retain every test coroutine through cleanup, including failed runs."""
        task = asyncio.create_task(coroutine)
        self.tasks.append(task)
        return task

    async def started(self):
        """Wait until a granted track has actually started reading audio."""
        run = self.spawn(self.track.run())
        await reached(self.audio.reading)
        await reached(self.speech.reading)
        return run

    async def test_delayed_webhook_retries_prepare_without_acquiring_audio(self):
        """Preparation may retry briefly; the same attempt claims exactly once."""
        original = self.gateway.post
        first = True
        waiting = asyncio.Event()
        release = asyncio.Event()

        async def post(body):
            nonlocal first
            if body["action"] == "prepare" and first:
                first = False
                self.gateway.requests.append(body.copy())
                waiting.set()
                raise ControlUnavailable("synthetic webhook delay")
            if body["action"] == "prepare":
                await release.wait()
            return await original(body)

        self.gateway.post = post
        with patch("live_send_control.PREPARE_RETRY_SECONDS", 0):
            run = self.spawn(self.track.run())
            await reached(waiting)
            self.factory.assert_not_called()
            self.audio_factory.assert_not_called()
            release.set()
            await reached(self.audio.reading)
            await self.track.aclose()
            await run
        preparations = [
            body for body in self.gateway.requests if body["action"] == "prepare"
        ]
        self.assertEqual(preparations[0], preparations[1])
        self.assertEqual(
            [body["action"] for body in self.gateway.requests].count("claim"), 1
        )
        self.factory.assert_called_once()

    async def test_preparation_exhaustion_has_zero_provider_and_no_stopped_ack(self):
        """No missing consent/webhook can authorize a provider by retry exhaustion."""
        self.gateway.errors["prepare"] = ControlUnavailable("synthetic unavailable")
        with patch("live_send_control.PREPARE_RETRY_SECONDS", 0):
            with self.assertRaises(ControlUnavailable):
                await self.track.run()
        self.factory.assert_not_called()
        self.audio_factory.assert_not_called()
        self.assertEqual(
            [body["action"] for body in self.gateway.requests], ["prepare"] * 3
        )
        self.assertFalse(self.gateway.entered["stopped"].is_set())

    async def test_renew_cancellation_finalizer_cannot_delay_audio_fence_or_close(self):
        """Hold cancelled renew cleanup while the independent watchdog closes SDK."""
        original_claim = self.lease.claim
        original_post = self.gateway.post
        cancelled = asyncio.Event()
        release = asyncio.Event()

        async def short_lease():
            granted = await original_claim()
            self.lease.deadline = time.monotonic() + 0.05
            return granted

        async def post(body):
            if body["action"] == "renew":
                self.gateway.entered["renew"].set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    cancelled.set()
                    await release.wait()
            return await original_post(body)

        self.lease.claim = short_lease
        self.gateway.post = post
        run = await self.started()
        try:
            await reached(self.gateway.entered["renew"])
            await reached(cancelled)
            await reached(self.provider.closed)
            self.assertFalse(self.lease.can_send())
            self.assertTrue(self.audio.stopped.is_set())
            self.assertEqual(self.speech.pushed, [])
            self.assertFalse(self.gateway.entered["stopped"].is_set())
        finally:
            release.set()
        await run
        self.assertTrue(self.gateway.entered["stopped"].is_set())
        self.assertFalse(self.lease.can_send())

    async def test_zero_resources_or_frames_before_claim_and_stop_during_claim(self):
        """Pending authority cannot acquire even an RTC AudioStream."""
        self.gateway.release["claim"].clear()
        run = self.spawn(self.track.run())
        await reached(self.gateway.entered["claim"])
        self.audio.frames.put_nowait(object())
        self.factory.assert_not_called()
        self.audio_factory.assert_not_called()
        self.assertEqual(self.speech.pushed, [])
        close = self.spawn(self.track.aclose())
        await asyncio.sleep(0)
        self.gateway.release["claim"].set()
        await asyncio.gather(run, close)
        self.factory.assert_not_called()
        self.audio_factory.assert_not_called()
        self.assertTrue(self.gateway.entered["stopped"].is_set())

    async def test_first_claim_is_the_only_opening_and_actual_frame_is_fenced_at_expiry(
        self,
    ):
        """The push boundary checks monotonic expiry even before watchdog runs."""
        await self.started()
        frame = rtc.AudioFrame.create(24000, 1, 480)
        self.audio.frames.put_nowait(frame)
        self.assertIs(await self.audio.delivered.get(), frame)
        self.assertEqual(self.speech.pushed, [frame])
        self.lease.deadline = time.monotonic() - 1
        self.audio.frames.put_nowait(object())
        async with asyncio.timeout(2):
            await asyncio.gather(self.track._audio_task, return_exceptions=True)
        self.assertEqual(self.speech.pushed, [frame])
        await self.track.aclose()
        self.factory.assert_called_once()

    async def test_expiry_fences_and_closes_while_renew_io_is_held(self):
        """A slow renew and its cancellation cannot delay the separate watchdog."""
        original = self.lease.claim

        async def short_lease():
            granted = await original()
            self.lease.deadline = time.monotonic() + 0.05
            return granted

        self.lease.claim = short_lease
        self.gateway.release["renew"].clear()
        run = await self.started()
        await reached(self.gateway.entered["renew"])
        await reached(self.speech.closed)
        self.assertTrue(self.track._closing)
        self.assertFalse(self.lease.can_send())
        self.assertEqual(self.speech.pushed, [])
        await run
        self.assertTrue(self.gateway.entered["stopped"].is_set())
        self.assertIn("live-send-authority-lost", self.track.incomplete_reasons)

    async def test_claim_replay_and_lost_claim_never_open(self):
        """Replay/ambiguity stops locally; no provider, audio stream or retry."""
        self.gateway.responses["claim"] = {"start_authorized": False, "replayed": True}
        await self.track.run()
        await self.track.run()
        self.factory.assert_not_called()
        self.audio_factory.assert_not_called()
        self.assertEqual(
            [body["action"] for body in self.gateway.requests],
            ["prepare", "claim", "stopped"],
        )

    async def test_lost_claim_has_no_second_prepare_or_provider(self):
        """A timed-out committed claim cannot be recovered by rerunning a track."""
        self.gateway.errors["claim"] = TimeoutError("synthetic lost response")
        with self.assertRaises(TimeoutError):
            await self.track.run()
        await self.track.run()
        self.factory.assert_not_called()
        self.assertEqual(
            [body["action"] for body in self.gateway.requests],
            ["prepare", "claim", "stopped"],
        )

    async def test_transport_lost_claim_can_ack_exact_owner_without_reclaim(self):
        """A lost HTTP response permits cleanup, without authorizing an opening."""
        self.gateway.errors["claim"] = ControlResponseLost("synthetic response lost")
        with self.assertRaises(ControlResponseLost):
            await self.track.run()
        await self.track.run()
        self.factory.assert_not_called()
        self.audio_factory.assert_not_called()
        self.assertFalse(await self.lease.claim())
        self.assertEqual(
            [body["action"] for body in self.gateway.requests],
            ["prepare", "claim", "stopped"],
        )
        claim = self.gateway.requests[1]
        stopped = self.gateway.requests[2]
        self.assertEqual(stopped, {**claim, "action": "stopped"})

    async def test_cancelled_claim_can_ack_exact_owner_without_reclaim(self):
        """Cancellation while waiting for claim leaves an ambiguous exact owner."""
        self.gateway.release["claim"].clear()
        run = self.spawn(self.track.run())
        await reached(self.gateway.entered["claim"])
        run.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await run
        self.factory.assert_not_called()
        self.audio_factory.assert_not_called()
        self.assertFalse(await self.lease.claim())
        self.assertEqual(
            [body["action"] for body in self.gateway.requests],
            ["prepare", "claim", "stopped"],
        )
        self.assertEqual(
            self.gateway.requests[2], {**self.gateway.requests[1], "action": "stopped"}
        )

    async def test_explicit_claim_http_refusal_never_acknowledges_stopped(self):
        """A temporary HTTP refusal is distinct from a transport-lost response."""
        self.gateway.errors["claim"] = ControlUnavailable("synthetic HTTP refusal")
        with self.assertRaises(ControlUnavailable):
            await self.track.run()
        self.factory.assert_not_called()
        self.audio_factory.assert_not_called()
        self.assertFalse(await self.lease.claim())
        self.assertEqual(
            [body["action"] for body in self.gateway.requests], ["prepare", "claim"]
        )

    async def test_wrong_claim_owner_never_opens_or_acknowledges_stopped(self):
        """A received claim for another owner cannot justify this worker's ACK."""
        self.gateway.responses["claim"] = {
            "worker_instance_ref": "different_worker_reference_001"
        }
        with self.assertRaises(ControlError):
            await self.track.run()
        await self.track.aclose()
        await self.track.run()
        self.factory.assert_not_called()
        self.audio_factory.assert_not_called()
        self.assertEqual(self.speech.pushed, [])
        self.assertEqual(
            [body["action"] for body in self.gateway.requests], ["prepare", "claim"]
        )
        self.assertFalse(self.lease.can_send())
        self.assertFalse(await self.lease.claim())

    async def test_incoherent_claim_receipts_never_open_or_acknowledge_stopped(self):
        """Exercise run-finally for malformed bindings, states, flags and leases."""
        invalid_receipts = [
            {"source_ref": "different_source_reference_001"},
            {"admission_ref": "different_admission_reference_001"},
            {"attempt_ref": "different_attempt_reference_001"},
            {"action": "renew"},
            {"version": True},
            {"unexpected": True},
            {"state": "unknown"},
            {"send_authorized": 1},
            {"start_authorized": 1},
            {"replayed": 0},
            {"replayed": True},
            {"state": "stop_requested"},
            {"lease_expires_at": True},
            {"lease_expires_at": int(time.time()) - 1},
        ]
        for receipt in invalid_receipts:
            with self.subTest(receipt=receipt):
                gateway = Gateway()
                gateway.responses["claim"] = receipt
                lease = authority(gateway)
                track = TrackTranscriber(
                    track=object(),
                    participant=self.track.participant,
                    track_sid="TR_track",
                    publisher=self.publisher,
                    vad=object(),
                    authority=lease,
                )
                with self.assertRaises(ControlError):
                    await track.run()
                await track.aclose()
                await track.run()
                self.factory.assert_not_called()
                self.audio_factory.assert_not_called()
                self.assertEqual(
                    [body["action"] for body in gateway.requests], ["prepare", "claim"]
                )
                self.assertFalse(lease.can_send())
                self.assertFalse(await lease.claim())

    async def test_malformed_claim_body_never_acknowledges_stopped(self):
        """A received body rejected by the HTTP parser is not a lost response."""
        self.gateway.errors["claim"] = ControlError("invalid control JSON")
        with self.assertRaises(ControlError):
            await self.track.run()
        self.factory.assert_not_called()
        self.audio_factory.assert_not_called()
        self.assertEqual(
            [body["action"] for body in self.gateway.requests], ["prepare", "claim"]
        )

    async def test_withdrawal_closes_and_joins_before_stopped_ack(self):
        """Core refusal fences audio before SDK close; ACK follows all local joins."""
        self.gateway.responses["renew"] = {
            "state": "stop_requested",
            "lease_expires_at": None,
            "start_authorized": False,
            "send_authorized": False,
        }
        self.speech.close_release.clear()
        self.audio.stop_release.clear()
        run = await self.started()
        await reached(self.speech.close_entered)
        await reached(self.audio.stopping)
        self.assertFalse(self.gateway.entered["stopped"].is_set())
        self.assertTrue(self.track._closing)
        self.audio.frames.put_nowait(object())
        self.speech.close_release.set()
        await reached(self.provider.closed)
        self.assertFalse(self.gateway.entered["stopped"].is_set())

        def assert_local_stop():
            self.assertTrue(self.audio.stopped.is_set())
            for resource in (self.speech, self.audio, self.provider):
                self.assertTrue(resource.closed.is_set())

        self.gateway.before_stopped = assert_local_stop
        self.audio.stop_release.set()
        await run
        self.assertEqual(self.speech.pushed, [])
        self.assertTrue(self.gateway.entered["stopped"].is_set())

    async def test_control_transport_loss_fences_and_ack_follows_local_stop(self):
        """An unreachable control endpoint closes rather than refreshing authority."""
        self.gateway.errors["renew"] = TimeoutError("synthetic timeout")
        run = await self.started()
        await run
        self.assertTrue(self.provider.closed.is_set())
        self.assertTrue(self.gateway.entered["stopped"].is_set())
        self.assertIn("live-send-authority-lost", self.track.incomplete_reasons)

    async def test_close_error_never_acknowledges_stopped(self):
        """Retain local close failures without certifying a stopped source."""
        self.speech.close_error = RuntimeError("synthetic SDK close failure")
        self.gateway.responses["renew"] = {
            "state": "stop_requested",
            "lease_expires_at": None,
            "start_authorized": False,
            "send_authorized": False,
        }
        run = await self.started()
        with self.assertRaises(BaseExceptionGroup):
            await run
        self.assertFalse(self.gateway.entered["stopped"].is_set())
        with self.assertRaises(BaseExceptionGroup):
            await self.track.aclose()
        self.assertFalse(self.gateway.entered["stopped"].is_set())

    async def test_accepted_publication_blocks_ack_but_does_not_delay_audio_fence(self):
        """Keep the existing PR216 delivery barrier under authority withdrawal."""
        publishing = asyncio.Event()
        release = asyncio.Event()

        async def publish(event):
            publishing.set()
            await release.wait()

        self.publisher.publish_segment.side_effect = publish
        self.gateway.release["renew"].clear()
        self.gateway.responses["renew"] = {
            "state": "stop_requested",
            "lease_expires_at": None,
            "start_authorized": False,
            "send_authorized": False,
        }
        run = await self.started()
        self.speech.events.put_nowait(
            stt.SpeechEvent(
                type=stt.SpeechEventType.FINAL_TRANSCRIPT,
                request_id="synthetic-item",
                alternatives=[
                    stt.SpeechData(language=LanguageCode("fr"), text="fixture")
                ],
            )
        )
        await reached(publishing)
        self.gateway.release["renew"].set()
        await reached(self.speech.closed)
        self.assertFalse(self.gateway.entered["stopped"].is_set())
        release.set()
        await run
        self.assertTrue(self.gateway.entered["stopped"].is_set())

    async def test_only_rtc_microphone_and_no_fresh_open_on_reconnect(self):
        """Actual SID tuples survive callback duplicates and room reconnection."""
        url = "http://127.0.0.1:8000/internal/control"
        room = WorkerRoom()
        ctx = SimpleNamespace(
            room=room,
            proc=SimpleNamespace(userdata={"vad": object()}),
            job=SimpleNamespace(
                metadata=json.dumps(
                    {
                        "liveControl": {
                            "version": 2,
                            "url": url,
                            "capability": "payload:timestamp:signature",
                        }
                    }
                )
            ),
        )
        with patch.dict(os.environ, {"LIVE_TRANSCRIPT_CONTROL_URL": url}):
            manager = LiveTranscriber(ctx)
        manager._control = self.gateway
        manager.start()
        self.addAsyncCleanup(manager.aclose)
        participant = SimpleNamespace(identity="display-name", sid="PA_participant")
        microphone = SimpleNamespace(
            sid="TR_track", source=rtc.TrackSource.SOURCE_MICROPHONE, subscribed=True
        )
        screen = SimpleNamespace(
            sid="TR_screen", source=rtc.TrackSource.SOURCE_SCREENSHARE_AUDIO
        )
        track = SimpleNamespace(kind=rtc.TrackKind.KIND_AUDIO)
        video = SimpleNamespace(kind=rtc.TrackKind.KIND_VIDEO)
        manager.on_track_subscribed(track, screen, participant)
        manager.on_track_subscribed(video, microphone, participant)
        self.assertEqual(manager._tasks, set())
        manager.on_track_subscribed(track, microphone, participant)
        await reached(self.audio.reading)
        manager.on_track_subscribed(track, microphone, participant)
        await asyncio.gather(*list(manager._tasks))
        self.assertFalse(self.speech.closed.is_set())
        microphone.track = track
        participant.track_publications = {"TR_track": microphone}
        room.remote_participants = {participant.identity: participant}
        manager.on_reconnecting()
        self.assertTrue(next(iter(manager._sessions.values()))._closing)
        await asyncio.gather(*list(manager._tasks))
        manager.on_reconnected()
        await asyncio.gather(*list(manager._tasks))
        self.factory.assert_called_once()
        self.assertEqual(len(manager._epochs), 1)
        self.assertEqual(
            [body["action"] for body in self.gateway.requests].count("claim"), 1
        )
        for body in self.gateway.requests:
            self.assertEqual(body["participant_sid"], "PA_participant")
            self.assertEqual(body["room_sid"], "RM_room")
