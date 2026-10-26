"""Version-pinned Deepgram transport with real write fences and native evidence."""

import asyncio
import json
from collections.abc import Callable
from importlib.metadata import version

import aiohttp
from livekit.agents import APIConnectOptions, stt
from livekit.plugins import deepgram
from livekit.plugins.deepgram import stt as sdk

from live_deepgram_drain import MAX_NATIVE_FRAME_BYTES, DeepgramDrain
from live_input_drain import InputDrainError

DRAIN_TIMEOUT_SECONDS = 25
MAX_SDK_EVENTS = 512


class _DeepgramSocket:
    """Fence bytes and protocol strings at the last boundary before real I/O."""

    def __init__(self, socket, provider):
        self._socket = socket
        self._provider = provider
        self._senders: set[asyncio.Task] = set()
        self._lock = asyncio.Lock()
        self._fenced = False
        self._close_task: asyncio.Task | None = None

    def fence_audio(self) -> None:
        self._fenced = True
        for task in tuple(self._senders):
            task.cancel()

    async def _send(self, *, pcm: bytes | None = None, text: str | None = None) -> None:
        provider = self._provider
        if self._fenced or not provider.can_send():
            raise InputDrainError("deepgram-socket-write-fenced")
        task = asyncio.current_task()
        self._senders.add(task)
        try:
            async with self._lock:
                if self._fenced or not provider.can_send():
                    raise InputDrainError("deepgram-socket-write-fenced")
                ledger = provider.ledger
                ledger.check()
                if pcm is not None:
                    if len(pcm) % 2 or ledger.close_sent:
                        raise InputDrainError("deepgram-socket-pcm-invalid")
                    await self._socket.send_bytes(pcm)
                    ledger.appended(len(pcm) // 2)
                    return
                await self._send_control(text)
        except BaseException:
            if not self._fenced:
                provider.ledger.fail("deepgram-socket-write-failed")
            raise
        finally:
            self._senders.discard(task)

    async def _send_control(self, text: str) -> None:
        ledger = self._provider.ledger
        kind = json.loads(text)["type"]
        if kind == "KeepAlive":
            if not ledger.close_sent:
                await self._socket.send_str(text)
            return
        if (
            ledger.expected_samples is None
            or ledger.appended_samples != ledger.expected_samples
        ):
            raise InputDrainError("deepgram-input-tail-unconfirmed")
        if kind == "Finalize" and not ledger.finalize_sent:
            await self._socket.send_str(text)
            ledger.finalize_sent = True
        elif kind == "CloseStream" and ledger.finalize_sent and not ledger.close_sent:
            ledger.close_started = True
            await self._socket.send_str(text)
            ledger.close_sent = True
        else:
            raise InputDrainError("deepgram-input-control-invalid")
        ledger.update_terminal()

    async def send_bytes(self, data: bytes) -> None:
        await self._send(pcm=data)

    async def send_str(self, message: str) -> None:
        await self._send(text=message)

    async def receive(self):
        message = await self._socket.receive()
        if message.type == aiohttp.WSMsgType.TEXT:
            try:
                if len(message.data.encode("utf-8")) > MAX_NATIVE_FRAME_BYTES:
                    raise ValueError("oversized native event")
                if not isinstance(json.loads(message.data), dict):
                    raise ValueError("native event must be an object")
            except (ValueError, TypeError):
                self._provider.ledger.fail("deepgram-native-frame-invalid")
                self._provider.ledger.check()
        elif message.type in {
            aiohttp.WSMsgType.CLOSE,
            aiohttp.WSMsgType.CLOSED,
            aiohttp.WSMsgType.CLOSING,
        }:
            self._provider.ledger.observe_close(self.close_code)
        elif message.type == aiohttp.WSMsgType.ERROR:
            self._provider.ledger.fail("deepgram-socket-receive-failed")
            self._provider.ledger.check()
        return message

    @property
    def close_code(self):
        return self._socket.close_code

    async def close(self):
        self.fence_audio()
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._socket.close())
        await asyncio.shield(self._close_task)


class _DeepgramStream(sdk.SpeechStream):
    """Keep the SDK parser/loops, adding provenance and a proved terminal join."""

    def __init__(self, *, owner, **kwargs):
        self._owner = owner
        super().__init__(**kwargs)
        self._event_ch._maxsize = MAX_SDK_EVENTS

    async def _connect_ws(self):
        owner = self._owner
        if owner._connection_attempted:
            owner._reconnect_refused()
            raise InputDrainError("canonical live source cannot reconnect")
        if not owner.can_send():
            raise InputDrainError("deepgram-connection-not-authorized")
        owner._connection_attempted = True
        socket = await super()._connect_ws()
        owner._socket = _DeepgramSocket(socket, owner)
        if not owner.can_send():
            await owner._socket.close()
            raise InputDrainError("deepgram-connection-fenced")
        return owner._socket

    def _process_stream_event(self, data: dict) -> None:
        send = self._event_ch.send_nowait
        try:
            native = self._owner.ledger.observe(data)

            def emit(event):
                if native is not None and event.alternatives:
                    for alternative in event.alternatives:
                        alternative.metadata = {
                            "canonical_item_ref": native["item_ref"],
                            "native_provenance": native,
                        }
                send(event)

            self._event_ch.send_nowait = emit
            super()._process_stream_event(data)
        except Exception:
            self._owner.ledger.fail("deepgram-sdk-event-unreadable")
            raise
        finally:
            self._event_ch.send_nowait = send

    async def _run(self):
        sdk_run = asyncio.create_task(super()._run())
        terminal = asyncio.create_task(self._owner.ledger.terminal.wait())
        try:
            done, _ = await asyncio.wait(
                (sdk_run, terminal), return_when=asyncio.FIRST_COMPLETED
            )
            if sdk_run in done:
                sdk_run.result()
                raise InputDrainError("deepgram-sdk-ended-before-terminal-proof")
            self._owner.ledger.check()
            # SDK recv has consumed Metadata and the expected peer close. Its
            # keepalive loop does not finish itself in 1.8.3; join it explicitly.
            sdk_run.cancel()
            result = (await asyncio.gather(sdk_run, return_exceptions=True))[0]
            if isinstance(result, BaseException) and not isinstance(
                result, asyncio.CancelledError
            ):
                raise result
            self._owner.ledger.check()
            self._owner._terminal_complete = True
        finally:
            terminal.cancel()
            if not sdk_run.done() and not sdk_run.cancelling():
                sdk_run.cancel()
            # A drain timeout and hard stop can cancel this wrapper twice.
            # A second cancellation must not interrupt the SDK's task/socket
            # finalizer or close the event channel before it reports usage.
            joined = asyncio.gather(sdk_run, terminal, return_exceptions=True)
            cancelled = False
            while not joined.done():
                try:
                    await asyncio.shield(joined)
                except asyncio.CancelledError:
                    cancelled = True
            joined.result()
            if cancelled:
                raise asyncio.CancelledError


