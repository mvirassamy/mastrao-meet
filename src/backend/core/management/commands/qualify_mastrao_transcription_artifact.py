"""Emit the exact canonical transcript artifact bytes for one audio vector.

The provider-free Platform integration uses this command to obtain the REAL
bytes produced by the fake-ASR pipeline (transcribe, map speakers, persist)
so Core can validate them against the shared artifact schema v1 instead of
fabricating a lookalike artifact on its own side.
"""

# pylint: disable=cyclic-import

import base64
import json
import tempfile
from pathlib import Path

from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand, CommandError
from django.test.utils import override_settings

from core.mastrao_transcription_artifact import map_speakers, persist_transcript
from core.mastrao_transcription_worker import transcribe_audio


class Command(BaseCommand):
    """Qualify the canonical transcript artifact bytes provider-free."""

    help = "Emit the exact fake-ASR transcript artifact bytes for a vector"

    def add_arguments(self, parser):
        parser.add_argument("vector_path")

    def handle(self, *args, **options):
        try:
            vector = json.loads(
                Path(options["vector_path"]).read_text(encoding="utf-8")
            )
            transcription_ref = vector["transcription_ref"]
            audio_bytes = base64.b64decode(vector["audio_base64"], validate=True)
            result_path = Path(vector["result_path"])
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise CommandError("Invalid transcription artifact vector") from error
        transcript = map_speakers(transcribe_audio(audio_bytes))
        with tempfile.TemporaryDirectory(prefix="mastrao_artifact_") as workdir:
            storages = {
                "default": {
                    "BACKEND": "django.core.files.storage.FileSystemStorage",
                    "OPTIONS": {"location": workdir},
                },
                "staticfiles": {
                    "BACKEND": "django.core.files.storage.FileSystemStorage",
                    "OPTIONS": {"location": f"{workdir}/static"},
                },
            }
            with override_settings(STORAGES=storages):
                artifact = persist_transcript(transcription_ref, transcript)
                with default_storage.open(artifact["object_ref"], "rb") as stream:
                    stored = stream.read()
        result_path.write_text(
            json.dumps(
                {
                    "object_ref": artifact["object_ref"],
                    "byte_size": artifact["byte_size"],
                    "checksum_digest": artifact["checksum_digest"],
                    "segment_count": artifact["segment_count"],
                    "engine_ref": artifact["engine_ref"],
                    "artifact_base64": base64.b64encode(stored).decode(),
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
