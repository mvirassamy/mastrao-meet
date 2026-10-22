"""Resolve one microphone epoch solely from authenticated, persisted RTC facts."""

# Generated protobuf enum constants and exact identity guards are intentional.
# pylint: disable=no-member,too-many-boolean-expressions

from livekit import api

from core import models
from core.mastrao_host_contract import EXTERNAL_ID
from core.mastrao_recording_contract import RecordingContractRefused


def epoch_references(epoch):
    """An epoch owns stable admission/leg identities across delivery retries."""
    return {
        "admission_ref": "live_admission_" + epoch.pk.hex,
        "leg_ref": "live_leg_" + epoch.pk.hex,
    }


def _assert_publication(epoch, binding, issued):
    publication = epoch.first_publication
    connection = epoch.connection
    if (
        publication is None
        or publication.event_type != "track_published"
        or publication.room_binding_id != binding.pk
        or publication.room_sid != connection.room_sid
        or publication.participant_sid != connection.participant_sid
        or publication.track_sid != epoch.track_sid
        or publication.rtc_identity != issued.rtc_identity
        or publication.track_type != api.TrackType.AUDIO
        or publication.track_source != api.TrackSource.MICROPHONE
    ):
        raise RecordingContractRefused()


def _assert_join(connection, issued, grant):
    joins = models.MastraoRtcObservation.objects.filter(
        room_binding_id=connection.room_binding_id,
        room_sid=connection.room_sid,
        participant_sid=connection.participant_sid,
        event_type="participant_joined",
    )
    join = joins.order_by("event_time_seconds", "event_id").first()
    if (
        join is None
        or join.token_binding_ref != issued.pk
        or join.rtc_identity != issued.rtc_identity
        or not (
            int(issued.issued_at.timestamp())
            <= join.event_time_seconds
            < int(issued.expires_at.timestamp())
        )
        or not (
            int(grant.issued_at.timestamp())
            <= join.event_time_seconds
            < int(grant.expires_at.timestamp())
        )
    ):
        raise RecordingContractRefused()


def trusted_live_track(scope, command):
    """Keep immutable ACK identity after closure; new sends require live facts.

    Token/grant expiry bounds the authenticated join, not the established call.
    Core alone revalidates current grant/session/consent capture authority.
    """
    epoch = (
        models.MastraoRtcTrackEpoch.objects.select_related(
            "first_publication",
            "connection__room_binding",
            "connection__media_token_binding__host_grant__identity__user",
            "connection__media_token_binding__guest_grant",
        )
        .filter(
            connection__room_binding_id=scope["binding_id"],
            connection__room_sid=command["room_sid"],
            connection__participant_sid=command["participant_sid"],
            track_sid=command["track_sid"],
        )
        .first()
    )
    if epoch is None:
        raise RecordingContractRefused()
    connection = epoch.connection
    binding = connection.room_binding
    issued = connection.media_token_binding
    if issued is None or issued.room_binding_id != binding.pk:
        raise RecordingContractRefused()
    grant = issued.host_grant or issued.guest_grant
    if (
        grant is None
        or binding.room_id != scope["room_id"]
        or grant.room_binding_id != binding.pk
        or grant.meeting_ref != binding.meeting_ref
        or grant.room_ref != binding.room_ref
        or grant.provider_binding_digest != binding.provider_binding_digest
        or grant.grant_digest != issued.grant_digest
        or grant.session_nonce_digest != issued.session_nonce_digest
    ):
        raise RecordingContractRefused()
    _assert_publication(epoch, binding, issued)
    _assert_join(connection, issued, grant)
    organization = grant.organization_external_id
    if not isinstance(organization, str) or not EXTERNAL_ID.fullmatch(organization):
        raise RecordingContractRefused()
    if command["action"] not in {"stopped", "deliver"}:
        _assert_live_epoch(epoch, binding, issued, grant)
    kind = "host"
    participant_ref = None
    if issued.host_grant_id:
        participant_ref = grant.identity.host_ref
    else:
        kind = "guest"
        participant_ref = grant.guest_ref
    facts = {
        "organization_external_id": organization,
        "meeting_ref": binding.meeting_ref,
        "room_ref": binding.room_ref,
        "provider_binding_digest": binding.provider_binding_digest,
        "participant_session_digest": issued.session_nonce_digest,
        "grant_ref": grant.grant_ref,
        "grant_digest": issued.grant_digest,
        "participant_kind": kind,
        "participant_ref": participant_ref,
        "room_sid": connection.room_sid,
        "participant_sid": connection.participant_sid,
        "track_sid": epoch.track_sid,
        **epoch_references(epoch),
    }
    if (
        command["action"] != "prepare"
        and command["admission_ref"] != facts["admission_ref"]
    ):
        raise RecordingContractRefused()
    return facts


def _assert_live_epoch(epoch, binding, issued, grant):
    connection = epoch.connection
    if (
        epoch.ended
        or epoch.conflict
        or connection.ended
        or connection.correlation != "correlated"
        or binding.closing_at is not None
        or hasattr(binding, "closure")
        or models.MastraoRtcConnection.objects.filter(media_token_binding=issued)
        .exclude(pk=connection.pk)
        .exists()
    ):
        raise RecordingContractRefused()
    if issued.host_grant_id:
        if not grant.identity.user.is_active:
            raise RecordingContractRefused()
    elif (
        grant.admission_state != models.MastraoGuestGrant.AdmissionState.ALLOWED
        or grant.decision_allow is not True
        or grant.decision_confirmed_at is None
        or not grant.decision_receipt_digest
    ):
        raise RecordingContractRefused()
