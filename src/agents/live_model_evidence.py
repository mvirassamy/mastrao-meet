"""Bound model facts supplied by native responses, independently of transcript text."""

from typing import Literal, TypedDict

ProviderName = Literal["openai", "mistral", "deepgram"]
ModelOrigin = Literal["session", "result", "terminal"]
MAX_MODELS = 8
MAX_MODEL_UNITS = 160
MODEL_FIELDS = ("origin", "model_ref", "name", "version", "architecture")
# Match ECMAScript trim used by Core, without altering the supplied value.
MODEL_WHITESPACE = (
    " \t\n\r\v\f\u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006"
    "\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000\ufeff"
)


class ModelRecord(TypedDict):
    """One exact model observation with its native source category."""

    origin: ModelOrigin
    model_ref: str | None
    name: str | None
    version: str | None
    architecture: str | None


class ModelEvidence(TypedDict):
    """The complete bounded projection supplied to durable delivery."""

    provider: ProviderName
    models: list[ModelRecord]
    truncated: bool


def valid_model_string(value: object) -> bool:
    """Validate exact Unicode without shortening or normalizing provider facts."""
    if not isinstance(value, str) or not 0 < len(value) <= MAX_MODEL_UNITS:
        return False
    try:
        return (
            bool(value.strip(MODEL_WHITESPACE))
            and len(value.encode("utf-16-le")) // 2 <= MAX_MODEL_UNITS
        )
    except UnicodeError:
        return False


class ModelEvidenceCollector:
    """Keep eight distinct observations in received order for one physical leg."""

    def __init__(self, provider: ProviderName):
        """Start empty for a newly acquired leg, never from requested options."""
        self._provider = provider
        self._models: list[ModelRecord] = []
        self._truncated = False

    @property
    def snapshot(self) -> ModelEvidence:
        """Copy only the allowlist; never expose native response containers."""
        return {
            "provider": self._provider,
            "models": [record.copy() for record in self._models],
            "truncated": self._truncated,
        }

    def _field(self, value: object) -> str | None:
        if value is None:
            return None
        if valid_model_string(value):
            return value
        self._truncated = True
        return None

    def _add(
        self,
        origin: ModelOrigin,
        model_ref: object = None,
        name: object = None,
        version: object = None,
        architecture: object = None,
    ) -> None:
        record: ModelRecord = {
            "origin": origin,
            "model_ref": self._field(model_ref),
            "name": self._field(name),
            "version": self._field(version),
            "architecture": self._field(architecture),
        }
        if not any(record[field] is not None for field in MODEL_FIELDS[1:]):
            return
        if record in self._models:
            return
        if len(self._models) == MAX_MODELS:
            self._truncated = True
            return
        self._models.append(record)

    def _object(self, value: object) -> dict:
        if value is None:
            return {}
        if isinstance(value, dict):
            return value
        self._truncated = True
        return {}

    def observe(self, event: dict) -> None:
        """Extract the concrete provider's documented model fields only."""
        match self._provider:
            case "openai":
                if event.get("type") in {"session.created", "session.updated"}:
                    session = self._object(event.get("session"))
                    audio = self._object(session.get("audio"))
                    audio_input = self._object(audio.get("input"))
                    transcription = self._object(audio_input.get("transcription"))
                    self._add("session", model_ref=transcription.get("model"))
            case "mistral":
                if event.get("type") in {"session.created", "session.updated"}:
                    session = self._object(event.get("session"))
                    self._add("session", model_ref=session.get("model"))
                elif event.get("type") == "transcription.done":
                    self._add("terminal", model_ref=event.get("model"))
            case "deepgram":
                self._observe_deepgram(event)

    def _observe_deepgram(self, event: dict) -> None:
        if event.get("type") == "Results":
            metadata = self._object(event.get("metadata"))
            info = self._object(metadata.get("model_info"))
            self._add(
                "result",
                metadata.get("model_uuid"),
                info.get("name"),
                info.get("version"),
                info.get("arch"),
            )
        elif event.get("type") == "Metadata":
            models = event.get("models", [])
            info = self._object(event.get("model_info"))
            if not isinstance(models, list):
                self._truncated = True
                models = []
            for model_ref in models:
                details = {}
                if isinstance(model_ref, str):
                    details = self._object(info.get(model_ref))
                self._add(
                    "terminal",
                    model_ref,
                    details.get("name"),
                    details.get("version"),
                    details.get("arch"),
                )
            for model_ref, value in info.items():
                details = self._object(value)
                self._add(
                    "terminal",
                    model_ref,
                    details.get("name"),
                    details.get("version"),
                    details.get("arch"),
                )
