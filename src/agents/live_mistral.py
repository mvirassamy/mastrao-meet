"""Single-acquisition Mistral realtime transport with physical write fencing."""

import asyncio
import base64
import json
import os
from collections.abc import Callable
from importlib.metadata import version

import aiohttp
from livekit import rtc
from livekit.agents import APIConnectOptions, stt

from live_input_drain import InputDrainError
from live_mistral_transcript import MistralTranscript

MODEL = "voxtral-mini-transcribe-realtime-3"
REALTIME_URL = f"wss://api.mistral.ai/v1/audio/transcriptions/realtime?model={MODEL}"
SAMPLE_RATE = 24000
STREAMING_DELAY_MS = 480
CONNECT_TIMEOUT_SECONDS = 10
DRAIN_TIMEOUT_SECONDS = 25
MAX_NATIVE_FRAME_BYTES = 2 * 1024 * 1024
MAX_NATIVE_EVENTS = 32768
MAX_PENDING_SAMPLES = SAMPLE_RATE * 10
MAX_QUEUED_FRAMES = 512
MAX_QUEUED_EVENTS = 512
NORMAL_CLOSE_CODE = 1000
MAX_NATIVE_REQUEST_LENGTH = 160
CLOSE_TIMEOUT_SECONDS = 5


async def _refuse_redirect(session, context, params) -> None:
    """A redirected HTTP upgrade is a second acquisition, so close and refuse."""
    params.response.close()
    raise InputDrainError("mistral-handshake-redirect-refused")


async def _join(task: asyncio.Future):
    """Join cleanup through repeated waiter cancellation before propagating it."""
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
    result = task.result()
    if cancelled:
        raise asyncio.CancelledError
    return result


class _MistralSocket:
    """Check the immutable leg lease at the physical JSON/PCM egress boundary."""

    def __init__(self, socket, owner):
        self.raw = socket
        self._owner = owner
        self._lock = asyncio.Lock()
        self._writers: set[asyncio.Task] = set()
        self._close_task: asyncio.Task | None = None

    def fence_audio(self) -> None:
        for task in tuple(self._writers):
            if task is not asyncio.current_task():
                task.cancel()

    async def send(
        self, event: dict | None = None, *, samples: int = 0, pong: bytes | None = None
    ) -> None:
        self._owner._check()
        task = asyncio.current_task()
        self._writers.add(task)
        try:
            async with self._lock:
                self._owner._check()
                if pong is not None:
                    await self.raw.pong(pong)
                else:
                    payload = json.dumps(event, separators=(",", ":"))
                    if event is not None and event.get("type") == "input_audio.end":
                        # A queued writer has not started EOF. Native done may
                        # race the send await only after this lock is acquired.
                        self._owner._end_started = True
                    await self.raw.send_str(payload)
                    # A successful local write counts even if authority expired
                    # while it awaited I/O. It is never a provider acceptance ACK.
                    self._owner._appended_samples += samples
                self._owner._check()
        finally:
            self._writers.discard(task)

    async def receive(self) -> dict:
        while True:
            self._owner._check()
            message = await self.raw.receive()
            self._owner._check()
            self._owner._native_events += 1
            if self._owner._native_events > MAX_NATIVE_EVENTS:
                raise InputDrainError("mistral-native-event-capacity-exceeded")
            if message.type == aiohttp.WSMsgType.PING:
                await self.send(pong=message.data)
                continue
            if message.type == aiohttp.WSMsgType.PONG:
                continue
            if message.type != aiohttp.WSMsgType.TEXT:
                raise InputDrainError("mistral-socket-ended-before-done")
            if len(message.data.encode("utf-8")) > MAX_NATIVE_FRAME_BYTES:
                raise InputDrainError("mistral-native-frame-capacity-exceeded")
            try:
                event = json.loads(message.data)
            except (TypeError, ValueError):
                raise InputDrainError("mistral-native-frame-invalid") from None
            if not isinstance(event, dict) or not isinstance(event.get("type"), str):
                raise InputDrainError("mistral-native-frame-invalid")
            if event["type"] == "error":
                raise InputDrainError("mistral-native-provider-error")
            return event

    async def close(self) -> None:
        """Close is cleanup only; it can never send input_audio.end or PCM."""
        self.fence_audio()
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._finish_close())
        await _join(self._close_task)

    async def _finish_close(self) -> None:
        # aiohttp 3.14.4 aborts/releases its response on close cancellation,
        # including a blocked close-frame write, before re-raising.
        async with asyncio.timeout(CLOSE_TIMEOUT_SECONDS):
            await self.raw.close()


