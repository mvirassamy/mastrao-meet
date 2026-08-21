"""Browser handoff consumer backed by Cabinet Core redemption."""

import hashlib
import re
import secrets
import time
from datetime import UTC, datetime

from django.conf import settings
from django.contrib.auth import login
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.http import HttpResponse, JsonResponse
from django.middleware.csrf import get_token
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from core import models
from core.mastrao_core_http import post_core_json, read_bounded_core_json
from core.mastrao_host_contract import (
    HostHandoffRefused,
    compact_digest,
    sign_redemption,
    verify_host_grant,
    verify_host_handoff,
)
from core.mastrao_host_grant import (
    SESSION_COMPACT_GRANTS_KEY,
    SESSION_NONCE_KEY,
    SESSION_PLATFORM_REF_KEY,
)
from core.mastrao_identity import mastrao_host_subject, mastrao_technical_owner_subject
from core.mastrao_room_lifecycle import MastraoRoomClosed, assert_mastrao_room_open

MAX_HANDOFF_BODY_BYTES = 20_000
MAX_HANDOFF_ATTEMPTS_PER_MINUTE = 3
COMPACT_JWS = re.compile(
    r"^[A-Za-z0-9_-]{1,4096}\.[A-Za-z0-9_-]{1,12288}\.[A-Za-z0-9_-]{1,4096}$"
)
SESSION_BACKEND = "core.authentication.handoff.MastraoHostAuthenticationBackend"


def _increment_attempt_counter(key):
    if cache.add(key, 1, timeout=70):
        return 1
    try:
        return cache.incr(key)
    except ValueError as error:
        raise HostHandoffRefused(status=503) from error


def _admit_public_attempt(_request, host_handoff):
    """Reject obvious garbage and bound public redemption work."""

    if not isinstance(host_handoff, str) or not COMPACT_JWS.fullmatch(host_handoff):
        raise HostHandoffRefused()
    verify_host_handoff(host_handoff)
    bucket = int(timezone.now().timestamp()) // 60
    credential = hashlib.sha256(host_handoff.encode("ascii")).hexdigest()
    key = f"mastrao-host-handoff:{credential}:{bucket}"
    if _increment_attempt_counter(key) > MAX_HANDOFF_ATTEMPTS_PER_MINUTE:
        raise HostHandoffRefused()
    global_key = f"mastrao-host-handoff:global:{bucket}"
    if _increment_attempt_counter(global_key) > (
        settings.MASTRAO_HOST_HANDOFF_GLOBAL_ATTEMPTS_PER_MINUTE
    ):
        raise HostHandoffRefused(status=503)


def _safe_json_response(response):
    return read_bounded_core_json(
        response, HostHandoffRefused, expected_fields={"host_grant"}
    )


def _redeem(host_handoff):
    assertion, redemption = sign_redemption(host_handoff)
    body = post_core_json(
        endpoint=settings.MASTRAO_CORE_REDEMPTION_ENDPOINT,
        expected_path="/internal/v1/meetings/host-handoffs/redeem",
        body={
            "host_handoff": host_handoff,
            "redemption_assertion": assertion,
        },
        timeout=settings.MASTRAO_CORE_REDEMPTION_TIMEOUT_SECONDS,
        refusal=HostHandoffRefused,
        expected_fields={"host_grant"},
    )
    grant = verify_host_grant(body["host_grant"])
    if grant["redemption_id"] != redemption["redemption_id"] or grant[
        "credential_digest"
    ] != compact_digest(host_handoff):
        raise HostHandoffRefused()
    return grant, body["host_grant"]


def _resolve_identity(host_ref):
    subject = mastrao_host_subject(host_ref)
    identity = (
        models.MastraoHostIdentity.objects.select_for_update()
        .select_related("user")
        .filter(host_ref=host_ref)
        .first()
    )
    if identity:
        if (
            identity.user.sub != subject
            or identity.user.is_device
            or not identity.user.is_active
        ):
            raise HostHandoffRefused()
        return identity
    if models.User.objects.filter(sub=subject).exists():
        raise HostHandoffRefused()
    user = models.User(sub=subject, is_device=False)
    user.set_unusable_password()
    user.save()
    return models.MastraoHostIdentity.objects.create(host_ref=host_ref, user=user)


def _binding_for(grant):
    binding = (
        models.MastraoRoomBinding.objects.select_for_update()
        .select_related("room", "owner")
        .filter(meeting_ref=grant["meeting_ref"], room_ref=grant["room_ref"])
        .first()
    )
    if (  # pylint: disable=too-many-boolean-expressions
        not binding
        or binding.provider_binding_digest != grant["provider_binding_digest"]
        or binding.room.access_level != models.RoomAccessLevel.RESTRICTED
        or not binding.owner.is_device
        or binding.owner.sub != mastrao_technical_owner_subject(binding.owner_ref)
        or not models.ResourceAccess.objects.filter(
            resource=binding.room,
            user=binding.owner,
            role=models.RoleChoices.OWNER,
        ).exists()
    ):
        raise HostHandoffRefused()
    try:
        assert_mastrao_room_open(binding)
    except MastraoRoomClosed as error:
        raise HostHandoffRefused() from error
    return binding


