"""Focused proofs for irreversible canonical room closure."""

# pylint: disable=no-member

import json
import time
from unittest import mock

from django.test import RequestFactory, override_settings
from django.utils import timezone

import pytest

from core import models
from core.api.viewsets import RoomViewSet
from core.mastrao_meeting_close import request_meeting_close
from core.mastrao_room_close_adapter import close_mastrao_room
from core.mastrao_room_lifecycle import MastraoRoomClosed
from core.services.room_management import (
    RoomManagementException,
    RoomNotFoundException,
    ensure_livekit_room,
)


def _binding(suffix="one"):
    owner = models.User(sub=f"owner_{suffix}", is_device=True)
    owner.set_unusable_password()
    owner.save()
    room = models.Room.objects.create(
        name="Canonical meeting",
        slug=f"room_{suffix}_0123456789",
        access_level=models.RoomAccessLevel.RESTRICTED,
    )
    return models.MastraoRoomBinding.objects.create(
        effect_key=f"create_effect_{suffix}_0123456789",
        arguments_digest="a" * 64,
        meeting_ref=f"meeting_{suffix}_0123456789",
        room_ref=f"room_{suffix}_0123456789",
        owner_ref=f"owner_{suffix}_0123456789",
        room=room,
        owner=owner,
        provider_binding_digest="b" * 64,
    )


def _effect(binding, suffix="one"):
    now = int(time.time())
    return {
        "version": 1,
        "type": "mastrao.core-meeting-room-close-effect",
        "issuer": "cabinet-core-local",
        "audience": "mastrao-meet-local",
        "operation": "close_private_room",
        "operation_version": 1,
        "close_ref": f"close_{suffix}_0123456789",
        "effect_key": f"close_effect_{suffix}_0123456789",
        "arguments_digest": "c" * 64,
        "organization_external_id": "organization_0123456789",
        "meeting_ref": binding.meeting_ref,
        "room_ref": binding.room_ref,
        "provider_binding_digest": binding.provider_binding_digest,
        "issued_at": now,
        "expires_at": now + 30,
        "jti": f"closejti_{suffix}_0123456789",
    }


def _request():
    return RequestFactory().post(
        "/internal/mastrao/rooms/close/",
        data=json.dumps({"room_close_effect": "header.payload.signature"}),
        content_type="application/json",
    )


@pytest.mark.django_db
def test_authorized_lifecycle_projection_distinguishes_ending_and_ended():
    """The browser gets only the authoritative minimal terminal state."""

    binding = _binding("lifecycle")
    binding.closing_at = timezone.now()
    binding.save(update_fields=["closing_at", "updated_at"])
    view = RoomViewSet.as_view({"get": "mastrao_meeting_lifecycle"})
    factory = RequestFactory()

    with mock.patch(
        "core.api.permissions.active_host_close_grant", return_value=mock.Mock()
    ):
        response = view(
            factory.get(f"/api/v1.0/rooms/{binding.room.slug}/lifecycle/"),
            pk=binding.room.slug,
        )
    assert response.status_code == 200
    assert response.data == {"state": "ending"}
    assert response["Cache-Control"] == "no-store"

    models.MastraoRoomClosure.objects.create(
        room_binding=binding,
        organization_external_id="organization_lifecycle_0123456789",
        meeting_ref=binding.meeting_ref,
        room_ref=binding.room_ref,
        provider_binding_digest=binding.provider_binding_digest,
        close_ref="close_lifecycle_0123456789",
        effect_key="close_effect_lifecycle_0123456789",
        arguments_digest="c" * 64,
        state=models.MastraoRoomClosure.State.APPLIED,
        requested_at=timezone.now(),
        applied_at=timezone.now(),
        provider_observation=models.MastraoRoomClosure.ProviderObservation.DELETED,
        receipt_claims={"state": "ended"},
        receipt_digest="d" * 64,
    )
    with mock.patch(
        "core.api.permissions.active_host_close_grant", return_value=mock.Mock()
    ):
        response = view(
            factory.get(f"/api/v1.0/rooms/{binding.room.slug}/lifecycle/"),
            pk=binding.room.slug,
        )
    assert response.data == {"state": "ended"}


