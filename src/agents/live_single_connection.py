"""Single-acquisition OpenAI transport for a canonically authorized RTC epoch.

LiveKit 1.8.3 SpeechStream._run reconnects inside its own while loop, including
on the ten-minute session rotation. max_retry=0 alone cannot prevent that.
This small, version-pinned integration replaces its pool with a single-use
pool: even reuse of the same socket is refused on a second acquisition.
"""

import asyncio
import base64
import json
from collections.abc import Callable
from importlib.metadata import version

import aiohttp
from livekit.agents import APIConnectOptions, stt, utils
from livekit.plugins import openai

from live_input_drain import InputDrain, InputDrainError

DRAIN_TIMEOUT_SECONDS = 25


class _AuthorizedSocket:
    """Fence the pinned SDK's queued writes at its actual socket boundary."""

    def __init__(
        self,
        socket: aiohttp.ClientWebSocketResponse,
        can_send: Callable[[], bool],
        failed: Callable[[str], None],
    ):
        self._socket = socket
        self._can_send = can_send
        self._fenced = False
        self._senders: set[asyncio.Task] = set()
        self._write_lock = asyncio.Lock()
        self._close_task: asyncio.Task[None] | None = None
        self.drain = InputDrain(failed)

    def fence_audio(self) -> None:
        """Discard future queued writes and cancel writes already awaiting I/O."""
        self._fenced = True
        self.drain.fence()
        for sender in tuple(self._senders):
            sender.cancel()

    async def send_json(self, message: dict[str, object]) -> None:
        """Never hand a queued append or commit to the socket after revocation."""
        await self._send(message, tail_commit=False)

    async def _send(self, message: dict[str, object], *, tail_commit: bool) -> None:
        if self._fenced or not self._can_send():
            self.drain.fence()
            return
        sender = asyncio.current_task()
        if sender is None:
            raise RuntimeError("canonical socket writes require a cancellable task")
        self._senders.add(sender)
        try:
            async with self._write_lock:
                # Recheck after waiting behind an append or a configuration write.
                if self._fenced or not self._can_send():
                    self.drain.fence()
                    return
                kind = message["type"]
                commit = None
                if kind == "input_audio_buffer.commit":
                    if self.drain.expected_samples is not None and not tail_commit:
                        return
                    commit = self.drain.committing()
                await self._socket.send_json(message)
                if kind == "input_audio_buffer.append":
                    samples = (
                        len(base64.b64decode(message["audio"], validate=True)) // 2
                    )
                    self.drain.appended(samples)
                if commit is not None:
                    self.drain.commit_sent(commit)
        except BaseException:
            if not self._fenced:
                self.drain.fail("live-input-socket-write-failed")
            raise
        finally:
            self._senders.discard(sender)

    async def drain_input(self) -> frozenset[str]:
        """Commit the flushed tail once, then wait for ACKs and all raw finals."""
        await self.drain.wait_appended()
        if self.drain.appended_samples > self.drain.committed_samples:
            await self._send({"type": "input_audio_buffer.commit"}, tail_commit=True)
        expected = await self.drain.wait_completed()
        if not self._can_send():
            raise InputDrainError("live-input-drain-lease-ended")
        if self.drain.committed_samples != self.drain.expected_samples:
            raise InputDrainError("live-input-uncommitted-tail")
        return expected

    async def receive(self) -> aiohttp.WSMessage:
        """Retain the SDK reader while accepted transcript events are drained."""
        try:
            message = await self._socket.receive()
            if message.type == aiohttp.WSMsgType.TEXT:
                data = json.loads(message.data)
                if not isinstance(data, dict):
                    raise InputDrainError("live-input-provider-event-invalid")
                self.drain.observe(data)
            elif message.type in {
                aiohttp.WSMsgType.CLOSED,
                aiohttp.WSMsgType.CLOSE,
                aiohttp.WSMsgType.CLOSING,
                aiohttp.WSMsgType.ERROR,
            }:
                if not self._fenced:
                    self.drain.fail("live-input-socket-receive-closed")
            return message
        except BaseException:
            if not self._fenced:
                self.drain.fail("live-input-socket-receive-failed")
            raise

    @property
    def close_code(self) -> int | None:
        """Expose the close status consumed by the reviewed SDK receive loop."""
        return self._socket.close_code

    async def close(self) -> None:
        """Fence writes before joining the underlying socket close."""
        self.fence_audio()
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._socket.close())
        await asyncio.shield(self._close_task)


