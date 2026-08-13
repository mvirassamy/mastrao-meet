"""Browser handoff consumer backed by Cabinet Core redemption."""

import hashlib
import json
import secrets
from datetime import UTC, datetime
from urllib.parse import urlparse

from django.conf import settings
from django.contrib.auth import login
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.http import HttpResponse, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

import requests

from core import models
from core.mastrao_host_contract import (
    HostHandoffRefused,
    compact_digest,
    sign_redemption,
    verify_host_grant,
)
from core.mastrao_host_grant import SESSION_NONCE_KEY
from core.mastrao_identity import mastrao_host_subject, mastrao_technical_owner_subject

MAX_HANDOFF_BODY_BYTES = 20_000
SESSION_BACKEND = "core.authentication.handoff.MastraoHostAuthenticationBackend"


def _safe_json_response(response):
    declared = response.headers.get("content-length")
    if declared is not None and (not declared.isdecimal() or int(declared) > 20_000):
        raise HostHandoffRefused(status=503)
    if len(response.content) > 20_000 or response.status_code != 200:
        raise HostHandoffRefused(status=503 if response.status_code >= 500 else 404)
    try:
        body = response.json()
    except (ValueError, json.JSONDecodeError) as error:
        raise HostHandoffRefused(status=503) from error
    if not isinstance(body, dict) or set(body) != {"host_grant"}:
        raise HostHandoffRefused(status=503)
    return body


def _redeem(host_handoff):
    endpoint = urlparse(settings.MASTRAO_CORE_REDEMPTION_ENDPOINT)
    if (  # pylint: disable=too-many-boolean-expressions
        endpoint.scheme != "http"
        or endpoint.hostname
        not in {
            "127.0.0.1",
            "localhost",
            "::1",
            "host.docker.internal",
            "127.0.0.1.nip.io",
        }
        or endpoint.username
        or endpoint.password
        or endpoint.query
        or endpoint.fragment
    ):
        raise HostHandoffRefused(status=503)
    assertion, redemption = sign_redemption(host_handoff)
    try:
        response = requests.post(
            settings.MASTRAO_CORE_REDEMPTION_ENDPOINT,
            json={
                "host_handoff": host_handoff,
                "redemption_assertion": assertion,
            },
            timeout=settings.MASTRAO_CORE_REDEMPTION_TIMEOUT_SECONDS,
            allow_redirects=False,
        )
    except requests.RequestException as error:
        raise HostHandoffRefused(status=503) from error
    body = _safe_json_response(response)
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
        if identity.user.sub != subject or identity.user.is_device:
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
    return binding


def _commit_grant(request, grant, compact_grant):
    session_nonce = secrets.token_urlsafe(32)
    try:
        with transaction.atomic():
            binding = _binding_for(grant)
            identity = _resolve_identity(grant["host_ref"])
            login(request, identity.user, backend=SESSION_BACKEND)
            request.session[SESSION_NONCE_KEY] = session_nonce
            request.session.set_expiry(
                max(1, grant["expires_at"] - int(timezone.now().timestamp()))
            )
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
        grant, compact_grant = _redeem(fields["host_handoff"])
        _, binding = _commit_grant(request, grant, compact_grant)
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