@pytest.mark.django_db(transaction=True)
@override_settings(
    MASTRAO_MEETING_CLOSE_ENABLED=True,
    LIVEKIT_EXPLICIT_ROOM_CREATION=True,
)
def test_core_close_acceptance_fences_room_before_effect_delivery():
    """A successful Core transition blocks local media before reconciliation."""

    binding = _binding("accepted")
    grant = mock.Mock(
        room_binding_id=binding.pk,
        meeting_ref=binding.meeting_ref,
        room_ref=binding.room_ref,
    )
    response = {
        "version": 1,
        "matter_ref": "matter_accepted_0123456789",
        "meeting_ref": binding.meeting_ref,
        "room_ref": binding.room_ref,
        "state": "ending",
        "state_version": 2,
        "requested_at": int(time.time()),
    }
    with (
        mock.patch(
            "core.mastrao_meeting_close.active_host_close_grant",
            return_value=grant,
        ),
        mock.patch(
            "core.mastrao_meeting_close.active_host_compact_grant",
            return_value="host.payload.signature",
        ),
        mock.patch(
            "core.mastrao_meeting_close.sign_meeting_close_request",
            return_value=("close.payload.signature", {}),
        ),
        mock.patch(
            "core.mastrao_meeting_close.post_core_json",
            return_value=response,
        ),
    ):
        assert (
            request_meeting_close(
                mock.Mock(), binding.room, "close_request_accepted_0123456789"
            )
            == response
        )

    binding.refresh_from_db()
    assert binding.closing_at is not None
    assert not models.MastraoRoomClosure.objects.filter(room_binding=binding).exists()
    with pytest.raises(MastraoRoomClosed):
        ensure_livekit_room(str(binding.room_id))


@pytest.mark.django_db(transaction=True)
@override_settings(
    MASTRAO_ROOM_ADAPTER_ENABLED=True,
    MASTRAO_MEETING_CLOSE_ENABLED=True,
    LIVEKIT_EXPLICIT_ROOM_CREATION=True,
    MASTRAO_ROOM_RECEIPT_ISSUER="mastrao-meet-local",
    MASTRAO_ROOM_RECEIPT_AUDIENCE="cabinet-core-local",
    ROOM_TELEPHONY_ENABLED=False,
    ROOMKIT_ENABLED=False,
)
def test_close_tombstones_deletes_and_replays_without_second_provider_call():
    """The exact effect is applied once and replays one stable receipt."""
    binding = _binding()
    effect = _effect(binding)

    with (
        mock.patch(
            "core.mastrao_room_close_adapter.verify_room_close_effect",
            return_value=effect,
        ),
        mock.patch(
            "core.mastrao_room_close_adapter.sign_room_close_receipt",
            return_value="receipt.payload.signature",
        ),
        mock.patch(
            "core.mastrao_room_close_adapter.RoomManagement.delete_room"
        ) as delete_room,
        mock.patch("core.mastrao_room_close_adapter.LobbyService.clear_room_cache"),
    ):
        first = close_mastrao_room(_request())
        second = close_mastrao_room(_request())

    assert first.status_code == second.status_code == 200
    assert (
        json.loads(first.content)
        == json.loads(second.content)
        == {"room_close_receipt": "receipt.payload.signature"}
    )
    delete_room.assert_called_once_with(str(binding.room_id))
    closure = models.MastraoRoomClosure.objects.get(room_binding=binding)
    assert closure.state == models.MastraoRoomClosure.State.APPLIED
    assert closure.provider_observation == "deleted"
    with pytest.raises(MastraoRoomClosed):
        ensure_livekit_room(str(binding.room_id))


