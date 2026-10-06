"""Exchange a durable lobby locator through the existing guest session machinery."""

import hashlib
import json
import re

from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from core.mastrao_guest_contract import (
    EXTERNAL_ID,
    GuestHandoffRefused,
    _base_assertion,
    _sign,
    verify_guest_bootstrap,
)
from core.mastrao_guest_grant import SESSION_NONCE_KEY
from core.mastrao_guest_handoff import (
    GUEST_RETRY_COOKIE,
    _commit_grant,
    _post_core,
    _safe_headers,
    _same_origin,
)

SHARE_REF = re.compile(r"^share_[A-Za-z0-9_-]{32}$")
REDEMPTION_ID = re.compile(r"^redemption_[a-f0-9]{32}$")


@csrf_exempt
@require_POST
def consume_guest_share_link(request):
    """The locator grants only a bounded lobby session, never media or host rights."""
    headers = _safe_headers()
    try:
        if (
            not settings.MASTRAO_MEETING_INTEGRATION_CONFIGURED
            or request.user.is_authenticated
            or not _same_origin(request)
            or request.content_type != "application/json"
            or len(request.body) > 18_432
        ):
            raise GuestHandoffRefused()
        body = json.loads(request.body)
        required = {
            "organization_external_id",
            "share_ref",
            "redemption_id",
        }
        if (
            not isinstance(body, dict)
            or not required <= set(body)
            or set(body) - required - {"choice_token"}
        ):
            raise GuestHandoffRefused()
        choice_token = body.get("choice_token")
        if choice_token is not None and (
            not isinstance(choice_token, str)
            or len(choice_token) > 16_384
            or not re.fullmatch(
                r"[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", choice_token
            )
        ):
            raise GuestHandoffRefused()
        organization = body["organization_external_id"]
        share_ref = body["share_ref"]
        redemption_id = body["redemption_id"]
        for value, pattern in (
            (organization, EXTERNAL_ID),
            (share_ref, SHARE_REF),
            (redemption_id, REDEMPTION_ID),
        ):
            if not isinstance(value, str) or not pattern.fullmatch(value):
                raise GuestHandoffRefused()
        nonce = request.session.get(SESSION_NONCE_KEY)
        if (
            not isinstance(nonce, str)
            or len(nonce) < 32
            or nonce != request.COOKIES.get(GUEST_RETRY_COOKIE)
        ):
            raise GuestHandoffRefused()
        payload = {
            **_base_assertion(
                "mastrao.meet-guest-share-redemption", "redeem_guest_share_link"
            ),
            "organization_external_id": organization,
            "share_ref_digest": hashlib.sha256(share_ref.encode("ascii")).hexdigest(),
            "redemption_id": redemption_id,
            "session_nonce_digest": hashlib.sha256(nonce.encode()).hexdigest(),
        }
        # The attempt and secret nonce must survive response loss/process restart.
        attempt = {
            "redemption_id": redemption_id,
            "organization_external_id": organization,
            "share_ref_digest": payload["share_ref_digest"],
        }
        previous = request.session.get("mastrao_share_redemption")
        if previous and previous != attempt:
            raise GuestHandoffRefused()
        request.session["mastrao_share_redemption"] = attempt
        request.session.save()
        result = _post_core(
            "MASTRAO_CORE_GUEST_SHARE_REDEMPTION_ENDPOINT",
            "/internal/v1/meetings/guest-share-links/redeem",
            {
                "share_ref": share_ref,
                "redemption_assertion": _sign(
                    payload, "mastrao-meeting-guest-share-redemption+jws"
                ),
            },
            "guest_grant",
        )
        grant = verify_guest_bootstrap(result["guest_grant"])
        if (
            grant["organization_external_id"] != organization
            or grant["redemption_id"] != redemption_id
            or grant["credential_digest"] != payload["share_ref_digest"]
        ):
            raise GuestHandoffRefused()
        # Signature, lifetime and exact room/provider checks are the existing boundary.
        _, binding = _commit_grant(request, grant, result["guest_grant"])
        if choice_token is not None:
            choices = request.session.get("mastrao_video_choices", {})
            choices[binding.meeting_ref] = choice_token
            request.session["mastrao_video_choices"] = choices
        request.session.pop("mastrao_share_redemption", None)
        return JsonResponse({"room_url": f"/{binding.room.slug}"}, headers=headers)
    except (GuestHandoffRefused, ValueError, TypeError) as error:
        status = error.status if isinstance(error, GuestHandoffRefused) else 404
        if status == 404:
            request.session.pop("mastrao_share_redemption", None)
        return JsonResponse({"message": "Unavailable"}, status=status, headers=headers)