class _MistralStream(stt.RecognizeStream):
    """Use the pinned SDK channels and iterator with the documented WS protocol."""

    def __init__(self, owner):
        self._owner = owner
        self.pushed_samples = 0
        self._transcript: MistralTranscript | None = None
        super().__init__(
            stt=owner,
            conn_options=APIConnectOptions(max_retry=0),
            sample_rate=SAMPLE_RATE,
        )
        self._input_ch._maxsize = MAX_QUEUED_FRAMES
        self._event_ch._maxsize = MAX_QUEUED_EVENTS

    def _emit(self, event: stt.SpeechEvent) -> None:
        self._owner._check()
        backlog = max((len(buffer) for buffer in self._tee._buffers), default=0)
        if self._event_ch.qsize() + backlog >= MAX_QUEUED_EVENTS:
            raise InputDrainError("mistral-event-backlog-capacity-exceeded")
        self._event_ch.send_nowait(event)

    def push_frame(self, frame: rtc.AudioFrame) -> None:
        """Admit exact 24-kHz mono PCM16, without hidden resampling or replay."""
        owner = self._owner
        owner._check()
        if (
            owner._expected_samples is not None
            or frame.sample_rate != SAMPLE_RATE
            or frame.num_channels != 1
            or frame.samples_per_channel <= 0
            or frame.data.nbytes != 2 * frame.samples_per_channel
        ):
            owner._fail("mistral-input-frame-invalid")
            owner._check()
        pending = self.pushed_samples - owner.appended_samples
        if pending + frame.samples_per_channel > MAX_PENDING_SAMPLES:
            owner._fail("mistral-input-capacity-exceeded")
            owner._check()
        try:
            super().push_frame(frame)
        except Exception:
            owner._fail("mistral-input-admission-failed")
            raise
        self.pushed_samples += frame.samples_per_channel

    def flush(self) -> None:
        """Allow the SDK's EOF sentinel only for an explicitly sealed leg."""
        self._owner._check()
        if self._owner._expected_samples is None:
            raise InputDrainError("mistral-input-flush-not-authorized")
        super().flush()

    async def _send_audio(self) -> None:
        owner = self._owner
        async for frame in self._input_ch:
            owner._check()
            if isinstance(frame, self._FlushSentinel):
                continue
            await owner._socket.send(
                {
                    "type": "input_audio.append",
                    "audio": base64.b64encode(frame.data.cast("B")).decode("ascii"),
                },
                samples=frame.samples_per_channel,
            )
        owner._check()
        if (
            owner._expected_samples is None
            or owner.appended_samples != owner._expected_samples
            or self.pushed_samples != owner._expected_samples
        ):
            raise InputDrainError("mistral-input-tail-unconfirmed")
        await owner._socket.send({"type": "input_audio.end"})
        owner._end_sent = True

    async def _receive_text(self) -> dict:
        owner = self._owner
        while True:
            event = await owner._socket.receive()
            match event["type"]:
                case "transcription.text.delta":
                    self._transcript.append(event.get("text"))
                case "transcription.language":
                    self._transcript.set_language(event.get("audio_language"))
                case "transcription.done":
                    if not owner._end_started:
                        raise InputDrainError("mistral-done-outside-planned-end")
                    # Retain even a mismatched full transcript as native evidence.
                    owner._terminal_metadata = event
                    if event.get("model") != MODEL:
                        raise InputDrainError("mistral-done-model-invalid")
                    return event
                case _:
                    raise InputDrainError("mistral-native-event-out-of-order")

    async def _connection(self) -> None:
        owner = self._owner
        async with asyncio.timeout(CONNECT_TIMEOUT_SECONDS):
            await owner._connect()
            created = await owner._socket.receive()
            if created["type"] != "session.created":
                raise InputDrainError("mistral-session-created-missing")
            owner._native_request_id = _session_identity(created)
            self._transcript = MistralTranscript(owner._native_request_id, self._emit)
            await owner._socket.send(
                {
                    "type": "session.update",
                    "session": {
                        "audio_format": {
                            "encoding": "pcm_s16le",
                            "sample_rate": SAMPLE_RATE,
                        },
                        "target_streaming_delay_ms": STREAMING_DELAY_MS,
                    },
                }
            )
            updated = await owner._socket.receive()
            _verify_session(updated)
            if _session_identity(updated) != owner._native_request_id:
                raise InputDrainError("mistral-session-request-changed")
        sender = asyncio.create_task(self._send_audio())
        receiver = asyncio.create_task(self._receive_text())
        try:
            done, _ = await asyncio.wait(
                (sender, receiver), return_when=asyncio.FIRST_COMPLETED
            )
            for task in done:
                task.result()
            await asyncio.gather(sender, receiver)
            owner._check()
            if not owner._end_sent:
                raise InputDrainError("mistral-end-write-unconfirmed")
            event = receiver.result()
            owner._final_refs = self._transcript.finish(
                event.get("text"), event.get("language")
            )
        finally:
            sender.cancel()
            receiver.cancel()
            await _join(asyncio.gather(sender, receiver, return_exceptions=True))

    async def _run(self) -> None:
        owner = self._owner
        try:
            await self._run_once()
        except asyncio.CancelledError:
            owner.fence_audio()
            raise
        except Exception as error:
            reason = "mistral-transport-failed"
            if isinstance(error, InputDrainError):
                reason = str(error)
            owner._fail(reason)
            raise owner._error from None

    async def _run_once(self) -> None:
        owner = self._owner
        connection = asyncio.create_task(self._connection())
        revoked = asyncio.create_task(owner._fenced.wait())
        try:
            done, _ = await asyncio.wait(
                (connection, revoked), return_when=asyncio.FIRST_COMPLETED
            )
            owner._check()
            if connection not in done:
                raise InputDrainError("mistral-authority-fenced")
            connection.result()
        finally:
            await _join(asyncio.create_task(self._cleanup(connection, revoked)))
        owner._check()
        raw = owner._socket.raw
        if (
            not raw.closed
            or raw.close_code != NORMAL_CLOSE_CODE
            or raw.exception() is not None
        ):
            raise InputDrainError("mistral-terminal-close-unproved")
        owner._terminal_complete = True

    async def _cleanup(self, connection: asyncio.Task, revoked: asyncio.Task) -> None:
        connection.cancel()
        revoked.cancel()
        await asyncio.gather(connection, revoked, return_exceptions=True)
        await self._owner._close_transport()


