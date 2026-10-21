"""Single-acquisition OpenAI transport for a canonically authorized RTC epoch.

LiveKit 1.8.3 SpeechStream._run reconnects inside its own while loop, including
on the ten-minute session rotation. max_retry=0 alone cannot prevent that.
This small, version-pinned integration replaces its pool with a single-use
pool: even reuse of the same socket is refused on a second acquisition.
"""

import asyncio
from collections.abc import Callable
from importlib.metadata import version

import aiohttp
from livekit.agents import APIConnectOptions, utils
from livekit.plugins import openai


class _AuthorizedSocket:
    """Fence the pinned SDK's queued writes at its actual socket boundary."""

    def __init__(
        self, socket: aiohttp.ClientWebSocketResponse, can_send: Callable[[], bool]
    ):
        self._socket = socket
        self._can_send = can_send
        self._fenced = False
        self._senders: set[asyncio.Task] = set()

    def fence_audio(self) -> None:
        """Discard future queued writes and cancel writes already awaiting I/O."""
        self._fenced = True
        for sender in tuple(self._senders):
            sender.cancel()

    async def send_json(self, message: dict[str, object]) -> None:
        """Never hand a queued append or commit to the socket after revocation."""
        if self._fenced or not self._can_send():
            return
        sender = asyncio.current_task()
        if sender is None:
            raise RuntimeError("canonical socket writes require a cancellable task")
        self._senders.add(sender)
        try:
            await self._socket.send_json(message)
        finally:
            self._senders.discard(sender)

    async def receive(self) -> aiohttp.WSMessage:
        """Retain the SDK reader while accepted transcript events are drained."""
        return await self._socket.receive()

    @property
    def close_code(self) -> int | None:
        """Expose the close status consumed by the reviewed SDK receive loop."""
        return self._socket.close_code

    async def close(self) -> None:
        """Fence writes before joining the underlying socket close."""
        self.fence_audio()
        await self._socket.close()


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
        self._connection_attempted = False
        self._audio_fenced = False
        self._socket: _AuthorizedSocket | None = None
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
        self._socket = _AuthorizedSocket(socket, self._can_send)
        if self._audio_fenced:
            self._socket.fence_audio()
        return self._socket

    def fence_audio(self) -> None:
        """Stop SDK writes synchronously before asynchronous cleanup is scheduled."""
        self._audio_fenced = True
        if self._socket is not None:
            self._socket.fence_audio()

    def stream(self):
        """Disable the base SpeechStream's independent retry loop as well."""
        return super().stream(conn_options=APIConnectOptions(max_retry=0))
