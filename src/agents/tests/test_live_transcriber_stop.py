"""Deterministic local shutdown barriers, including the installed realtime SDK."""

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from livekit import rtc
from livekit.agents import LanguageCode, stt
from livekit.plugins import openai
from openai import AsyncOpenAI

from live_transcriber import MODEL, LiveTranscriber, TrackTranscriber


async def reached(event):
    """Bound a synchronization wait so a regression fails instead of hanging."""
    async with asyncio.timeout(2):
        await event.wait()


class CloseGate:
    """Expose entry, release and completion of one SDK-shaped close call."""

    def __init__(self):
        """Close immediately unless a test holds or fails this resource."""
        self.close_entered = asyncio.Event()
        self.close_release = asyncio.Event()
        self.close_release.set()
        self.closed = asyncio.Event()
        self.close_calls = 0
        self.close_error = None

    async def aclose(self):
        """Block at an observable point before completing or failing closure."""
        self.close_calls += 1
        self.close_entered.set()
        await self.close_release.wait()
        if self.close_error is not None:
            raise self.close_error
        self.closed.set()


class AudioSource(CloseGate):
    """Deliver frames and make audio-pump cancellation observable."""

    def __init__(self):
        """Start with an idle, cancellable frame source."""
        super().__init__()
        self.frames = asyncio.Queue()
        self.delivered = asyncio.Queue()
        self.reading = asyncio.Event()
        self.stopping = asyncio.Event()
        self.stop_release = asyncio.Event()
        self.stop_release.set()
        self.stopped = asyncio.Event()

    def __aiter__(self):
        """Return this controlled audio iterator."""
        return self

    async def __anext__(self):
        """Yield one supplied frame or acknowledge cancellation after release."""
        self.reading.set()
        try:
            frame = await self.frames.get()
        except asyncio.CancelledError:
            self.stopping.set()
            await self.stop_release.wait()
            self.stopped.set()
            raise
        self.delivered.put_nowait(frame)
        return SimpleNamespace(frame=frame)


class SpeechSource(CloseGate):
    """Emit supplied transcripts until local stream closure ends iteration."""

    def __init__(self):
        """Start with no speech events or pushed audio."""
        super().__init__()
        self.events = asyncio.Queue()
        self.reading = asyncio.Event()
        self.pushed = []

    def __aiter__(self):
        """Return this controlled speech iterator."""
        return self

    async def __anext__(self):
        """Wait for a transcript or a synthetic end of stream."""
        self.reading.set()
        event = await self.events.get()
        if event is None:
            raise StopAsyncIteration
        return event

    def push_frame(self, frame):
        """Record frames admitted through the production audio pump."""
        self.pushed.append(frame)

    async def aclose(self):
        """Finish iteration only once SDK closure has completed."""
        await super().aclose()
        self.events.put_nowait(None)


class BlockedSocket:
    """Hold the real plugin's first audio send; never open a network socket."""

    def __init__(self):
        """Expose cancellation of the real SDK sender and receiver."""
        self.sending = asyncio.Event()
        self.sender_stopping = asyncio.Event()
        self.sender_release = asyncio.Event()
        self.sender_stopped = asyncio.Event()
        self.receiver_started = asyncio.Event()
        self.receiver_stopped = asyncio.Event()
        self.audio_attempts = []
        self.message_types = []
        self.socket_closed = asyncio.Event()
        self.close = AsyncMock()
        self.close.side_effect = self.socket_closed.set
        self.close_code = 1000

    async def send_json(self, message):
        """Allow session setup but hold audio until the SDK cancels the send."""
        self.message_types.append(message["type"])
        if message["type"] != "input_audio_buffer.append":
            return
        self.audio_attempts.append(message)
        self.sending.set()
        try:
            await asyncio.Event().wait()
        finally:
            self.sender_stopping.set()
            await self.sender_release.wait()
            self.sender_stopped.set()

    async def receive(self):
        """Keep the real receive loop idle until shutdown cancels it."""
        self.receiver_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            self.receiver_stopped.set()


