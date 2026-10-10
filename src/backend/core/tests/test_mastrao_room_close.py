"""Focused proofs for irreversible canonical room closure."""

# pylint: disable=no-member

import json
import time
from unittest import mock

from django.http import Http404
from django.test import Client, RequestFactory, override_settings
from django.utils import timezone

import pytest
from livekit import api as livekit_api

from core import models
from core.api.permissions import HasMeetingLifecycleAccess
from core.api.viewsets import RoomViewSet
from core.mastrao_meeting_close import request_meeting_close
from core.mastrao_room_close_adapter import close_mastrao_room
from core.mastrao_room_close_contract import RoomCloseRefused
from core.mastrao_room_lifecycle import MastraoRoomClosed
from core.recording.worker.exceptions import WorkerConnectionError
from core.services.room_management import (
    RoomManagementException,
    RoomNotFoundException,
    ensure_livekit_room,
)
from core.services.subtitle_control import ensure_subtitle_control
from core.services.subtitle_reconciliation import (
    SubtitleReconciliationAmbiguous,
    publish_subtitle_snapshot,
    reconcile_subtitle_control,
    schedule_subtitle_reconciliation,
)
from core.tasks.subtitle import (
    process_subtitle_reconciliation,
    process_subtitle_snapshot_publication,
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


def _active_subtitle_control(room, room_sid):
    control = ensure_subtitle_control(room, room_sid=room_sid)
    control.desired_state = models.RoomSubtitleControl.DesiredState.ON
    control.public_state = models.RoomSubtitleControl.PublicState.STARTING
    control.save(update_fields=["desired_state", "public_state", "updated_at"])
    return control


@pytest.mark.django_db
def test_authorized_lifecycle_projection_distinguishes_ending_and_ended():
    """The browser gets only the authoritative minimal terminal state."""

    binding = _binding("lifecycle")
    binding.room_ref = "room_11111111111111111111111111111111"
    binding.save(update_fields=["room_ref", "updated_at"])
    binding.closing_at = timezone.now()
    binding.save(update_fields=["closing_at", "updated_at"])
    view = RoomViewSet.as_view({"get": "mastrao_meeting_lifecycle"})
    factory = RequestFactory()
    lifecycle_grant = mock.Mock(room_binding=binding)
    with mock.patch(
        "core.api.viewsets.active_host_close_grant_for_room_ref",
        return_value=lifecycle_grant,
    ):
        response = view(
            factory.get(f"/api/v1.0/rooms/{binding.room_ref}/lifecycle/"),
            pk=binding.room_ref,
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
        "core.api.viewsets.active_host_close_grant_for_room_ref",
        return_value=lifecycle_grant,
    ):
        response = view(
            factory.get(f"/api/v1.0/rooms/{binding.room_ref}/lifecycle/"),
            pk=binding.room_ref,
        )
    assert response.data == {"state": "ended"}


@pytest.mark.django_db
def test_lifecycle_projection_masks_unauthorized_room_existence():
    """Unauthorized lifecycle reads cannot enumerate canonical rooms."""

    binding = _binding("masked")
    client = Client()

    response = client.get(f"/api/v1.0/rooms/{binding.room.slug}/lifecycle/")
    assert response.status_code == 404
    assert response["Cache-Control"] == "no-store"

    missing = client.get("/api/v1.0/rooms/room_missing_0123456789/lifecycle/")
    assert missing.status_code == 404
    assert missing["Cache-Control"] == "no-store"
    assert missing.json() == response.json()

    permission = HasMeetingLifecycleAccess()
    request = RequestFactory().get(f"/api/v1.0/rooms/{binding.room.slug}/lifecycle/")
    request.session = {}

    with pytest.raises(Http404):
        permission.has_object_permission(
            request,
            mock.Mock(),
            binding.room,
        )


@pytest.mark.django_db(transaction=True)
@override_settings(
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
@pytest.mark.parametrize("status", [404, 409, 503])
@override_settings(
    LIVEKIT_EXPLICIT_ROOM_CREATION=True,
)
def test_refused_or_unaccepted_core_close_does_not_create_local_fence(status):
    """Meet cannot become the lifecycle authority before Core accepts the close."""

    binding = _binding("lost_response")
    grant = mock.Mock(
        room_binding_id=binding.pk,
        meeting_ref=binding.meeting_ref,
        room_ref=binding.room_ref,
    )
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
            side_effect=RoomCloseRefused(status=status),
        ),
        pytest.raises(RoomCloseRefused),
    ):
        request_meeting_close(
            mock.Mock(), binding.room, "close_request_lost_0123456789"
        )

    binding.refresh_from_db()
    assert binding.closing_at is None
    assert not models.MastraoRoomClosure.objects.filter(room_binding=binding).exists()


