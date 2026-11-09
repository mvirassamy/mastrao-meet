"""Read the bounded speaker evidence bound to one completed room mix."""

import json
import re

from django.core.files.storage import default_storage

from botocore.exceptions import BotoCoreError, ClientError

from core import models
from core.mastrao_recording_contract import RecordingContractRefused

MAX_SPEAKER_EVIDENCE_BYTES = 5_000_000
SPEAKER_EVIDENCE_OBJECT_KEY = "mastrao_speaker_evidence_object_ref"
SPEAKER_EVIDENCE_OBJECT_REF = re.compile(
    r"^mastrao-speaker-evidence/[A-Za-z0-9_-]{16,160}\.json$"
)


def _evidence_object_ref(source):
    binding = (
        models.MastraoRecordingBinding.objects.select_related("recording")
        .filter(room_binding=source.run_effect.room_binding)
        .first()
    )
    if binding is None or binding.recording is None:
        raise RecordingContractRefused(status=503)
    object_ref = binding.recording.options.get(SPEAKER_EVIDENCE_OBJECT_KEY)
    if not isinstance(object_ref, str) or not SPEAKER_EVIDENCE_OBJECT_REF.fullmatch(
        object_ref
    ):
        raise RecordingContractRefused(status=503)
    return object_ref


def _matches_source(evidence, manifest):
    if not isinstance(evidence, dict):
        return False
    if evidence.get("version") != 1:
        return False
    if evidence.get("meeting_ref") != manifest.get("meeting_ref"):
        return False
    if evidence.get("room_ref") != manifest.get("room_ref"):
        return False
    if not isinstance(evidence.get("participants"), list):
        return False
    return isinstance(evidence.get("events"), list)


def read_speaker_evidence(source):
    """Return one verified-shape evidence artifact for the source meeting."""

    object_ref = _evidence_object_ref(source)
    try:
        with default_storage.open(object_ref, "rb") as stream:
            raw = stream.read(MAX_SPEAKER_EVIDENCE_BYTES + 1)
    except (BotoCoreError, ClientError, OSError, ValueError) as error:
        raise RecordingContractRefused(status=503) from error
    if len(raw) > MAX_SPEAKER_EVIDENCE_BYTES:
        raise RecordingContractRefused(status=503)
    try:
        evidence = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as error:
        raise RecordingContractRefused(status=503) from error
    manifest = source.manifest
    if not _matches_source(evidence, manifest):
        raise RecordingContractRefused(status=503)
    return evidence
