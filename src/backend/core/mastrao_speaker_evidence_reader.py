"""Read the bounded speaker evidence bound to one completed room mix."""

import hashlib
import json
import re

from django.core.files.storage import default_storage

from botocore.exceptions import BotoCoreError, ClientError

from core.mastrao_recording_contract import RecordingContractRefused

MAX_SPEAKER_EVIDENCE_BYTES = 5_000_000
SPEAKER_EVIDENCE_OBJECT_REF = re.compile(
    r"^room-audio-speaker-evidence/v1/[a-f0-9]{64}\.json$"
)


def _evidence_object_ref(source):
    object_ref = source.manifest.get("speaker_evidence_object_ref")
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
    expected_size = source.manifest.get("speaker_evidence_byte_size")
    expected_digest = source.manifest.get("speaker_evidence_checksum_digest")
    if (
        isinstance(expected_size, bool)
        or not isinstance(expected_size, int)
        or not 1 <= expected_size <= MAX_SPEAKER_EVIDENCE_BYTES
        or not isinstance(expected_digest, str)
        or not re.fullmatch(r"[a-f0-9]{64}", expected_digest)
    ):
        raise RecordingContractRefused(status=503)
    try:
        with default_storage.open(object_ref, "rb") as stream:
            raw = stream.read(MAX_SPEAKER_EVIDENCE_BYTES + 1)
    except (BotoCoreError, ClientError, OSError, ValueError) as error:
        raise RecordingContractRefused(status=503) from error
    if len(raw) != expected_size or hashlib.sha256(raw).hexdigest() != expected_digest:
        raise RecordingContractRefused(status=503)
    try:
        evidence = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as error:
        raise RecordingContractRefused(status=503) from error
    manifest = source.manifest
    if not _matches_source(evidence, manifest):
        raise RecordingContractRefused(status=503)
    return evidence
