"""Offline protocol, retention, authority and terminal proofs for Mistral live."""

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import aiohttp
from live_mistral_fixtures import (
    MistralSocket,
    done_event,
    eventually,
    frame,
    session_event,
    session_for,
)
from livekit import rtc

from live_delivery_codec import event_bytes
from live_input_drain import InputDrainError
from live_mistral import MODEL, REALTIME_URL, AuthorizedMistral
from live_provider_session import application_item_ref


class MistralTransportTests(unittest.IsolatedAsyncioTestCase):
    """Exercise real transport tasks and the pinned SDK public iterator."""

    async def asyncSetUp(self):
        """Own all synthetic sockets, providers and consumers through cleanup."""
        self.authorized = True
        self.socket = MistralSocket()
        self.session = session_for(self.socket)
        self.failed = Mock()
        self.reconnect = Mock()
        self.provider = self.make_provider()
        self.stream = None
        self.events = []
        self.reader = None
        self.tasks = []

    def make_provider(self, **overrides):
        """Use literal fixture credentials; never inspect process secrets."""
        args = {
            "can_send": lambda: self.authorized,
            "reconnect_refused": self.reconnect,
            "failed": self.failed,
            "api_key": "synthetic-unused-key",
            "http_session": self.session,
            "model": MODEL,
            "language": None,
            "sample_rate": 24000,
            "target_streaming_delay_ms": 480,
        }
        args.update(overrides)
        return AuthorizedMistral(**args)

    def spawn(self, coroutine):
        """Track every auxiliary waiter to prevent leaked test tasks."""
        task = asyncio.create_task(coroutine)
        self.tasks.append(task)
        return task

    async def start(self, *, configured=True, collect=True):
        """Acquire exactly one stream, optionally stall the public consumer."""
        self.stream = self.provider.stream()
        if collect:

            async def read():
                async for event in self.stream:
                    self.events.append(event)

            self.reader = self.spawn(read())
        await eventually(lambda: self.provider._socket is not None)
        if configured:
            await self.socket.entered["session.update"].wait()
            await asyncio.sleep(0)

    async def finish(self, samples=0):
        """Read finals through natural iterator exhaustion after the close join."""
        self.provider.seal_input(self.stream, samples)
        async with asyncio.timeout(2):
            refs = await self.provider.drain_input()
            if self.reader is not None:
                await self.reader
        return refs

    async def asyncTearDown(self):
        """Release physical cleanup gates and join every task even after failure."""
        for gate in self.socket.release.values():
            gate.set()
        async with asyncio.timeout(2):
            result = (
                await asyncio.gather(self.provider.aclose(), return_exceptions=True)
            )[0]
            if isinstance(result, BaseException):
                self.assertIsInstance(result, TimeoutError)
                self.assertIsNotNone(self.provider._error)
            for task in self.tasks:
                task.cancel()
            await asyncio.gather(*self.tasks, return_exceptions=True)

    def latest(self):
        """Apply the actual consumer's replacement key, preserving first order."""
        return {application_item_ref(event): event for event in self.events}

    async def reader_error(self):
        """Require terminal failure without reconnect or successful drain proof."""
        async with asyncio.timeout(2):
            result = (await asyncio.gather(self.reader, return_exceptions=True))[0]
        self.assertIsInstance(result, InputDrainError)
        self.assertFalse(self.provider._terminal_complete)
        self.session.ws_connect.assert_awaited_once()
        self.failed.assert_called_once()
        return str(result)

    async def test_handshake_pcm_tail_once_and_exact_native_done(self):
        """Verify fixed URL/profile, successful local samples and final equality."""
        await self.start()
        self.socket.delta(" Bonjour")
        self.socket.delta(" le monde. ")
        for number, samples in ((1, 480), (2, 480), (3, 7)):
            self.stream.push_frame(frame(number, samples))
        refs = await self.finish(967)
        self.assertEqual(refs, frozenset(self.latest()))
        self.assertEqual(
            "".join(e.alternatives[0].text for e in self.latest().values()),
            " Bonjour le monde. ",
        )
        self.assertEqual(self.socket.samples, [1] * 480 + [2] * 480 + [3] * 7)
        self.assertEqual(self.socket.chunks, [480, 480, 7])
        self.assertEqual(self.provider.appended_samples, 967)
        self.assertTrue(self.provider.terminal_drain_complete(self.stream))
        self.assertIsNone(await self.provider.input_window(next(iter(refs))))
        call = self.session.ws_connect.await_args
        self.assertEqual(call.args, (REALTIME_URL,))
        self.assertFalse(call.kwargs["autoping"])
        self.assertFalse(call.kwargs["autoclose"])
        self.assertIsNone(call.kwargs["heartbeat"])
        update = self.socket.sent[0]
        self.assertEqual(
            update,
            {
                "type": "session.update",
                "session": {
                    "audio_format": {"encoding": "pcm_s16le", "sample_rate": 24000},
                    "target_streaming_delay_ms": 480,
                },
            },
        )
        self.assertEqual(
            [e["type"] for e in self.socket.sent].count("input_audio.end"), 1
        )
        self.assertEqual(self.provider.terminal_metadata["text"], self.socket.text)
        copy = self.provider.terminal_metadata
        copy["text"] = "mutated"
        self.assertEqual(self.provider.terminal_metadata["text"], self.socket.text)
        self.failed.assert_not_called()
        self.session.close.assert_not_awaited()

    async def test_long_unicode_shards_preserve_whitespace_and_no_double_done(self):
        """UTF-16 boundaries preserve astral/decomposed text and whitespace deltas."""
        await self.start()
        pieces = ["a" * 3999, " ", " ", "😀e\u0301!", "🦀" * 2001, " fin\n"]
        for piece in pieces:
            self.socket.delta(piece)
            await asyncio.sleep(0)
        refs = await self.finish()
        latest = self.latest()
        self.assertGreater(len(refs), 2)
        self.assertEqual(refs, frozenset(latest))
        self.assertEqual(
            "".join(e.alternatives[0].text for e in latest.values()), "".join(pieces)
        )
        for sequence, (ref, event) in enumerate(latest.items(), 1):
            data = event.alternatives[0]
            self.assertLessEqual(len(data.text.encode("utf-16-le")) // 2, 4000)
            wire = event_bytes(
                {
                    "version": 2,
                    "source_ref": "source_fixture_001",
                    "leg_ref": "leg_fixture_0001",
                    "sequence": sequence,
                    "kind": "final",
                    "item_ref": ref,
                    "revision": 1,
                    "text": data.text,
                    "language": "fr",
                    "timing": {"state": "unavailable"},
                }
            )
            self.assertLessEqual(len(wire), 32 * 1024)
            self.assertEqual(event.request_id, self.socket.request_id)
        self.assertEqual(self.provider.terminal_metadata["text"], "".join(pieces))
        before = len(self.events)
        self.assertEqual(await self.provider.drain_input(), refs)
        self.assertEqual(len(self.events), before)

    async def test_unrepresentable_trailing_whitespace_is_explicitly_incomplete(self):
        """Never shrink published shards through a non-atomic text migration."""
        await self.start()
        self.socket.delta("x" * 4000)
        await eventually(lambda: len(self.events) == 1)
        self.socket.delta(" \n")
        self.provider.seal_input(self.stream, 0)
        with self.assertRaises(InputDrainError):
            await self.provider.drain_input()
        self.assertEqual(
            await self.reader_error(), "mistral-whitespace-item-unrepresentable"
        )
        self.assertEqual(
            [e.alternatives[0].text for e in self.latest().values()], ["x" * 4000]
        )
        self.assertEqual(self.provider.terminal_metadata["text"], "x" * 4000 + " \n")

    async def test_done_suffix_and_done_only_use_existing_shard_rules(self):
        """An authoritative suffix is appended exactly once, without normalization."""
        await self.start()
        self.socket.delta("hello")
        self.socket.done_text = "hello" + "界" * 4001
        await self.finish()
        self.assertEqual(
            "".join(e.alternatives[0].text for e in self.latest().values()),
            self.socket.done_text,
        )
        self.assertEqual(len(self.latest()), 2)

    async def test_done_only_result_is_not_lost(self):
        """Full text without prior deltas still becomes bounded canonical shards."""
        await self.start()
        self.socket.done_text = "only terminal " * 700
        await self.finish()
        self.assertEqual(
            "".join(e.alternatives[0].text for e in self.latest().values()),
            self.socket.done_text,
        )

    async def test_correction_or_retraction_is_explicitly_incomplete(self):
        """Current Core cannot delete old shard sets; never falsely certify them."""
        await self.start()
        self.socket.delta("original final text")
        await eventually(lambda: bool(self.events))
        self.socket.done_text = "corrected text"
        self.provider.seal_input(self.stream, 0)
        with self.assertRaises(InputDrainError):
            await self.provider.drain_input()
        self.assertEqual(await self.reader_error(), "mistral-done-text-incompatible")
        self.assertEqual(self.provider.terminal_metadata["text"], "corrected text")
        self.assertEqual(
            [e.alternatives[0].text for e in self.latest().values()],
            ["original final text"],
        )

    async def test_empty_done_cannot_retract_a_received_final(self):
        """Empty done after text cannot manufacture a successful empty leg."""
        await self.start()
        self.socket.delta("retained")
        self.socket.done_text = ""
        self.provider.seal_input(self.stream, 0)
        with self.assertRaises(InputDrainError):
            await self.provider.drain_input()
        self.assertEqual(await self.reader_error(), "mistral-done-text-incompatible")

    async def test_empty_audio_with_empty_done_returns_no_refs(self):
        """An empty terminal result still requires EOF, normal close and join."""
        await self.start()
        self.assertEqual(await self.finish(), frozenset())
        self.assertEqual(self.events, [])
        self.assertEqual(self.provider.terminal_metadata["text"], "")

    async def test_no_pcm_before_created_and_verified_updated(self):
        """Queueing input never bypasses either protocol handshake barrier."""
        self.socket.messages.get_nowait()
        self.socket.auto_update = False
        await self.start(configured=False)
        self.stream.push_frame(frame(1))
        await asyncio.sleep(0)
        self.assertEqual(self.socket.sent, [])
        self.socket.feed(session_event("session.created"))
        await self.socket.entered["session.update"].wait()
        await asyncio.sleep(0)
        self.assertEqual(self.socket.samples, [])
        self.socket.feed(self.socket.updated)
        await self.finish(480)
        self.assertEqual(self.socket.samples, [1] * 480)

    async def test_configuration_refusal_fences_without_audio_or_end(self):
        """Reject a pinned language or wrong applied rate before PCM."""
        self.socket.updated["session"]["language"] = "fr"
        await self.start(configured=False)
        self.stream.push_frame(frame(1))
        self.assertEqual(
            await self.reader_error(), "mistral-session-configuration-refused"
        )
        self.assertEqual(self.socket.samples, [])
        self.assertNotIn("input_audio.end", [e["type"] for e in self.socket.sent])

    async def test_native_request_identity_cannot_change(self):
        """Created and applied settings must belong to the same real request."""
        self.socket.updated["session"]["request_id"] = "another-native-request"
        await self.start(configured=False)
        self.assertEqual(await self.reader_error(), "mistral-session-request-changed")

    async def test_revocation_cancels_blocked_pcm_and_queued_frames(self):
        """Synchronous fencing prevents blocked and subsequently queued PCM/EOF."""
        self.socket.release["input_audio.append"].clear()
        await self.start()
        for number in range(1, 5):
            self.stream.push_frame(frame(number))
        await self.socket.entered["input_audio.append"].wait()
        self.authorized = False
        self.provider.fence_audio()
        self.socket.release["input_audio.append"].set()
        await self.provider.aclose()
        self.assertEqual(self.socket.samples, [])
        self.assertEqual(self.provider.appended_samples, 0)
        self.assertNotIn("input_audio.end", [e["type"] for e in self.socket.sent])
        self.session.ws_connect.assert_awaited_once()

    async def test_end_write_and_lock_waiter_cancel_without_flush(self):
        """A blocked EOF and lock waiter cannot escape a revoked leg."""
        self.socket.release["input_audio.end"].clear()
        await self.start()
        self.provider.seal_input(self.stream, 0)
        await self.socket.entered["input_audio.end"].wait()
        queued = self.spawn(
            self.provider._socket.send(
                {"type": "input_audio.append", "audio": "AAA="}, samples=1
            )
        )
        await asyncio.sleep(0)
        self.provider.fence_audio()
        self.socket.release["input_audio.end"].set()
        result = (await asyncio.gather(queued, return_exceptions=True))[0]
        self.assertIsInstance(result, asyncio.CancelledError)
        await self.provider.aclose()
        self.assertEqual([e["type"] for e in self.socket.sent], ["session.update"])
        self.assertFalse(self.provider._end_sent)

    async def test_post_await_expiry_counts_success_but_prevents_next_write(self):
        """Physical success is counted locally even when its return loses authority."""

        async def expire(kind):
            if kind == "input_audio.append":
                self.authorized = False

        self.socket.after_send = expire
        await self.start()
        self.stream.push_frame(frame(1))
        self.stream.push_frame(frame(2))
        await self.reader_error()
        self.assertEqual(self.provider.appended_samples, 480)
        self.assertEqual(self.socket.samples, [1] * 480)
        self.assertNotIn("input_audio.end", [e["type"] for e in self.socket.sent])

    async def test_done_before_end_send_returns_waits_for_physical_success(self):
        """A native response can race the awaited send's drain completion."""
        returned = asyncio.Event()

        async def hold(kind):
            if kind == "input_audio.end":
                await returned.wait()

        self.socket.after_send = hold
        await self.start()
        self.socket.delta("race")
        self.provider.seal_input(self.stream, 0)
        drain = self.spawn(self.provider.drain_input())
        await eventually(lambda: self.provider.terminal_metadata is not None)
        self.assertFalse(drain.done())
        self.assertFalse(self.provider._end_sent)
        returned.set()
        self.assertEqual(await drain, frozenset(self.latest()))
        await self.reader

    async def test_done_queued_behind_pong_before_end_write_is_refused(self):
        """Sealing cannot authorize native done before EOF owns the write lock."""
        await self.start()
        self.socket.release["pong"].clear()
        self.socket.messages.put_nowait(
            aiohttp.WSMessage(aiohttp.WSMsgType.PING, b"fixture", "")
        )
        await self.socket.entered["pong"].wait()
        self.provider.seal_input(self.stream, 0)
        pong_and_end_writers = 2
        await eventually(
            lambda: len(self.provider._socket._writers) == pong_and_end_writers
        )
        self.assertFalse(self.socket.entered["input_audio.end"].is_set())
        self.socket.feed(done_event("premature terminal"))
        self.socket.release["pong"].set()
        with self.assertRaises(InputDrainError):
            await self.provider.drain_input()
        self.assertEqual(await self.reader_error(), "mistral-done-outside-planned-end")
        self.assertIsNone(self.provider.terminal_metadata)
        self.assertEqual(self.events, [])

    async def test_done_while_end_waits_for_write_lock_is_refused(self):
        """A waiting EOF writer cannot certify unsolicited terminal text."""
        await self.start()
        async with self.provider._socket._lock:
            self.provider.seal_input(self.stream, 0)
            await eventually(lambda: bool(self.provider._socket._writers))
            self.socket.feed(done_event("premature terminal"))
            self.assertEqual(
                await self.reader_error(), "mistral-done-outside-planned-end"
            )
        self.assertFalse(self.provider._end_started)
        self.assertFalse(self.socket.entered["input_audio.end"].is_set())
        self.assertNotIn(
            "input_audio.end", [event["type"] for event in self.socket.sent]
        )
        self.assertIsNone(self.provider.terminal_metadata)

    async def test_official_language_event_before_text_preserves_final_identity(self):
        """Realtime audio_language applies to deltas without another done copy."""
        await self.start()
        self.socket.feed({"type": "transcription.language", "audio_language": "fr"})
        self.socket.delta("Bonjour")
        await eventually(lambda: bool(self.events))
        self.assertEqual(self.events[0].alternatives[0].language, "fr")
        refs = await self.finish()
        self.assertEqual(refs, frozenset(self.latest()))
        self.assertEqual(len(self.events), 1)
        self.assertEqual(self.events[0].alternatives[0].text, "Bonjour")
        self.assertTrue(self.provider.terminal_drain_complete(self.stream))

    async def test_language_event_revises_existing_shards_without_duplicate_text(self):
        """Late language detection changes metadata on the same bounded refs."""
        await self.start()
        text = "a" * 4000 + "😀e\u0301"
        self.socket.delta(text)
        shard_count = 2
        await eventually(lambda: len(self.events) == shard_count)
        refs = frozenset(self.latest())
        self.socket.feed({"type": "transcription.language", "audio_language": "fr"})
        revised_event_count = shard_count * 2
        await eventually(lambda: len(self.events) == revised_event_count)
        self.assertEqual(frozenset(self.latest()), refs)
        self.assertTrue(
            all(
                event.alternatives[0].language == "fr"
                for event in self.latest().values()
            )
        )
        self.socket.feed({"type": "transcription.language", "audio_language": "fr"})
        self.assertEqual(await self.finish(), refs)
        self.assertEqual(len(self.events), 4)
        self.assertEqual(
            "".join(event.alternatives[0].text for event in self.latest().values()),
            text,
        )

    async def test_language_event_requires_bounded_audio_language(self):
        """Reject missing, malformed and oversized native language metadata."""
        await self.start()
        self.socket.feed(
            {"type": "transcription.language", "audio_language": "x" * 4000}
        )
        self.assertEqual(await self.reader_error(), "mistral-language-event-invalid")
        self.assertEqual(self.events, [])
        self.assertFalse(self.socket.entered["input_audio.end"].is_set())

    async def test_language_event_does_not_accept_the_done_field_name(self):
        """The official realtime event requires audio_language, not language."""
        await self.start()
        self.socket.feed({"type": "transcription.language", "language": "fr"})
        self.assertEqual(await self.reader_error(), "mistral-language-event-invalid")
        self.assertEqual(self.events, [])

    async def test_done_and_close_join_both_required(self):
        """No final in a queue or native done can bypass physical close."""
        self.socket.release["close"].clear()
        await self.start()
        self.socket.delta("final")
        self.provider.seal_input(self.stream, 0)
        drain = self.spawn(self.provider.drain_input())
        await self.socket.entered["close"].wait()
        self.assertFalse(drain.done())
        self.assertFalse(self.provider._terminal_complete)
        with self.assertRaises(InputDrainError):
            self.provider.terminal_drain_complete(self.stream)
        self.socket.release["close"].set()
        await drain
        await self.reader
        self.assertTrue(self.provider.terminal_drain_complete(self.stream))

    async def test_abnormal_terminal_close_refuses_drain(self):
        """Native done cannot mask an abnormal socket close result."""
        self.socket.close_result_code = 1006
        await self.start()
        self.provider.seal_input(self.stream, 0)
        with self.assertRaises(InputDrainError):
            await self.provider.drain_input()
        self.assertEqual(await self.reader_error(), "mistral-terminal-close-unproved")

    async def test_early_close_never_reconnects(self):
        """Even a normal peer close before done is an incomplete leg."""
        await self.start()
        self.socket.peer_close()
        await self.reader_error()
        with self.assertRaises(InputDrainError):
            self.provider.stream()

    async def test_provider_error_never_reconnects(self):
        """Provider refusal is terminal and never exposes its arbitrary message."""
        await self.start()
        self.socket.feed(
            {"type": "error", "error": {"message": "synthetic private content"}}
        )
        self.assertEqual(await self.reader_error(), "mistral-native-provider-error")

    async def test_malformed_native_event_fails_in_actual_parser(self):
        """Malformed text is not swallowed by an SDK parser or receive loop."""
        await self.start()
        self.socket.messages.put_nowait(
            aiohttp.WSMessage(aiohttp.WSMsgType.TEXT, "{", "")
        )
        self.assertEqual(await self.reader_error(), "mistral-native-frame-invalid")

    async def test_missing_done_times_out_and_fences(self):
        """A sealed input is not proof of completed transcription."""
        self.socket.auto_done = False
        await self.start()
        self.provider.seal_input(self.stream, 0)
        with patch("live_mistral.DRAIN_TIMEOUT_SECONDS", 0.01):
            with self.assertRaises(TimeoutError):
                await self.provider.drain_input()
        self.assertFalse(self.provider._terminal_complete)
        self.failed.assert_called_once_with("mistral-input-drain-incomplete")

    async def test_aggregate_and_revision_bounds_are_explicit(self):
        """Independent text and revision capacity exhaustion stops the leg."""
        await self.start()
        with patch("live_mistral_transcript.MAX_REVISIONS_PER_SHARD", 1):
            self.socket.delta("one")
            await eventually(lambda: len(self.events) == 1)
            self.socket.delta(" two")
            self.assertEqual(
                await self.reader_error(), "mistral-revision-capacity-exceeded"
            )

    async def test_tee_backlog_is_bounded_when_public_consumer_stalls(self):
        """The SDK metrics peer cannot silently grow the public peer's deque."""
        await self.start(collect=False)
        with patch("live_mistral.MAX_QUEUED_EVENTS", 4):
            for text in ("a", "b", "c", "d", "e"):
                self.socket.delta(text)
                await asyncio.sleep(0)
            await eventually(self.stream._task.done)
        self.assertIsInstance(self.stream._task.exception(), InputDrainError)
        self.failed.assert_called_once_with("mistral-event-backlog-capacity-exceeded")
        self.assertLessEqual(max(map(len, self.stream._tee._buffers)), 4)

    async def test_failed_initial_handshake_is_never_retried(self):
        """One ambiguous ws_connect failure exhausts all acquisition authority."""
        self.session.ws_connect.side_effect = aiohttp.ClientConnectionError("fixture")
        self.stream = self.provider.stream()

        async def read():
            async for event in self.stream:
                self.events.append(event)

        self.reader = self.spawn(read())
        await self.reader_error()
        with self.assertRaises(InputDrainError):
            self.provider.stream()

    async def test_closed_profile_rejects_model_language_rate_and_delay_overrides(self):
        """Only the parent's frozen explicit configuration is accepted."""
        for change in (
            {"model": "other"},
            {"language": "fr"},
            {"sample_rate": 16000},
            {"target_streaming_delay_ms": 80},
        ):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.make_provider(**change)
        self.session.ws_connect.assert_not_awaited()

    async def test_exact_seal_and_only_one_stream(self):
        """Wrong counts, foreign streams and direct unplanned EOF are refused."""
        await self.start()
        with self.assertRaises(InputDrainError):
            self.provider.stream()
        with self.assertRaises(InputDrainError):
            self.stream.end_input()
        self.stream.push_frame(frame(1))
        with self.assertRaises(InputDrainError):
            self.provider.seal_input(self.stream, 479)
        self.assertNotIn("input_audio.end", [e["type"] for e in self.socket.sent])

    async def test_invalid_input_geometry_is_not_resampled(self):
        """The admitted ledger and actual PCM format must have the same clock."""
        await self.start()
        wrong = rtc.AudioFrame(
            data=b"\0\0" * 8, sample_rate=16000, num_channels=1, samples_per_channel=8
        )
        with self.assertRaises(InputDrainError):
            self.stream.push_frame(wrong)
        self.assertEqual(self.socket.samples, [])

    async def test_runtime_credential_reads_only_mistral_key(self):
        """No provider key or selection fallback is consulted at runtime."""
        with patch("live_mistral.os.environ.get", return_value="synthetic-key") as get:
            extra = self.make_provider(api_key=None)
        get.assert_called_once_with("MISTRAL_API_KEY")
        await extra.aclose()

    async def test_repeated_cancellation_waits_for_socket_cleanup(self):
        """Cancelled shutdown waiters still join one physical close."""
        await self.start()
        self.socket.release["close"].clear()
        closing = self.spawn(self.provider.aclose())
        await self.socket.entered["close"].wait()
        closing.cancel()
        await asyncio.sleep(0)
        closing.cancel()
        await asyncio.sleep(0)
        self.assertFalse(closing.done())
        self.socket.release["close"].set()
        result = (await asyncio.gather(closing, return_exceptions=True))[0]
        self.assertIsInstance(result, asyncio.CancelledError)
        self.assertTrue(self.socket.closed)
        self.assertEqual(self.socket.close_calls, 1)
        self.assertNotIn("input_audio.end", [e["type"] for e in self.socket.sent])

    async def test_fenced_update_does_not_write_config_or_queued_pcm(self):
        """A blocked configuration write remains cancellable at physical egress."""
        self.socket.release["session.update"].clear()
        await self.start(configured=False)
        self.stream.push_frame(frame(1))
        await self.socket.entered["session.update"].wait()
        self.provider.fence_audio()
        self.socket.release["session.update"].set()
        await self.provider.aclose()
        self.assertEqual(self.socket.sent, [])
        self.assertTrue(self.socket.closed)

    async def test_fence_while_waiting_created_closes_without_any_protocol_write(self):
        """Cancellation of the handshake receive cannot manufacture an EOF."""
        self.socket.messages.get_nowait()
        await self.start(configured=False)
        self.provider.fence_audio()
        await self.provider.aclose()
        self.assertEqual(self.socket.sent, [])
        self.assertTrue(self.socket.closed)

    async def test_revoke_after_connection_await_closes_without_session_update(self):
        """A connection return cannot reauthorize a leg revoked during handshake."""

        async def connect(*args, **kwargs):
            self.authorized = False
            return self.socket

        self.session.ws_connect.side_effect = connect
        self.stream = self.provider.stream()

        async def read():
            async for event in self.stream:
                self.events.append(event)

        self.reader = self.spawn(read())
        await self.reader_error()
        self.assertEqual(self.socket.sent, [])
        self.assertTrue(self.socket.closed)

    async def test_native_done_without_authorized_end_is_refused(self):
        """Native full text cannot end an active, unsealed leg."""
        await self.start()
        self.socket.feed(done_event("unsolicited"))
        self.assertEqual(await self.reader_error(), "mistral-done-outside-planned-end")
        self.assertNotIn(
            "input_audio.end", [event["type"] for event in self.socket.sent]
        )

    async def test_wrong_done_model_cannot_certify_selected_provider_profile(self):
        """Bind terminal evidence to the frozen model as well as the session."""
        await self.start()
        event = done_event("")
        event["model"] = "unexpected-model"
        self.socket.terminal_frames = [event]
        self.provider.seal_input(self.stream, 0)
        with self.assertRaises(InputDrainError):
            await self.provider.drain_input()
        self.assertEqual(await self.reader_error(), "mistral-done-model-invalid")

    async def test_no_duplicate_final_when_done_changes_no_material_content(self):
        """Exact confirmation does not manufacture another canonical revision."""
        await self.start()
        self.socket.done_language = None
        self.socket.delta("same")
        await eventually(lambda: len(self.events) == 1)
        await self.finish()
        self.assertEqual(len(self.events), 1)

    async def test_aggregate_byte_cap_fails_instead_of_truncating_text(self):
        """Retained full text and the canonical transcript share a bounded total."""
        await self.start()
        with patch("live_mistral_transcript.MAX_TRANSCRIPT_BYTES", 4):
            self.socket.delta("界界")
            self.assertEqual(
                await self.reader_error(), "mistral-transcript-capacity-exceeded"
            )

    async def test_shard_cap_fails_instead_of_evicting_old_text(self):
        """An additional shard cannot silently replace an earlier identity."""
        await self.start()
        with patch("live_mistral_transcript.MAX_SHARDS", 1):
            self.socket.delta("a" * 4001)
            self.assertEqual(
                await self.reader_error(), "mistral-shard-capacity-exceeded"
            )
        self.assertEqual(
            next(iter(self.latest().values())).alternatives[0].text, "a" * 4000
        )

    async def test_native_frame_cap_is_independent_of_canonical_item_cap(self):
        """Reject oversized raw events before JSON parsing or SDK emission."""
        await self.start()
        with patch("live_mistral.MAX_NATIVE_FRAME_BYTES", 100):
            self.socket.delta("x" * 101)
            self.assertEqual(
                await self.reader_error(), "mistral-native-frame-capacity-exceeded"
            )

    async def test_pending_pcm_bound_fences_before_unbounded_sdk_queue(self):
        """Blocked egress cannot accumulate more than ten seconds of PCM."""
        self.socket.release["input_audio.append"].clear()
        await self.start()
        with patch("live_mistral.MAX_PENDING_SAMPLES", 480):
            self.stream.push_frame(frame(1))
            await self.socket.entered["input_audio.append"].wait()
            with self.assertRaises(InputDrainError):
                self.stream.push_frame(frame(2))
        self.assertEqual(self.provider.appended_samples, 0)
        self.assertEqual(self.socket.samples, [])

    async def test_close_timeout_cannot_certify_terminal_drain(self):
        """Bound close-frame writes as well as aiohttp's peer-close wait."""
        self.socket.release["close"].clear()
        await self.start()
        self.provider.seal_input(self.stream, 0)
        with patch("live_mistral.CLOSE_TIMEOUT_SECONDS", 0.01):
            with self.assertRaises(InputDrainError):
                await self.provider.drain_input()
        await self.reader_error()
        self.assertFalse(self.provider._terminal_complete)

    async def test_explicit_ping_egress_is_fenced_during_blocked_pong(self):
        """Disable aiohttp auto-pong so revocation controls that physical write."""
        await self.start()
        self.socket.release["pong"].clear()
        self.socket.messages.put_nowait(
            aiohttp.WSMessage(aiohttp.WSMsgType.PING, b"fixture", "")
        )
        await self.socket.entered["pong"].wait()
        self.provider.fence_audio()
        self.socket.release["pong"].set()
        await self.provider.aclose()
        self.assertEqual(self.socket.pongs, [])
        self.assertNotIn(
            "input_audio.end", [event["type"] for event in self.socket.sent]
        )

    async def test_owned_http_session_closes_after_native_terminal_join(self):
        """The runtime client bypasses environment proxies and joins its cleanup."""
        await self.provider.aclose()
        self.provider = self.make_provider(http_session=None)
        with patch(
            "live_mistral.aiohttp.ClientSession", return_value=self.session
        ) as client:
            await self.start()
            await self.finish()
        self.assertFalse(client.call_args.kwargs["trust_env"])
        self.session.close.assert_awaited_once()
        self.assertTrue(self.socket.closed)

    async def test_unbounded_native_language_is_refused_before_event_emission(self):
        """Language metadata cannot bypass canonical per-event byte bounds."""
        await self.start()
        self.socket.done_text = "only terminal"
        self.socket.done_language = "x" * 4000
        self.provider.seal_input(self.stream, 0)
        with self.assertRaises(InputDrainError):
            await self.provider.drain_input()
        self.assertEqual(await self.reader_error(), "mistral-done-language-invalid")
        self.assertEqual(self.events, [])

    async def test_real_aiohttp_upgrade_disconnection_is_not_retried_internally(self):
        """Run real aiohttp request/retry logic with a synthetic client middleware."""
        await self.provider.aclose()
        real_client = aiohttp.ClientSession
        attempts = []
        clients = []

        async def disconnect(request, handler):
            attempts.append(str(request.url))
            raise aiohttp.ServerDisconnectedError("synthetic pre-upgrade disconnect")

        def create_client(**kwargs):
            client = real_client(middlewares=(disconnect,), **kwargs)
            clients.append(client)
            return client

        self.provider = self.make_provider(http_session=None)
        with patch("live_mistral.aiohttp.ClientSession", side_effect=create_client):
            self.stream = self.provider.stream()

            async def read():
                async for event in self.stream:
                    self.events.append(event)

            self.reader = self.spawn(read())
            async with asyncio.timeout(2):
                result = (await asyncio.gather(self.reader, return_exceptions=True))[0]
        self.assertIsInstance(result, InputDrainError)
        self.assertEqual(len(attempts), 1)
        self.assertEqual(len(clients), 1)
        self.assertTrue(clients[0].closed)
        self.assertFalse(clients[0]._retry_connection)
        self.session.ws_connect.assert_not_awaited()
        self.reconnect.assert_not_called()

    async def test_real_aiohttp_upgrade_redirect_is_closed_without_following(self):
        """A real request trace refuses a synthetic redirect before a second GET."""
        await self.provider.aclose()
        real_client = aiohttp.ClientSession
        attempts = []
        clients = []
        response = SimpleNamespace(status=302, _raw_cookie_headers=(), close=Mock())

        async def redirect(request, handler):
            attempts.append(str(request.url))
            return response

        def create_client(**kwargs):
            client = real_client(middlewares=(redirect,), **kwargs)
            clients.append(client)
            return client

        self.provider = self.make_provider(http_session=None)
        with patch("live_mistral.aiohttp.ClientSession", side_effect=create_client):
            self.stream = self.provider.stream()

            async def read():
                async for event in self.stream:
                    self.events.append(event)

            self.reader = self.spawn(read())
            async with asyncio.timeout(2):
                result = (await asyncio.gather(self.reader, return_exceptions=True))[0]
        self.assertIsInstance(result, InputDrainError)
        self.assertEqual(str(result), "mistral-handshake-redirect-refused")
        self.assertEqual(len(attempts), 1)
        self.assertTrue(response.close.called)
        self.assertTrue(clients[0].closed)
        self.session.ws_connect.assert_not_awaited()
        self.reconnect.assert_not_called()