class AuthorizedDeepgram(deepgram.STT):
    """One concrete canonical session; no reconnect, fallback, or native ACK fiction."""

    def __init__(
        self,
        *,
        can_send: Callable[[], bool],
        reconnect_refused: Callable[[], None],
        failed: Callable[[str], None],
        **kwargs,
    ):
        """Require the reviewed SDK before creating any credentialed transport."""
        if (
            version("livekit-agents") != "1.8.3"
            or version("livekit-plugins-deepgram") != "1.8.3"
        ):
            raise RuntimeError("canonical live transport requires reviewed SDK 1.8.3")
        super().__init__(**kwargs)
        self._can_send = can_send
        self._reconnect_refused = reconnect_refused
        self._audio_fenced = False
        self._connection_attempted = False
        self._terminal_complete = False
        self._socket: _DeepgramSocket | None = None
        self._canonical_stream: _DeepgramStream | None = None
        self._drain_task: asyncio.Task | None = None
        self.ledger = DeepgramDrain(failed)

    def can_send(self) -> bool:
        """Retain both the immutable leg lease and the immediate local fence."""
        return not self._audio_fenced and self._can_send()

    @property
    def appended_samples(self) -> int:
        """Count only binary socket writes that actually returned successfully."""
        return self.ledger.appended_samples

    @property
    def terminal_metadata(self) -> dict | None:
        """Native connection evidence, never an item ACK or coverage certificate."""
        if self.ledger.metadata is None:
            return None
        return json.loads(json.dumps(self.ledger.metadata))

    def fence_audio(self) -> None:
        """Cancel admitted and queued I/O synchronously without flush or EOF."""
        self._audio_fenced = True
        if self._socket is not None:
            self._socket.fence_audio()

    def stream(self):
        """Construct one real SDK stream with retries disabled."""
        if self._canonical_stream is not None or not self.can_send():
            raise InputDrainError("deepgram-stream-acquisition-refused")
        self._canonical_stream = _DeepgramStream(
            owner=self,
            stt=self,
            opts=self._sanitize_options(),
            conn_options=APIConnectOptions(max_retry=0),
            api_key=self._api_key,
            http_session=self._ensure_session(),
            base_url=self._opts.endpoint_url,
        )
        return self._canonical_stream

    def seal_input(self, stream: stt.RecognizeStream, expected_samples: int) -> None:
        """Flush only a planned leg's exact admitted tail under its active lease."""
        if (
            not self.can_send()
            or self._socket is None
            or stream is not self._canonical_stream
        ):
            raise InputDrainError("deepgram-input-seal-not-authorized")
        if self.ledger.seal(expected_samples):
            stream.end_input()

    async def input_window(self, item_id: str) -> None:
        """Word spans and terminal duration prove no PCM commit window."""
        return None

    async def drain_input(self) -> frozenset[str]:
        """Share one bounded terminal outcome, never a provider retry."""
        if self._drain_task is None:
            self._drain_task = asyncio.create_task(self._drain_input())
        return await asyncio.shield(self._drain_task)

    async def _drain_input(self) -> frozenset[str]:
        if self.ledger.expected_samples is None or self._canonical_stream is None:
            raise InputDrainError("deepgram-input-was-not-sealed")
        try:
            async with asyncio.timeout(DRAIN_TIMEOUT_SECONDS):
                await asyncio.shield(self._canonical_stream._task)
            self.terminal_drain_complete(self._canonical_stream)
            return frozenset(self.ledger.final_items)
        except BaseException:
            self.ledger.fail("deepgram-input-drain-incomplete")
            raise

    def terminal_drain_complete(self, stream: stt.RecognizeStream) -> bool:
        """Permit a completed SDK task only after native terminal evidence and join."""
        self.ledger.check()
        if (
            not self.can_send()
            or stream is not self._canonical_stream
            or not self._terminal_complete
            or not stream._task.done()
            or stream._task.cancelled()
            or stream._task.exception() is not None
        ):
            raise InputDrainError("deepgram-terminal-drain-unproved")
        return True

    async def aclose(self):
        """Fence first, then join the actual SDK and physical socket cleanup."""
        self.fence_audio()
        if self._drain_task is not None:
            if not self._drain_task.done():
                self._drain_task.cancel()
            await asyncio.gather(self._drain_task, return_exceptions=True)
        if self._canonical_stream is not None:
            await self._canonical_stream.aclose()
        if self._socket is not None:
            await self._socket.close()
