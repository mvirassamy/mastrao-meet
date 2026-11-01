"""Validate a correlated RTC microphone epoch against Core authority."""

# Generated LiveKit protobuf members intentionally use dynamic attributes.
# pylint: disable=no-member,too-many-boolean-expressions

from livekit import api

from core import models
from core.mastrao_recording_contract import RecordingContractRefused


def assert_track_authority(epoch, binding, effect):
    """Reject a track unless its persisted RTC and grant bindings are exact."""

    connection = epoch.connection
    issued = connection.media_token_binding
    if (
        connection.room_binding_id != binding.pk
        or connection.correlation != "correlated"
        or connection.ended
        or epoch.ended
        or epoch.conflict
        or issued is None
        or str(issued.pk) != effect["media_token_binding_ref"]
        or issued.room_binding_id != binding.pk
        or connection.room_sid != effect["room_sid"]
        or connection.participant_sid != effect["participant_sid"]
        or epoch.track_sid != effect["track_sid"]
        or epoch.first_publication is None
        or epoch.first_publication.track_type != api.TrackType.AUDIO
        or epoch.first_publication.track_source != api.TrackSource.MICROPHONE
        or issued.grant_digest != effect["grant_digest"]
        or issued.session_nonce_digest != effect["session_nonce_digest"]
    ):
        raise RecordingContractRefused()

    if effect["participant_kind"] == "host":
        grant = issued.host_grant
        if grant is None or not grant.identity.user.is_active:
            raise RecordingContractRefused()
        participant_ref = grant.identity.host_ref
    else:
        grant = issued.guest_grant
        if (
            grant is None
            or grant.organization_external_id != effect["organization_external_id"]
            or grant.admission_state != models.MastraoGuestGrant.AdmissionState.ALLOWED
            or grant.decision_allow is not True
            or grant.decision_confirmed_at is None
            or not grant.decision_receipt_digest
        ):
            raise RecordingContractRefused()
        participant_ref = grant.guest_ref

    if (
        participant_ref != effect["participant_ref"]
        or grant.grant_ref != effect["grant_ref"]
        or grant.grant_digest != issued.grant_digest
        or grant.session_nonce_digest != issued.session_nonce_digest
        or grant.room_binding_id != binding.pk
        or grant.meeting_ref != binding.meeting_ref
        or grant.room_ref != binding.room_ref
        or grant.provider_binding_digest != binding.provider_binding_digest
    ):
        raise RecordingContractRefused()
