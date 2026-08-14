"""Private adapter applying exact canonical room close effects."""

# pylint: disable=no-member

import json
from datetime import UTC, datetime

from django.conf import settings
from django.db import IntegrityError, transaction
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from core import models
from core.mastrao_room_close_contract import (
    RoomCloseRefused,
    build_room_close_receipt_claims,
    compact_receipt_digest,
    sign_room_close_receipt,
    verify_room_close_effect,
)
from core.services.lobby import LobbyService
from core.services.room_management import (
    RoomManagement,
    RoomManagementException,
    RoomNotFoundException,
)
from core.services.sip_management import SIPException, SIPManagement

MAX_BODY_BYTES = 32_768


def _validate_binding(binding, effect):
    if (
        binding is None
        or binding.meeting_ref != effect["meeting_ref"]
        or binding.room_ref != effect["room_ref"]
        or binding.provider_binding_digest != effect["provider_binding_digest"]
    ):
        raise RoomCloseRefused()


def _validate_existing(closure, effect):
    # pylint: disable=too-many-boolean-expressions
    if (
        closure.organization_external_id != effect["organization_external_id"]
        or closure.meeting_ref != effect["meeting_ref"]
        or closure.room_ref != effect["room_ref"]
        or closure.provider_binding_digest != effect["provider_binding_digest"]
        or closure.close_ref != effect["close_ref"]
        or closure.effect_key != effect["effect_key"]
        or closure.arguments_digest != effect["arguments_digest"]
    ):
        raise RoomCloseRefused(status=409)
    return closure


@transaction.atomic
def _persist_tombstone(effect):
    binding = (
        models.MastraoRoomBinding.objects.select_for_update()
        .select_related("room")
        .filter(meeting_ref=effect["meeting_ref"], room_ref=effect["room_ref"])
        .first()
    )
    _validate_binding(binding, effect)
    closure = (
        models.MastraoRoomClosure.objects.select_for_update()
        .filter(room_binding=binding)
        .first()
    )
    if closure:
        return _validate_existing(closure, effect)
    return models.MastraoRoomClosure.objects.create(
        room_binding=binding,
        organization_external_id=effect["organization_external_id"],
        meeting_ref=effect["meeting_ref"],
        room_ref=effect["room_ref"],
        provider_binding_digest=effect["provider_binding_digest"],
        close_ref=effect["close_ref"],
        effect_key=effect["effect_key"],
        arguments_digest=effect["arguments_digest"],
        requested_at=datetime.fromtimestamp(effect["issued_at"], tz=UTC),
    )


@transaction.atomic
def _apply_tombstone(closure_id, effect):
    closure = (
        models.MastraoRoomClosure.objects.select_for_update()
        .select_related("room_binding__room")
        .get(pk=closure_id)
    )
    _validate_existing(closure, effect)
    if closure.state == models.MastraoRoomClosure.State.APPLIED:
        return sign_room_close_receipt(closure.receipt_claims)

    room_id = closure.room_binding.room_id
    try:
        RoomManagement().delete_room(str(room_id))
        observation = models.MastraoRoomClosure.ProviderObservation.DELETED
    except RoomNotFoundException:
        observation = models.MastraoRoomClosure.ProviderObservation.ALREADY_ABSENT
    except RoomManagementException as error:
        raise RoomCloseRefused(status=503) from error

    try:
        LobbyService().clear_room_cache(room_id)
        if settings.ROOM_TELEPHONY_ENABLED or settings.ROOMKIT_ENABLED:
            SIPManagement().delete_dispatch_rule(room_id)
    except SIPException as error:
        raise RoomCloseRefused(status=503) from error
    except Exception as error:  # pylint: disable=broad-exception-caught
        raise RoomCloseRefused(status=503) from error

    claims = build_room_close_receipt_claims(effect, observation)
    compact = sign_room_close_receipt(claims)
    closure.state = models.MastraoRoomClosure.State.APPLIED
    closure.applied_at = timezone.now()
    closure.provider_observation = observation
    closure.receipt_claims = claims
    closure.receipt_digest = compact_receipt_digest(compact)
    closure.save(
        update_fields=[
            "state",
            "applied_at",
            "provider_observation",
            "receipt_claims",
            "receipt_digest",
            "updated_at",
        ]
    )
    return compact


@csrf_exempt
@require_POST
def close_mastrao_room(request):
    """Tombstone and delete one exact provider room, with replayable receipt."""

    if not settings.MASTRAO_ROOM_ADAPTER_ENABLED:
        return JsonResponse({"message": "Not found"}, status=404)

    try:
        declared = request.headers.get("content-length")
        if (
            request.content_type != "application/json"
            or declared is None
            or not declared.isdecimal()
            or int(declared) > MAX_BODY_BYTES
            or len(request.body) > MAX_BODY_BYTES
        ):
            raise RoomCloseRefused()
        body = json.loads(request.body)
        if not isinstance(body, dict) or set(body) != {"room_close_effect"}:
            raise RoomCloseRefused()
        effect = verify_room_close_effect(body["room_close_effect"])
        closure = _persist_tombstone(effect)
        compact = _apply_tombstone(closure.pk, effect)
        return JsonResponse(
            {"room_close_receipt": compact},
            headers={"Cache-Control": "private, no-store"},
        )
    except (ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError):
        return JsonResponse({"message": "Not found"}, status=404)
    except RoomCloseRefused as error:
        return JsonResponse(
            {"message": "Not found" if error.status == 404 else "Unavailable"},
            status=error.status,
            headers={"Cache-Control": "private, no-store"},
        )
    except IntegrityError:
        return JsonResponse({"message": "Unavailable"}, status=409)