def _session_identity(event: dict) -> str:
    session = event.get("session")
    if not isinstance(session, dict):
        raise InputDrainError("mistral-session-invalid")
    request_id = session.get("request_id")
    if (
        not isinstance(request_id, str)
        or not 0 < len(request_id) <= MAX_NATIVE_REQUEST_LENGTH
        or session.get("model") != MODEL
    ):
        raise InputDrainError("mistral-session-identity-invalid")
    return request_id


def _verify_session(event: dict) -> None:
    session = event.get("session")
    if event["type"] != "session.updated" or not isinstance(session, dict):
        raise InputDrainError("mistral-session-updated-missing")
    audio = session.get("audio_format")
    if (
        not isinstance(audio, dict)
        or audio.get("encoding") != "pcm_s16le"
        or type(audio.get("sample_rate")) is not int
        or audio["sample_rate"] != SAMPLE_RATE
        or type(session.get("target_streaming_delay_ms")) is not int
        or session["target_streaming_delay_ms"] != STREAMING_DELAY_MS
        or session.get("language") is not None
        or session.get("diarize") not in (None, False)
        or session.get("turn_detection") is not None
        or session.get("context_bias") not in (None, [])
        or session.get("speaker_references") not in (None, [])
    ):
        raise InputDrainError("mistral-session-configuration-refused")