class _SingleAcquisitionPool(utils.ConnectionPool[_AuthorizedSocket]):
    """Prevent SDK internal reconnects before they can acquire another socket."""

    def __init__(
        self,
        provider: openai.STT,
        can_send: Callable[[], bool],
        reconnect_refused: Callable[[], None],
    ):
        super().__init__(connect_cb=provider._connect_ws, close_cb=provider._close_ws)
        self._reconnect_refused = reconnect_refused
        self._acquired = False
        self._can_send = can_send

    async def get(self, *, timeout: float) -> _AuthorizedSocket:
        """Seal acquisition even if the initial handshake fails or is cancelled."""
        if self._acquired:
            self._reconnect_refused()
            raise RuntimeError("canonical live source cannot reconnect")
        if not self._can_send():
            raise RuntimeError("canonical live source cannot reconnect")
        self._acquired = True
        return await super().get(timeout=timeout)


class AuthorizedOpenAI(openai.STT):
    """Run the existing pinned provider with no SDK retries or reconnections."""

    def __init__(
        self,
        *,
        can_send: Callable[[], bool],
        reconnect_refused: Callable[[], None],
        failed: Callable[[str], None],
        **kwargs,
    ):
        """Fail closed on SDK updates until the single-connection proof is rerun."""
        if (
            version("livekit-agents") != "1.8.3"
            or version("livekit-plugins-openai") != "1.8.3"
        ):
            raise RuntimeError("canonical live transport requires reviewed SDK 1.8.3")
        super().__init__(**kwargs)
        self._can_send = can_send
        self._reconnect_refused = reconnect_refused
        self._failed = failed
        self._connection_attempted = False
        self._audio_fenced = False
        self._socket: _AuthorizedSocket | None = None
        self._drain_task: asyncio.Task[frozenset[str]] | None = None
        # The original pool is empty: this instance is never prewarmed/shared.
        self._pool = _SingleAcquisitionPool(self, can_send, reconnect_refused)

    async def _connect_ws(self, timeout: float) -> _AuthorizedSocket:
        """Recheck the lease after any pool work and before socket construction."""
        if self._connection_attempted:
            self._reconnect_refused()
            raise RuntimeError("canonical live source cannot open another socket")
        if self._audio_fenced or not self._can_send():
            raise RuntimeError("canonical live source cannot open another socket")
        self._connection_attempted = True
        socket = await super()._connect_ws(timeout)
        self._socket = _AuthorizedSocket(socket, self._can_send, self._failed)
        if self._audio_fenced:
            self._socket.fence_audio()
        return self._socket

    def fence_audio(self) -> None:
        """Stop SDK writes synchronously before asynchronous cleanup is scheduled."""
        self._audio_fenced = True
        if self._socket is not None:
            self._socket.fence_audio()

    @property
    def appended_samples(self) -> int:
        """Expose actual successful socket progress for bounded SDK admission."""
        if self._socket is None:
            return 0
        return self._socket.drain.appended_samples

    def seal_input(self, stream: stt.RecognizeStream, expected_samples: int) -> None:
        """End only planned input admission while the immutable leg lease is valid."""
        if self._audio_fenced or not self._can_send() or self._socket is None:
            raise InputDrainError("live-input-seal-not-authorized")
        already_sealed = self._socket.drain.expected_samples is not None
        self._socket.drain.seal(expected_samples)
        if not already_sealed:
            stream.end_input()

    async def input_window(self, item_id: str) -> tuple[int, int] | None:
        """Bound final provenance waits without guessing when an ACK is missing."""
        if self._socket is None:
            return None
        try:
            async with asyncio.timeout(DRAIN_TIMEOUT_SECONDS):
                return await self._socket.drain.input_window(item_id)
        except TimeoutError:
            return None

    async def drain_input(self) -> frozenset[str]:
        """Share a bounded, retained drain outcome between all callers."""
        if self._drain_task is None:
            self._drain_task = asyncio.create_task(self._drain_input())
        return await asyncio.shield(self._drain_task)

    async def _drain_input(self) -> frozenset[str]:
        if self._socket is None or self._socket.drain.expected_samples is None:
            raise InputDrainError("live-input-was-not-sealed")
        try:
            async with asyncio.timeout(DRAIN_TIMEOUT_SECONDS):
                return await self._socket.drain_input()
        except BaseException:
            self._socket.drain.fail("live-input-drain-incomplete")
            raise

    async def aclose(self) -> None:
        """Join outstanding drain work and close the sole physical socket."""
        self.fence_audio()
        if self._drain_task is not None:
            if not self._drain_task.done():
                self._drain_task.cancel()
            await asyncio.gather(self._drain_task, return_exceptions=True)
        await super().aclose()
        # ConnectionPool logs and swallows close_cb Exceptions. Rejoin the exact
        # retained close result so rotation cannot acknowledge a failed socket close.
        if self._socket is not None:
            await self._socket.close()

    def terminal_drain_complete(self, stream: stt.RecognizeStream) -> bool:
        """OpenAI must still be live at its existing ACK/publication barrier."""
        if stream._task.done():
            raise InputDrainError("live-continuation-sdk-ended-during-drain")
        return False

    def stream(self):
        """Disable the base SpeechStream's independent retry loop as well."""
        return super().stream(conn_options=APIConnectOptions(max_retry=0))
