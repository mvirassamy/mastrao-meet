"""Request-time projection of one anonymous Mastrao guest grant."""

import hashlib
import re

from django.utils import timezone

from core import models
from core.mastrao_host_grant import active_host_grant

SESSION_NONCE_KEY = "mastrao_guest_session_nonce"
SESSION_GRANT_REF_KEY = "mastrao_guest_grant_ref"
SESSION_COMPACT_GRANT_KEY = "mastrao_guest_compact_grant"
CANONICAL_ROOM_SLUG = re.compile(r"^room_[a-f0-9]{32}$")


def _nonce_digest(nonce):
    if not isinstance(nonce, str) or len(nonce) < 32:
        return None
    return hashlib.sha256(nonce.encode()).hexdigest()


def _session_bound_guest_grant(
    request, room, *, observed_at=None, include_closed=False
):
    """Resolve the exact non-expired guest grant bound to this browser and room."""

    if getattr(request, "user", None) and request.user.is_authenticated:
        return None
    digest = _nonce_digest(request.session.get(SESSION_NONCE_KEY))
    grant_ref = request.session.get(SESSION_GRANT_REF_KEY)
    if digest is None or not isinstance(grant_ref, str):
        return None
    observed_at = observed_at or timezone.now()
    queryset = models.MastraoGuestGrant.objects.select_related("room_binding").filter(
        grant_ref=grant_ref,
        room_binding__room=room,
        session_nonce_digest=digest,
        expires_at__gt=observed_at,
    )
    if not include_closed:
        queryset = queryset.filter(
            room_binding__closing_at__isnull=True,
            room_binding__closure__isnull=True,
        )
    return queryset.first()


def active_guest_grant(request, room, *, observed_at=None):
    """Resolve an open-room guest grant for media and lobby capabilities."""

    return _session_bound_guest_grant(request, room, observed_at=observed_at)


def active_guest_lifecycle_grant(request, room, *, observed_at=None):
    """Resolve the exact guest grant after close for lifecycle reads only."""

    return _session_bound_guest_grant(
        request, room, observed_at=observed_at, include_closed=True
    )


def active_guest_lifecycle_grant_for_room_ref(request, room_ref, *, observed_at=None):
    """Resolve a guest grant by canonical room ref before room disclosure."""

    if getattr(request, "user", None) and request.user.is_authenticated:
        return None
    digest = _nonce_digest(request.session.get(SESSION_NONCE_KEY))
    grant_ref = request.session.get(SESSION_GRANT_REF_KEY)
    if digest is None or not isinstance(grant_ref, str):
        return None
    observed_at = observed_at or timezone.now()
    return (
        models.MastraoGuestGrant.objects.select_related(
            "room_binding", "room_binding__room", "room_binding__closure"
        )
        .filter(
            grant_ref=grant_ref,
            room_binding__room_ref=room_ref,
            session_nonce_digest=digest,
            expires_at__gt=observed_at,
        )
        .first()
    )


def active_guest_compact_grant(request, grant):
    """Return the Core bearer only when it belongs to the active local session."""

    compact = request.session.get(SESSION_COMPACT_GRANT_KEY)
    if not isinstance(compact, str):
        return None
    if request.session.get(SESSION_GRANT_REF_KEY) != grant.grant_ref:
        return None
    return compact


def can_access_canonical_room(request, room):
    """Require an exact host or guest projection for a canonical Mastrao room."""

    if not CANONICAL_ROOM_SLUG.fullmatch(room.slug):
        return True
    if not hasattr(room, "mastrao_binding"):
        return True
    return (
        active_host_grant(request, room) is not None
        or active_guest_grant(request, room) is not None
    )
