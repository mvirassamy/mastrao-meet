"""Room-scoped technical worker capability and signed Core command gateway.

No participant credential, consent mirror or send state is retained here.
The Core send ledger binds the opaque source to the signed epoch and owner.
"""

# Strict wire guards deliberately exclude bool integers.
# pylint: disable=unidiomatic-typecheck,too-many-boolean-expressions

import ipaddress
import json
import re
import time
from urllib.parse import urlparse
from uuid import UUID, uuid4

from django.conf import settings
from django.core import signing
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from core import models
from core.mastrao_core_http import post_core_json, validate_core_endpoint
from core.mastrao_live_track_provenance import trusted_live_track
from core.mastrao_live_worker_contract import (
    CONTROL_PATH,
    PREPARE_JOSE,
    PREPARE_PATH,
    PREPARE_TYPE,
    SEND_JOSE,
    SEND_PATH,
    SEND_TYPE,
    validate_admission,
    validate_send,
    worker_command,
)
from core.mastrao_recording_contract import RecordingContractRefused, _sign

CAPABILITY_SALT = "mastrao.live-worker-control.v1"
CAPABILITY_FIELDS = {
    "version",
    "room_id",
    "binding_id",
    "room_sid",
    "generation",
    "provider",
    "issued_at",
    "expires_at",
}


def _capability_lifetime():
    lifetime = settings.MASTRAO_LIVE_WORKER_CAPABILITY_MAX_AGE_SECONDS
    if type(lifetime) is not int or not 30 <= lifetime <= 86_400:
        raise RecordingContractRefused(status=503)
    return lifetime


def _control_url():
    value = settings.MASTRAO_LIVE_WORKER_CONTROL_URL
    try:
        target = urlparse(value)
        hostname = target.hostname
        port = target.port
    except ValueError as error:
        raise RecordingContractRefused(status=503) from error
    if (
        target.scheme not in {"http", "https"}
        or not hostname
        or not _private_hostname(hostname)
        or target.path != CONTROL_PATH
        or target.username is not None
        or target.password is not None
        or target.query
        or target.fragment
        or port == 0
        or any(character.isspace() for character in value)
    ):
        raise RecordingContractRefused(status=503)
    return value


def _private_hostname(hostname):
    if hostname in {"backend", "meet-backend"}:
        return True
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        return (
            re.fullmatch(r"[A-Za-z0-9.-]+\.(?:internal|svc\.cluster\.local)", hostname)
            is not None
        )
    return address.is_loopback or any(
        address in ipaddress.ip_network(network)
        for network in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fc00::/7")
    )


def _active_control(control, generation):
    return (
        settings.MASTRAO_LIVE_WORKER_CONTROL_ENABLED
        and settings.MASTRAO_MEETING_INTEGRATION_CONFIGURED
        and settings.ROOM_SUBTITLE_ENABLED
        and control.is_current
        and control.control_generation == generation
        and control.provider == "openai-live"
        and control.desired_state == models.RoomSubtitleControl.DesiredState.ON
        and control.room_finished_at is None
    )


def live_control_metadata(room_sid, generation, provider):
    """Opt in canonical OpenAI jobs; disabled feature preserves legacy metadata."""
    if provider != "openai-live" or not settings.MASTRAO_LIVE_WORKER_CONTROL_ENABLED:
        return None
    control = (
        models.RoomSubtitleControl.objects.select_related("room")
        .filter(room_sid=room_sid)
        .first()
    )
    if control is None:
        raise RecordingContractRefused(status=503)
    binding = models.MastraoRoomBinding.objects.filter(room_id=control.room_id).first()
    if binding is None:
        return None
    if not _active_control(control, generation):
        raise RecordingContractRefused()
    url = _control_url()
    validate_core_endpoint(
        settings.MASTRAO_CORE_LIVE_PREPARE_ENDPOINT,
        PREPARE_PATH,
        RecordingContractRefused,
    )
    validate_core_endpoint(
        settings.MASTRAO_CORE_LIVE_SEND_ENDPOINT, SEND_PATH, RecordingContractRefused
    )
    now = int(time.time())
    scope = {
        "version": 1,
        "room_id": str(control.room_id),
        "binding_id": str(binding.pk),
        "room_sid": room_sid,
        "generation": generation,
        "provider": provider,
        "issued_at": now,
        "expires_at": now + _capability_lifetime(),
    }
    return {
        "version": 1,
        "url": url,
        "capability": signing.dumps(scope, salt=CAPABILITY_SALT),
    }


