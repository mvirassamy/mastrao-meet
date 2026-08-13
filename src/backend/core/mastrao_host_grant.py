"""Request-time projection of one expiring, session-bound media-host grant."""

import hashlib
from datetime import timedelta

from django.utils import timezone

from core import models
from core.mastrao_identity import is_mastrao_host_subject

SESSION_NONCE_KEY = "mastrao_host_session_nonce"


def _nonce_digest(nonce):
    if not isinstance(nonce, str) or len(nonce) < 32:
        return None
    return hashlib.sha256(nonce.encode()).hexdigest()


def active_host_grant(request, room):
    """Resolve the current session-bound grant for this exact room."""
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated or not is_mastrao_host_subject(user.sub):
        return None
    digest = _nonce_digest(request.session.get(SESSION_NONCE_KEY))
    if digest is None:
        return None
    return (
        models.MastraoHostGrant.objects.select_related("identity", "room_binding")
        .filter(
            identity__user=user,
            room_binding__room=room,
            session_nonce_digest=digest,
            expires_at__gt=timezone.now(),
        )
        .order_by("-expires_at")
        .first()
    )


def host_media_role(request, room):
    """Project an active host grant to the existing media-admin role."""
    return models.RoleChoices.ADMIN if active_host_grant(request, room) else None


def host_grant_ttl(request, room):
    """Return the remaining LiveKit credential lifetime for the grant."""
    grant = active_host_grant(request, room)
    if not grant:
        return None
    seconds = max(1, int((grant.expires_at - timezone.now()).total_seconds()))
    return timedelta(seconds=seconds)
