"""One track's bounded durable journal across all physical provider legs."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from live_delivery_codec import (
    MAX_EVENTS_PER_SOURCE,
    event_digest,
    validate_receipt,
    validated_language,
)
from live_send_control import ControlError, LiveSendAuthority


class DurablePublisher:
    """Retain source order and terminal uncertainty without HTTP or ASR retry."""

    def __init__(self, authority: LiveSendAuthority, failed: Callable[[], None]):
        """Bind to one RTC source authority, never to the shared room publisher."""
        self._authority = authority
        self._failed = failed
        self._lock = asyncio.Lock()
        self._sequence = 0
        self._finals: dict[tuple[str, str, int], tuple[int, str]] = {}
        self._final_count = 0
        self._error: BaseException | None = None
        self._unconfirmed_error: BaseException | None = None
        self._finish_task: asyncio.Task[None] | None = None
        self._ended = False

    def _require_open(self) -> None:
        if self._error is not None:
            raise self._error
        if self._ended:
            raise ControlError("durable source already ended")

    def _event(self, **content: object) -> dict[str, object]:
        return {
            "version": 2,
            "source_ref": self._authority.source["source_ref"],
            "leg_ref": self._authority.leg_ref,
            "sequence": self._sequence + 1,
            **content,
        }

    async def publish_final(
        self, item_ref: str, revision: int, text: str, language: object
    ) -> None:
        """Confirm exact provider identity/content before the caller reports success."""
        try:
            async with self._lock:
                self._require_open()
                event = self._event(
                    kind="final",
                    item_ref=item_ref,
                    revision=revision,
                    text=text,
                    language=validated_language(language),
                    timing={"state": "unavailable"},
                )
                identity = (self._authority.leg_ref, item_ref, revision)
                previous = self._finals.get(identity)
                # An acknowledged final remains committed even if its later UI
                # publication fails. Replay must neither resend nor renumber it.
                if previous is not None:
                    sequence, digest = previous
                    event["sequence"] = sequence
                    if event_digest(event) != digest:
                        raise ControlError("conflicting durable final identity")
                    return
                # Keep space for a possible explicit gap and the source end.
                if self._sequence >= MAX_EVENTS_PER_SOURCE - 2:
                    raise ControlError("durable source event capacity exceeded")
                await self._send(event, self._final_count + 1)
                self._finals[identity] = (event["sequence"], event_digest(event))
        except BaseException as error:
            self._fail(error)
            raise

    async def _send(self, event: dict[str, object], final_count: int) -> None:
        # Validate before crossing HTTP. Retain attempted order and acknowledged
        # identity digests, not a second transcript copy owned by the worker.
        event_digest(event)
        self._sequence = event["sequence"]
        authority = self._authority
        try:
            receipt = await authority.client.deliver(
                {**authority.binding, **authority.source, "leg_ref": event["leg_ref"]},
                event,
            )
            validate_receipt(receipt, event, final_count)
        except BaseException as error:
            self._unconfirmed_error = error
            raise
        self._final_count = final_count

    def _fail(self, error: BaseException) -> None:
        if self._error is not None:
            return
        self._error = error
        self._authority.fence()
        self._failed()

    async def finish(self, gap_reason: str | None) -> None:
        """Share one end attempt; an uncertain earlier event leaves it unterminated."""
        if self._finish_task is None:
            self._finish_task = asyncio.create_task(self._finish(gap_reason))
        try:
            await asyncio.shield(self._finish_task)
        except asyncio.CancelledError as error:
            # Cancelling a waiter leaves closure running. Cancelling closure
            # itself is terminal, even if its coroutine never began executing.
            if self._finish_task.cancelled():
                self._fail(error)
            raise

    async def _finish(self, gap_reason: str | None) -> None:
        try:
            async with self._lock:
                if self._unconfirmed_error is not None:
                    raise self._unconfirmed_error
                if self._error is not None and gap_reason is None:
                    gap_reason = "delivery_failed"
                if gap_reason is not None:
                    await self._send(
                        self._event(kind="gap", reason=gap_reason), self._final_count
                    )
                await self._send(
                    self._event(
                        kind="end",
                        reason="capture_incomplete",
                        final_event_count=self._final_count,
                    ),
                    self._final_count,
                )
                self._ended = True
                if self._error is not None:
                    raise self._error
        except BaseException as error:
            self._fail(error)
            raise
