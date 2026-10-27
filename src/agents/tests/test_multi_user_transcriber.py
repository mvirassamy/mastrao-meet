"""Provider-selection tests for the multi-user transcription worker."""

import unittest
from unittest.mock import Mock, patch

import multi_user_transcriber


class LiveProviderSelectionTests(unittest.TestCase):
    """Keep provider selection explicit and fail closed."""

    def test_mistral_live_does_not_require_silero(self):
        """Create the measured Mistral path without an unrelated VAD dependency."""
        ctx = object()
        with patch.object(
            multi_user_transcriber,
            "STT_PROVIDER",
            multi_user_transcriber.MISTRAL_LIVE_PROVIDER,
        ):
            with patch.object(
                multi_user_transcriber,
                "LiveTranscriber",
                return_value=Mock(),
            ) as transcriber:
                multi_user_transcriber.create_live_transcriber(ctx)

        self.assertIs(transcriber.call_args.args[0], ctx)
        self.assertFalse(transcriber.call_args.kwargs["requires_vad"])
        self.assertIs(
            transcriber.call_args.kwargs["provider_factory"],
            multi_user_transcriber.create_mistral_live_stt,
        )

    def test_openai_live_keeps_silero_requirement(self):
        """Preserve the existing rollback provider contract."""
        ctx = object()
        with patch.object(
            multi_user_transcriber,
            "STT_PROVIDER",
            multi_user_transcriber.OPENAI_LIVE_PROVIDER,
        ):
            with patch.object(
                multi_user_transcriber,
                "LiveTranscriber",
                return_value=Mock(),
            ) as transcriber:
                multi_user_transcriber.create_live_transcriber(ctx)

        self.assertIs(transcriber.call_args.args[0], ctx)
        self.assertTrue(transcriber.call_args.kwargs["requires_vad"])


if __name__ == "__main__":
    unittest.main()
