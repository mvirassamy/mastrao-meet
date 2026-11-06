"""Provider-independent transcript events emitted by live ASR adapters."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class LiveTranscriptEventKind(StrEnum):
    """Canonical lifecycle states for one logical transcript unit."""

    APPEND = "append"
    SNAPSHOT = "snapshot"
    COMPLETE = "complete"
    FAILED = "failed"


@dataclass(frozen=True)
class LiveTranscriptEvent:
    """One provider-neutral transcript event before source publication."""

    kind: LiveTranscriptEventKind
    logical_unit_id: str
    local_sequence: int
    text: str
    language: str | None = None
    start_time_ms: int | None = None
    end_time_ms: int | None = None

    def __post_init__(self) -> None:
        """Reject incomplete canonical events at the adapter boundary."""
        if not self.logical_unit_id or self.local_sequence <= 0:
            raise ValueError("live transcript unit and sequence are required")
        if self.kind is not LiveTranscriptEventKind.FAILED and not self.text.strip():
            raise ValueError("live transcript text is required")
