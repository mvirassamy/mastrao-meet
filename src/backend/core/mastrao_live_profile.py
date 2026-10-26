"""Approved live profiles at the Meet/Core boundary; no admin selection here."""

from core.mastrao_recording_contract import RecordingContractRefused

PROFILE_DIGESTS = {
    "openai-live-fr-en-v1": "3e427e95b56ac2f2c43dfcbc6e1d6a88b1f4e4ef85484e506a166919edac7bf7",
    "deepgram-live-multi-v1": "854db1101119b3e2749dd87e5edc5608a783aa70d87c5a4d757892fc938b7963",
    "mistral-live-multi-v1": "ee3a31d7f8f4f54c71f6b1c30c57c065af090c8d872c183bb43819047c8e5005",
}
SELECTION_FIELDS = {
    "version",
    "revision",
    "profile_ref",
    "profile_version",
    "config_digest",
}


def validate_live_profile(selection):
    """Require the exact immutable Core selection before forwarding to a worker."""
    # Keep the immutable schema predicates together; exact integers reject bool.
    # pylint: disable=too-many-boolean-expressions,unidiomatic-typecheck
    if not isinstance(selection, dict) or set(selection) != SELECTION_FIELDS:
        raise RecordingContractRefused(status=503)
    reference = selection["profile_ref"]
    if (
        type(selection["version"]) is not int
        or selection["version"] != 1
        or type(selection["profile_version"]) is not int
        or selection["profile_version"] != 1
        or type(selection["revision"]) is not int
        or not 1 <= selection["revision"] <= 2**53 - 1
        or not isinstance(reference, str)
        or reference not in PROFILE_DIGESTS
        or selection["config_digest"] != PROFILE_DIGESTS[reference]
    ):
        raise RecordingContractRefused(status=503)
    return selection