@pytest.mark.django_db(transaction=True)
@override_settings(
    MASTRAO_MEETING_INTEGRATION_CONFIGURED=True,
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
    MASTRAO_MEETING_INTEGRATION_CONFIGURED=True,
    ROOM_SUBTITLE_ENABLED=True,
    CELERY_ENABLED=True,
    ROOM_TELEPHONY_ENABLED=False,
    ROOMKIT_ENABLED=False,
)
def test_close_stops_subtitles_without_room_finished_webhook():
    """The close effect commits OFF before deleting the provider room."""

    binding = _binding("subtitles")
    effect = _effect(binding, "subtitles")
    control = _active_subtitle_control(binding.room, "RM_close_subtitles")

    def delete_room(_room_id):
        control.refresh_from_db()
        assert control.desired_state == models.RoomSubtitleControl.DesiredState.OFF
        assert (
            control.reason_code == models.RoomSubtitleControl.ReasonCode.ROOM_FINISHED
        )
        assert control.room_finished_at is not None

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
            side_effect=delete_room,
        ) as provider_delete,
        mock.patch("core.mastrao_room_close_adapter.LobbyService.clear_room_cache"),
        mock.patch("core.services.subtitle_reconciliation.publish_subtitle_snapshot"),
        mock.patch(
            "core.tasks.subtitle.process_subtitle_reconciliation.apply_async"
        ) as schedule,
    ):
        first = close_mastrao_room(_request())
        control.refresh_from_db()
        generation = control.control_generation
        second = close_mastrao_room(_request())

    assert first.status_code == second.status_code == 200
    control.refresh_from_db()
    assert control.control_generation == generation
    provider_delete.assert_called_once_with(str(binding.room_id))
    schedule.assert_called_once()
    assert schedule.call_args.kwargs["args"] == [control.room_sid]

    client = mock.AsyncMock()
    client.agent_dispatch.list_dispatch.side_effect = livekit_api.TwirpError(
        msg="room not found", code="not_found", status=404
    )
    with mock.patch("core.utils.create_livekit_client", return_value=client):
        settled = reconcile_subtitle_control(control.room_sid)

    assert settled.desired_state == models.RoomSubtitleControl.DesiredState.OFF
    assert settled.public_state == models.RoomSubtitleControl.PublicState.STOPPED
    client.agent_dispatch.create_dispatch.assert_not_awaited()


