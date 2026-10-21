"""Track actual PCM writes, commit ACKs and terminal items for one SDK socket."""

import asyncio
from collections.abc import Callable

MAX_DRAIN_ITEMS = 100_000


class InputDrainError(RuntimeError):
    """Prevent rotation when a provider boundary cannot be proved complete."""


class InputDrain:
    """Retain bounded evidence from the socket, independent of SDK usage clocks."""

    def __init__(self):
        """Start a fresh ledger for one physical, single-acquisition connection."""
        self.appended_samples = 0
        self.committed_samples = 0
        self.commits = 0
        self.expected_samples: int | None = None
        self._acks: dict[str, str | None] = {}
        self._completed: dict[str, bool] = {}
        self._error: InputDrainError | None = None
        self._changed = asyncio.Event()

    def fail(self, reason: str) -> None:
        """Wake every waiter with the same retained failure."""
        if self._error is None:
            self._error = InputDrainError(reason)
        self._changed.set()

    def check(self) -> None:
        """Keep a previously uncertain outcome terminal for every caller."""
        if self._error is not None:
            raise self._error

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

    def committing(self) -> None:
        """Reserve ACK accounting before I/O can yield to the receive task."""
        self.commits += 1
        self.committed_samples = self.appended_samples
        if self.commits > MAX_DRAIN_ITEMS:
            self.fail("live-input-commit-capacity-exceeded")
        self.check()

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
            previous = data.get("previous_item_id")
            if previous is not None and (not isinstance(previous, str) or not previous):
                self.fail("live-input-ack-predecessor-invalid")
            elif item_id in self._acks and self._acks[item_id] != previous:
                self.fail("live-input-ack-identity-changed")
            else:
                self._acks[item_id] = previous
            if len(self._acks) > self.commits:
                self.fail("live-input-unexpected-commit-ack")
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

    async def wait_appended(self) -> None:
        """Wait through held I/O and the SDK's partial 50-ms tail."""
        await self._wait(lambda: self.appended_samples == self.expected_samples)

    async def wait_completed(self) -> frozenset[str]:
        """Wait for every commit ACK and every item, including empty completions."""
        await self._wait(
            lambda: (
                len(self._acks) == self.commits
                and self._acks.keys() <= self._completed.keys()
            )
        )
        if self._completed.keys() != self._acks.keys():
            raise InputDrainError("live-input-unacknowledged-final")
        # ACK order need not match final arrival order. Validate the predecessor
        # chain as a set, without assuming the server echoes client event IDs.
        successors: dict[str | None, str] = {}
        for item, previous in self._acks.items():
            if previous in successors:
                raise InputDrainError("live-input-ack-chain-inconsistent")
            successors[previous] = item
        predecessor = None
        visited = set()
        while predecessor in successors:
            predecessor = successors[predecessor]
            if predecessor in visited:
                raise InputDrainError("live-input-ack-chain-inconsistent")
            visited.add(predecessor)
        if visited != self._acks.keys():
            raise InputDrainError("live-input-ack-chain-inconsistent")
        return frozenset(key for key, nonempty in self._completed.items() if nonempty)

    async def _wait(self, ready: Callable[[], bool]) -> None:
        while True:
            self.check()
            if ready():
                return
            self._changed.clear()
            await self._changed.wait()