class AuthorizedMistral(stt.STT):
    """One closed-profile leg; no plugin, retry, reconnect or provider fallback."""

    def __init__(  # noqa: PLR0913 -- Explicit frozen factory options and I/O seam.
        self,
        *,
        can_send: Callable[[], bool],
        reconnect_refused: Callable[[], None],
        failed: Callable[[str], None],
        model: str = MODEL,
        language: None = None,
        sample_rate: int = SAMPLE_RATE,
        target_streaming_delay_ms: int = STREAMING_DELAY_MS,
        api_key: str | None = None,
        http_session: aiohttp.ClientSession | None = None,
    ):
        """Accept only the frozen Mistral profile and its one credential source."""
        if (
            model != MODEL
            or language is not None
            or type(sample_rate) is not int
            or sample_rate != SAMPLE_RATE
            or type(target_streaming_delay_ms) is not int
            or target_streaming_delay_ms != STREAMING_DELAY_MS
        ):
            raise ValueError("Mistral live configuration must match the sealed profile")
        if version("livekit-agents") != "1.8.3":
            raise RuntimeError("canonical live transport requires reviewed SDK 1.8.3")
        if version("aiohttp") != "3.14.4":
            raise RuntimeError("canonical Mistral transport requires aiohttp 3.14.4")
        if not can_send():
            raise InputDrainError("mistral-open-not-authorized")
        if api_key is None:
            api_key = os.environ.get("MISTRAL_API_KEY")
        if not isinstance(api_key, str) or not api_key.strip():
            raise ValueError("MISTRAL_API_KEY is required")
        super().__init__(
            capabilities=stt.STTCapabilities(
                streaming=True, interim_results=True, offline_recognize=False
            )
        )
        self._can_send = can_send
        self._reconnect_refused = reconnect_refused
        self._failed = failed
        self._api_key = api_key
        self._http_session = http_session
        self._owns_http_session = http_session is None
        self._fenced = asyncio.Event()
        self._error: InputDrainError | None = None
        self._socket: _MistralSocket | None = None
        self._canonical_stream: _MistralStream | None = None
        self._connection_attempted = False
        self._appended_samples = 0
        self._expected_samples: int | None = None
        self._end_started = False
        self._end_sent = False
        self._terminal_metadata: dict | None = None
        self._native_request_id: str | None = None
        self._native_events = 0
        self._terminal_complete = False
        self._final_refs: frozenset[str] = frozenset()
        self._drain_task: asyncio.Task | None = None
        self._close_task: asyncio.Task | None = None
        self._transport_close_task: asyncio.Task | None = None

    @property
    def model(self) -> str:
        """Return the fixed model without consulting environment overrides."""
        return MODEL

    @property
    def provider(self) -> str:
        """Label SDK metrics with the selected provider."""
        return "mistral"

    @property
    def appended_samples(self) -> int:
        """Count only successful local physical socket PCM writes."""
        return self._appended_samples

    @property
    def terminal_metadata(self) -> dict | None:
        """Return native done evidence, including its exact full text, by value."""
        if self._terminal_metadata is None:
            return None
        return json.loads(json.dumps(self._terminal_metadata))

    def can_send(self) -> bool:
        """Bind every egress check to this immutable leg and local fence."""
        return not self._fenced.is_set() and self._can_send()

    def _check(self) -> None:
        if self._error is not None:
            raise self._error
        if not self.can_send():
            raise InputDrainError("mistral-authority-fenced")

    def _fail(self, reason: str) -> None:
        if self._error is None:
            self._error = InputDrainError(reason)
            self.fence_audio()
            self._failed(reason)

    def fence_audio(self) -> None:
        """Synchronously revoke queued/blocked writes without flushing or EOF."""
        self._fenced.set()
        if self._socket is not None:
            self._socket.fence_audio()

    def stream(self) -> stt.RecognizeStream:
        """Acquire the sole stream, retaining the pinned SDK's actual iterator."""
        self._check()
        if self._canonical_stream is not None:
            raise InputDrainError("mistral-stream-acquisition-refused")
        self._canonical_stream = _MistralStream(self)
        return self._canonical_stream

    async def _connect(self) -> None:
        self._check()
        if self._connection_attempted:
            self._reconnect_refused()
            raise InputDrainError("mistral-reconnect-refused")
        self._connection_attempted = True
        if self._http_session is None:
            trace = aiohttp.TraceConfig()
            trace.on_request_redirect.append(_refuse_redirect)
            self._http_session = aiohttp.ClientSession(
                trust_env=False,
                timeout=aiohttp.ClientTimeout(total=CONNECT_TIMEOUT_SECONDS),
                trace_configs=[trace],
            )
            # Pinned aiohttp retries a disconnected upgrade GET internally,
            # independently of LiveKit's max_retry or our acquisition counter.
            self._http_session._retry_connection = False
        socket = await self._http_session.ws_connect(
            REALTIME_URL,
            headers={"Authorization": f"Bearer {self._api_key}"},
            autoping=False,
            autoclose=False,
            heartbeat=None,
            timeout=aiohttp.ClientWSTimeout(ws_close=CLOSE_TIMEOUT_SECONDS),
            max_msg_size=MAX_NATIVE_FRAME_BYTES,
        )
        self._socket = _MistralSocket(socket, self)
        self._check()

    def seal_input(self, stream: stt.RecognizeStream, expected_samples: int) -> None:
        """Authorize one EOF only for this stream's exact admitted PCM tail."""
        self._check()
        if (
            stream is None
            or stream is not self._canonical_stream
            or type(expected_samples) is not int
            or expected_samples < self.appended_samples
            or expected_samples != self._canonical_stream.pushed_samples
        ):
            self._fail("mistral-input-seal-invalid")
            self._check()
        if self._expected_samples is not None:
            if self._expected_samples != expected_samples:
                self._fail("mistral-input-seal-count-changed")
                self._check()
            return
        self._expected_samples = expected_samples
        stream.end_input()

    async def input_window(self, item_id: str) -> None:
        """Text offsets and native done are not per-item PCM acceptance ACKs."""
        return None

    async def drain_input(self) -> frozenset[str]:
        """Share one bounded terminal result and its final application refs."""
        if self._drain_task is None:
            self._drain_task = asyncio.create_task(self._drain_input())
        return await asyncio.shield(self._drain_task)

    async def _drain_input(self) -> frozenset[str]:
        self._check()
        if self._expected_samples is None or self._canonical_stream is None:
            raise InputDrainError("mistral-input-was-not-sealed")
        try:
            async with asyncio.timeout(DRAIN_TIMEOUT_SECONDS):
                await asyncio.shield(self._canonical_stream._task)
            self.terminal_drain_complete(self._canonical_stream)
            return self._final_refs
        except BaseException:
            self._fail("mistral-input-drain-incomplete")
            raise

    def terminal_drain_complete(self, stream: stt.RecognizeStream) -> bool:
        """Require done equality, a normal closed socket and a joined SDK task."""
        self._check()
        if (
            stream is not self._canonical_stream
            or not self._terminal_complete
            or not stream._task.done()
            or stream._task.cancelled()
            or stream._task.exception() is not None
        ):
            raise InputDrainError("mistral-terminal-drain-unproved")
        return True

    async def _close_transport(self) -> None:
        if self._transport_close_task is None:
            self._transport_close_task = asyncio.create_task(self._finish_transport())
        await _join(self._transport_close_task)

    async def _finish_transport(self) -> None:
        try:
            if self._socket is not None:
                await self._socket.close()
        finally:
            if self._owns_http_session and self._http_session is not None:
                await self._http_session.close()

    async def aclose(self) -> None:
        """Fence first and join every task and physical close exactly once."""
        self.fence_audio()
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._close())
        await _join(self._close_task)

    async def _close(self) -> None:
        if self._drain_task is not None:
            self._drain_task.cancel()
            await asyncio.gather(self._drain_task, return_exceptions=True)
        if self._canonical_stream is not None:
            await self._canonical_stream.aclose()
        await self._close_transport()

    async def _recognize_impl(self, buffer, *, language, conn_options):
        raise NotImplementedError(
            "Mistral canonical sessions only support live streams"
        )
