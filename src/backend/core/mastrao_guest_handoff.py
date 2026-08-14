"""Online consumer and Core client for anonymous Mastrao guest grants."""

import hashlib
import json
import re
import secrets
import threading
import time
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse

from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

import requests

from core import models, utils
from core.mastrao_guest_contract import (
    GuestHandoffRefused,
    compact_digest,
    sign_guest_decision,
    sign_guest_decision_receipt,
    sign_guest_media_request,
    sign_guest_redemption,
    verify_guest_bootstrap,
    verify_guest_decision_grant,
    verify_guest_invitation,
    verify_guest_media_grant,
)
from core.mastrao_guest_grant import (
    SESSION_COMPACT_GRANT_KEY,
    SESSION_GRANT_REF_KEY,
    SESSION_NONCE_KEY,
    active_guest_compact_grant,
    active_guest_grant,
)
from core.mastrao_host_grant import active_host_compact_grant, active_host_grant

MAX_BODY_BYTES = 20_000
MAX_ATTEMPTS_PER_MINUTE = 5
MAX_CONCURRENT_GUEST_VERIFICATIONS = 8
GUEST_RETRY_COOKIE = "mastraoGuestRetry"
COMPACT_JWS = re.compile(
    r"^[A-Za-z0-9_-]{1,4096}\.[A-Za-z0-9_-]{1,12288}\.[A-Za-z0-9_-]{1,4096}$"
)
_GUEST_VERIFY_SLOTS = threading.BoundedSemaphore(MAX_CONCURRENT_GUEST_VERIFICATIONS)


def _safe_headers():
    return {
        "Cache-Control": "private, no-store",
        "Referrer-Policy": "no-referrer",
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "frame-ancestors 'none'",
    }


def _same_origin(request):
    origin = request.headers.get("origin")
    if (
        request.headers.get("sec-fetch-site") not in {"same-origin", "same-site"}
        or not origin
    ):
        return False
    if not isinstance(settings.APPLICATION_BASE_URL, str):
        return False
    parsed = urlparse(origin)
    expected = urlparse(settings.APPLICATION_BASE_URL)
    return (
        parsed.scheme in {"http", "https"}
        and not parsed.username
        and not parsed.password
        and parsed.path in {"", "/"}
        and not parsed.query
        and not parsed.fragment
        and parsed.scheme == expected.scheme
        and parsed.netloc == expected.netloc
    )


def _safe_json_response(response, expected_field=None):
    declared = response.headers.get("content-length")
    if declared is not None and (not declared.isdecimal() or int(declared) > 20_000):
        response.close()
        raise GuestHandoffRefused(status=503)
    if response.status_code != 200:
        response.close()
        raise GuestHandoffRefused(status=503 if response.status_code >= 500 else 404)
    try:
        chunks = []
        size = 0
        for chunk in response.iter_content(chunk_size=4_096):
            size += len(chunk)
            if size > 20_000:
                raise GuestHandoffRefused(status=503)
            chunks.append(chunk)
        body = json.loads(b"".join(chunks))
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as error:
        raise GuestHandoffRefused(status=503) from error
    finally:
        response.close()
    if not isinstance(body, dict):
        raise GuestHandoffRefused(status=503)
    if expected_field and set(body) != {expected_field}:
        raise GuestHandoffRefused(status=503)
    return body


def _core_endpoint(setting_name, expected_path):
    value = getattr(settings, setting_name, "")
    endpoint = urlparse(value)
    allowed_hosts = {
        "127.0.0.1",
        "localhost",
        "::1",
        "host.docker.internal",
        "127.0.0.1.nip.io",
    }
    if endpoint.scheme != "http" or endpoint.hostname not in allowed_hosts:
        raise GuestHandoffRefused(status=503)
    if any((endpoint.username, endpoint.password, endpoint.query, endpoint.fragment)):
        raise GuestHandoffRefused(status=503)
    if endpoint.path != expected_path:
        raise GuestHandoffRefused(status=503)
    return value


def _post_core(setting_name, expected_path, body, expected_field=None):
    endpoint = _core_endpoint(setting_name, expected_path)
    try:
        with requests.Session() as session:
            session.trust_env = False
            response = session.post(
                endpoint,
                json=body,
                timeout=settings.MASTRAO_CORE_GUEST_TIMEOUT_SECONDS,
                allow_redirects=False,
                stream=True,
            )
            return _safe_json_response(response, expected_field)
    except requests.RequestException as error:
        raise GuestHandoffRefused(status=503) from error


def _grant_cache_key(grant_ref):
    return f"mastrao-guest-compact:{grant_ref}"


