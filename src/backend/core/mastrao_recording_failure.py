"""Signed terminal provider observations for canonical Mastrao recordings."""

import time
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
    if failure_code is None or (
        failure_code != PROVIDER_NOT_STARTED and not binding.provider_recording_ref
    ):
        return False
    now = int(time.time())
    claims = {
        "version": 1,
        "type": FAILURE_RECEIPT_TYPE,
        "issuer": settings.MASTRAO_RECORDING_RECEIPT_ISSUER,
        "audience": settings.MASTRAO_RECORDING_RECEIPT_AUDIENCE,
        "operation": "confirm_meeting_recording_failed",
        "operation_version": 1,
        "organization_external_id": binding.organization_external_id,
        "meeting_ref": binding.meeting_ref,
        "room_ref": binding.room_ref,
        "recording_ref": binding.recording_ref,
        "provider_binding_digest": binding.provider_binding_digest,
        "failure_code": failure_code,
        "issued_at": now,
        "expires_at": now + MAX_ASSERTION_SECONDS,
        "jti": f"recording_failure_{uuid4().hex}",
    }
    if binding.provider_recording_ref:
        claims["provider_recording_ref"] = binding.provider_recording_ref
    result = post_core_json(
        endpoint=settings.MASTRAO_CORE_RECORDING_FAILURE_ENDPOINT,
        expected_path="/internal/v1/meetings/recording/failures",
        body={"recording_failure_receipt": sign_failure_receipt(claims)},
        timeout=settings.MASTRAO_CORE_RECORDING_TIMEOUT_SECONDS,
        refusal=RecordingContractRefused,
        expected_fields={"recordingRef", "state"},
    )
    if result != {"recordingRef": binding.recording_ref, "state": "failed"}:
        raise RecordingContractRefused(status=503)
    with transaction.atomic():
        locked = models.MastraoRecordingBinding.objects.select_for_update().get(
            pk=binding.pk
        )
        locked.state = locked.State.FAILED
        locked.save(update_fields=["state", "updated_at"])
    return True
