"""Signed terminal provider observations for canonical Mastrao recordings."""

import time
from urllib.parse import urlparse
from uuid import uuid4

from django.conf import settings
from django.db import transaction

from livekit import api as livekit_api

from core import models
from core.mastrao_core_http import post_core_json
from core.mastrao_recording_contract import (
    FAILURE_RECEIPT_TYPE,
    MAX_ASSERTION_SECONDS,
    RecordingContractRefused,
    sign_failure_receipt,
)

# LiveKit's generated enum members are not visible to pylint.
# pylint: disable=no-member

FAILURE_STATES = {
    livekit_api.EgressStatus.EGRESS_ABORTED: "provider_aborted",
    livekit_api.EgressStatus.EGRESS_FAILED: "provider_failed",
}
PROVIDER_NOT_STARTED = "provider_not_started"


def _can_tombstone_local_stale_failure(error):
    if getattr(error, "status", None) != 404 or not settings.DEBUG:
        return False
    endpoint = urlparse(settings.MASTRAO_CORE_RECORDING_FAILURE_ENDPOINT)
    return endpoint.hostname in {
        "127.0.0.1",
        "localhost",
        "127.0.0.1.nip.io",
        "host.docker.internal",
        "cabinet-core",
    }


def report_mastrao_recording_failure(recording, provider_status):
    """Report one exact provider terminal failure to Core, idempotently."""

    try:
        binding = recording.mastrao_binding
    except models.MastraoRecordingBinding.DoesNotExist:
        return False
    failure_code = (
        PROVIDER_NOT_STARTED
        if provider_status is None
        else FAILURE_STATES.get(provider_status)
    )
    if failure_code is None:
        return False
    with transaction.atomic():
        locked = models.MastraoRecordingBinding.objects.select_for_update().get(
            pk=binding.pk
        )
        core_state_is_terminal = locked.state in {
            models.MastraoRecordingBinding.State.CANCELLED,
            models.MastraoRecordingBinding.State.FINALIZED,
        }
        provider_registration_conflicts = (
            failure_code == PROVIDER_NOT_STARTED and bool(locked.provider_recording_ref)
        ) or (
            failure_code != PROVIDER_NOT_STARTED and not locked.provider_recording_ref
        )
        if core_state_is_terminal or provider_registration_conflicts:
            return False
        local_recording = models.Recording.objects.select_for_update().get(
            pk=locked.recording_id
        )
        locked.state = locked.State.FAILED
        locked.save(update_fields=["state", "updated_at"])
        if local_recording.status in {
            models.RecordingStatusChoices.INITIATED,
            models.RecordingStatusChoices.ACTIVE,
        }:
            if failure_code == PROVIDER_NOT_STARTED:
                local_recording.status = models.RecordingStatusChoices.FAILED_TO_START
            else:
                local_recording.status = models.RecordingStatusChoices.ABORTED
            local_recording.save(update_fields=["status", "updated_at"])

        now = int(time.time())
        claims = {
            "version": 1,
            "type": FAILURE_RECEIPT_TYPE,
            "issuer": settings.MASTRAO_RECORDING_RECEIPT_ISSUER,
            "audience": settings.MASTRAO_RECORDING_RECEIPT_AUDIENCE,
            "operation": "confirm_meeting_recording_failed",
            "operation_version": 1,
            "organization_external_id": locked.organization_external_id,
            "meeting_ref": locked.meeting_ref,
            "room_ref": locked.room_ref,
            "recording_ref": locked.recording_ref,
            "provider_binding_digest": locked.provider_binding_digest,
            "failure_code": failure_code,
            "issued_at": now,
            "expires_at": now + MAX_ASSERTION_SECONDS,
            "jti": f"recording_failure_{uuid4().hex}",
        }
        if locked.provider_recording_ref:
            claims["provider_recording_ref"] = locked.provider_recording_ref
        try:
            result = post_core_json(
                endpoint=settings.MASTRAO_CORE_RECORDING_FAILURE_ENDPOINT,
                expected_path="/internal/v1/meetings/recording/failures",
                body={"recording_failure_receipt": sign_failure_receipt(claims)},
                timeout=settings.MASTRAO_CORE_RECORDING_TIMEOUT_SECONDS,
                refusal=RecordingContractRefused,
                expected_fields={"recordingRef", "state"},
            )
        except RecordingContractRefused as error:
            if not _can_tombstone_local_stale_failure(error):
                raise
            return True
        if result != {"recordingRef": locked.recording_ref, "state": "failed"}:
            raise RecordingContractRefused(status=503)
        return True
