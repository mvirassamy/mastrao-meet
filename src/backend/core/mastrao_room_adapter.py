"""Private HTTP boundary for signed Mastrao room effects."""

import json

from django.conf import settings
from django.db import IntegrityError
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from core.mastrao_room_binding import ensure_room
from core.mastrao_room_contract import (
    MAX_BODY_BYTES,
    RoomEffectRefused,
    sign_receipt,
    verify_effect,
)


@csrf_exempt
@require_POST
def ensure_mastrao_room(request):
    """Create or find one restricted room from a signed exact effect."""

    if not settings.MASTRAO_ROOM_ADAPTER_ENABLED:
        return JsonResponse({"message": "Not found"}, status=404)
    declared_length = request.headers.get("content-length")
    try:
        if (
            declared_length is None
            or not declared_length.isdecimal()
            or int(declared_length) > MAX_BODY_BYTES
        ):
            raise RoomEffectRefused()
        if len(request.body) > MAX_BODY_BYTES:
            raise RoomEffectRefused()
        body = json.loads(request.body)
        if not isinstance(body, dict) or set(body) != {"room_effect"}:
            raise RoomEffectRefused()
        effect = verify_effect(body["room_effect"])
        binding = ensure_room(effect)
        return JsonResponse({"room_receipt": sign_receipt(effect, binding)})
    except (ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError):
        return JsonResponse({"message": "Not found"}, status=404)
    except RoomEffectRefused as error:
        return JsonResponse(
            {"message": "Not found" if error.status == 404 else "Unavailable"},
            status=error.status,
        )
    except IntegrityError:
        return JsonResponse({"message": "Unavailable"}, status=409)