@pytest.mark.django_db(transaction=True)
@override_settings(
    MASTRAO_MEETING_INTEGRATION_CONFIGURED=True,
    ROOM_SUBTITLE_ENABLED=True,
    CELERY_ENABLED=True,
    ROOM_TELEPHONY_ENABLED=False,
    ROOMKIT_ENABLED=False,
)
@pytest.mark.parametrize("attempts", [0, 3])
@pytest.mark.parametrize("work", ["reconciliation", "snapshot"])
def test_queued_subtitle_work_after_canonical_close_does_not_contact_livekit(
    attempts, work
):
    """A committed canonical deletion settles queued work without provider retries."""
    binding = _binding("queued_subtitles")
    effect = _effect(binding, "queued_subtitles")
    control = _active_subtitle_control(binding.room, "RM_queued_close")
    with (
        mock.patch(
            "core.mastrao_room_close_adapter.verify_room_close_effect",
            return_value=effect,
        ),
        mock.patch(
            "core.mastrao_room_close_adapter.sign_room_close_receipt",
            return_value="receipt.payload.signature",
        ),
        mock.patch("core.mastrao_room_close_adapter.RoomManagement.delete_room"),
        mock.patch("core.mastrao_room_close_adapter.LobbyService.clear_room_cache"),
        mock.patch("core.services.subtitle_reconciliation.publish_subtitle_snapshot"),
        mock.patch("core.tasks.subtitle.process_subtitle_reconciliation.apply_async"),
    ):
        assert close_mastrao_room(_request()).status_code == 200

    closure = models.MastraoRoomClosure.objects.get(room_binding=binding)
    assert closure.state == models.MastraoRoomClosure.State.APPLIED
    control.refresh_from_db()
    closed_generation = control.control_generation
    control.attempts = attempts
    control.next_retry_at = timezone.now()
    control.observed_dispatch_ids = ["AD_Bo6CVqcR7niT"]
    control.agent_present = True
    control.worker_ready = True
    control.session_id = "subtitle-agent-session"
    control.save()
    client = mock.AsyncMock()
    client.agent_dispatch.list_dispatch.side_effect = livekit_api.TwirpError(
        msg="room deleted", code="unavailable", status=503
    )
    client.room.send_data.side_effect = livekit_api.TwirpError(
        msg="room deleted", code="unavailable", status=503
    )
    with (
        mock.patch("core.utils.create_livekit_client", return_value=client) as provider,
        mock.patch(
            "core.tasks.subtitle.process_subtitle_reconciliation.apply_async"
        ) as reconcile_retry,
        mock.patch(
            "core.tasks.subtitle.process_subtitle_snapshot_publication.apply_async"
        ) as snapshot_retry,
    ):
        if work == "reconciliation":
            for _ in range(2):
                result = process_subtitle_reconciliation(control.room_sid)
                assert (
                    result.public_state
                    == models.RoomSubtitleControl.PublicState.STOPPED
                )
            reconcile_subtitle_control(control.room_sid, deadline=0)
            assert schedule_subtitle_reconciliation(control.room_sid) == 0
        else:
            process_subtitle_snapshot_publication(str(binding.room_id), attempt=1)
            publish_subtitle_snapshot(binding.room_id, attempt=2)

        provider.assert_not_called()
        reconcile_retry.assert_not_called()
        snapshot_retry.assert_not_called()
        process_subtitle_reconciliation(control.room_sid)

    provider.assert_not_called()
    reconcile_retry.assert_not_called()
    snapshot_retry.assert_not_called()
    control.refresh_from_db()
    assert control.desired_state == models.RoomSubtitleControl.DesiredState.OFF
    assert control.public_state == models.RoomSubtitleControl.PublicState.STOPPED
    assert control.reason_code == models.RoomSubtitleControl.ReasonCode.ROOM_FINISHED
    assert control.room_finished_at is not None
    assert control.control_generation == closed_generation
    assert control.observed_dispatch_ids == []
    assert not control.agent_present
    assert not control.worker_ready
    assert control.session_id is None
    assert control.attempts == 0
    assert control.next_retry_at is None


