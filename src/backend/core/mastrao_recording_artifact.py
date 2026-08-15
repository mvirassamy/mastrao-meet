"""Finalize one verified RoomComposite MP4 with Cabinet Core."""

# The immutable storage snapshot deliberately carries every value revalidated later.
# pylint: disable=too-many-instance-attributes

import hashlib
import json
import time
from dataclasses import dataclass
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


@dataclass(frozen=True)
class _ArtifactSnapshot:
    """Stable database coordinates captured before inspecting object storage."""

    binding_model: type
    recording_model: type
    binding_id: object
    binding_version: object
    recording_id: object
    recording_version: object
    object_ref: str
    replay_claims: dict | None = None


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


def _snapshot_artifact(recording):
    """Take a short locked snapshot, without reading object storage."""

    binding_model = type(recording.mastrao_binding)
    recording_model = type(recording)
    with transaction.atomic():
        binding = binding_model.objects.select_for_update().get(
            pk=recording.mastrao_binding.pk
        )
        if binding.recording_id is None:
            raise RecordingContractRefused(status=503)
        recording = recording_model.objects.get(pk=binding.recording_id)
        if binding.artifact_receipt_claims:
            return _ArtifactSnapshot(
                binding_model=binding_model,
                recording_model=recording_model,
                binding_id=binding.pk,
                binding_version=binding.updated_at,
                recording_id=binding.recording_id,
                recording_version=recording.updated_at,
                object_ref=recording.key,
                replay_claims=dict(binding.artifact_receipt_claims),
            )
        return _ArtifactSnapshot(
            binding_model=binding_model,
            recording_model=recording_model,
            binding_id=binding.pk,
            binding_version=binding.updated_at,
            recording_id=recording.pk,
            recording_version=recording.updated_at,
            object_ref=recording.key,
        )


def _persist_artifact_receipt(snapshot, size, checksum):
    """Revalidate the snapshot and persist one exact receipt under a short lock."""

    with transaction.atomic():
        binding = snapshot.binding_model.objects.select_for_update().get(
            pk=snapshot.binding_id
        )
        if (
            binding.recording_id != snapshot.recording_id
            or binding.updated_at != snapshot.binding_version
        ):
            raise RecordingContractRefused(status=409)
        recording = snapshot.recording_model.objects.get(pk=snapshot.recording_id)
        if (
            recording.updated_at != snapshot.recording_version
            or recording.key != snapshot.object_ref
        ):
            raise RecordingContractRefused(status=409)
        if binding.artifact_receipt_claims:
            claims = dict(binding.artifact_receipt_claims)
            if (
                claims.get("object_ref") != snapshot.object_ref
                or claims.get("byte_size") != size
                or claims.get("checksum_digest") != checksum
                or binding.object_ref != snapshot.object_ref
                or binding.byte_size != size
                or binding.checksum_digest != checksum
            ):
                raise RecordingContractRefused(status=409)
            now = int(time.time())
            if claims.get("expires_at", 0) >= now:
                return claims
            claims.update(
                issued_at=now,
                expires_at=now + 30,
                jti=f"artifact_{uuid4().hex}",
            )
            binding.artifact_receipt_claims = claims
            binding.artifact_receipt_digest = _canonical_digest(claims)
            binding.save(
                update_fields=[
                    "artifact_receipt_claims",
                    "artifact_receipt_digest",
                    "updated_at",
                ]
            )
            return claims
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
            "object_ref": snapshot.object_ref,
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
        binding.object_ref = snapshot.object_ref
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
        return claims


def _prepare_artifact_receipt(recording):
    """Inspect outside transactions, then persist before the Core callback."""

    snapshot = _snapshot_artifact(recording)
    size, checksum = _inspect_object(snapshot.object_ref)
    claims = _persist_artifact_receipt(snapshot, size, checksum)
    return snapshot.binding_model, snapshot.binding_id, claims


def finalize_mastrao_artifact(recording):
    """Inspect, persist, sign and replay one exact artifact finalization."""

    required_settings = (
        settings.MASTRAO_RECORDING_STORAGE_BINDING_DIGEST,
        settings.MASTRAO_RECORDING_REGION_REF,
        settings.MASTRAO_RECORDING_ENCRYPTION_REF,
        settings.MASTRAO_RECORDING_LIFECYCLE_POLICY_REF,
    )
    if not all(required_settings):
        raise RecordingContractRefused(status=503)
    binding_model, binding_id, claims = _prepare_artifact_receipt(recording)
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
    with transaction.atomic():
        binding = binding_model.objects.select_for_update().get(pk=binding_id)
        if (
            binding.artifact_ref != claims["artifact_ref"]
            or binding.artifact_receipt_claims != claims
            or binding.artifact_receipt_digest != _canonical_digest(claims)
        ):
            raise RecordingContractRefused(status=503)
        binding.state = binding.State.FINALIZED
        binding.save(update_fields=["state", "updated_at"])
        return binding
