"""Track actual PCM writes, commit ACKs and terminal items for one SDK socket."""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass

MAX_DRAIN_ITEMS = 100_000


class InputDrainError(RuntimeError):
    """Prevent rotation when a provider boundary cannot be proved complete."""


@dataclass
class CommitWindow:
    """One issued commit's local PCM bounds, confirmed only after its send."""

    start_sample: int
    end_sample: int
    sent: bool = False


class InputDrain:
    """Retain bounded evidence from the socket, independent of SDK usage clocks."""

    def __init__(self, failed: Callable[[str], None] | None = None):
        """Start a fresh ledger for one physical, single-acquisition connection."""
        self.appended_samples = 0
        self.committed_samples = 0
        self.commits = 0
        self.expected_samples: int | None = None
        self._acks: dict[str, str | None] = {}
        self._completed: dict[str, bool] = {}
        self._commit_windows: list[CommitWindow] = []
        self._successors: dict[str | None, str] = {}
        self._item_windows: dict[str, CommitWindow] = {}
        self._last_ack: str | None = None
        self._error: InputDrainError | None = None
        self._failed = failed
        self._fenced = False
        self._changed = asyncio.Event()

    def fail(self, reason: str) -> None:
        """Wake every waiter with the same retained failure."""
        if self._error is None:
            self._error = InputDrainError(reason)
            if self._failed is not None:
                self._failed(reason)
        self._changed.set()

    def fence(self) -> None:
        """Wake waiters on intentional closure without inventing a failure."""
        self._fenced = True
        self._changed.set()

    def check(self) -> None:
        """Keep a previously uncertain outcome terminal for every caller."""
        if self._error is not None:
            raise self._error
        if self._fenced:
            raise InputDrainError("live-input-authority-fenced")

    def seal(self, expected_samples: int) -> None:
        """Seal the admitted input count before the SDK consumes its EOF."""
        self.check()
        if self.expected_samples is not None:
            if self.expected_samples != expected_samples:
                self.fail("live-input-seal-count-changed")
                self.check()
            return
        if (
            type(expected_samples) is not int
            or expected_samples < self.appended_samples
        ):
            self.fail("live-input-seal-count-invalid")
            self.check()
        self.expected_samples = expected_samples
        self._changed.set()

    def appended(self, samples: int) -> None:
        """Count only an append whose underlying socket send completed."""
        self.appended_samples += samples
        if (
            self.expected_samples is not None
            and self.appended_samples > self.expected_samples
        ):
            self.fail("live-input-appended-count-mismatch")
        self._changed.set()

    def committing(self) -> CommitWindow:
        """Reserve ACK accounting before I/O can yield to the receive task."""
        window = CommitWindow(self.committed_samples, self.appended_samples)
        self.commits += 1
        self.committed_samples = self.appended_samples
        if self.commits > MAX_DRAIN_ITEMS:
            self.fail("live-input-commit-capacity-exceeded")
        self.check()
        self._commit_windows.append(window)
        return window

    def commit_sent(self, window: CommitWindow) -> None:
        """Confirm the reserved interval only after the underlying send returns."""
        window.sent = True
        self._changed.set()

    def _resolve_ack_chain(self) -> None:
        """Pair the rooted item chain with commit FIFO, never receive order."""
        while self._last_ack in self._successors:
            item = self._successors[self._last_ack]
            if item in self._item_windows:
                self.fail("live-input-ack-chain-inconsistent")
                return
            ordinal = len(self._item_windows)
            self._item_windows[item] = self._commit_windows[ordinal]
            self._last_ack = item

    def _observe_ack(self, item_id: str, previous: object) -> None:
        """Retain a unique predecessor chain while permitting reordered ACKs."""
        if previous is not None and (not isinstance(previous, str) or not previous):
            self.fail("live-input-ack-predecessor-invalid")
            self.check()
        if item_id in self._acks and self._acks[item_id] != previous:
            self.fail("live-input-ack-identity-changed")
            self.check()
        successor = self._successors.get(previous)
        if successor is not None and successor != item_id:
            self.fail("live-input-ack-chain-inconsistent")
            self.check()
        self._acks[item_id] = previous
        self._successors[previous] = item_id
        if len(self._acks) > self.commits:
            self.fail("live-input-unexpected-commit-ack")
        self.check()
        self._resolve_ack_chain()

    def observe(self, data: dict[str, object]) -> None:
        """Observe raw events without replacing the SDK's receive dispatch."""
        kind = data.get("type")
        if kind in {"error", "conversation.item.input_audio_transcription.failed"}:
            self.fail("live-input-provider-terminal-error")
            self.check()
        if kind not in {
            "input_audio_buffer.committed",
            "conversation.item.input_audio_transcription.completed",
        }:
            return
        item_id = data.get("item_id")
        if not isinstance(item_id, str) or not item_id:
            self.fail("live-input-item-identity-missing")
            self.check()
        if kind == "input_audio_buffer.committed":
            self._observe_ack(item_id, data.get("previous_item_id"))
        else:
            transcript = data.get("transcript")
            if not isinstance(transcript, str):
                self.fail("live-input-completion-invalid")
            else:
                self._completed[item_id] = bool(transcript.strip())
        if max(len(self._acks), len(self._completed)) > MAX_DRAIN_ITEMS:
            self.fail("live-input-item-capacity-exceeded")
        self._changed.set()
        self.check()

    async def input_window(self, item_id: str) -> tuple[int, int] | None:
        """Wait for a real completed item, its ACK chain and successful commit."""
        while True:
            if self._error is not None:
                return None
            window = self._item_windows.get(item_id)
            if window is not None and window.sent and item_id in self._completed:
                if window.end_sample <= window.start_sample:
                    return None
                return window.start_sample, window.end_sample
            if self._fenced:
                return None
            self._changed.clear()
            await self._changed.wait()

    async def wait_appended(self) -> None:
        """Wait through held I/O and the SDK's partial 50-ms tail."""
        await self._wait(lambda: self.appended_samples == self.expected_samples)

    async def wait_completed(self) -> frozenset[str]:
        """Wait for every commit ACK and every item, including empty completions."""
        await self._wait(
            lambda: (
                len(self._acks) == self.commits
                and self._acks.keys() <= self._completed.keys()
                and all(window.sent for window in self._commit_windows)
            )
        )
        if self._completed.keys() != self._acks.keys():
            raise InputDrainError("live-input-unacknowledged-final")
        if self._item_windows.keys() != self._acks.keys():
            raise InputDrainError("live-input-ack-chain-inconsistent")
        return frozenset(key for key, nonempty in self._completed.items() if nonempty)

    async def _wait(self, ready: Callable[[], bool]) -> None:
        while True:
            self.check()
            if ready():
                return
            self._changed.clear()
            await self._changed.wait()
