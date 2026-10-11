"""No-URL-secret browser bootstrap for one finalized recording artifact."""

import base64
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
from django.http import FileResponse, HttpResponse
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
RETRY_COOKIE_PATH = "/recordings/"
# Readable by the waiting page: tells it that the file download has started.
STARTED_COOKIE = "mastrao_recording_started"
MEETING_HISTORY_PATH = "/reunions/historique"
SESSION_KEY = "mastrao_recording_download"
logger = logging.getLogger(__name__)

# Watercolor card shared by every page of the download, allowed by its hash.
PAGE_STYLE = """*{box-sizing:border-box}
body{margin:0;background:#edf2ff url(/assets/home/aquarelle.webp) center/cover;color:#3d485e;
font-family:"Inter Variable",Inter,ui-sans-serif,system-ui,-apple-system,sans-serif}
main{min-height:100vh;display:grid;place-items:center;padding:24px}
[hidden]{display:none!important}
.card{width:min(440px,100%);padding:30px;border-radius:16px;text-align:center;
background:rgba(255,255,255,.96);box-shadow:0 24px 60px -30px rgba(8,20,46,.45)}
.brand{display:flex;justify-content:center;align-items:center;gap:8px;
margin-bottom:14px;color:#08142e;font-size:17px;font-weight:600}
.brand img{width:26px;height:auto}
.camera{width:76px;height:76px;margin-bottom:8px}
[data-muted] .camera{filter:grayscale(1);opacity:.45}
h1{margin:0;color:#08142e;font-size:21px;font-weight:600}
p{margin:8px 0 0;font-size:14.5px;line-height:1.55}
.note{display:flex;justify-content:center;align-items:center;gap:10px;
margin-top:18px;padding:10px 12px;border-radius:10px;background:#f5f7fb;font-size:13.5px}
.badge{display:grid;place-items:center;width:18px;height:18px;flex:none;
border-radius:50%;color:#fff;font-size:11px;font-weight:700}
.ok{background:#1d7a4f}.refused{background:#a3262f}
.spinner{width:18px;height:18px;flex:none;border-radius:50%;
border:2.5px solid rgba(45,91,227,.22);border-top-color:#2d5be3;animation:spin .9s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}
@media (prefers-reduced-motion:reduce){.spinner{animation:none}}
.button{display:inline-flex;align-items:center;height:38px;margin-top:18px;padding:0 16px;
border:1px solid #d7dfe9;border-radius:10px;background:#fff;color:#2d5be3;
font-weight:500;text-decoration:none}"""
PAGE_STYLE_SOURCE = (
    "'sha256-"
    + base64.b64encode(hashlib.sha256(PAGE_STYLE.encode()).digest()).decode()
    + "'"
)


def _card(*, title, message, note, action="", attributes=""):
    """One watercolor card; its content is fixed French copy, never user data."""
    return f"""<section class="card"{attributes}>
<div class="brand"><img src="/assets/mastrao-logo.png" alt="">Mastrao</div>
<img class="camera" src="/assets/illustrations/section-video.webp" alt="">
<h1>{title}</h1><p>{message}</p><p class="note">{note}</p>{action}</section>"""


def _page(title, body):
    return f"""<!doctype html><html lang="fr"><head><meta charset="utf-8">
<meta name="referrer" content="no-referrer">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title><style>{PAGE_STYLE}</style></head>
<body>{body}</body></html>"""


def _unavailable_page(status):
    markup = _page(
        "Vidéo indisponible",
        '<main role="status" aria-live="polite">'
        + _card(
            title="Vidéo indisponible",
            message="Cette vidéo ne peut pas être téléchargée pour le moment. "
            "Relancez le téléchargement depuis l’historique de la réunion.",
            note='<span class="badge refused">!</span>'
            "Aucun fichier n’a été téléchargé.",
            action=f'<a class="button" href="{MEETING_HISTORY_PATH}">'
            "Retour à l’historique</a>",
            attributes=" data-muted",
        )
        + "</main>",
    )
    response = HttpResponse(
        markup, status=status, content_type="text/html; charset=utf-8"
    )
    for name, value in _headers().items():
        response[name] = value
    response["Content-Security-Policy"] = (
        f"default-src 'none'; style-src {PAGE_STYLE_SOURCE}; img-src 'self'; "
        "frame-ancestors 'none'"
    )
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
    # The page stays displayed once the file starts downloading, so its script
    # switches to the started card when the download response sets its cookie.
    markup = _page(
        "Préparation de votre vidéo",
        '<main role="status" aria-live="polite">'
        + _card(
            attributes=' id="preparing"',
            title="Préparation de votre vidéo",
            message="Nous vérifions votre accès et l’intégrité du fichier. "
            "Le téléchargement démarre tout seul.",
            note='<span class="spinner"></span>'
            "Vérification de l’intégrité de la vidéo…",
        )
        + _card(
            attributes=' id="started" hidden',
            title="Le téléchargement a démarré",
            message="Retrouvez la vidéo dans les téléchargements de votre "
            "navigateur. Vous pouvez fermer cet onglet.",
            note='<span class="badge ok">✓</span>Fichier MP4',
        )
        + f"""</main>
<form id="continue" method="post" action="/recordings/access/">
<input type="hidden" name="stage" value="consume">
<input type="hidden" name="recording_access_grant" value="{html.escape(compact, quote=True)}">
<noscript><button type="submit">Continuer</button></noscript></form>
<script nonce="{nonce}">
document.getElementById('continue').submit()
const watch = setInterval(() => {{
  if (!document.cookie.split('; ').includes('{STARTED_COOKIE}=1')) return
  clearInterval(watch)
  document.getElementById('preparing').hidden = true
  document.getElementById('started').hidden = false
}}, 500)
</script>""",
    )
    response = HttpResponse(markup, content_type="text/html; charset=utf-8")
    for name, value in _headers().items():
        response[name] = value
    response["Content-Security-Policy"] = (
        "default-src 'none'; form-action 'self'; "
        f"script-src 'nonce-{nonce}'; style-src {PAGE_STYLE_SOURCE}; "
        "img-src 'self'; base-uri 'none'; frame-ancestors 'none'"
    )
    response.delete_cookie(STARTED_COOKIE, path=RETRY_COOKIE_PATH)
    response.set_cookie(
        RETRY_COOKIE,
        retry,
        max_age=remaining,
        httponly=True,
        secure=request.is_secure(),
        samesite="Lax",
        path=RETRY_COOKIE_PATH,
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

    if not (
        settings.MASTRAO_MEETING_RECORDING_ENABLED
        and settings.MASTRAO_MEETING_RECORDING_ARTIFACT_ACCESS_ENABLED
    ):
        return _unavailable_page(404)
    try:
        fields = _bounded_post(request)
        if set(fields) == {"recording_access_grant"}:
            return _bootstrap(request)
        if set(fields) == {"stage", "recording_access_grant"}:
            return _consume(request)
        raise RecordingContractRefused()
    except RecordingContractRefused as error:
        return _unavailable_page(error.status)


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
        response.set_cookie(
            STARTED_COOKIE,
            "1",
            max_age=60,
            secure=request.is_secure(),
            samesite="Lax",
            path=RETRY_COOKIE_PATH,
        )
        return response


@require_GET
def recording_download(request):
    """Prepare once, then atomically stream once from the same clean URL."""

    try:
        if not settings.MASTRAO_MEETING_RECORDING_ARTIFACT_ACCESS_ENABLED:
            raise RecordingContractRefused()
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
        response = _unavailable_page(404)
        response.delete_cookie(RETRY_COOKIE, path=RETRY_COOKIE_PATH)
        return response
