"""Request-time projection of one expiring, session-bound media-host grant."""

import hashlib

from django.utils import timezone

from core import models
from core.mastrao_identity import is_mastrao_host_subject

SESSION_NONCE_KEY = "mastrao_host_session_nonce"
SESSION_PLATFORM_REF_KEY = "mastrao_host_platform_session_ref"


def _nonce_digest(nonce):
    if not isinstance(nonce, str) or len(nonce) < 32:
        return None
    return hashlib.sha256(nonce.encode()).hexdigest()


def active_host_grant(request, room, *, observed_at=None):
    """Resolve the current session-bound grant for this exact room."""
    user = getattr(request, "user", None)
    if (
        not user
        or not user.is_authenticated
        or not user.is_active
        or not is_mastrao_host_subject(user.sub)
    ):
        return None
    digest = _nonce_digest(request.session.get(SESSION_NONCE_KEY))
    platform_session_ref = request.session.get(SESSION_PLATFORM_REF_KEY)
    if digest is None or not isinstance(platform_session_ref, str):
        return None
    observed_at = observed_at or timezone.now()
    return (
        models.MastraoHostGrant.objects.select_related("identity", "room_binding")
        .filter(
            identity__user=user,
            room_binding__room=room,
            session_nonce_digest=digest,
            platform_session_ref=platform_session_ref,
            expires_at__gt=observed_at,
        )
        .order_by("-expires_at")
        .first()
    )


def host_media_projection(request, room):
    """Resolve one grant snapshot into its media role and bounded token TTL."""

    observed_at = timezone.now()
    grant = active_host_grant(request, room, observed_at=observed_at)
    if not grant:
        return None, None
    if (grant.expires_at - observed_at).total_seconds() < 1:
        return None, None
    return models.RoleChoices.ADMIN, grant.expires_at
