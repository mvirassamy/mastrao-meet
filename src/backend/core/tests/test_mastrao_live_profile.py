"""Profile substitutions cannot reach the physical worker boundary."""

import pytest

from core.mastrao_live_profile import validate_live_profile
from core.mastrao_recording_contract import RecordingContractRefused

# Pinned immutable selections must remain readable after later admin changes.
PROFILES = [
    (
        "openai-live-fr-en-v1",
        "3e427e95b56ac2f2c43dfcbc6e1d6a88b1f4e4ef85484e506a166919edac7bf7",
    ),
    (
        "deepgram-live-multi-v1",
        "854db1101119b3e2749dd87e5edc5608a783aa70d87c5a4d757892fc938b7963",
    ),
    (
        "mistral-live-multi-v1",
        "ee3a31d7f8f4f54c71f6b1c30c57c065af090c8d872c183bb43819047c8e5005",
    ),
]


@pytest.mark.parametrize("reference,digest", PROFILES)
def test_profile_is_strict_and_preserves_admin_revision(reference, digest):
    selection = {
        "version": 1,
        "revision": 7,
        "profile_ref": reference,
        "profile_version": 1,
        "config_digest": digest,
    }
    assert validate_live_profile(selection) is selection
    for replacement in (
        {"version": True},
        {"revision": True},
        {"revision": 0},
        {"revision": 2**53},
        {"profile_version": 2},
        {"profile_ref": []},
        {"profile_ref": "unknown"},
        {"config_digest": "a" * 64},
        {"model_ref": "another-model"},
        {"configuration": {"language": "fr"}},
    ):
        with pytest.raises(RecordingContractRefused) as error:
            validate_live_profile({**selection, **replacement})
        assert error.value.status == 503


@pytest.mark.parametrize("reference,digest", PROFILES)
def test_another_approved_profile_digest_cannot_substitute_this_selection(
    reference, digest
):
    for other_reference, other_digest in PROFILES:
        if other_reference == reference:
            continue
        with pytest.raises(RecordingContractRefused) as error:
            validate_live_profile(
                {
                    "version": 1,
                    "revision": 7,
                    "profile_ref": reference,
                    "profile_version": 1,
                    "config_digest": other_digest,
                }
            )
        assert error.value.status == 503
