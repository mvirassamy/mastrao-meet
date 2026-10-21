"""Single-acquisition OpenAI transport for a canonically authorized RTC epoch.

LiveKit 1.8.3 SpeechStream._run reconnects inside its own while loop, including
on the ten-minute session rotation. max_retry=0 alone cannot prevent that.
This small, version-pinned integration replaces its pool with a single-use
pool: even reuse of the same socket is refused on a second acquisition.
"""

from collections.abc import Callable
from importlib.metadata import version

import aiohttp
from livekit.agents import APIConnectOptions, utils
from livekit.plugins import openai


class _SingleAcquisitionPool(utils.ConnectionPool[aiohttp.ClientWebSocketResponse]):
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

    async def get(self, *, timeout: float) -> aiohttp.ClientWebSocketResponse:
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
        # The original pool is empty: this instance is never prewarmed/shared.
        self._pool = _SingleAcquisitionPool(self, can_send, reconnect_refused)

    async def _connect_ws(self, timeout: float) -> aiohttp.ClientWebSocketResponse:
        """Recheck the lease after any pool work and before socket construction."""
        if self._connection_attempted:
            self._reconnect_refused()
            raise RuntimeError("canonical live source cannot open another socket")
        if not self._can_send():
            raise RuntimeError("canonical live source cannot open another socket")
        self._connection_attempted = True
        return await super()._connect_ws(timeout)

    def stream(self):
        """Disable the base SpeechStream's independent retry loop as well."""
        return super().stream(conn_options=APIConnectOptions(max_retry=0))