@pytest.mark.django_db(transaction=True)
@override_settings(
    MASTRAO_MEETING_INTEGRATION_CONFIGURED=True,
    ROOM_SUBTITLE_ENABLED=True,
    CELERY_ENABLED=True,
    ROOM_TELEPHONY_ENABLED=False,
    ROOMKIT_ENABLED=False,
)
def test_pending_close_retries_without_restarting_subtitles():
    """A failed provider delete retains one terminal subtitle intent."""

    binding = _binding("subtitle_retry")
    effect = _effect(binding, "subtitle_retry")
    control = _active_subtitle_control(binding.room, "RM_close_retry")
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
            side_effect=[
                RoomManagementException("unavailable"),
                RoomNotFoundException("already absent"),
            ],
        ) as provider_delete,
        mock.patch("core.mastrao_room_close_adapter.LobbyService.clear_room_cache"),
        mock.patch("core.services.subtitle_reconciliation.publish_subtitle_snapshot"),
        mock.patch(
            "core.tasks.subtitle.process_subtitle_reconciliation.apply_async"
        ) as schedule,
    ):
        first = close_mastrao_room(_request())
        control.refresh_from_db()
        generation = control.control_generation
        assert first.status_code == 503
        assert (
            models.MastraoRoomClosure.objects.get(room_binding=binding).state
            == models.MastraoRoomClosure.State.PENDING
        )
        client = mock.AsyncMock()
        client.agent_dispatch.list_dispatch.side_effect = TimeoutError(
            "provider unavailable before deletion"
        )
        with mock.patch("core.utils.create_livekit_client", return_value=client):
            with pytest.raises(SubtitleReconciliationAmbiguous):
                reconcile_subtitle_control(control.room_sid)
        client.agent_dispatch.list_dispatch.assert_awaited_once()
        control.refresh_from_db()
        assert control.public_state == models.RoomSubtitleControl.PublicState.STOPPING
        assert control.next_retry_at is not None
        second = close_mastrao_room(_request())

    assert first.status_code == 503
    assert second.status_code == 200
    control.refresh_from_db()
    assert control.desired_state == models.RoomSubtitleControl.DesiredState.OFF
    assert control.reason_code == models.RoomSubtitleControl.ReasonCode.ROOM_FINISHED
    assert control.control_generation == generation
    assert (
        models.MastraoRoomClosure.objects.get(room_binding=binding).state == "applied"
    )
    assert provider_delete.call_count == 2
    assert schedule.call_count == 3


@pytest.mark.django_db(transaction=True)
@override_settings(
    MASTRAO_MEETING_INTEGRATION_CONFIGURED=True,
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
    MASTRAO_MEETING_INTEGRATION_CONFIGURED=True,
    MASTRAO_ROOM_RECEIPT_ISSUER="mastrao-meet-local",
    MASTRAO_ROOM_RECEIPT_AUDIENCE="cabinet-core-local",
    ROOM_TELEPHONY_ENABLED=False,
    ROOMKIT_ENABLED=False,
)
def test_already_aborted_recording_does_not_block_room_close():
    """A terminal-aborted provider recording still allows room closure."""

    binding = _binding("aborted")
    effect = _effect(binding, "aborted")
    recording = models.Recording.objects.create(
        room=binding.room,
        status=models.RecordingStatusChoices.ACTIVE,
        mode=models.RecordingModeChoices.SCREEN_RECORDING,
        worker_id="EG_already_aborted",
    )
    recording_binding = models.MastraoRecordingBinding.objects.create(
        room_binding=binding,
        recording=recording,
        organization_external_id=effect["organization_external_id"],
        meeting_ref=binding.meeting_ref,
        room_ref=binding.room_ref,
        recording_ref="recording_aborted_0123456789",
        provider_binding_digest=binding.provider_binding_digest,
        policy_ref="policy_aborted_0123456789",
        notice_version="notice_aborted_0123456789",
        notice_digest="d" * 64,
        retention_expires_at=timezone.now() + timezone.timedelta(days=30),
        state=models.MastraoRecordingBinding.State.ACTIVE,
        provider_recording_ref="EG_already_aborted",
    )

    worker_service = mock.Mock()
    worker_service.stop.side_effect = WorkerConnectionError(
        "LiveKit client connection error, "
        "egress with status EGRESS_ABORTED cannot be stopped."
    )

    with (
        mock.patch(
            "core.mastrao_room_close_adapter.verify_room_close_effect",
            return_value=effect,
        ),
        mock.patch(
            "core.mastrao_room_close_adapter.get_worker_service",
            return_value=worker_service,
        ),
        mock.patch(
            "core.mastrao_room_close_adapter.sign_room_close_receipt",
            return_value="receipt.payload.signature",
        ),
        mock.patch("core.mastrao_room_close_adapter.RoomManagement.delete_room"),
        mock.patch("core.mastrao_room_close_adapter.LobbyService.clear_room_cache"),
    ):
        response = close_mastrao_room(_request())

    assert response.status_code == 200
    recording.refresh_from_db()
    recording_binding.refresh_from_db()
    closure = models.MastraoRoomClosure.objects.get(room_binding=binding)
    assert recording.status == models.RecordingStatusChoices.ABORTED
    assert recording_binding.state == models.MastraoRecordingBinding.State.PROCESSING
    assert closure.state == models.MastraoRoomClosure.State.APPLIED


