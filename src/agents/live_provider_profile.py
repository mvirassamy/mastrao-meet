"""Closed worker profiles matching the authoritative meeting runtime contract."""

from dataclasses import dataclass

from live_model_evidence import ProviderName

PREPARE_VERSION = 3
OPENAI_PROFILE = "openai-live-fr-en-v1"
DEEPGRAM_PROFILE = "deepgram-live-multi-v1"
MISTRAL_PROFILE = "mistral-live-multi-v1"
OPENAI_CONFIG_DIGEST = (
    "3e427e95b56ac2f2c43dfcbc6e1d6a88b1f4e4ef85484e506a166919edac7bf7"
)
DEEPGRAM_CONFIG_DIGEST = (
    "854db1101119b3e2749dd87e5edc5608a783aa70d87c5a4d757892fc938b7963"
)

MISTRAL_CONFIG_DIGEST = (
    "ee3a31d7f8f4f54c71f6b1c30c57c065af090c8d872c183bb43819047c8e5005"
)


@dataclass(frozen=True)
class LiveProfileSelection:
    """A copied immutable policy snapshot, never an audio-send capability."""

    version: int
    revision: int
    profile_ref: str
    profile_version: int
    config_digest: str

    @property
    def provider(self) -> ProviderName:
        """Bind delivery identity to the admitted closed profile."""
        match self.profile_ref:
            case "openai-live-fr-en-v1":
                return "openai"
            case "deepgram-live-multi-v1":
                return "deepgram"
            case "mistral-live-multi-v1":
                return "mistral"
            case _:
                raise ValueError("unsupported live provider profile")

    @classmethod
    def parse(cls, value: object) -> "LiveProfileSelection":
        """Reject mixed wire versions, arbitrary options and unknown digests."""
        if not isinstance(value, dict) or set(value) != {
            "version",
            "revision",
            "profile_ref",
            "profile_version",
            "config_digest",
        }:
            raise ValueError("invalid live profile selection")
        selection = cls(**value)
        selection.validate()
        return selection

    def validate(self) -> None:
        """Revalidate exact runtime support before each physical acquisition."""
        if (
            type(self.version) is not int
            or self.version != 1
            or type(self.profile_version) is not int
            or self.profile_version != 1
            or type(self.revision) is not int
            or not 0 < self.revision < 2**53
        ):
            raise ValueError("invalid live profile version or revision")
        match self.profile_ref:
            case "openai-live-fr-en-v1":
                expected = OPENAI_CONFIG_DIGEST
            case "deepgram-live-multi-v1":
                expected = DEEPGRAM_CONFIG_DIGEST
            case "mistral-live-multi-v1":
                expected = MISTRAL_CONFIG_DIGEST
            case _:
                raise ValueError("unsupported live provider profile")
        if self.config_digest != expected:
            raise ValueError("live profile configuration mismatch")
