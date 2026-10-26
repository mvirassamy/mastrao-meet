"""The concrete canonical STT sessions consumed by TrackTranscriber."""

from collections.abc import Callable
from typing import Protocol

from livekit.agents import stt

from live_deepgram import AuthorizedDeepgram
from live_input_drain import InputDrainError
from live_mistral import AuthorizedMistral
from live_provider_profile import LiveProfileSelection
from live_single_connection import AuthorizedOpenAI


class CanonicalProviderSession(Protocol):
    """Only progress, fencing and terminal evidence beyond the shared STT API."""

    @property
    def appended_samples(self) -> int:
        """Count successful local socket PCM writes."""
        ...

    def stream(self) -> stt.RecognizeStream:
        """Acquire one SDK stream."""
        ...

    def fence_audio(self) -> None:
        """Synchronously cancel blocked and queued writes."""
        ...

    def seal_input(self, stream: stt.RecognizeStream, expected_samples: int) -> None:
        """End a planned leg with its exact admitted sample count."""
        ...

    async def drain_input(self) -> frozenset[str]:
        """Wait for bounded terminal evidence and return final application refs."""
        ...

    async def input_window(self, item_id: str) -> tuple[int, int] | None:
        """Return proved PCM bounds, or leave timing unavailable."""
        ...

    def terminal_drain_complete(self, stream: stt.RecognizeStream) -> bool:
        """Accept terminal SDK completion only with provider-specific evidence."""
        ...

    async def aclose(self) -> None:
        """Join SDK and physical socket cleanup."""
        ...


def application_item_ref(event: stt.SpeechEvent) -> str:
    """Deepgram has native spans; OpenAI already supplies per-item request IDs."""
    if event.alternatives:
        metadata = event.alternatives[0].metadata
        if metadata is not None and "canonical_item_ref" in metadata:
            return metadata["canonical_item_ref"]
    return event.request_id


def create_canonical_provider(
    selection: LiveProfileSelection,
    *,
    can_send: Callable[[], bool],
    reconnect_refused: Callable[[], None],
    failed: Callable[[str], None],
    vad: object,
) -> CanonicalProviderSession:
    """Construct exactly the sealed profile; only credentials use SDK mechanisms."""
    selection.validate()
    if not can_send():
        raise InputDrainError("canonical-provider-open-not-authorized")
    match selection.profile_ref:
        case "openai-live-fr-en-v1":
            if vad is None:
                raise InputDrainError("canonical-openai-vad-unavailable")
            return AuthorizedOpenAI(
                can_send=can_send,
                reconnect_refused=reconnect_refused,
                failed=failed,
                language=["fr", "en"],
                model="gpt-live-transcribe",
                use_realtime=True,
                vad=vad,
            )
        case "deepgram-live-multi-v1":
            return AuthorizedDeepgram(
                can_send=can_send,
                reconnect_refused=reconnect_refused,
                failed=failed,
                model="nova-3",
                language="multi",
                sample_rate=24000,
                interim_results=True,
                punctuate=True,
                smart_format=False,
                endpointing_ms=25,
                filler_words=True,
                mip_opt_out=True,
            )
        case "mistral-live-multi-v1":
            return AuthorizedMistral(
                can_send=can_send,
                reconnect_refused=reconnect_refused,
                failed=failed,
                model="voxtral-mini-transcribe-realtime-3",
                language=None,
                sample_rate=24000,
                target_streaming_delay_ms=480,
            )
        case _:
            raise InputDrainError("canonical-provider-profile-unavailable")
