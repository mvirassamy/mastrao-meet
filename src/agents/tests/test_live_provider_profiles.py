"""An authoritative closed profile is required before any worker claim."""

import unittest
from dataclasses import FrozenInstanceError
from unittest.mock import patch

from test_live_send_control import Gateway, authority

from live_input_drain import InputDrainError
from live_provider_profile import LiveProfileSelection
from live_provider_session import create_canonical_provider
from live_send_control import CONTROL_VERSION, ControlError

OPENAI_SELECTION = {
    "version": 1,
    "revision": 7,
    "profile_ref": "openai-live-fr-en-v1",
    "profile_version": 1,
    "config_digest": "3e427e95b56ac2f2c43dfcbc6e1d6a88b1f4e4ef85484e506a166919edac7bf7",
}
DEEPGRAM_SELECTION = {
    **OPENAI_SELECTION,
    "profile_ref": "deepgram-live-multi-v1",
    "config_digest": "854db1101119b3e2749dd87e5edc5608a783aa70d87c5a4d757892fc938b7963",
}

MISTRAL_SELECTION = {
    **OPENAI_SELECTION,
    "profile_ref": "mistral-live-multi-v1",
    "config_digest": "ee3a31d7f8f4f54c71f6b1c30c57c065af090c8d872c183bb43819047c8e5005",
}


class ProfileAuthorityTests(unittest.IsolatedAsyncioTestCase):
    """Selection is immutable across claims, renewals, and physical rotations."""

    async def test_prepare3_selection_is_copied_and_frozen_across_rotation(self):
        """Prepare3 selection is copied and frozen across rotation."""
        for selection in (OPENAI_SELECTION, DEEPGRAM_SELECTION, MISTRAL_SELECTION):
            with self.subTest(profile=selection["profile_ref"]):
                gateway = Gateway()
                snapshot = dict(selection)
                gateway.responses["prepare"] = {"version": 3, "live_profile": snapshot}
                lease = authority(gateway)
                self.assertTrue(await lease.claim())
                selected = lease.live_profile
                self.assertEqual(selected.profile_ref, selection["profile_ref"])
                snapshot["revision"] = 999
                self.assertEqual(selected.revision, 7)
                with self.assertRaises(FrozenInstanceError):
                    selected.revision = 8
                await lease.rotate("next_leg_reference_0001", 2400)
                await lease._send("renew")
                self.assertIs(lease.live_profile, selected)
                self.assertTrue(
                    all(body["version"] == CONTROL_VERSION for body in gateway.requests)
                )

    async def test_old_or_tampered_prepare_fails_before_claim(self):
        """Old or tampered prepare fails before claim."""
        bad = [
            {"version": 2},
            {"live_profile": None},
            *(
                {"live_profile": {**OPENAI_SELECTION, **change}}
                for change in (
                    {"revision": True},
                    {"revision": 0},
                    {"revision": 2**53},
                    {"version": True},
                    {"profile_version": True},
                    {"profile_ref": "unknown"},
                    {"config_digest": "0" * 64},
                    {"language": ["en"]},
                )
            ),
        ]
        for override in bad:
            with self.subTest(override=override):
                gateway = Gateway()
                gateway.responses["prepare"] = {
                    "version": 3,
                    "live_profile": dict(OPENAI_SELECTION),
                    **override,
                }
                with self.assertRaises(ControlError):
                    await authority(gateway).claim()
                self.assertFalse(gateway.entered["claim"].is_set())


class ProviderFactoryTests(unittest.TestCase):
    """Each admitted profile constructs only its provider with the frozen options."""

    def test_three_profiles_construct_only_the_selected_provider(self):
        """Construct the admitted provider without touching other credentials."""
        cases = (
            (OPENAI_SELECTION, "openai", "gpt-live-transcribe", ["fr", "en"]),
            (DEEPGRAM_SELECTION, "deepgram", "nova-3", "multi"),
            (MISTRAL_SELECTION, "mistral", "voxtral-mini-transcribe-realtime-3", None),
        )
        for selection, selected, model, language in cases:
            with (
                self.subTest(provider=selected),
                patch("live_provider_session.AuthorizedOpenAI") as openai,
                patch("live_provider_session.AuthorizedDeepgram") as deepgram,
                patch("live_provider_session.AuthorizedMistral") as mistral,
            ):
                constructors = {
                    "openai": openai,
                    "deepgram": deepgram,
                    "mistral": mistral,
                }
                result = create_canonical_provider(
                    LiveProfileSelection.parse(selection),
                    can_send=lambda: True,
                    reconnect_refused=lambda: None,
                    failed=lambda _reason: None,
                    vad=object(),
                )
                for name, constructor in constructors.items():
                    if name == selected:
                        constructor.assert_called_once()
                    else:
                        constructor.assert_not_called()
                self.assertIs(result, constructors[selected].return_value)
                options = constructors[selected].call_args.kwargs
                self.assertEqual(options["model"], model)
                self.assertEqual(options["language"], language)

    def test_mistral_is_never_constructed_before_send_authority(self):
        """The third option cannot acquire a credentialed transport early."""
        with patch("live_provider_session.AuthorizedMistral") as mistral:
            with self.assertRaises(InputDrainError):
                create_canonical_provider(
                    LiveProfileSelection.parse(MISTRAL_SELECTION),
                    can_send=lambda: False,
                    reconnect_refused=lambda: None,
                    failed=lambda _reason: None,
                    vad=None,
                )
            mistral.assert_not_called()
