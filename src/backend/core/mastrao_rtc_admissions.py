"""Deliver authenticated human joins with their exact persisted Core media authority."""

# Generated protobuf fields and exact integer/compound evidence checks are intentional.
# pylint: disable=no-member,too-many-return-statements,too-many-boolean-expressions
# pylint: disable=unidiomatic-typecheck

import hashlib
import re
import time
from uuid import uuid4

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.db import transaction

from livekit.protocol.models import ParticipantInfo

from core import models
from core.mastrao_core_http import post_core_json
from core.mastrao_guest_contract import (
    GuestHandoffRefused,
    _sign,
    verify_guest_media_grant,
)
from core.mastrao_host_contract import HostHandoffRefused, verify_host_grant

OBSERVATION_JOSE_TYPE = "mastrao-meeting-start-observation+jws"


class RtcAdmissionRefused(Exception):
    """Opaque transport refusal without authority or webhook contents."""

    def __init__(self, status=503):
        self.status = status
        super().__init__("rtc_admission_refused")


def verify_participant_authority(grant, compact, digest, *, observed_at=None):
    """Verify signature and exact grant binding at mint or historical RTC event time."""
    if (
        not isinstance(compact, str)
        or hashlib.sha256(compact.encode()).hexdigest() != digest
    ):
        raise PermissionDenied("Media authority mismatch")
    host = isinstance(grant, models.MastraoHostGrant)
    try:
        if host:
            claims = verify_host_grant(compact, observed_at=observed_at)
        else:
            claims = verify_guest_media_grant(compact, observed_at=observed_at)
    except (HostHandoffRefused, GuestHandoffRefused) as error:
        raise PermissionDenied("Media authority unavailable") from error
    for field in (
        "meeting_ref",
        "room_ref",
        "provider_binding_digest",
        "credential_digest",
    ):
        if claims[field] != getattr(grant, field):
            raise PermissionDenied("Media authority mismatch")
    if host:
        if (
            claims["host_ref"] != grant.identity.host_ref
            or claims["grant_ref"] != grant.grant_ref
            or claims["handoff_ref"] != grant.handoff_ref
            or claims["platform_session_ref"] != grant.platform_session_ref
            or digest != grant.grant_digest
            or claims["issued_at"] != int(grant.issued_at.timestamp())
            or claims["expires_at"] != int(grant.expires_at.timestamp())
        ):
            raise PermissionDenied("Media authority mismatch")
    else:
        for field in (
            "organization_external_id",
            "guest_ref",
            "redemption_id",
            "invitation_ref",
        ):
            if claims[field] != getattr(grant, field):
                raise PermissionDenied("Media authority mismatch")
    return claims


def _qualified_evidence(observation):  # noqa: PLR0911 - fail-closed evidence boundaries
    """A verified STANDARD join must match the existing token correlation."""
    if (
        observation.event_type != "participant_joined"
        or observation.participant_kind != ParticipantInfo.STANDARD
        or observation.event_time_seconds > int(time.time())
    ):
        return None
    connection = models.MastraoRtcConnection.objects.filter(
        room_binding_id=observation.room_binding_id,
        room_sid=observation.room_sid,
        participant_sid=observation.participant_sid,
        correlation="correlated",
        media_token_binding_id=observation.token_binding_ref,
    ).first()
    if connection is None or connection.media_token_binding_id is None:
        return None
    token = connection.media_token_binding
    if (
        not token.participant_authority
        or token.rtc_identity != observation.rtc_identity
        or not int(token.issued_at.timestamp())
        <= observation.event_time_seconds
        < int(token.expires_at.timestamp())
    ):
        return None
    grant = token.host_grant or token.guest_grant
    binding = observation.room_binding
    if (
        grant.room_binding_id != binding.pk
        or grant.meeting_ref != binding.meeting_ref
        or grant.room_ref != binding.room_ref
        or grant.provider_binding_digest != binding.provider_binding_digest
        or token.grant_digest != grant.grant_digest
        or token.session_nonce_digest != grant.session_nonce_digest
    ):
        return None
    if token.guest_grant_id and (
        grant.decision_allow is not True
        or not grant.decision_receipt_digest
        or grant.decision_confirmed_at is None
        or int(grant.decision_confirmed_at.timestamp()) > observation.event_time_seconds
    ):
        return None
    try:
        authority = verify_participant_authority(
            grant,
            token.participant_authority,
            token.authorization_digest,
            observed_at=observation.event_time_seconds,
        )
    except PermissionDenied:
        return None
    host = token.host_grant_id is not None
    return {
        "version": 1,
        "type": "mastrao.meet-meeting-start-observation",
        "issuer": settings.MASTRAO_ROOM_RECEIPT_ISSUER,
        "audience": settings.MASTRAO_ROOM_RECEIPT_AUDIENCE,
        "purpose": "record_verified_meeting_start",
        "organization_external_id": authority["organization_external_id"],
        "meeting_ref": binding.meeting_ref,
        "room_ref": binding.room_ref,
        "provider_binding_digest": binding.provider_binding_digest,
        "event_type": "participant_joined",
        "event_id": observation.event_id,
        "room_sid": observation.room_sid,
        "participant_sid": observation.participant_sid,
        "participant_kind": "human",
        "participant_ref": authority["host_ref"] if host else authority["guest_ref"],
        "token_binding_ref": str(token.pk),
        "authority_kind": "host" if host else "guest",
        "authority_digest": token.authorization_digest,
        "webhook_body_digest": observation.payload_digest,
        "observed_at": observation.event_time_seconds,
    }, token.participant_authority