@pytest.mark.django_db(transaction=True)
@override_settings(
    MASTRAO_ROOM_ADAPTER_ENABLED=True,
    MASTRAO_MEETING_CLOSE_ENABLED=True,
    LIVEKIT_EXPLICIT_ROOM_CREATION=True,
    ROOM_TELEPHONY_ENABLED=False,
    ROOMKIT_ENABLED=False,
)
def test_provider_failure_keeps_pending_tombstone_that_blocks_creation():
    """A provider failure never removes the durable close intent."""
    binding = _binding("failure")
    effect = _effect(binding, "failure")

    with (
        mock.patch(
            "core.mastrao_room_close_adapter.verify_room_close_effect",
            return_value=effect,
        ),
        mock.patch(
            "core.mastrao_room_close_adapter.RoomManagement.delete_room",
            side_effect=RoomManagementException("unavailable"),
        ),
    ):
        response = close_mastrao_room(_request())

    assert response.status_code == 503
    closure = models.MastraoRoomClosure.objects.get(room_binding=binding)
    assert closure.state == models.MastraoRoomClosure.State.PENDING
    with pytest.raises(MastraoRoomClosed):
        ensure_livekit_room(str(binding.room_id))


@pytest.mark.django_db(transaction=True)
@override_settings(
    MASTRAO_ROOM_ADAPTER_ENABLED=True,
    MASTRAO_MEETING_CLOSE_ENABLED=True,
    MASTRAO_ROOM_RECEIPT_ISSUER="mastrao-meet-local",
    MASTRAO_ROOM_RECEIPT_AUDIENCE="cabinet-core-local",
    ROOM_TELEPHONY_ENABLED=False,
    ROOMKIT_ENABLED=False,
)
def test_missing_provider_room_is_a_successful_idempotent_close():
    """Provider absence proves the requested room state."""
    binding = _binding("missing")
    effect = _effect(binding, "missing")

    with (
        mock.patch(
            "core.mastrao_room_close_adapter.verify_room_close_effect",
            return_value=effect,
        ),
        mock.patch(
            "core.mastrao_room_close_adapter.sign_room_close_receipt",
            return_value="receipt.payload.signature",
        ),
        mock.patch(
            "core.mastrao_room_close_adapter.RoomManagement.delete_room",
            side_effect=RoomNotFoundException("missing"),
        ),
        mock.patch("core.mastrao_room_close_adapter.LobbyService.clear_room_cache"),
    ):
        response = close_mastrao_room(_request())

    assert response.status_code == 200
    closure = models.MastraoRoomClosure.objects.get(room_binding=binding)
    assert closure.provider_observation == "already_absent"


@pytest.mark.django_db(transaction=True)
@override_settings(
    MASTRAO_ROOM_ADAPTER_ENABLED=True,
    MASTRAO_MEETING_CLOSE_ENABLED=False,
    LIVEKIT_EXPLICIT_ROOM_CREATION=True,
    MASTRAO_ROOM_RECEIPT_ISSUER="mastrao-meet-local",
    MASTRAO_ROOM_RECEIPT_AUDIENCE="cabinet-core-local",
    ROOM_TELEPHONY_ENABLED=False,
    ROOMKIT_ENABLED=False,
)
def test_accepted_close_effect_reconciles_after_new_intent_flag_is_disabled():
    """Rollout rollback gates new intents, never an already accepted Core effect."""

    binding = _binding("rollback")
    effect = _effect(binding, "rollback")
    with (
        mock.patch(
            "core.mastrao_room_close_adapter.verify_room_close_effect",
            return_value=effect,
        ),
        mock.patch(
            "core.mastrao_room_close_adapter.sign_room_close_receipt",
            return_value="receipt.payload.signature",
        ),
        mock.patch(
            "core.mastrao_room_close_adapter.RoomManagement.delete_room"
        ) as delete_room,
        mock.patch("core.mastrao_room_close_adapter.LobbyService.clear_room_cache"),
    ):
        response = close_mastrao_room(_request())

    assert response.status_code == 200
    delete_room.assert_called_once_with(str(binding.room_id))
    assert models.MastraoRoomClosure.objects.filter(room_binding=binding).exists()


@override_settings(MASTRAO_ROOM_ADAPTER_ENABLED=False)
def test_close_adapter_refuses_effects_when_room_adapter_is_disabled():
    """The global room adapter kill switch also closes the destructive endpoint."""

    with (
        mock.patch(
            "core.mastrao_room_close_adapter.verify_room_close_effect"
        ) as verify,
        mock.patch(
            "core.mastrao_room_close_adapter.RoomManagement.delete_room"
        ) as delete_room,
    ):
        response = close_mastrao_room(_request())

    assert response.status_code == 404
    verify.assert_not_called()
    delete_room.assert_not_called()