def _commit_grant(request, grant, compact_grant):
    remaining_seconds = int(grant["expires_at"] - time.time())
    if remaining_seconds < 1:
        raise HostHandoffRefused()
    try:
        with transaction.atomic():
            binding = _binding_for(grant)
            identity = _resolve_identity(grant["host_ref"])
            existing_user_id = (
                request.user.pk if request.user.is_authenticated else None
            )
            existing_nonce = request.session.get(SESSION_NONCE_KEY)
            existing_platform_session_ref = request.session.get(
                SESSION_PLATFORM_REF_KEY
            )
            login(request, identity.user, backend=SESSION_BACKEND)
            session_nonce = (
                existing_nonce
                if existing_user_id == identity.user.pk
                and existing_platform_session_ref == grant["platform_session_ref"]
                and isinstance(existing_nonce, str)
                and existing_nonce
                else secrets.token_urlsafe(32)
            )
            request.session[SESSION_NONCE_KEY] = session_nonce
            request.session[SESSION_PLATFORM_REF_KEY] = grant["platform_session_ref"]
            compact_grants = request.session.get(SESSION_COMPACT_GRANTS_KEY, {})
            if not isinstance(compact_grants, dict):
                compact_grants = {}
            if existing_platform_session_ref != grant["platform_session_ref"]:
                compact_grants = {}
            compact_grants[grant["grant_ref"]] = compact_grant
            request.session[SESSION_COMPACT_GRANTS_KEY] = compact_grants
            request.session.set_expiry(remaining_seconds)
            created = models.MastraoHostGrant.objects.create(
                handoff_ref=grant["handoff_ref"],
                grant_ref=grant["grant_ref"],
                grant_digest=compact_digest(compact_grant),
                credential_digest=grant["credential_digest"],
                meeting_ref=grant["meeting_ref"],
                room_ref=grant["room_ref"],
                provider_binding_digest=grant["provider_binding_digest"],
                identity=identity,
                room_binding=binding,
                platform_session_ref=grant["platform_session_ref"],
                session_nonce_digest=hashlib.sha256(session_nonce.encode()).hexdigest(),
                issued_at=datetime.fromtimestamp(grant["issued_at"], tz=UTC),
                expires_at=datetime.fromtimestamp(grant["expires_at"], tz=UTC),
            )
            return created, binding
    except (IntegrityError, ValidationError, HostHandoffRefused):
        request.session.flush()
        raise


@csrf_exempt
@require_POST
def consume_mastrao_host_handoff(request):
    """Redeem one short bearer, establish the host session, then cleanly redirect."""

    if not settings.MASTRAO_HOST_HANDOFF_ENABLED:
        return JsonResponse({"message": "Not found"}, status=404)
    try:
        if request.headers.get("origin") != settings.MASTRAO_PLATFORM_ORIGIN:
            raise HostHandoffRefused()
        if request.headers.get("sec-fetch-site") not in {
            "cross-site",
            "same-site",
            "same-origin",
        }:
            raise HostHandoffRefused()
        if request.content_type != "application/x-www-form-urlencoded":
            raise HostHandoffRefused()
        declared = request.headers.get("content-length")
        if (
            declared is None
            or not declared.isdecimal()
            or int(declared) > MAX_HANDOFF_BODY_BYTES
            or len(request.body) > MAX_HANDOFF_BODY_BYTES
        ):
            raise HostHandoffRefused()
        fields = request.POST
        if set(fields) != {"host_handoff"} or len(fields.getlist("host_handoff")) != 1:
            raise HostHandoffRefused()
        _admit_public_attempt(request, fields["host_handoff"])
        grant, compact_grant = _redeem(fields["host_handoff"])
        _, binding = _commit_grant(request, grant, compact_grant)
        # The handoff authenticates an otherwise anonymous browser. Seed the
        # CSRF cookie now so the first authenticated room mutation can carry a
        # matching token instead of being rejected by DRF session auth.
        get_token(request)
        response = HttpResponse(status=303)
        response["Location"] = f"/{binding.room.slug}"
        response["Cache-Control"] = "private, no-store"
        response["Referrer-Policy"] = "no-referrer"
        return response
    except HostHandoffRefused as error:
        return JsonResponse(
            {"message": "Not found" if error.status == 404 else "Unavailable"},
            status=error.status,
            headers={
                "Cache-Control": "private, no-store",
                "Referrer-Policy": "no-referrer",
            },
        )
    except (IntegrityError, ValidationError):
        request.session.flush()
        return JsonResponse({"message": "Not found"}, status=404)