def _decision_cache_key(decision_id):
    return f"mastrao-guest-decision:{decision_id}"


def _cache_compact_grant(grant, compact_grant):
    remaining = int((grant.expires_at - timezone.now()).total_seconds())
    if remaining > 0:
        cache.set(_grant_cache_key(grant.grant_ref), compact_grant, timeout=remaining)


def remember_guest_compact_grant(request, grant):
    """Restore the transient host-decision copy from the guest's bound session."""

    compact = active_guest_compact_grant(request, grant)
    if compact and compact_digest(compact) == grant.grant_digest:
        _cache_compact_grant(grant, compact)


def _binding_for(grant):
    binding = (
        models.MastraoRoomBinding.objects.select_for_update()
        .select_related("room", "owner")
        .filter(meeting_ref=grant["meeting_ref"], room_ref=grant["room_ref"])
        .first()
    )
    if (
        not binding
        or binding.provider_binding_digest != grant["provider_binding_digest"]
        or binding.room.access_level != models.RoomAccessLevel.RESTRICTED
    ):
        raise GuestHandoffRefused()
    return binding


def _redeem(guest_invitation, redemption_id):
    invitation = verify_guest_invitation(guest_invitation)
    assertion, asserted = sign_guest_redemption(guest_invitation, redemption_id)
    body = _post_core(
        "MASTRAO_CORE_GUEST_REDEMPTION_ENDPOINT",
        "/internal/v1/meetings/guest-invitations/redeem",
        {
            "guest_invitation": guest_invitation,
            "redemption_assertion": assertion,
        },
        "guest_grant",
    )
    grant = verify_guest_bootstrap(body["guest_grant"])
    if (
        grant["invitation_ref"] != invitation["invitation_ref"]
        or grant["organization_external_id"] != invitation["organization_external_id"]
        or grant["redemption_id"] != asserted["redemption_id"]
        or grant["credential_digest"] != asserted["credential_digest"]
    ):
        raise GuestHandoffRefused()
    return grant, body["guest_grant"]


@transaction.atomic
def _commit_grant(request, grant, compact_grant):
    remaining = int(grant["expires_at"] - time.time())
    if remaining < 1:
        raise GuestHandoffRefused()
    binding = _binding_for(grant)
    existing = (
        models.MastraoGuestGrant.objects.select_for_update()
        .filter(redemption_id=grant["redemption_id"])
        .first()
    )
    current_nonce = request.COOKIES.get(GUEST_RETRY_COOKIE)
    if not isinstance(current_nonce, str) or len(current_nonce) < 32:
        raise GuestHandoffRefused()
    current_nonce_digest = hashlib.sha256(current_nonce.encode()).hexdigest()
    if existing:
        exact = (
            current_nonce_digest == existing.session_nonce_digest
            and existing.grant_ref == grant["grant_ref"]
            and existing.grant_digest == compact_digest(compact_grant)
            and existing.room_binding_id == binding.id
        )
        if not exact:
            raise GuestHandoffRefused()
        request.session[SESSION_GRANT_REF_KEY] = existing.grant_ref
        request.session[SESSION_NONCE_KEY] = current_nonce
        request.session[SESSION_COMPACT_GRANT_KEY] = compact_grant
        request.session.set_expiry(remaining)
        _cache_compact_grant(existing, compact_grant)
        return existing, binding
    try:
        created = models.MastraoGuestGrant.objects.create(
            grant_ref=grant["grant_ref"],
            redemption_id=grant["redemption_id"],
            invitation_ref=grant["invitation_ref"],
            guest_ref=grant["guest_ref"],
            organization_external_id=grant["organization_external_id"],
            grant_digest=compact_digest(compact_grant),
            credential_digest=grant["credential_digest"],
            meeting_ref=grant["meeting_ref"],
            room_ref=grant["room_ref"],
            provider_binding_digest=grant["provider_binding_digest"],
            room_binding=binding,
            session_nonce_digest=current_nonce_digest,
            issued_at=datetime.fromtimestamp(grant["issued_at"], tz=UTC),
            expires_at=datetime.fromtimestamp(grant["expires_at"], tz=UTC),
        )
    except (IntegrityError, ValidationError) as error:
        raise GuestHandoffRefused() from error
    request.session[SESSION_GRANT_REF_KEY] = grant["grant_ref"]
    request.session[SESSION_NONCE_KEY] = current_nonce
    request.session[SESSION_COMPACT_GRANT_KEY] = compact_grant
    request.session.set_expiry(remaining)
    _cache_compact_grant(created, compact_grant)
    return created, binding


