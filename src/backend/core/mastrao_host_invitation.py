"""Exchange durable Core host invitations for the existing short host handoff."""

import hashlib
import json
import re
import secrets
import threading
import time
from contextlib import contextmanager
from importlib import import_module

from django.conf import settings
from django.contrib.auth import BACKEND_SESSION_KEY, SESSION_KEY
from django.contrib.sessions.backends.base import UpdateError
from django.core import signing
from django.core.cache import cache
from django.http import JsonResponse
from django.middleware.csrf import get_token
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from core import models
from core.mastrao_browser_handoff import (
    browser_handoff_headers,
    is_same_origin_handoff,
)
from core.mastrao_core_http import post_core_json
from core.mastrao_host_contract import (
    MAX_GRANT_SECONDS,
    HostHandoffRefused,
    compact_digest,
    verify_host_handoff,
)
from core.mastrao_host_grant import (
    SESSION_COMPACT_GRANTS_KEY,
    SESSION_NONCE_KEY,
    SESSION_PLATFORM_REF_KEY,
)
from core.mastrao_host_handoff import SESSION_BACKEND, establish_mastrao_host_session
from core.mastrao_room_lifecycle import MastraoRoomClosed, assert_mastrao_room_open

MAX_BODY_BYTES = 20_000
MAX_INVITATION_BYTES = 16_384
MAX_ATTEMPTS_PER_MINUTE = 5
MAX_CONCURRENT_EXCHANGES = 8
EXCHANGE_ID = re.compile(r"^exchange_[a-f0-9]{32}$")
EXCHANGE_SESSION_REF = re.compile(r"^hostsession_[a-f0-9]{32}$")
EXCHANGE_COOKIE = "mastraoHostExchange"
EXCHANGE_COOKIE_MAX_AGE = 600
EXCHANGE_COOKIE_SALT = "mastrao-host-invitation-exchange"
EXCHANGE_CLAIM_SECONDS = 30
EXCHANGE_RESULT_WAIT_SECONDS = 2
_EXCHANGE_SLOTS = threading.BoundedSemaphore(MAX_CONCURRENT_EXCHANGES)


@contextmanager
def _exchange_slot():
    """Bound concurrent Core exchanges without making requests wait in-process."""

    if not _EXCHANGE_SLOTS.acquire(blocking=False):
        raise HostHandoffRefused(status=503)
    try:
        yield
    finally:
        _EXCHANGE_SLOTS.release()


def _validate_invitation(host_invitation):
    if not isinstance(host_invitation, str):
        raise HostHandoffRefused()
    try:
        encoded = host_invitation.encode("ascii")
    except UnicodeEncodeError as error:
        raise HostHandoffRefused() from error
    if not 32 <= len(encoded) <= MAX_INVITATION_BYTES or any(
        character.isspace() for character in host_invitation
    ):
        raise HostHandoffRefused()


def _new_exchange_cookie():
    payload = {
        "session_ref": f"hostsession_{secrets.token_hex(16)}",
        "expires_at": int(time.time()) + MAX_GRANT_SECONDS,
    }
    return signing.dumps(payload, salt=EXCHANGE_COOKIE_SALT), payload


def _read_exchange_cookie(request):
    compact = request.COOKIES.get(EXCHANGE_COOKIE)
    if not isinstance(compact, str):
        raise HostHandoffRefused()
    try:
        payload = signing.loads(
            compact,
            salt=EXCHANGE_COOKIE_SALT,
            max_age=EXCHANGE_COOKIE_MAX_AGE,
        )
    except signing.BadSignature as error:
        raise HostHandoffRefused() from error
    if not isinstance(payload, dict) or set(payload) != {
        "session_ref",
        "expires_at",
    }:
        raise HostHandoffRefused()
    session_ref = payload["session_ref"]
    expires_at = payload["expires_at"]
    if (
        not isinstance(session_ref, str)
        or not EXCHANGE_SESSION_REF.fullmatch(session_ref)
        or not isinstance(expires_at, int)
        or isinstance(expires_at, bool)
        or expires_at <= int(time.time())
    ):
        raise HostHandoffRefused()
    return compact, payload