def _capability_scope(capability, command):
    if not isinstance(capability, str) or not 1 <= len(capability) <= 4096:
        raise RecordingContractRefused()
    try:
        scope = signing.loads(
            capability, salt=CAPABILITY_SALT, max_age=_capability_lifetime()
        )
    except (signing.BadSignature, ValueError, TypeError, UnicodeDecodeError) as error:
        raise RecordingContractRefused() from error
    if (
        not isinstance(scope, dict)
        or set(scope) != CAPABILITY_FIELDS
        or type(scope["version"]) is not int
        or scope["version"] != 1
        or type(scope["generation"]) is not int
        or scope["generation"] < 0
        or scope["provider"] != "openai-live"
        or scope["room_sid"] != command["room_sid"]
        or type(scope["issued_at"]) is not int
        or type(scope["expires_at"]) is not int
        or not scope["issued_at"] <= time.time() < scope["expires_at"]
        or not 0 < scope["expires_at"] - scope["issued_at"] <= _capability_lifetime()
    ):
        raise RecordingContractRefused()
    try:
        scope["room_id"] = UUID(scope["room_id"])
        scope["binding_id"] = UUID(scope["binding_id"])
    except (TypeError, ValueError, AttributeError) as error:
        raise RecordingContractRefused() from error
    control = models.RoomSubtitleControl.objects.filter(
        room_id=scope["room_id"],
        room_sid=scope["room_sid"],
    ).first()
    if control is None or (
        command["action"] != "stopped"
        and not _active_control(control, scope["generation"])
    ):
        raise RecordingContractRefused()
    if command["action"] != "stopped":
        _control_url()
    return scope


def _envelope(assertion_type):
    now = int(time.time())
    return {
        "version": 1,
        "type": assertion_type,
        "issuer": settings.MASTRAO_RECORDING_RECEIPT_ISSUER,
        "audience": settings.MASTRAO_RECORDING_RECEIPT_AUDIENCE,
        "issued_at": now,
        "expires_at": now + 30,
        "jti": "live_worker_" + uuid4().hex,
    }


def execute_worker_command(capability, body):
    """Sign only trusted RTC identities; only Core may authorize a send."""
    command = worker_command(body)
    scope = _capability_scope(capability, command)
    facts = trusted_live_track(scope, command)
    if command["action"] == "prepare":
        assertion = {
            **_envelope(PREPARE_TYPE),
            **facts,
            "attempt_ref": command["attempt_ref"],
            "provider": "openai",
            "model_ref": "gpt-live-transcribe",
        }
        result = _post(
            settings.MASTRAO_CORE_LIVE_PREPARE_ENDPOINT,
            PREPARE_PATH,
            PREPARE_JOSE,
            assertion,
        )
        validate_admission(result)
        return {
            "version": 1,
            "source_ref": result["source_ref"],
            "admission_ref": facts["admission_ref"],
        }
    assertion = {
        **_envelope(SEND_TYPE),
        **{
            name: facts[name]
            for name in (
                "organization_external_id",
                "meeting_ref",
                "room_ref",
                "room_sid",
                "participant_sid",
                "track_sid",
                "admission_ref",
            )
        },
        **{
            name: command[name]
            for name in (
                "action",
                "source_ref",
                "attempt_ref",
                "worker_instance_ref",
            )
        },
    }
    result = _post(
        settings.MASTRAO_CORE_LIVE_SEND_ENDPOINT, SEND_PATH, SEND_JOSE, assertion
    )
    validate_send(result, command)
    if command["action"] != "stopped":
        scope = _capability_scope(capability, command)
        trusted_live_track(scope, command)
    return result


def _post(endpoint, path, jose, assertion):
    return post_core_json(
        endpoint=endpoint,
        expected_path=path,
        body={"request": _sign(assertion, jose)},
        timeout=settings.MASTRAO_CORE_RECORDING_TIMEOUT_SECONDS,
        refusal=RecordingContractRefused,
    )


@csrf_exempt
@require_POST
def live_worker_control(request):
    """Private machine endpoint; participant sessions never authenticate it."""
    try:
        authorization = request.headers.get("Authorization", "")
        if (
            not authorization.startswith("Bearer ")
            or request.content_type != "application/json"
        ):
            raise RecordingContractRefused()
        if len(request.body) > 8192:
            raise RecordingContractRefused(status=400)
        body = json.loads(request.body, object_pairs_hook=_unique_json_fields)
        result = execute_worker_command(authorization[7:], body)
        response = JsonResponse(result)
    except (json.JSONDecodeError, UnicodeDecodeError, RecursionError):
        response = JsonResponse({"message": "Unavailable"}, status=400)
    except RecordingContractRefused as error:
        response = JsonResponse({"message": "Unavailable"}, status=error.status)
    response["Cache-Control"] = "no-store"
    return response


def _unique_json_fields(pairs):
    result = {}
    for name, value in pairs:
        if name in result:
            raise RecordingContractRefused(status=400)
        result[name] = value
    return result