@pytest.mark.django_db(transaction=True)
@override_settings(
    MASTRAO_MEETING_INTEGRATION_CONFIGURED=True,
    MASTRAO_ROOM_RECEIPT_ISSUER="mastrao-meet-local",
    MASTRAO_ROOM_RECEIPT_AUDIENCE="cabinet-core-local",
    ROOM_TELEPHONY_ENABLED=False,
    ROOMKIT_ENABLED=False,
)
def test_close_preserves_finalized_recording_during_status_save_race():
    """Room close cannot regress a finalized artifact while status is stale."""

    binding = _binding("finalized")
    effect = _effect(binding, "finalized")
    recording = models.Recording.objects.create(
        room=binding.room,
        status=models.RecordingStatusChoices.ACTIVE,
        mode=models.RecordingModeChoices.SCREEN_RECORDING,
        worker_id="EG_finalized",
    )
    recording_binding = models.MastraoRecordingBinding.objects.create(
        room_binding=binding,
        recording=recording,
        organization_external_id=effect["organization_external_id"],
        meeting_ref=binding.meeting_ref,
        room_ref=binding.room_ref,
        recording_ref="recording_finalized_0123456789",
        provider_binding_digest=binding.provider_binding_digest,
        policy_ref="policy_finalized_0123456789",
        notice_version="notice_finalized_0123456789",
        notice_digest="d" * 64,
        retention_expires_at=timezone.now() + timezone.timedelta(days=30),
        state=models.MastraoRecordingBinding.State.FINALIZED,
        provider_recording_ref="EG_finalized",
    )

    with (
        mock.patch(
            "core.mastrao_room_close_adapter.verify_room_close_effect",
            return_value=effect,
        ),
        mock.patch(
            "core.mastrao_room_close_adapter.get_worker_service"
        ) as get_worker_service,
        mock.patch(
            "core.mastrao_room_close_adapter.sign_room_close_receipt",
            return_value="receipt.payload.signature",
        ),
        mock.patch("core.mastrao_room_close_adapter.RoomManagement.delete_room"),
        mock.patch("core.mastrao_room_close_adapter.LobbyService.clear_room_cache"),
    ):
        response = close_mastrao_room(_request())

    assert response.status_code == 200
    get_worker_service.assert_not_called()
    recording_binding.refresh_from_db()
    assert recording_binding.state == models.MastraoRecordingBinding.State.FINALIZED
    assert (
        models.MastraoRoomClosure.objects.get(room_binding=binding).state
        == models.MastraoRoomClosure.State.APPLIED
    )


@pytest.mark.django_db(transaction=True)
@override_settings(
    MASTRAO_MEETING_INTEGRATION_CONFIGURED=True,
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
    MASTRAO_MEETING_INTEGRATION_CONFIGURED=True,
    LIVEKIT_EXPLICIT_ROOM_CREATION=True,
    MASTRAO_ROOM_RECEIPT_ISSUER="mastrao-meet-local",
    MASTRAO_ROOM_RECEIPT_AUDIENCE="cabinet-core-local",
    ROOM_TELEPHONY_ENABLED=False,
    ROOMKIT_ENABLED=False,
)
def test_accepted_close_effect_reconciles_without_a_product_flag():
    """An accepted Core effect always reaches its idempotent provider outcome."""

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


@override_settings(MASTRAO_MEETING_INTEGRATION_CONFIGURED=False)
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
