"""Bind a personal email capability only to an admitted browser participant."""

from django.conf import settings

from core import models
from core.mastrao_core_http import post_core_json
from core.mastrao_guest_grant import active_guest_grant
from core.mastrao_recording_contract import RecordingContractRefused
from core.mastrao_room_contract import OPAQUE_REFERENCE


def bind_video_invitation(request, room, participant, status):
    """Return whether session status must be reread after a binding attempt."""

    choices = request.session.get("mastrao_video_choices", {})
    if not isinstance(choices, dict):
        return False
    token = choices.get(status["meeting_ref"])
    if not token:
        return False
    if status["mode"] != "recorded" or status["video"]["decision_lock"] != "open":
        choices.pop(status["meeting_ref"], None)
        request.session["mastrao_video_choices"] = choices
        return False
    guest = active_guest_grant(request, room)
    if (
        participant["kind"] != "guest"
        or not guest
        or guest.admission_state != models.MastraoGuestGrant.AdmissionState.ALLOWED
        or guest.decision_confirmed_at is None
    ):
        return False
    try:
        result = post_core_json(
            endpoint=settings.MASTRAO_CORE_VIDEO_PARTICIPANT_ENDPOINT,
            expected_path="/internal/v1/meetings/video-invitations/participant",
            body={
                "participant_grant": participant["compact"],
                "participant_session_digest": participant["session_digest"],
                "choice_token": token,
            },
            timeout=settings.MASTRAO_CORE_RECORDING_TIMEOUT_SECONDS,
            refusal=RecordingContractRefused,
            expected_fields={"version", "invitation_ref"},
            passthrough_statuses={409},
        )
    except RecordingContractRefused as error:
        if error.status not in {404, 409}:
            raise
    else:
        if (
            result["version"] != 1
            or not isinstance(result["invitation_ref"], str)
            or not OPAQUE_REFERENCE.fullmatch(result["invitation_ref"])
        ):
            raise RecordingContractRefused(status=503)
    # A locked or invalid capability is discarded; the next projection remains
    # authoritative and cannot turn an unbound invitation into no opposition.
    choices.pop(status["meeting_ref"], None)
    request.session["mastrao_video_choices"] = choices
    return True
