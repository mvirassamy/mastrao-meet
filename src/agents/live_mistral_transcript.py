"""Bound Mistral's append-only final text to application shards, without clocks."""

import hashlib
from collections.abc import Callable

from livekit.agents import stt

from live_delivery_codec import validated_language
from live_input_drain import InputDrainError

MAX_ITEM_UTF16_UNITS = 4000
MAX_TRANSCRIPT_BYTES = 1024 * 1024
MAX_SHARDS = 512
MAX_REVISIONS_PER_SHARD = 2048
MAX_REVISIONS_PER_LEG = 16000
MAX_BMP_CODEPOINT = 0xFFFF


class MistralTranscript:
    """Use native request identity plus local ordinals, never invented native IDs."""

    def __init__(self, request_id: str, emit: Callable[[stt.SpeechEvent], None]):
        """Retain bounded exact text and revision counts for one native request."""
        self._request_id = request_id
        self._emit = emit
        self._chunks: list[str] = [""]
        self._tail_units = 0
        self._bytes = 0
        self._finished = False
        self._language = ""
        self._last_emitted: dict[int, tuple[str, str]] = {}
        self._revisions: dict[int, int] = {}
        self._total_revisions = 0

    def append(self, text: object) -> None:
        """Revise only the active shard; preserve Unicode and delta whitespace."""
        if self._finished or not isinstance(text, str):
            raise InputDrainError("mistral-delta-invalid")
        self._bytes += len(text.encode("utf-8"))
        if self._bytes > MAX_TRANSCRIPT_BYTES:
            raise InputDrainError("mistral-transcript-capacity-exceeded")
        cursor = 0
        while cursor < len(text):
            available = MAX_ITEM_UTF16_UNITS - self._tail_units
            if available < 1 + (ord(text[cursor]) > MAX_BMP_CODEPOINT):
                self._open_shard()
                continue
            start = cursor
            used = 0
            while cursor < len(text):
                width = 1 + (ord(text[cursor]) > MAX_BMP_CODEPOINT)
                if used + width > available:
                    break
                used += width
                cursor += 1
            self._chunks[-1] += text[start:cursor]
            self._tail_units += used
            self._emit_chunk(len(self._chunks) - 1, language=self._language)

    def set_language(self, language: object) -> None:
        """Apply the realtime audio_language field to the same bounded shards."""
        if self._finished or validated_language(language) is None:
            raise InputDrainError("mistral-language-event-invalid")
        self._language = language
        for index in range(len(self._chunks)):
            self._emit_chunk(index, language=language)

    def _open_shard(self) -> None:
        if len(self._chunks) >= MAX_SHARDS:
            raise InputDrainError("mistral-shard-capacity-exceeded")
        tail = self._chunks[-1]
        if not tail.strip():
            raise InputDrainError("mistral-whitespace-item-unrepresentable")
        # A whitespace-only delta can wait here for the next nonspace text.
        # Never repartition an already published final: shrinking one item
        # and moving its suffix to another cannot be published atomically.
        self._chunks.append("")
        self._tail_units = 0

    def finish(self, text: object, language: object) -> frozenset[str]:
        """Verify exact prefix and add only a missing authoritative done suffix."""
        if self._finished or not isinstance(text, str):
            raise InputDrainError("mistral-done-invalid")
        if language is None:
            language = ""
        if not isinstance(language, str) or (
            language and validated_language(language) is None
        ):
            raise InputDrainError("mistral-done-language-invalid")
        prefix = "".join(self._chunks)
        if not text.startswith(prefix):
            raise InputDrainError("mistral-done-text-incompatible")
        self.append(text[len(prefix) :])
        if any(chunk and not chunk.strip() for chunk in self._chunks):
            raise InputDrainError("mistral-whitespace-item-unrepresentable")
        self._finished = True
        refs = set()
        for index, chunk in enumerate(self._chunks):
            if chunk:
                self._emit_chunk(index, language=language)
                refs.add(self._ref(index))
        return frozenset(refs)

    def _ref(self, index: int) -> str:
        identity = f"{self._request_id}\n{index}".encode("utf-8")
        return "mi_" + hashlib.sha256(identity).hexdigest()

    def _emit_chunk(self, index: int, *, language: str) -> None:
        text = self._chunks[index]
        content = (text, language)
        if not text.strip() or self._last_emitted.get(index) == content:
            return
        revisions = self._revisions.get(index, 0) + 1
        if (
            revisions > MAX_REVISIONS_PER_SHARD
            or self._total_revisions >= MAX_REVISIONS_PER_LEG
        ):
            raise InputDrainError("mistral-revision-capacity-exceeded")
        item_ref = self._ref(index)
        self._emit(
            stt.SpeechEvent(
                type=stt.SpeechEventType.FINAL_TRANSCRIPT,
                request_id=self._request_id,
                alternatives=[
                    stt.SpeechData(
                        text=text,
                        language=language,
                        metadata={
                            "canonical_item_ref": item_ref,
                            "native_provenance": {
                                "provider": "mistral",
                                "request_id": self._request_id,
                                "identity_scope": "application-request-shard",
                                "application_shard_ordinal": index,
                            },
                        },
                    )
                ],
            )
        )
        self._last_emitted[index] = content
        self._revisions[index] = revisions
        self._total_revisions += 1