def _active_exchange_session(request):
    _, payload = _read_exchange_cookie(request)
    return payload["session_ref"], payload["expires_at"]


def _increment_attempt_counter(key):
    if cache.add(key, 1, timeout=70):
        return 1
    try:
        return cache.incr(key)
    except ValueError as error:
        raise HostHandoffRefused(status=503) from error


def _admit_exchange_attempt(host_invitation, session_ref):
    bucket = int(time.time()) // 60
    credential = hashlib.sha256(host_invitation.encode("ascii")).hexdigest()
    scoped_key = f"mastrao-host-invitation:{session_ref}:{credential}:{bucket}"
    if _increment_attempt_counter(scoped_key) > MAX_ATTEMPTS_PER_MINUTE:
        raise HostHandoffRefused()
    global_key = f"mastrao-host-invitation:global:{bucket}"
    if _increment_attempt_counter(global_key) > (
        settings.MASTRAO_HOST_HANDOFF_GLOBAL_ATTEMPTS_PER_MINUTE
    ):
        raise HostHandoffRefused(status=503)


def _exchange_invitation(host_invitation, exchange_id, session_ref, expires_at):
    body = post_core_json(
        endpoint=settings.MASTRAO_CORE_HOST_INVITATION_EXCHANGE_ENDPOINT,
        expected_path=("/internal/v1/application-meetings/host-invitations/exchange"),
        body={
            "credential": host_invitation,
            "exchange_id": exchange_id,
            "session_ref": session_ref,
            "session_expires_at": expires_at,
        },
        timeout=settings.MASTRAO_CORE_REDEMPTION_TIMEOUT_SECONDS,
        refusal=HostHandoffRefused,
        expected_fields={
            "version",
            "meeting_ref",
            "handoff_ref",
            "expires_at",
            "host_handoff",
        },
    )
    host_handoff = body["host_handoff"]
    handoff = verify_host_handoff(host_handoff)
    if not _handoff_matches_exchange(body, handoff, session_ref, expires_at):
        raise HostHandoffRefused()
    return host_handoff, handoff


def _handoff_matches_exchange(body, handoff, session_ref, expires_at):
    response_matches_handoff = (
        body["version"] == 1
        and body["meeting_ref"] == handoff["meeting_ref"]
        and body["handoff_ref"] == handoff["handoff_ref"]
        and body["expires_at"] == handoff["expires_at"]
    )
    session_matches_exchange = (
        handoff["platform_session_ref"] == session_ref
        and handoff["grant_expires_at"] <= expires_at
    )
    return response_matches_handoff and session_matches_exchange


def _exchange_result_key(session_ref, host_handoff):
    digest = compact_digest(host_handoff)
    return f"mastrao-host-invitation-result:{session_ref}:{digest}"


def _exchange_claim_key(session_ref, host_handoff):
    digest = compact_digest(host_handoff)
    return f"mastrao-host-invitation-claim:{session_ref}:{digest}"


@contextmanager
def _exchange_claim(session_ref, host_handoff):
    """Give one worker ownership of a handoff while identical retries wait."""

    key = _exchange_claim_key(session_ref, host_handoff)
    owner_token = secrets.token_hex(16)
    acquired = cache.add(key, owner_token, timeout=EXCHANGE_CLAIM_SECONDS)
    try:
        yield acquired
    finally:
        if acquired and cache.get(key) == owner_token:
            cache.delete(key)


def _remember_exchange_result(request, session_ref, host_handoff, handoff, binding):
    session_nonce = request.session.get(SESSION_NONCE_KEY)
    if not isinstance(session_nonce, str) or not session_nonce:
        raise HostHandoffRefused(status=503)
    grant = models.MastraoHostGrant.objects.filter(
        handoff_ref=handoff["handoff_ref"],
        room_binding=binding,
    ).first()
    if not grant:
        raise HostHandoffRefused(status=503)
    try:
        request.session.save()
    except UpdateError as error:
        raise HostHandoffRefused(status=503) from error
    session_key = request.session.session_key
    if not isinstance(session_key, str) or not session_key:
        raise HostHandoffRefused(status=503)
    cache.set(
        _exchange_result_key(session_ref, host_handoff),
        {
            "session_key": session_key,
            "user_id": str(request.user.pk),
            "binding_id": str(binding.pk),
            "room_slug": binding.room.slug,
            "platform_session_ref": grant.platform_session_ref,
            "grant_ref": grant.grant_ref,
            "session_nonce_digest": hashlib.sha256(session_nonce.encode()).hexdigest(),
            "expires_at": handoff["grant_expires_at"],
        },
        timeout=EXCHANGE_COOKIE_MAX_AGE,
    )


