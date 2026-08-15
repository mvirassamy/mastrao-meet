"""Finalize one verified RoomComposite MP4 with Cabinet Core."""

import hashlib
import json
import time
from uuid import uuid4

from django.conf import settings
from django.core.files.storage import default_storage
from django.db import transaction
from django.utils import timezone

from core.mastrao_core_http import post_core_json
from core.mastrao_recording_contract import (
    ARTIFACT_RECEIPT_TYPE,
    RecordingContractRefused,
    sign_artifact_receipt,
)

MAX_ARTIFACT_BYTES = 20_000_000_000


def _canonical_digest(value):
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _inspect_object(object_ref):
    checksum = hashlib.sha256()
    size = 0
    with default_storage.open(object_ref, "rb") as stream:
        while chunk := stream.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_ARTIFACT_BYTES:
                raise RecordingContractRefused(status=503)
            checksum.update(chunk)
    if size < 1:
        raise RecordingContractRefused(status=503)
    return size, checksum.hexdigest()


@transaction.atomic
def finalize_mastrao_artifact(recording):
    """Inspect, persist, sign and replay one exact artifact finalization."""

    binding = recording.mastrao_binding
    required_settings = (
        settings.MASTRAO_RECORDING_STORAGE_BINDING_DIGEST,
        settings.MASTRAO_RECORDING_REGION_REF,
        settings.MASTRAO_RECORDING_ENCRYPTION_REF,
        settings.MASTRAO_RECORDING_LIFECYCLE_POLICY_REF,
    )
    if not all(required_settings):
        raise RecordingContractRefused(status=503)
    if binding.artifact_receipt_claims:
        claims = binding.artifact_receipt_claims
    else:
        size, checksum = _inspect_object(recording.key)
        now = int(time.time())
        artifact_ref = binding.artifact_ref or f"artifact_{uuid4().hex}"
        claims = {
            "version": 1,
            "type": ARTIFACT_RECEIPT_TYPE,
            "issuer": settings.MASTRAO_RECORDING_RECEIPT_ISSUER,
            "audience": settings.MASTRAO_RECORDING_RECEIPT_AUDIENCE,
            "operation": "confirm_meeting_recording_artifact",
            "operation_version": 1,
            "organization_external_id": binding.organization_external_id,
            "meeting_ref": binding.meeting_ref,
            "room_ref": binding.room_ref,
            "recording_ref": binding.recording_ref,
            "provider_binding_digest": binding.provider_binding_digest,
            "artifact_ref": artifact_ref,
            "storage_binding_digest": settings.MASTRAO_RECORDING_STORAGE_BINDING_DIGEST,
            "object_ref": recording.key,
            "content_type": "video/mp4",
            "byte_size": size,
            "checksum_algorithm": "sha256",
            "checksum_digest": checksum,
            "region_ref": settings.MASTRAO_RECORDING_REGION_REF,
            "encryption_ref": settings.MASTRAO_RECORDING_ENCRYPTION_REF,
            "lifecycle_policy_ref": settings.MASTRAO_RECORDING_LIFECYCLE_POLICY_REF,
            "retention_expires_at": int(binding.retention_expires_at.timestamp()),
            "verified_at": now,
            "issued_at": now,
            "expires_at": now + 30,
            "jti": f"artifact_{uuid4().hex}",
        }
        binding.artifact_ref = artifact_ref
        binding.storage_binding_digest = claims["storage_binding_digest"]
        binding.object_ref = recording.key
        binding.content_type = "video/mp4"
        binding.byte_size = size
        binding.checksum_algorithm = "sha256"
        binding.checksum_digest = checksum
        binding.region_ref = claims["region_ref"]
        binding.encryption_ref = claims["encryption_ref"]
        binding.lifecycle_policy_ref = claims["lifecycle_policy_ref"]
        binding.artifact_verified_at = timezone.now()
        binding.artifact_receipt_claims = claims
        binding.artifact_receipt_digest = _canonical_digest(claims)
        binding.state = binding.State.PROCESSING
        binding.save()
    result = post_core_json(
        endpoint=settings.MASTRAO_CORE_RECORDING_ARTIFACT_ENDPOINT,
        expected_path="/internal/v1/meetings/recording/artifacts/finalize",
        body={"recording_artifact_receipt": sign_artifact_receipt(claims)},
        timeout=settings.MASTRAO_CORE_RECORDING_TIMEOUT_SECONDS,
        refusal=RecordingContractRefused,
        expected_fields={"artifactRef"},
    )
    if result["artifactRef"] != claims["artifact_ref"]:
        raise RecordingContractRefused(status=503)
    binding.state = binding.State.FINALIZED
    binding.save(update_fields=["state", "updated_at"])
    return binding