class LiveTranscriptionStopTests(unittest.IsolatedAsyncioTestCase):
    """Exercise shutdown interleavings without clocks, sleeps or providers."""

    def setUp(self):
        """Create one track and retain every spawned test task for cleanup."""
        self.audio = AudioSource()
        self.speech = SpeechSource()
        self.provider = CloseGate()
        self.provider.stream = MagicMock(return_value=self.speech)
        self.publisher = SimpleNamespace(
            publish_segment=AsyncMock(), publish_gap=AsyncMock()
        )
        self.track = TrackTranscriber(
            track=object(),
            participant=SimpleNamespace(identity="alice"),
            track_sid="TR_A",
            publisher=self.publisher,
            vad=object(),
        )
        self.tasks = []
        self.releases = []
        self.enterContext(
            patch("live_transcriber.create_openai_live_stt", return_value=self.provider)
        )
        self.enterContext(
            patch(
                "live_transcriber.rtc.AudioStream.from_track", return_value=self.audio
            )
        )

    async def asyncTearDown(self):
        """Release gates and join tasks even when a barrier assertion fails."""
        self.audio.stop_release.set()
        for event in self.releases:
            event.set()
        for resource in (self.audio, self.speech, self.provider):
            resource.close_release.set()
        for task in self.tasks:
            if not task.done():
                task.cancel()
        async with asyncio.timeout(2):
            await asyncio.gather(*self.tasks, return_exceptions=True)

    def spawn(self, coroutine):
        """Retain a task so a red test cannot leak it into later tests."""
        task = asyncio.create_task(coroutine)
        self.tasks.append(task)
        return task

    async def start_track(self):
        """Run until both production event and audio pumps are waiting."""
        task = self.spawn(self.track.run())
        await reached(self.speech.reading)
        await reached(self.audio.reading)
        return task

    async def concurrent_close(self, close):
        """Enter another close call before checking whether it returned early."""
        entered = asyncio.Event()

        async def call():
            entered.set()
            await close()

        task = self.spawn(call())
        await reached(entered)
        return task

    async def test_concurrent_track_closes_wait_for_every_resource(self):
        """Every caller waits while any resource close remains in progress."""
        self.track._speech_stream = self.speech
        self.track._audio_stream = self.audio
        self.track._provider = self.provider
        self.speech.close_release.clear()
        self.provider.close_release.clear()
        first = self.spawn(self.track.aclose())
        await reached(self.speech.close_entered)
        second = await self.concurrent_close(self.track.aclose)
        self.assertFalse(second.done())
        self.speech.close_release.set()
        await reached(self.provider.close_entered)
        self.assertFalse(first.done())
        self.assertFalse(second.done())
        self.provider.close_release.set()
        await asyncio.gather(first, second)
        await self.track.aclose()
        for resource in (self.speech, self.audio, self.provider):
            self.assertTrue(resource.closed.is_set())
            self.assertEqual(resource.close_calls, 1)

    async def test_frame_arriving_after_stop_is_not_pushed(self):
        """A frame unblocked during SDK closure cannot enter its input queue."""
        self.track._speech_stream = self.speech
        self.speech.close_release.clear()
        pump = self.spawn(self.track._push_audio(self.audio, self.speech))
        await reached(self.audio.reading)
        close = self.spawn(self.track.aclose())
        await reached(self.speech.close_entered)
        self.audio.frames.put_nowait(object())
        async with asyncio.timeout(2):
            await pump
        self.assertEqual(self.speech.pushed, [])
        self.speech.close_release.set()
        await close

    async def test_stop_before_run_does_not_acquire_resources_or_read_audio(self):
        """A stopped track cannot start either acquisition or a new audio pump."""
        await self.track.aclose()
        await self.track.run()
        self.audio.frames.put_nowait(object())
        pump = self.spawn(self.track._push_audio(self.audio, self.speech))
        entered = await self.concurrent_close(self.track.aclose)
        self.assertTrue(pump.done())
        await entered
        self.assertFalse(self.audio.reading.is_set())
        self.provider.stream.assert_not_called()
        self.assertEqual(self.speech.pushed, [])

    async def test_stop_waits_for_audio_pump_cancellation(self):
        """Closing SDK resources cannot substitute for joining the audio task."""
        run = await self.start_track()
        self.audio.stop_release.clear()
        close = self.spawn(self.track.aclose())
        await reached(self.audio.stopping)
        await reached(self.speech.closed)
        await reached(self.provider.closed)
        another = await self.concurrent_close(self.track.aclose)
        self.assertFalse(close.done())
        self.assertFalse(another.done())
        self.audio.stop_release.set()
        await asyncio.gather(run, close, another)
        self.assertTrue(self.audio.stopped.is_set())

    async def test_stop_preserves_and_joins_an_inflight_final_publication(self):
        """An already produced final finishes before the stop barrier returns."""
        publishing = asyncio.Event()
        publish_release = asyncio.Event()

        async def publish(event):
            publishing.set()
            await publish_release.wait()

        self.publisher.publish_segment.side_effect = publish
        self.releases.append(publish_release)
        run = await self.start_track()
        self.speech.events.put_nowait(
            stt.SpeechEvent(
                type=stt.SpeechEventType.FINAL_TRANSCRIPT,
                request_id="final-a",
                alternatives=[
                    stt.SpeechData(language=LanguageCode("fr"), text="bonjour")
                ],
            )
        )
        await reached(publishing)
        close = self.spawn(self.track.aclose())
        await reached(self.provider.closed)
        another = await self.concurrent_close(self.track.aclose)
        self.assertFalse(close.done())
        self.assertFalse(another.done())
        publish_release.set()
        await asyncio.gather(run, close, another)
        event = self.publisher.publish_segment.call_args.args[0]
        self.assertTrue(event.final)
        self.assertEqual(event.data.text, "bonjour")

    async def test_natural_run_end_fences_audio_and_joins_resources(self):
        """The run-finally path establishes the same barrier as explicit stop."""
        run = await self.start_track()
        self.speech.close_release.clear()
        self.speech.events.put_nowait(None)
        await reached(self.speech.close_entered)
        self.audio.frames.put_nowait(object())
        late_pump = self.spawn(self.track._push_audio(self.audio, self.speech))
        close = await self.concurrent_close(self.track.aclose)
        self.assertTrue(late_pump.done())
        self.assertEqual(self.speech.pushed, [])
        self.assertFalse(run.done())
        self.assertFalse(close.done())
        self.speech.close_release.set()
        await asyncio.gather(run, close)
        self.assertTrue(self.provider.closed.is_set())

    async def test_cancelled_close_cannot_interrupt_shared_cleanup(self):
        """Cancelling one waiter leaves other callers waiting for real closure."""
        self.track._speech_stream = self.speech
        self.track._audio_stream = self.audio
        self.track._provider = self.provider
        self.speech.close_release.clear()
        first = self.spawn(self.track.aclose())
        await reached(self.speech.close_entered)
        first.cancel()
        second = await self.concurrent_close(self.track.aclose)
        first.cancel()
        self.assertFalse(second.done())
        self.speech.close_release.set()
        results = await asyncio.gather(first, second, return_exceptions=True)
        self.assertIsInstance(results[0], asyncio.CancelledError)
        self.assertIsNone(results[1])
        self.assertTrue(self.provider.closed.is_set())
        self.assertEqual(self.speech.close_calls, 1)

    async def test_repeated_run_cancellation_waits_for_resource_closure(self):
        """Cancellation during finally cannot tear down the shared close task."""
        run = await self.start_track()
        self.speech.close_release.clear()
        run.cancel()
        await reached(self.speech.close_entered)
        run.cancel()
        close = await self.concurrent_close(self.track.aclose)
        self.assertFalse(run.done())
        self.assertFalse(close.done())
        self.speech.close_release.set()
        results = await asyncio.gather(run, close, return_exceptions=True)
        self.assertIsInstance(results[0], asyncio.CancelledError)
        self.assertIsNone(results[1])
        self.assertTrue(self.audio.closed.is_set())
        self.assertTrue(self.provider.closed.is_set())

    async def test_failed_or_cancelled_sdk_close_is_retained_for_all_callers(self):
        """Failed closure never becomes success and other resources still close."""
        for error in (RuntimeError("SDK close failed"), asyncio.CancelledError()):
            with self.subTest(error=type(error).__name__):
                track = TrackTranscriber(
                    track=object(),
                    participant=SimpleNamespace(identity="alice"),
                    track_sid="TR_failure",
                    publisher=self.publisher,
                    vad=object(),
                )
                speech, audio, provider = CloseGate(), CloseGate(), CloseGate()
                speech.close_error = error
                track._speech_stream = speech
                track._audio_stream = audio
                track._provider = provider
                with self.assertLogs("transcriber.live", level="ERROR"):
                    for _ in range(2):
                        with self.assertRaises(BaseExceptionGroup):
                            await track.aclose()
                self.assertTrue(audio.closed.is_set())
                self.assertTrue(provider.closed.is_set())
                self.assertEqual(speech.close_calls, 1)

    def manager(self):
        """Build the real lifecycle manager with an entirely local room."""
        room = SimpleNamespace(
            local_participant=SimpleNamespace(send_text=AsyncMock()),
            off=MagicMock(),
            remote_participants={},
        )
        ctx = SimpleNamespace(
            room=room, proc=SimpleNamespace(userdata={"vad": object()})
        )
        return LiveTranscriber(ctx)

    async def test_concurrent_manager_closes_wait_for_track_barrier(self):
        """The worker-level wrapper must not bypass a pending per-track stop."""
        manager = self.manager()
        manager._sessions[("alice", "TR_A")] = self.track
        self.track._speech_stream = self.speech
        self.speech.close_release.clear()
        first = self.spawn(manager.aclose())
        await reached(self.speech.close_entered)
        second = await self.concurrent_close(manager.aclose)
        self.assertFalse(second.done())
        self.speech.close_release.set()
        await asyncio.gather(first, second)
        self.assertEqual(manager.ctx.room.off.call_count, 6)

    async def test_failed_stop_prevents_track_replacement_and_manager_success(self):
        """Retain a failed stop so a later subscribe cannot reopen that track."""
        manager = self.manager()
        key = ("alice", "TR_A")
        manager._sessions[key] = self.track
        run = self.spawn(manager._run_session(key, self.track))
        manager._session_tasks[key] = run
        await reached(self.audio.reading)
        self.speech.close_error = RuntimeError("SDK close failed")
        with self.assertLogs("transcriber.live", level="ERROR"):
            with self.assertRaises(ExceptionGroup):
                await manager._stop_session(key)
            self.assertIs(manager._sessions[key], self.track)
            with patch("live_transcriber.TrackTranscriber") as replacement:
                with self.assertRaises(ExceptionGroup):
                    await manager._ensure_session(
                        object(), "TR_A", SimpleNamespace(identity="alice")
                    )
                replacement.assert_not_called()
            for _ in range(2):
                with self.assertRaises(ExceptionGroup):
                    await manager.aclose()

    async def test_failed_sdk_close_cancels_reader_and_drains_accepted_finals(self):
        """An SDK iterator left open on failure cannot hold the stop forever."""
        publishing, release = asyncio.Event(), asyncio.Event()
        self.releases.append(release)
        published = []

        async def publish(event):
            if event.item_id == "a":
                publishing.set()
                await release.wait()
            published.append(event.item_id)

        self.publisher.publish_segment.side_effect = publish
        for item_id in ("a", "b"):
            self.speech.events.put_nowait(
                stt.SpeechEvent(
                    type=stt.SpeechEventType.FINAL_TRANSCRIPT,
                    request_id=item_id,
                    alternatives=[stt.SpeechData(language="fr", text=item_id)],
                )
            )
        run = await self.start_track()
        await reached(publishing)
        self.speech.close_error = RuntimeError("private SDK failure")
        with self.assertLogs("transcriber.live", level="WARNING") as logs:
            first = self.spawn(self.track.aclose())
            await reached(self.provider.closed)
            second = await self.concurrent_close(self.track.aclose)
            self.assertFalse(first.done())
            self.assertFalse(second.done())
            release.set()
            async with asyncio.timeout(2):
                results = await asyncio.gather(
                    run, first, second, return_exceptions=True
                )
        for result in results:
            self.assertIsInstance(result, ExceptionGroup)
        self.assertIs(results[1], results[2])
        self.assertEqual(published, ["a", "b"])
        self.assertTrue(self.track._reader_task.cancelled())
        self.assertTrue(self.track._publication_task.done())
        self.provider.stream.assert_called_once()
        self.publisher.publish_gap.assert_any_await("sdk-close-failed")
        self.assertNotIn("private SDK failure", "\n".join(logs.output))

    async def test_real_sdk_cancels_sender_with_buffered_audio_before_return(self):
        """Execute SDK 1.8.3 closure, with audio queued behind a blocked send."""
        socket = BlockedSocket()
        self.releases.append(socket.sender_release)
        client = await self.enterAsyncContext(AsyncOpenAI(api_key="test-key"))
        connector = AsyncMock(return_value=socket)
        # Test-only connection boundary: production uses public SDK close methods.
        with patch.object(openai.STT, "_connect_ws", connector):
            provider = openai.STT(
                client=client, model=MODEL, use_realtime=True, vad=None
            )
        self.addAsyncCleanup(provider.aclose)
        with patch("live_transcriber.create_openai_live_stt", return_value=provider):
            run = self.spawn(self.track.run())
            await reached(socket.receiver_started)
            first_frame = rtc.AudioFrame.create(24000, 1, 1200)
            self.audio.frames.put_nowait(first_frame)
            await reached(socket.sending)
            self.audio.frames.put_nowait(rtc.AudioFrame.create(24000, 1, 1200))
            async with asyncio.timeout(2):
                await self.audio.delivered.get()
                await self.audio.delivered.get()
            stream = self.track._speech_stream
            self.audio.stop_release.clear()
            close = self.spawn(self.track.aclose())
            await reached(socket.sender_stopping)
            another = await self.concurrent_close(self.track.aclose)
            self.assertFalse(close.done())
            self.assertFalse(another.done())
            socket.sender_release.set()
            await reached(socket.socket_closed)
            self.assertFalse(close.done())
            self.assertFalse(another.done())
            self.audio.stop_release.set()
            await asyncio.gather(run, close, another)
        self.assertTrue(socket.sender_stopped.is_set())
        self.assertTrue(socket.receiver_stopped.is_set())
        self.assertTrue(stream._task.done())
        self.assertTrue(stream._metrics_task.done())
        socket.close.assert_awaited_once()
        connector.assert_awaited_once()
        self.assertEqual(len(socket.audio_attempts), 1)
        self.assertNotIn("input_audio_buffer.commit", socket.message_types)