def _replayed_exchange_result(session_ref, host_handoff):
    result = cache.get(_exchange_result_key(session_ref, host_handoff))
    if result is None:
        return None
    if not _valid_replayed_exchange_result(result):
        raise HostHandoffRefused()
    if not _authenticated_session_exists(result):
        raise HostHandoffRefused()
    binding = (
        models.MastraoRoomBinding.objects.select_related("room")
        .filter(pk=result["binding_id"], room__slug=result["room_slug"])
        .first()
    )
    if not binding:
        raise HostHandoffRefused()
    try:
        assert_mastrao_room_open(binding)
    except MastraoRoomClosed as error:
        raise HostHandoffRefused() from error
    return result


def _valid_replayed_exchange_result(result):
    if not isinstance(result, dict):
        return False
    expected_fields = {
        "session_key",
        "user_id",
        "binding_id",
        "room_slug",
        "platform_session_ref",
        "grant_ref",
        "session_nonce_digest",
        "expires_at",
    }
    if set(result) != expected_fields:
        return False
    string_fields = (
        "session_key",
        "user_id",
        "binding_id",
        "room_slug",
        "platform_session_ref",
        "grant_ref",
        "session_nonce_digest",
    )
    if any(not isinstance(result[field], str) for field in string_fields):
        return False
    expires_at = result["expires_at"]
    return (
        isinstance(expires_at, int)
        and not isinstance(expires_at, bool)
        and expires_at > int(time.time())
    )


def _authenticated_session_exists(result):
    session_store = import_module(settings.SESSION_ENGINE).SessionStore(
        session_key=result["session_key"]
    )
    if not session_store.exists(result["session_key"]):
        return False
    session = session_store.load()
    session_nonce = session.get(SESSION_NONCE_KEY)
    compact_grants = session.get(SESSION_COMPACT_GRANTS_KEY)
    session_matches = (
        session.get(SESSION_KEY) == result["user_id"]
        and session.get(BACKEND_SESSION_KEY) == SESSION_BACKEND
        and session.get(SESSION_PLATFORM_REF_KEY) == result["platform_session_ref"]
        and isinstance(session_nonce, str)
        and hashlib.sha256(session_nonce.encode()).hexdigest()
        == result["session_nonce_digest"]
        and isinstance(compact_grants, dict)
        and isinstance(compact_grants.get(result["grant_ref"]), str)
    )
    if not session_matches:
        return False
    return models.MastraoHostGrant.objects.filter(
        grant_ref=result["grant_ref"],
        room_binding_id=result["binding_id"],
        identity__user_id=result["user_id"],
        identity__user__is_active=True,
        platform_session_ref=result["platform_session_ref"],
        session_nonce_digest=result["session_nonce_digest"],
        expires_at__gt=timezone.now(),
    ).exists()


def _wait_for_exchange_result(session_ref, host_handoff):
    deadline = time.monotonic() + EXCHANGE_RESULT_WAIT_SECONDS
    while time.monotonic() < deadline:
        result = _replayed_exchange_result(session_ref, host_handoff)
        if result:
            return result
        time.sleep(0.02)
    raise HostHandoffRefused(status=503)


def _restore_session_cookie(response, replayed):
    max_age = max(1, replayed["expires_at"] - int(time.time()))
    response.set_cookie(
        settings.SESSION_COOKIE_NAME,
        replayed["session_key"],
        max_age=max_age,
        path=settings.SESSION_COOKIE_PATH,
        domain=settings.SESSION_COOKIE_DOMAIN,
        secure=settings.SESSION_COOKIE_SECURE,
        httponly=settings.SESSION_COOKIE_HTTPONLY,
        samesite=settings.SESSION_COOKIE_SAMESITE,
    )


