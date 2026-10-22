"""Sign exact final-event bytes for the existing private Core journal."""

from django.conf import settings

from core.mastrao_core_http import post_core_json
from core.mastrao_live_delivery_contract import (
    DELIVERY_JOSE,
    DELIVERY_PATH,
    canonical_delivery_event,
    validate_delivery_receipt,
)
from core.mastrao_recording_contract import RecordingContractRefused, _sign


def deliver_worker_event(command, facts, envelope):
    """Forward authenticated RTC facts, a bound digest and unmodified text."""
    event, digest = canonical_delivery_event(command["event"], command)
    assertion = {
        **envelope,
        "version": 2,
        **{
            name: facts[name]
            for name in (
                "organization_external_id",
                "meeting_ref",
                "room_ref",
                "room_sid",
                "participant_sid",
                "track_sid",
                "admission_ref",
            )
        },
        **{
            name: command[name]
            for name in ("source_ref", "attempt_ref", "worker_instance_ref", "leg_ref")
        },
        "event_digest": digest,
    }
    result = post_core_json(
        endpoint=settings.MASTRAO_CORE_LIVE_DELIVERY_ENDPOINT,
        expected_path=DELIVERY_PATH,
        body={"request": _sign(assertion, DELIVERY_JOSE), "event": event},
        timeout=settings.MASTRAO_CORE_RECORDING_TIMEOUT_SECONDS,
        refusal=RecordingContractRefused,
    )
    return validate_delivery_receipt(result, event, digest)
