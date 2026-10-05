"""Recover exact-meeting host access via the existing authenticated Platform flow."""

import secrets
import time
from uuid import uuid4

from django.http import JsonResponse
from django.views.decorators.http import require_POST

from core import models
from core.mastrao_guest_grant import CANONICAL_ROOM_SLUG
from core.mastrao_host_contract import (
    HostHandoffRefused,
    compact_digest,
    verify_host_handoff,
)
from core.mastrao_host_grant import SESSION_NONCE_KEY, SESSION_PLATFORM_REF_KEY
from core.mastrao_host_handoff import _admit_public_attempt, _commit_grant, _redeem
from core.mastrao_meeting_history import IDEMPOTENCY_KEY
from core.mastrao_platform_facade import PlatformFacadeError, request_platform


def _redemption_for_attempt(previous, handoff_digest, handoff_expires_at):
    """Only a persisted attempt may replay its exact credential after handoff expiry."""
    if previous:
        if previous["handoff_digest"] != handoff_digest:
            raise HostHandoffRefused()
        return previous["redemption_id"]
    if handoff_expires_at <= time.time():
        raise HostHandoffRefused()
    return f"redemption_{uuid4().hex}"


@require_POST
def recover_meeting_host(request, room_ref):
    """OIDC/selected-organization/current ACL authorize a fresh handoff, not the URL."""
    headers = {"Cache-Control": "private, no-store", "Referrer-Policy": "no-referrer"}
    key = request.headers.get("X-Idempotency-Key", "")
    try:
        if not request.user.is_authenticated:
            raise PlatformFacadeError(status=401)
        if not CANONICAL_ROOM_SLUG.fullmatch(room_ref):
            raise PlatformFacadeError(status=404)
        if not IDEMPOTENCY_KEY.fullmatch(key) or request.body:
            raise PlatformFacadeError(status=422)
        binding = (
            models.MastraoRoomBinding.objects.select_related("room")
            .filter(room__slug=room_ref)
            .first()
        )
        if binding is None:
            raise PlatformFacadeError(status=404)
        body, _ = request_platform(
            request,
            "POST",
            f"/api/meet/meetings/{binding.meeting_ref}/host-handoff",
            accepted_statuses={201},
            options={"idempotency_key": key},
        )
        handoff = body.get("host_handoff")
        if (
            body.get("meeting_ref") != binding.meeting_ref
            or body.get("room_ref") != binding.room_ref
        ):
            raise PlatformFacadeError()
        _admit_public_attempt(request, handoff)
        claims = verify_host_handoff(handoff)
        if (
            claims["meeting_ref"] != binding.meeting_ref
            or claims["room_ref"] != binding.room_ref
        ):
            raise HostHandoffRefused()
        attempt_key = ":".join(
            (
                claims["organization_external_id"],
                claims["host_ref"],
                claims["platform_session_ref"],
                binding.meeting_ref,
                key,
            )
        )
        attempts = request.session.get("mastrao_host_recovery", {})
        previous = attempts.get(attempt_key)
        digest = compact_digest(handoff)
        redemption_id = _redemption_for_attempt(previous, digest, claims["expires_at"])
        nonce = request.session.get(SESSION_NONCE_KEY)
        if (
            request.session.get(SESSION_PLATFORM_REF_KEY)
            != claims["platform_session_ref"]
            or not isinstance(nonce, str)
            or len(nonce) < 32
        ):
            request.session[SESSION_NONCE_KEY] = secrets.token_urlsafe(32)
        request.session[SESSION_PLATFORM_REF_KEY] = claims["platform_session_ref"]
        attempts[attempt_key] = {
            "handoff_digest": digest,
            "redemption_id": redemption_id,
        }
        request.session["mastrao_host_recovery"] = attempts
        # Save the attempt before consuming any authority at Core.
        request.session.save()
        grant, compact = _redeem(handoff, redemption_id)
        _, resolved = _commit_grant(request, grant, compact, retain_oidc_user=True)
        if resolved.pk != binding.pk:
            raise HostHandoffRefused()
        return JsonResponse({"room_url": f"/{binding.room.slug}"}, headers=headers)
    except (PlatformFacadeError, HostHandoffRefused) as error:
        return JsonResponse(
            {"message": "Unavailable"}, status=error.status, headers=headers
        )