@csrf_exempt
@require_POST
def establish_mastrao_guest_session(request):
    """Establish a stable anonymous browser nonce before any Core mutation."""

    headers = _safe_headers()
    if not settings.MASTRAO_GUEST_INVITATION_ENABLED:
        return JsonResponse({"message": "Not found"}, status=404, headers=headers)
    try:
        if request.user.is_authenticated or not _same_origin(request):
            raise GuestHandoffRefused()
        if request.content_type != "application/json":
            raise GuestHandoffRefused()
        declared = request.headers.get("content-length")
        if (
            declared is None
            or not declared.isdecimal()
            or int(declared) > 2
            or request.body != b"{}"
        ):
            raise GuestHandoffRefused()
        nonce = request.COOKIES.get(GUEST_RETRY_COOKIE)
        if not isinstance(nonce, str) or len(nonce) < 32:
            session_nonce = request.session.get(SESSION_NONCE_KEY)
            nonce = (
                session_nonce
                if isinstance(session_nonce, str) and len(session_nonce) >= 32
                else secrets.token_urlsafe(32)
            )
        response = JsonResponse({"version": 1}, status=200, headers=headers)
        response.set_cookie(
            GUEST_RETRY_COOKIE,
            nonce,
            max_age=600,
            secure=settings.SESSION_COOKIE_SECURE,
            httponly=True,
            samesite="Lax",
            path="/",
        )
        return response
    except GuestHandoffRefused as error:
        return JsonResponse(
            {"message": "Not found" if error.status == 404 else "Unavailable"},
            status=error.status,
            headers=headers,
        )


@csrf_exempt
@require_POST
def consume_mastrao_guest_invitation(request):
    """Redeem only after the guest explicitly submits the fragment capability."""

    headers = _safe_headers()
    if not settings.MASTRAO_GUEST_INVITATION_ENABLED:
        return JsonResponse({"message": "Not found"}, status=404, headers=headers)
    try:
        if request.user.is_authenticated:
            raise GuestHandoffRefused()
        if not _same_origin(request) or request.content_type != "application/json":
            raise GuestHandoffRefused()
        declared = request.headers.get("content-length")
        if (
            declared is None
            or not declared.isdecimal()
            or int(declared) > MAX_BODY_BYTES
            or len(request.body) > MAX_BODY_BYTES
        ):
            raise GuestHandoffRefused()
        body = json.loads(request.body)
        if not isinstance(body, dict) or set(body) != {
            "guest_invitation",
            "redemption_id",
        }:
            raise GuestHandoffRefused()
        compact = body["guest_invitation"]
        redemption_id = body["redemption_id"]
        if not isinstance(compact, str) or not COMPACT_JWS.fullmatch(compact):
            raise GuestHandoffRefused()
        if not isinstance(redemption_id, str) or not re.fullmatch(
            r"redemption_[a-f0-9]{32}", redemption_id
        ):
            raise GuestHandoffRefused()
        if not _GUEST_VERIFY_SLOTS.acquire(blocking=False):
            raise GuestHandoffRefused(status=503)
        try:
            verify_guest_invitation(compact)
        finally:
            _GUEST_VERIFY_SLOTS.release()
        bucket = int(timezone.now().timestamp()) // 60
        key = f"mastrao-guest-attempt:{compact_digest(compact)}:{bucket}"
        attempts = 1 if cache.add(key, 1, timeout=70) else cache.incr(key)
        if attempts > MAX_ATTEMPTS_PER_MINUTE:
            raise GuestHandoffRefused()
        grant, compact_grant = _redeem(compact, redemption_id)
        _, binding = _commit_grant(request, grant, compact_grant)
        return JsonResponse(
            {"room_url": f"/{binding.room.slug}"}, status=200, headers=headers
        )
    except (GuestHandoffRefused, ValueError, json.JSONDecodeError) as error:
        status = error.status if isinstance(error, GuestHandoffRefused) else 404
        return JsonResponse(
            {"message": "Not found" if status == 404 else "Unavailable"},
            status=status,
            headers=headers,
        )


def _exact_guest(grant, payload):
    return all(
        payload[name] == getattr(grant, name)
        for name in (
            "invitation_ref",
            "redemption_id",
            "guest_ref",
            "meeting_ref",
            "room_ref",
            "provider_binding_digest",
            "credential_digest",
        )
    )