def _replay_response(request, replayed, headers):
    get_token(request)
    response = JsonResponse(
        {"room_url": f"/{replayed['room_slug']}"},
        status=200,
        headers=headers,
    )
    _restore_session_cookie(response, replayed)
    return response


def _request_body(request):
    declared = request.headers.get("content-length")
    if (
        request.content_type != "application/json"
        or declared is None
        or not declared.isdecimal()
        or int(declared) > MAX_BODY_BYTES
        or len(request.body) > MAX_BODY_BYTES
    ):
        raise HostHandoffRefused()
    try:
        body = json.loads(request.body)
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as error:
        raise HostHandoffRefused() from error
    if not isinstance(body, dict) or set(body) != {
        "host_invitation",
        "exchange_id",
    }:
        raise HostHandoffRefused()
    if not isinstance(body["exchange_id"], str) or not EXCHANGE_ID.fullmatch(
        body["exchange_id"]
    ):
        raise HostHandoffRefused()
    _validate_invitation(body["host_invitation"])
    return body


@csrf_exempt
@require_POST
def establish_mastrao_host_invitation_session(request):
    """Issue a stateless exchange identity before any Core mutation."""

    headers = browser_handoff_headers()
    if not settings.MASTRAO_HOST_HANDOFF_ENABLED:
        return JsonResponse({"message": "Not found"}, status=404, headers=headers)
    try:
        if not is_same_origin_handoff(request):
            raise HostHandoffRefused()
        declared = request.headers.get("content-length")
        if (
            request.content_type != "application/json"
            or declared is None
            or not declared.isdecimal()
            or int(declared) > 2
            or request.body != b"{}"
        ):
            raise HostHandoffRefused()
        try:
            compact, _ = _read_exchange_cookie(request)
        except HostHandoffRefused:
            compact, _ = _new_exchange_cookie()
        response = JsonResponse({"version": 1}, status=200, headers=headers)
        response.set_cookie(
            EXCHANGE_COOKIE,
            compact,
            max_age=EXCHANGE_COOKIE_MAX_AGE,
            path="/handoff/host/invitation/",
            secure=settings.SESSION_COOKIE_SECURE,
            httponly=True,
            samesite="Lax",
        )
        return response
    except HostHandoffRefused as error:
        return JsonResponse(
            {"message": "Not found" if error.status == 404 else "Unavailable"},
            status=error.status,
            headers=headers,
        )


@csrf_exempt
@require_POST
def exchange_mastrao_host_invitation(request):
    """Resolve one fragment bearer through Core and establish the normal host session."""

    headers = browser_handoff_headers()
    if not settings.MASTRAO_HOST_HANDOFF_ENABLED:
        return JsonResponse({"message": "Not found"}, status=404, headers=headers)
    try:
        if not is_same_origin_handoff(request):
            raise HostHandoffRefused()
        body = _request_body(request)
        session_ref, expires_at = _active_exchange_session(request)
        _admit_exchange_attempt(body["host_invitation"], session_ref)
        with _exchange_slot():
            host_handoff, handoff = _exchange_invitation(
                body["host_invitation"],
                body["exchange_id"],
                session_ref,
                expires_at,
            )
            replayed = _replayed_exchange_result(session_ref, host_handoff)
            if replayed:
                return _replay_response(request, replayed, headers)
            with _exchange_claim(session_ref, host_handoff) as owns_exchange:
                if not owns_exchange:
                    replayed = _wait_for_exchange_result(session_ref, host_handoff)
                    return _replay_response(request, replayed, headers)
                replayed = _replayed_exchange_result(session_ref, host_handoff)
                if replayed:
                    return _replay_response(request, replayed, headers)
                binding = establish_mastrao_host_session(request, host_handoff)
                _remember_exchange_result(
                    request,
                    session_ref,
                    host_handoff,
                    handoff,
                    binding,
                )
                return JsonResponse(
                    {"room_url": f"/{binding.room.slug}"},
                    status=200,
                    headers=headers,
                )
    except HostHandoffRefused as error:
        return JsonResponse(
            {"message": "Not found" if error.status == 404 else "Unavailable"},
            status=error.status,
            headers=headers,
        )
