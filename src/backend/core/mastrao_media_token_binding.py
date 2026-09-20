"""Persist exact media token issuance before returning it to a participant.

The public opaque marker is only a lookup hint. A future capture admission must
bind trusted RTC connection/track observations and revalidate Core authority.
Token expiry bounds new admission, not the lifetime of an established call.
"""

# Generated LiveKit protobuf members and fail-closed contract checks are intentional here.
# pylint: disable=too-many-boolean-expressions

import hashlib
from datetime import UTC, datetime
from uuid import uuid4

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.utils import timezone

import jwt

from core import models, utils
from core.mastrao_host_grant import active_host_grant
from core.mastrao_room_lifecycle import assert_mastrao_room_open


def generate_host_media_config(request, room, **configuration):
    """Journal only the requesting browser's current exact-room host grant."""
    if not settings.MASTRAO_MEDIA_TOKEN_BINDING_ENABLED:
        return utils.generate_livekit_config(**configuration)
    grant = active_host_grant(request, room)
    if grant is None:
        raise PermissionDenied("Media grant unavailable")
    return _issue_bound_config(grant, grant.grant_digest, configuration)


def generate_guest_media_config(grant, authorization_digest, **configuration):
    """Called only after the Core guest-media grant has been verified."""
    if not settings.MASTRAO_MEDIA_TOKEN_BINDING_ENABLED:
        return utils.generate_livekit_config(**configuration)
    return _issue_bound_config(grant, authorization_digest, configuration)


def _validate_identity(grant, configuration):
    user = configuration["user"]
    if isinstance(grant, models.MastraoHostGrant):
        if (
            not user.is_authenticated
            or not user.is_active
            or user.pk != grant.identity.user_id
            or not grant.identity.user.is_active
        ):
            raise PermissionDenied("Media identity mismatch")
        return str(grant.identity.user.sub)
    if (
        user.is_authenticated
        or configuration.get("participant_id") != grant.guest_ref
        or grant.admission_state != models.MastraoGuestGrant.AdmissionState.ALLOWED
        or grant.decision_allow is not True
        or grant.decision_confirmed_at is None
        or not grant.decision_receipt_digest
    ):
        raise PermissionDenied("Guest media admission unavailable")
    return grant.guest_ref


@transaction.atomic
def _issue_bound_config(grant, authorization_digest, configuration):
    # Serialize issuance with room closure and grant updates. No remote work here.
    binding = models.MastraoRoomBinding.objects.select_for_update().get(
        pk=grant.room_binding_id
    )
    expected_grant = (
        grant.grant_digest,
        grant.session_nonce_digest,
        grant.credential_digest,
    )
    grant = type(grant).objects.select_for_update().get(pk=grant.pk)
    assert_mastrao_room_open(binding)
    if (
        str(binding.room_id) != configuration["room_id"]
        or grant.room_binding_id != binding.pk
        or grant.meeting_ref != binding.meeting_ref
        or grant.room_ref != binding.room_ref
        or grant.provider_binding_digest != binding.provider_binding_digest
        or grant.expires_at <= timezone.now()
        or configuration.get("expires_at") is None
        or expected_grant
        != (grant.grant_digest, grant.session_nonce_digest, grant.credential_digest)
    ):
        raise PermissionDenied("Media binding mismatch")
    identity = _validate_identity(grant, configuration)
    reference = uuid4()
    configuration = {
        **configuration,
        "expires_at": min(grant.expires_at, configuration["expires_at"]),
        "media_token_binding_ref": str(reference),
    }
    result = utils.generate_livekit_config(**configuration)
    claims = jwt.decode(
        result["token"],
        settings.LIVEKIT_CONFIGURATION["api_secret"],
        algorithms=["HS256"],
    )
    if (
        claims["sub"] != identity
        or claims["video"]["room"] != str(binding.room_id)
        or claims["attributes"]["mastrao.media_token_binding_ref"] != str(reference)
        or claims["video"].get("canUpdateOwnMetadata") is not False
    ):
        raise PermissionDenied("Media token mismatch")
    models.MastraoMediaTokenBinding.objects.create(
        id=reference,
        room_binding=binding,
        host_grant=grant if isinstance(grant, models.MastraoHostGrant) else None,
        guest_grant=grant if isinstance(grant, models.MastraoGuestGrant) else None,
        rtc_identity=identity,
        grant_digest=grant.grant_digest,
        session_nonce_digest=grant.session_nonce_digest,
        authorization_digest=authorization_digest,
        token_digest=hashlib.sha256(result["token"].encode()).hexdigest(),
        expires_at=datetime.fromtimestamp(claims["exp"], tz=UTC),
    )
    return result
