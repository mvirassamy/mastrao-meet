"""No-URL-secret browser bootstrap for one finalized recording artifact."""

import hashlib
import html
import logging
import secrets
import time
from datetime import UTC, datetime
from urllib.parse import urlparse

from django.conf import settings
from django.core.cache import cache
from django.core.files.storage import default_storage
from django.db import IntegrityError, transaction
from django.http import FileResponse, HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from botocore.exceptions import BotoCoreError, ClientError

from core import models
from core.mastrao_recording_artifact import MAX_ARTIFACT_BYTES
from core.mastrao_recording_contract import (
    RecordingContractRefused,
    compact_digest,
    verify_recording_access_grant,
)

MAX_BODY_BYTES = 20_000
RETRY_COOKIE = "mastrao_recording_retry"
SESSION_KEY = "mastrao_recording_download"
logger = logging.getLogger(__name__)


def _html_page(title, message, *, status=200):
    markup = f"""<!doctype html><html lang="fr"><head><meta charset="utf-8">
<meta name="referrer" content="no-referrer"><title>{html.escape(title)}</title></head>
<body><main><h1>{html.escape(title)}</h1>
<p role="status" aria-live="polite">{html.escape(message)}</p></main></body></html>"""
    response = HttpResponse(
        markup, status=status, content_type="text/html; charset=utf-8"
    )
    for name, value in _headers().items():
        response[name] = value
    response["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
    return response


def _headers():
    return {
        "Cache-Control": "private, no-store",
        "Pragma": "no-cache",
        "Referrer-Policy": "no-referrer",
        "X-Content-Type-Options": "nosniff",
    }


def _origin(value):
    parsed = urlparse(value or "")
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    return f"{parsed.scheme}://{parsed.netloc}"


def _bounded_post(request):
    if request.content_type != "application/x-www-form-urlencoded":
        raise RecordingContractRefused()
    declared = request.headers.get("content-length")
    if (
        declared is None
        or not declared.isdecimal()
        or int(declared) > MAX_BODY_BYTES
        or len(request.body) > MAX_BODY_BYTES
    ):
        raise RecordingContractRefused()
    return request.POST


def _form(request, expected):
    fields = _bounded_post(request)
    if set(fields) != expected or any(
        len(fields.getlist(name)) != 1 for name in expected
    ):
        raise RecordingContractRefused()
    return fields


def _bootstrap(request):
    if request.headers.get("origin") != settings.MASTRAO_PLATFORM_ORIGIN and not (
        request.headers.get("origin") == "null"
        and request.headers.get("sec-fetch-site") == "same-site"
    ):
        logger.warning("Mastrao recording access refused: bootstrap_origin")
        raise RecordingContractRefused()
    try:
        fields = _form(request, {"recording_access_grant"})
    except RecordingContractRefused:
        logger.warning("Mastrao recording access refused: bootstrap_form")
        raise
    compact = fields["recording_access_grant"]
    try:
        grant = verify_recording_access_grant(compact)
    except RecordingContractRefused:
        logger.warning("Mastrao recording access refused: bootstrap_grant")
        raise
    retry = secrets.token_urlsafe(32)
    retry_digest = hashlib.sha256(retry.encode()).hexdigest()
    remaining = grant["expires_at"] - int(time.time())
    if remaining < 1:
        logger.warning("Mastrao recording access refused: bootstrap_expired")
        raise RecordingContractRefused()
    cache.set(
        f"mastrao-recording-bootstrap:{retry_digest}",
        compact_digest(compact),
        timeout=remaining,
    )
    nonce = secrets.token_urlsafe(18)
    markup = f"""<!doctype html><html lang=\"fr\"><head><meta charset=\"utf-8\">
<meta name=\"referrer\" content=\"no-referrer\"><title>Vérification de l’enregistrement</title></head>
<body><main><h1>Préparation du téléchargement</h1>
<p role=\"status\" aria-live=\"polite\">Vérification de l’intégrité de l’enregistrement…</p></main>
<form id=\"continue\" method=\"post\" action=\"/recordings/access/\">
<input type=\"hidden\" name=\"stage\" value=\"consume\">
<input type=\"hidden\" name=\"recording_access_grant\" value=\"{html.escape(compact, quote=True)}\">
<noscript><button type=\"submit\">Continue</button></noscript></form>
<script nonce=\"{nonce}\">document.getElementById('continue').submit()</script></body></html>"""
    response = HttpResponse(markup, content_type="text/html; charset=utf-8")
    for name, value in _headers().items():
        response[name] = value
    response["Content-Security-Policy"] = (
        "default-src 'none'; form-action 'self'; "
        f"script-src 'nonce-{nonce}'; base-uri 'none'; frame-ancestors 'none'"
    )
    response.set_cookie(
        RETRY_COOKIE,
        retry,
        max_age=remaining,
        httponly=True,
        secure=request.is_secure(),
        samesite="Lax",
        path="/recordings/",
    )
    return response


@transaction.atomic
def _consume(request):
    expected_origin = _origin(request.build_absolute_uri("/"))
    if request.headers.get("sec-fetch-site") not in {"same-origin", "same-site"} or (
        request.headers.get("origin") not in {expected_origin, "null"}
    ):
        logger.warning("Mastrao recording access refused: consume_origin")
        raise RecordingContractRefused()
    try:
        fields = _form(request, {"stage", "recording_access_grant"})
    except RecordingContractRefused:
        logger.warning("Mastrao recording access refused: consume_form")
        raise
    if fields["stage"] != "consume":
        logger.warning("Mastrao recording access refused: consume_stage")
        raise RecordingContractRefused()
    retry = request.COOKIES.get(RETRY_COOKIE)
    if not isinstance(retry, str) or len(retry) < 32:
        logger.warning("Mastrao recording access refused: consume_cookie")
        raise RecordingContractRefused()
    retry_digest = hashlib.sha256(retry.encode()).hexdigest()
    compact = fields["recording_access_grant"]
    digest = compact_digest(compact)
    if cache.get(f"mastrao-recording-bootstrap:{retry_digest}") != digest:
        logger.warning("Mastrao recording access refused: consume_retry")
        raise RecordingContractRefused()
    try:
        grant = verify_recording_access_grant(compact)
    except RecordingContractRefused:
        logger.warning("Mastrao recording access refused: consume_grant")
        raise
    binding = (
        models.MastraoRecordingBinding.objects.select_for_update()
        .select_related("recording")
        .filter(
            organization_external_id=grant["organization_external_id"],
            meeting_ref=grant["meeting_ref"],
            recording_ref=grant["recording_ref"],
            artifact_ref=grant["artifact_ref"],
            state=models.MastraoRecordingBinding.State.FINALIZED,
            retention_expires_at__gt=datetime.now(tz=UTC),
            recording__isnull=False,
        )
        .first()
    )
    if not binding or not binding.object_ref:
        logger.warning("Mastrao recording access refused: consume_binding")
        raise RecordingContractRefused()
    defaults = {
        "recording_binding": binding,
        "grant_digest": digest,
        "artifact_ref": grant["artifact_ref"],
        "subject_external_id_digest": hashlib.sha256(
            grant["subject_external_id"].encode()
        ).hexdigest(),
        "platform_session_digest": grant["platform_session_digest"],
        "retry_cookie_digest": retry_digest,
        "expires_at": datetime.fromtimestamp(grant["expires_at"], tz=UTC),
    }
    try:
        access, created = models.MastraoRecordingArtifactAccess.objects.get_or_create(
            grant_jti=grant["jti"], defaults=defaults
        )
    except IntegrityError as error:
        logger.warning("Mastrao recording access refused: consume_conflict")
        raise RecordingContractRefused() from error
    if not created and (
        access.grant_digest != digest
        or access.retry_cookie_digest != retry_digest
        or access.recording_binding_id != binding.id
    ):
        logger.warning("Mastrao recording access refused: consume_replay")
        raise RecordingContractRefused()
    request.session.cycle_key()
    request.session[SESSION_KEY] = {
        "access_id": str(access.id),
        "retry_digest": retry_digest,
        "expires_at": grant["expires_at"],
        "stage": "ready",
    }
    response = HttpResponse(status=303)
    response["Location"] = "/recordings/download/current"
    for name, value in _headers().items():
        response[name] = value
    return response


@csrf_exempt
@require_POST
def recording_access(request):
    """Bootstrap then consume a short access grant without URL disclosure."""

    if not settings.MASTRAO_MEETING_RECORDING_ENABLED:
        return JsonResponse({"message": "Not found"}, status=404, headers=_headers())
    try:
        fields = _bounded_post(request)
        if set(fields) == {"recording_access_grant"}:
            return _bootstrap(request)
        if set(fields) == {"stage", "recording_access_grant"}:
            return _consume(request)
        raise RecordingContractRefused()
    except RecordingContractRefused as error:
        return JsonResponse(
            {"message": "Not found" if error.status == 404 else "Unavailable"},
            status=error.status,
            headers=_headers(),
        )


def _download_session(request):
    session = request.session.get(SESSION_KEY)
    retry = request.COOKIES.get(RETRY_COOKIE)
    if not isinstance(session, dict) or not isinstance(retry, str):
        raise RecordingContractRefused()
    retry_digest = hashlib.sha256(retry.encode()).hexdigest()
    if (
        session.get("retry_digest") != retry_digest
        or not isinstance(session.get("expires_at"), int)
        or session["expires_at"] <= int(time.time())
    ):
        raise RecordingContractRefused()
    return session, retry_digest


def _open_verified_stream(object_ref, expected_size, expected_checksum):
    stream = None
    try:
        stream = default_storage.open(object_ref, "rb")
        checksum = hashlib.sha256()
        size = 0
        while chunk := stream.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_ARTIFACT_BYTES:
                raise RecordingContractRefused()
            checksum.update(chunk)
        if size != expected_size or checksum.hexdigest() != expected_checksum:
            raise RecordingContractRefused()
        stream.seek(0)
        return stream
    except (BotoCoreError, ClientError, OSError, ValueError) as error:
        if stream is not None:
            stream.close()
        raise RecordingContractRefused() from error
    except RecordingContractRefused:
        if stream is not None:
            stream.close()
        raise


def _stream_once(request, session, retry_digest):
    started_at = datetime.now(tz=UTC)
    snapshot = (
        models.MastraoRecordingArtifactAccess.objects.select_related(
            "recording_binding"
        )
        .filter(
            id=session.get("access_id"),
            retry_cookie_digest=retry_digest,
            expires_at__gt=started_at,
            consumed_at__isnull=True,
            recording_binding__retention_expires_at__gt=started_at,
        )
        .first()
    )
    if not snapshot or not snapshot.recording_binding.object_ref:
        raise RecordingContractRefused()
    object_ref = snapshot.recording_binding.object_ref
    size = snapshot.recording_binding.byte_size
    checksum = snapshot.recording_binding.checksum_digest
    if not isinstance(size, int) or not isinstance(checksum, str):
        raise RecordingContractRefused()
    stream = _open_verified_stream(object_ref, size, checksum)
    verified_at = datetime.now(tz=UTC)
    with transaction.atomic():
        access = (
            models.MastraoRecordingArtifactAccess.objects.select_for_update()
            .select_related("recording_binding")
            .filter(
                id=snapshot.id,
                retry_cookie_digest=retry_digest,
                expires_at__gt=started_at,
                consumed_at__isnull=True,
                recording_binding__retention_expires_at__gt=verified_at,
                recording_binding__object_ref=object_ref,
                recording_binding__byte_size=size,
                recording_binding__checksum_digest=checksum,
            )
            .first()
        )
        if not access:
            stream.close()
            raise RecordingContractRefused()
        access.consumed_at = verified_at
        access.save(update_fields=["consumed_at", "updated_at"])
        request.session.pop(SESSION_KEY, None)
        request.session.modified = True
        response = FileResponse(
            stream,
            as_attachment=True,
            filename=f"meeting-{access.recording_binding.meeting_ref}.mp4",
            content_type="video/mp4",
        )
        for name, value in _headers().items():
            response[name] = value
        response["Content-Security-Policy"] = (
            "default-src 'none'; frame-ancestors 'none'"
        )
        return response


@require_GET
def recording_download(request):
    """Prepare once, then atomically stream once from the same clean URL."""

    try:
        session, retry_digest = _download_session(request)
        if session.get("stage") == "ready":
            session["stage"] = "prepared"
            request.session[SESSION_KEY] = session
            response = HttpResponse(status=303)
            response["Location"] = "/recordings/download/current"
            for name, value in _headers().items():
                response[name] = value
            return response
        if session.get("stage") != "prepared":
            raise RecordingContractRefused()
        return _stream_once(request, session, retry_digest)
    except RecordingContractRefused:
        request.session.pop(SESSION_KEY, None)
        return _html_page(
            "Enregistrement indisponible",
            "Fermez cet onglet puis réessayez depuis votre dossier Mastrao.",
            status=404,
        )