def decide_guest_admission(request, room, participant_id, allow_entry):
    """Authorize, apply and confirm one canonical guest admission decision."""

    host_grant = active_host_grant(request, room)
    compact_host = host_grant and active_host_compact_grant(request, host_grant)
    guest = models.MastraoGuestGrant.objects.filter(
        room_binding__room=room,
        guest_ref=participant_id,
        expires_at__gt=timezone.now(),
    ).first()
    if not host_grant or not compact_host or not guest:
        raise GuestHandoffRefused()
    compact_guest = cache.get(_grant_cache_key(guest.grant_ref))
    if (
        not isinstance(compact_guest, str)
        or compact_digest(compact_guest) != guest.grant_digest
    ):
        raise GuestHandoffRefused(status=503)
    decision_seed = f"{guest.grant_ref}:{allow_entry}".encode()
    decision_id = f"decision_{hashlib.sha256(decision_seed).hexdigest()}"
    assertion, _ = sign_guest_decision(
        guest, compact_host, compact_guest, decision_id, allow_entry
    )
    body = _post_core(
        "MASTRAO_CORE_GUEST_DECISION_ENDPOINT",
        "/internal/v1/meetings/guest-admissions/decide",
        {
            "host_grant": compact_host,
            "guest_grant": compact_guest,
            "decision_assertion": assertion,
        },
        "decision_grant",
    )
    decision = verify_guest_decision_grant(body["decision_grant"])
    expected = "allow" if allow_entry else "deny"
    if (
        decision["decision_id"] != decision_id
        or decision["decision"] != expected
        or not _exact_guest(guest, decision)
    ):
        raise GuestHandoffRefused()
    state = (
        models.MastraoGuestGrant.AdmissionState.ALLOWED
        if allow_entry
        else models.MastraoGuestGrant.AdmissionState.DENIED
    )
    digest = compact_digest(body["decision_grant"])
    if guest.decision_ref and (
        guest.decision_ref != decision_id or guest.decision_allow != allow_entry
    ):
        raise GuestHandoffRefused(status=409)
    guest.admission_state = state
    guest.decision_ref = decision_id
    guest.decision_allow = allow_entry
    guest.decision_grant_digest = digest
    guest.save(
        update_fields=[
            "admission_state",
            "decision_ref",
            "decision_allow",
            "decision_grant_digest",
            "updated_at",
        ]
    )
    cache.set(_decision_cache_key(decision_id), body["decision_grant"], timeout=60)
    receipt, _ = sign_guest_decision_receipt(guest, body["decision_grant"])
    confirmation = _post_core(
        "MASTRAO_CORE_GUEST_CONFIRM_ENDPOINT",
        "/internal/v1/meetings/guest-admissions/confirm",
        {"decision_grant": body["decision_grant"], "receipt_assertion": receipt},
    )
    if confirmation != {"version": 1, "decision_id": decision_id, "state": "confirmed"}:
        raise GuestHandoffRefused(status=503)
    guest.decision_receipt_digest = compact_digest(receipt)
    guest.decision_confirmed_at = timezone.now()
    guest.save(
        update_fields=["decision_receipt_digest", "decision_confirmed_at", "updated_at"]
    )
    return guest


def guest_media_config(request, room, username, color, participant_id):
    """Reauthorize and mint one participant-only exact-room token."""

    guest = active_guest_grant(request, room)
    if (
        not guest
        or guest.guest_ref != participant_id
        or guest.admission_state != models.MastraoGuestGrant.AdmissionState.ALLOWED
        or guest.decision_confirmed_at is None
    ):
        return None
    compact_guest = active_guest_compact_grant(request, guest)
    if not compact_guest or compact_digest(compact_guest) != guest.grant_digest:
        raise GuestHandoffRefused(status=503)
    _cache_compact_grant(guest, compact_guest)
    assertion, asserted = sign_guest_media_request(guest, compact_guest)
    body = _post_core(
        "MASTRAO_CORE_GUEST_MEDIA_ENDPOINT",
        "/internal/v1/meetings/guest-media/authorize",
        {"guest_grant": compact_guest, "media_request_assertion": assertion},
        "media_grant",
    )
    media = verify_guest_media_grant(body["media_grant"])
    if media["media_request_id"] != asserted["media_request_id"] or not _exact_guest(
        guest, media
    ):
        raise GuestHandoffRefused()
    expires_at = min(
        guest.expires_at,
        datetime.fromtimestamp(media["expires_at"], tz=UTC),
        timezone.now() + timedelta(seconds=300),
    )
    return utils.generate_livekit_config(
        room_id=str(room.id),
        user=request.user,
        username=username,
        color=color,
        configuration=room.configuration,
        participant_id=guest.guest_ref,
        role=None,
        expires_at=expires_at,
    )
