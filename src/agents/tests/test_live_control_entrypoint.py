"""Guard the real worker entrypoint before any legacy provider or room join."""

import importlib
import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from live_send_control import ControlError


class ControlEntrypointTests(unittest.IsolatedAsyncioTestCase):
    """A canonical dispatch cannot bypass Core through the legacy provider switch."""

    def setUp(self):
        """Import the actual entrypoint without loading unrelated local secrets."""
        with patch("dotenv.load_dotenv"):
            self.worker = importlib.import_module("multi_user_transcriber")
        self.url = "http://backend:8000/internal/v1/meetings/transcripts/live/control"
        self.enterContext(
            patch.dict(os.environ, {"LIVE_TRANSCRIPT_CONTROL_URL": self.url})
        )
        self.control = {
            "version": 2,
            "url": self.url,
            "capability": "payload:timestamp:signature",
        }
        self.live = self.enterContext(patch.object(self.worker, "LiveTranscriber"))
        self.legacy = self.enterContext(
            patch.object(self.worker, "MultiUserTranscriber")
        )
        self.session = self.enterContext(patch.object(self.worker, "AgentSession"))
        self.deepgram = self.enterContext(patch.object(self.worker.deepgram, "STT"))
        self.kyutai = self.enterContext(patch.object(self.worker.kyutai, "STT"))
        self.stt = self.enterContext(patch.object(self.worker, "create_stt_provider"))
        self.enterContext(patch.object(self.worker, "set_job_context"))
        self.ctx = SimpleNamespace(
            room=SimpleNamespace(name="fixture-room", remote_participants={}),
            job=SimpleNamespace(id="fixture-job", metadata=""),
            connect=AsyncMock(),
            add_shutdown_callback=MagicMock(),
        )

    def assert_no_provider_or_join(self):
        """Require denial before constructing a transcriber, STT, or AgentSession."""
        for boundary in (
            self.live,
            self.legacy,
            self.session,
            self.deepgram,
            self.kyutai,
            self.stt,
        ):
            boundary.assert_not_called()
        self.ctx.connect.assert_not_awaited()
        self.ctx.add_shutdown_callback.assert_not_called()

    async def test_canonical_jobs_ignore_legacy_provider_switch_and_metadata(self):
        """Only control preparation selects the profile for canonical jobs."""
        self.ctx.job.metadata = json.dumps(
            {"liveControl": self.control, "provider": "controlled-live"}
        )
        for provider in ("deepgram", "kyutai", "", "unknown"):
            with self.subTest(provider=provider):
                with patch.object(self.worker, "STT_PROVIDER", provider):
                    await self.worker.entrypoint(self.ctx)
                self.live.assert_called_once_with(self.ctx)
                self.live.return_value.start_existing_tracks.assert_called_once()
                self.legacy.assert_not_called()
                self.stt.assert_not_called()
                self.live.reset_mock()

    async def test_malformed_control_or_unconfigured_endpoint_never_route_to_legacy(
        self,
    ):
        """Even invalid liveControl metadata forbids the normal default route."""
        invalid = [
            "{",
            '{"liveControl":null}',
            json.dumps(
                {"liveControl": {**self.control, "url": "http://example.com/control"}}
            ),
        ]
        for metadata in invalid:
            self.ctx.job.metadata = metadata
            with self.subTest(metadata=metadata):
                with patch.object(self.worker, "STT_PROVIDER", "deepgram"):
                    with self.assertRaises(ControlError):
                        await self.worker.entrypoint(self.ctx)
                self.assert_no_provider_or_join()
        self.ctx.job.metadata = json.dumps({"liveControl": self.control})
        with patch.dict(os.environ, {"LIVE_TRANSCRIPT_CONTROL_URL": ""}):
            with patch.object(self.worker, "STT_PROVIDER", "openai-live"):
                with self.assertRaises(ControlError):
                    await self.worker.entrypoint(self.ctx)
        self.assert_no_provider_or_join()

    async def test_canonical_valid_job_routes_to_live_worker(self):
        """The true entrypoint forwards metadata only to the controlled live manager."""
        self.ctx.job.metadata = json.dumps({"liveControl": self.control})
        with patch.object(self.worker, "STT_PROVIDER", "openai-live"):
            await self.worker.entrypoint(self.ctx)
        self.live.assert_called_once_with(self.ctx)
        self.live.return_value.start.assert_called_once()
        self.live.return_value.start_existing_tracks.assert_called_once()
        self.ctx.connect.assert_awaited_once()
        self.legacy.assert_not_called()
        self.stt.assert_not_called()

    async def test_controlled_marker_without_capability_never_reaches_legacy(self):
        """A disabled endpoint or legacy flag cannot turn canonical intent into ASR."""
        self.ctx.job.metadata = json.dumps({"provider": "controlled-live"})
        for provider in ("deepgram", "kyutai", "openai-live", "unknown", ""):
            for endpoint in (self.url, ""):
                with (
                    self.subTest(provider=provider, endpoint_configured=bool(endpoint)),
                    patch.object(self.worker, "STT_PROVIDER", provider),
                    patch.dict(os.environ, {"LIVE_TRANSCRIPT_CONTROL_URL": endpoint}),
                ):
                    with self.assertRaisesRegex(ControlError, "requires liveControl"):
                        await self.worker.entrypoint(self.ctx)
                    self.assert_no_provider_or_join()

    async def test_existing_jobs_without_live_control_keep_legacy_routing(self):
        """An existing dispatch with no new field behaves exactly as before."""
        self.ctx.job.metadata = '{"generation":1,"provider":"deepgram"}'
        with patch.object(self.worker, "STT_PROVIDER", "deepgram"):
            await self.worker.entrypoint(self.ctx)
        self.legacy.assert_called_once_with(self.ctx)
        self.legacy.return_value.start.assert_called_once()
        self.ctx.connect.assert_awaited_once()
        self.live.assert_not_called()

    def test_control_endpoint_prewarms_vad_for_authoritative_openai_selection(self):
        """The legacy provider flag cannot prevent a prepared OpenAI leg opening."""
        proc = SimpleNamespace(userdata={})
        with (
            patch.object(self.worker, "configure_sentry"),
            patch.object(self.worker, "ENABLE_SILERO_VAD", False),
            patch.object(self.worker, "STT_PROVIDER", "deepgram"),
            patch.object(self.worker.silero.VAD, "load") as load,
        ):
            self.worker.prewarm(proc)
        load.assert_called_once_with()
        self.assertIs(proc.userdata["vad"], load.return_value)
