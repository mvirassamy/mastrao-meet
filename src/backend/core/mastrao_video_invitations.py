"""Core-authorized personal video notices using the existing SMTP service."""

# Keep the exact private response binding predicates visible.
# pylint: disable=too-many-boolean-expressions

import re
import time
from urllib.parse import urlencode
from uuid import uuid4

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import validate_email

from core.mastrao_core_http import post_core_json
from core.mastrao_guest_invitation_share import guest_invitation_url
from core.mastrao_platform_facade import PlatformFacadeError, _platform_origin
from core.mastrao_recording_contract import RecordingContractRefused, _sign
from core.mastrao_recording_session import _participant
from core.mastrao_room_contract import OPAQUE_REFERENCE
from core.services.invitation import InvitationError, InvitationService

DELIVERY_STATES = {"pending", "sending", "sent", "unknown"}
DELIVERY_FIELDS = {
    "version",
    "invitation_ref",
    "delivery_ref",
    "email",
    "delivery_state",
    "send_required",
}


def validate_invitations(invitations):
    """Only canonical recipient metadata may leave the creation bridge."""

    if not isinstance(invitations, list) or len(invitations) > 50:
        raise PlatformFacadeError()
    refs = set()
    for invitation in invitations:
        if not isinstance(invitation, dict) or set(invitation) != {
            "invitation_ref",
            "email",
            "delivery_state",
        }:
            raise PlatformFacadeError()
        ref, email = invitation["invitation_ref"], invitation["email"]
        if (
            not isinstance(ref, str)
            or not OPAQUE_REFERENCE.fullmatch(ref)
            or ref in refs
        ):
            raise PlatformFacadeError()
        if (
            not isinstance(email, str)
            or len(email) > 254
            or not isinstance(invitation["delivery_state"], str)
            or invitation["delivery_state"] not in DELIVERY_STATES
        ):
            raise PlatformFacadeError()
        try:
            validate_email(email)
        except ValidationError as error:
            raise PlatformFacadeError() from error
        refs.add(ref)
    return invitations


def _delivery_assertion(authority, invitation_ref, operation, outcome=None):
    now = int(time.time())
    payload = {
        "type": "mastrao.meet-video-delivery",
        "version": 1,
        "issuer": settings.MASTRAO_RECORDING_RECEIPT_ISSUER,
        "audience": settings.MASTRAO_RECORDING_RECEIPT_AUDIENCE,
        "organization_external_id": authority["organization_external_id"],
        "meeting_ref": authority["meeting_ref"],
        "invitation_ref": invitation_ref,
        "delivery_ref": invitation_ref,
        "operation": operation,
        "issued_at": now,
        "expires_at": now + 30,
        "jti": f"delivery_{uuid4().hex}",
    }
    if outcome is not None:
        payload["outcome"] = outcome
    return _sign(payload, "mastrao-meeting-video-delivery+jws")


def _delivery(compact, invitation):
    result = post_core_json(
        endpoint=settings.MASTRAO_CORE_VIDEO_DELIVERY_ENDPOINT,
        expected_path="/internal/v1/meetings/video-invitations/delivery",
        body={"delivery_assertion": compact},
        timeout=settings.MASTRAO_CORE_RECORDING_TIMEOUT_SECONDS,
        refusal=RecordingContractRefused,
        passthrough_statuses={409},
    )
    sending = result.get("send_required")
    fields = DELIVERY_FIELDS | ({"choice_token"} if sending is True else set())
    if (
        set(result) != fields
        or not isinstance(result.get("version"), int)
        or isinstance(result.get("version"), bool)
        or result.get("version") != 1
        or not isinstance(sending, bool)
        or result.get("invitation_ref") != invitation["invitation_ref"]
        or result.get("delivery_ref") != invitation["invitation_ref"]
        or result.get("email") != invitation["email"]
        or not isinstance(result.get("delivery_state"), str)
        or result.get("delivery_state") not in DELIVERY_STATES
        or (
            sending
            and (
                not isinstance(result["choice_token"], str)
                or len(result["choice_token"]) > 16384
                or not re.fullmatch(
                    r"[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+",
                    result["choice_token"],
                )
                or result["delivery_state"] != "sending"
            )
        )
    ):
        raise RecordingContractRefused(status=503)
    return result


def _send_one(request, authority, invitation, room_url, schedule):
    claim = _delivery(
        _delivery_assertion(authority, invitation["invitation_ref"], "claim"),
        invitation,
    )
    if not claim["send_required"]:
        return {**invitation, "delivery_state": claim["delivery_state"]}
    token = claim["choice_token"]
    choice_url = f"{_platform_origin()}/meet/video-choice?{urlencode({'token': token})}"
    personal_room_url = f"{room_url}&{urlencode({'video_choice': token})}"
    outcome = "sent"
    try:
        InvitationService.invite_to_scheduled_room(
            sender=request.user,
            email=invitation["email"],
            schedule=schedule,
            room_url=personal_room_url,
            choice_url=choice_url,
        )
    except (InvitationError, OSError):
        outcome = "unknown"
    acknowledgement = _delivery_assertion(
        authority, invitation["invitation_ref"], "ack", outcome
    )
    # A lost HTTP acknowledgement only replays that acknowledgement, never SMTP.
    for _ in range(2):
        try:
            result = _delivery(acknowledgement, invitation)
            return {**invitation, "delivery_state": result["delivery_state"]}
        except RecordingContractRefused:
            continue
    return {**invitation, "delivery_state": "unknown"}


def deliver_meeting_invitations(request, binding, invitations, schedule):
    """Claim each recipient once; uncertain attempts remain visibly uncertain."""

    validate_invitations(invitations)
    if not invitations:
        return []
    try:
        participant = _participant(request, binding.room)
        if participant["kind"] != "host":
            raise RecordingContractRefused()
        authority = {
            "organization_external_id": participant["claims"][
                "organization_external_id"
            ],
            "meeting_ref": binding.meeting_ref,
        }
        room_url = guest_invitation_url(request, binding)
        _platform_origin()
    except (PlatformFacadeError, RecordingContractRefused):
        return invitations
    delivered = []
    for invitation in invitations:
        try:
            delivered.append(
                _send_one(request, authority, invitation, room_url, schedule)
            )
        except RecordingContractRefused:
            delivered.append(invitation)
    return delivered