def _deliver(evidence, compact):
    now = int(time.time())
    claims = {
        **evidence,
        "issued_at": now,
        "expires_at": now + 30,
        "jti": f"rtc_{uuid4().hex}",
    }
    for field, pattern in (
        ("event_id", r"[A-Za-z0-9_-]{1,128}"),
        ("room_sid", r"RM_[A-Za-z0-9_-]{1,120}"),
        ("participant_sid", r"PA_[A-Za-z0-9_-]{1,120}"),
    ):
        if not re.fullmatch(pattern, claims[field]):
            raise RtcAdmissionRefused()
    if (
        not claims["issuer"]
        or not claims["audience"]
        or not settings.MASTRAO_ROOM_RECEIPT_KEY_ID
    ):
        raise RtcAdmissionRefused()
    response = post_core_json(
        endpoint=settings.MASTRAO_CORE_RTC_ADMISSION_ENDPOINT,
        expected_path="/internal/v1/meetings/rtc-admissions",
        body={
            "observation_assertion": _sign(claims, OBSERVATION_JOSE_TYPE),
            "participant_authority": compact,
        },
        timeout=5,
        refusal=RtcAdmissionRefused,
        expected_fields={"version", "meeting_ref", "room_ref", "first_started_at"},
        passthrough_statuses={400, 401, 403, 404, 409, 422, 429},
    )
    if (
        type(response["version"]) is not int
        or response["version"] != 1
        or response["meeting_ref"] != evidence["meeting_ref"]
        or response["room_ref"] != evidence["room_ref"]
        or type(response["first_started_at"]) is not int
        or not 0 < response["first_started_at"] <= evidence["observed_at"]
    ):
        raise RtcAdmissionRefused()
    return response


@transaction.atomic
def deliver_rtc_admission(observation_id):
    """Replay immutable facts after commit; Core failures leave native capture intact.

    The bounded HTTP call is serialized per room. If response or process is lost,
    an explicit retry presents the same evidence with fresh delivery timestamps.
    """
    observation = models.MastraoRtcObservation.objects.get(pk=observation_id)
    models.MastraoRoomBinding.objects.select_for_update().get(
        pk=observation.room_binding_id
    )
    observation = models.MastraoRtcObservation.objects.select_for_update().get(
        pk=observation_id
    )
    if observation.admission_receipt is not None:
        return False
    qualified = _qualified_evidence(observation)
    if qualified is None:
        return False
    observation.admission_attempts += 1
    try:
        observation.admission_receipt = _deliver(*qualified)
        observation.admission_status = 200
    except (RtcAdmissionRefused, GuestHandoffRefused) as error:
        observation.admission_status = error.status
    observation.save(
        update_fields=[
            "admission_attempts",
            "admission_status",
            "admission_receipt",
            "updated_at",
        ]
    )

    return True
