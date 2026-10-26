"""Native Deepgram span and terminal evidence, without invented commit ACKs."""

import asyncio
import hashlib
import json
import math
from collections.abc import Callable
from decimal import Decimal

from live_input_drain import MAX_DRAIN_ITEMS, InputDrainError

MAX_NATIVE_FRAME_BYTES = 32 * 1024
MAX_NATIVE_REQUEST_LENGTH = 160
NORMAL_CLOSE_CODE = 1000


def _number(value: object) -> bool:
    return type(value) in (int, float) and 0 <= value < 2**53 and math.isfinite(value)


class DeepgramDrain:
    """Count successful socket PCM writes and retain one terminal Metadata frame."""

    def __init__(self, failed: Callable[[str], None]):
        """Start one connection's bounded native span and terminal ledger."""
        self.appended_samples = 0
        self.expected_samples: int | None = None
        self.final_items: set[str] = set()
        self.request_id: str | None = None
        self.metadata: dict | None = None
        self.finalize_sent = False
        self.close_started = False
        self.close_sent = False
        self.peer_closed = False
        self.terminal = asyncio.Event()
        self._failed = failed
        self._error: InputDrainError | None = None

    def fail(self, reason: str) -> None:
        """Latch uncertainty and synchronously fence the owning track."""
        if self._error is None:
            self._error = InputDrainError(reason)
            self._failed(reason)
        self.terminal.set()

    def check(self) -> None:
        """Raise the first retained failure for every waiter."""
        if self._error is not None:
            raise self._error

    def seal(self, expected_samples: int) -> bool:
        """Return true only for the first planned EOF with a valid PCM count."""
        self.check()
        if (
            type(expected_samples) is not int
            or expected_samples < self.appended_samples
        ):
            raise InputDrainError("deepgram-input-seal-count-invalid")
        if self.expected_samples is not None:
            if expected_samples != self.expected_samples:
                raise InputDrainError("deepgram-input-seal-count-changed")
            return False
        self.expected_samples = expected_samples
        return True

    def appended(self, samples: int) -> None:
        """Track confirmed local socket sends without inventing server ACKs."""
        self.appended_samples += samples
        if (
            self.expected_samples is not None
            and self.appended_samples > self.expected_samples
        ):
            self.fail("deepgram-input-count-mismatch")
        self.check()

    def _bind_request(self, value: object) -> None:
        if (
            not isinstance(value, str)
            or not 0 < len(value) <= MAX_NATIVE_REQUEST_LENGTH
        ):
            raise InputDrainError("deepgram-native-request-invalid")
        if self.request_id is not None and self.request_id != value:
            raise InputDrainError("deepgram-native-request-changed")
        self.request_id = value

    def observe(self, data: dict) -> dict | None:
        """Derive an application item reference from native request/channel/span."""
        self.check()
        try:
            if not isinstance(data, dict):
                raise InputDrainError("deepgram-native-event-invalid")
            encoded = json.dumps(data, allow_nan=False, separators=(",", ":"))
            if len(encoded.encode("utf-8")) > MAX_NATIVE_FRAME_BYTES:
                raise InputDrainError("deepgram-native-frame-capacity-exceeded")
            kind = data.get("type")
            if kind == "Metadata":
                self._observe_metadata(data)
                return None
            if kind == "Error":
                raise InputDrainError("deepgram-native-provider-error")
            if kind != "Results":
                return None
            if self.metadata is not None:
                raise InputDrainError("deepgram-results-after-terminal-metadata")
            self._bind_request(data["metadata"]["request_id"])
            if (
                data["channel_index"] != [0, 1]
                or any(type(value) is not int for value in data["channel_index"])
                or not _number(data["start"])
                or not _number(data["duration"])
                or type(data["is_final"]) is not bool
            ):
                raise InputDrainError("deepgram-native-span-invalid")
            span = {
                "request_id": self.request_id,
                "channel_index": data["channel_index"],
                "start": "0",
            }
            if data["start"] != 0:
                span["start"] = str(Decimal(str(data["start"])).normalize())
            # Listen v1 exposes a segment start, not a native item UUID. Use
            # that anchor within this request/channel. Duration and word end
            # remain revision content; neither is an ACK or stable identity.
            identity = json.dumps(span, sort_keys=True, separators=(",", ":"))
            item_ref = "dg_" + hashlib.sha256(identity.encode()).hexdigest()
            alternatives = data["channel"]["alternatives"]
            if (
                data["is_final"]
                and alternatives
                and alternatives[0]["transcript"].strip()
            ):
                self.final_items.add(item_ref)
                if len(self.final_items) > MAX_DRAIN_ITEMS:
                    raise InputDrainError("deepgram-native-item-capacity-exceeded")
            return {
                "item_ref": item_ref,
                "provider": "deepgram",
                "span_identity": span,
                "results": json.loads(encoded),
            }
        except (KeyError, TypeError, ValueError, InputDrainError) as error:
            reason = "deepgram-native-event-invalid"
            if isinstance(error, InputDrainError):
                reason = str(error)
            self.fail(reason)
            self.check()
        return None

    def _observe_metadata(self, data: dict) -> None:
        if (
            self.expected_samples is None
            or not self.finalize_sent
            or not self.close_started
        ):
            raise InputDrainError("deepgram-metadata-outside-graceful-close")
        self._bind_request(data.get("request_id"))
        if (
            not _number(data.get("duration"))
            or type(data.get("channels")) is not int
            or data["channels"] != 1
        ):
            raise InputDrainError("deepgram-terminal-metadata-invalid")
        if self.metadata is not None and self.metadata != data:
            raise InputDrainError("deepgram-terminal-metadata-changed")
        self.metadata = json.loads(json.dumps(data))
        self.update_terminal()

    def observe_close(self, code: int | None) -> None:
        """Require terminal Metadata and a normal peer close on a sealed leg."""
        if (
            self.expected_samples is None
            or code != NORMAL_CLOSE_CODE
            or self.metadata is None
        ):
            self.fail("deepgram-terminal-close-unproved")
        self.peer_closed = True
        self.update_terminal()
        self.check()

    def update_terminal(self) -> None:
        """Wake the SDK join only after tail writes, controls, Metadata and close."""
        if (
            self.expected_samples is not None
            and self.appended_samples == self.expected_samples
            and self.finalize_sent
            and self.close_sent
            and self.metadata is not None
            and self.peer_closed
        ):
            self.terminal.set()
