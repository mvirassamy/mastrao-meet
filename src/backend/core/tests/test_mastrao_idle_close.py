"""Durable empty-room grace and canonical idle-close delivery."""

import time
from datetime import timedelta
from types import SimpleNamespace
from unittest import mock

from django.utils import timezone

import pytest

from core import models
from core.mastrao_idle_close import (
    IdleCloseRetryable,
    deliver_idle_close,
    idle_close_blocks_restart,
    observe_room_finished,
)
from core.mastrao_room_lifecycle import MastraoRoomClosed
from core.services.room_management import ensure_livekit_room
from core.tasks.idle_close import recover_idle_close_tasks


def _binding():
    owner = models.User(sub="device_idle_close", is_device=True)
    owner.set_unusable_password()
    owner.save()
    room = models.Room.objects.create(
        name="Idle close",
        access_level=models.RoomAccessLevel.RESTRICTED,
    )
    binding = models.MastraoRoomBinding.objects.create(
        effect_key="effect_idle_close_012345",
        arguments_digest="a" * 64,
        meeting_ref="meeting_idle_close_012345",
        room_ref="room_idle_close_0123456789",
        owner_ref="owner_idle_close_012345678",
        room=room,
        owner=owner,
        provider_binding_digest="b" * 64,
    )
    host_user = models.User(sub="host_idle_close")
    host_user.set_unusable_password()
    host_user.save()
    identity = models.MastraoHostIdentity.objects.create(
        host_ref="host_idle_close_0123456789",
        user=host_user,
    )
    now = timezone.now()
    models.MastraoHostGrant.objects.create(
        handoff_ref="handoff_idle_close_012345",
        grant_ref="grant_idle_close_012345678",
        grant_digest="c" * 64,
        credential_digest="d" * 64,
        meeting_ref=binding.meeting_ref,
        room_ref=binding.room_ref,
        provider_binding_digest=binding.provider_binding_digest,
        organization_external_id="organization_idle_close_012345",
        identity=identity,
        room_binding=binding,
        platform_session_ref="platformsession_idle_close_01",
        session_nonce_digest="e" * 64,
        issued_at=now,
        expires_at=now + timedelta(hours=1),
    )
    return binding


def _event(binding, *, event_id="EV_idle_close_012345", created_at=None):
    return SimpleNamespace(
        id=event_id,
        created_at=created_at or int(time.time()),
        room=SimpleNamespace(
            name=str(binding.room_id),
            sid="RM_idle_close_012345",
            departure_timeout=600,
        ),
    )


def _prove_room_empty(binding, event, *, elapsed_seconds=600):
    departed_at = event.created_at - elapsed_seconds
    models.MastraoRtcObservation.objects.create(
        room_binding=binding,
        event_id=f"left_{event.id}",
        payload_digest="f" * 64,
        event_type="participant_left",
        event_time_seconds=departed_at,
        room_sid=event.room.sid,
        participant_sid="PA_idle_close_012345",
        rtc_identity="departed_idle_close_participant",
    )
    models.MastraoRtcConnection.objects.create(
        room_binding=binding,
        room_sid=event.room.sid,
        participant_sid="PA_idle_close_012345",
        ended=True,
    )


def _observed_idle_close(binding):
    event = _event(binding)
    _prove_room_empty(binding, event)
    return observe_room_finished(event)


@pytest.mark.django_db(transaction=True)
def test_room_finished_schedules_immediate_close_and_blocks_restart(settings):
    """The terminal event schedules delivery after LiveKit's departure grace."""

    settings.CELERY_ENABLED = True
    settings.LIVEKIT_EXPLICIT_ROOM_CREATION = True
    binding = _binding()
    event = _event(binding)
    _prove_room_empty(binding, event)

    with mock.patch("core.mastrao_idle_close.current_app.send_task") as enqueue:
        candidate = observe_room_finished(event)

    assert candidate is not None
    enqueue.assert_called_once()
    assert enqueue.call_args.args == ("core.tasks.idle_close.process_idle_close",)
    assert "countdown" not in enqueue.call_args.kwargs
    assert idle_close_blocks_restart(binding.room_id) is True

    with (
        mock.patch(
            "core.services.room_management.RoomManagement.ensure_room"
        ) as ensure,
        pytest.raises(MastraoRoomClosed),
    ):
        ensure_livekit_room(str(binding.room_id))
    ensure.assert_not_called()


@pytest.mark.django_db(transaction=True)
def test_unproven_provider_departure_timeout_is_ignored(settings):
    """Old room generations cannot claim the new ten-minute close contract."""

    settings.CELERY_ENABLED = False
    binding = _binding()
    event = _event(binding)
    _prove_room_empty(binding, event)
    event.room.departure_timeout = 20

    assert observe_room_finished(event) is None
    assert not models.MastraoIdleCloseCandidate.objects.exists()


@pytest.mark.django_db(transaction=True)
def test_early_room_finished_is_not_treated_as_idle_timeout(settings):
    """An explicit provider close before the grace expires proves no idle close."""

    settings.CELERY_ENABLED = False
    binding = _binding()
    event = _event(binding)
    _prove_room_empty(binding, event, elapsed_seconds=599)

    assert observe_room_finished(event) is None
    assert not models.MastraoIdleCloseCandidate.objects.exists()


@pytest.mark.django_db(transaction=True)
def test_room_finished_with_an_observed_participant_still_present_is_ignored(settings):
    """A stale departure cannot prove that the provider room stayed empty."""

    settings.CELERY_ENABLED = False
    binding = _binding()
    event = _event(binding)
    _prove_room_empty(binding, event)
    models.MastraoRtcConnection.objects.filter(room_binding=binding).update(ended=False)

    assert observe_room_finished(event) is None
    assert not models.MastraoIdleCloseCandidate.objects.exists()


@pytest.mark.django_db(transaction=True)
def test_due_absent_room_delivers_exact_canonical_close(settings):
    """An absent provider room delivers its exact canonical binding."""

    settings.CELERY_ENABLED = False
    binding = _binding()
    candidate = _observed_idle_close(binding)
    assert candidate is not None
    response = {
        "version": 1,
        "matter_ref": "matter_idle_close_012345",
        "meeting_ref": binding.meeting_ref,
        "room_ref": binding.room_ref,
        "state": "ending",
        "state_version": 2,
        "requested_at": int(time.time()),
    }

    with (
        mock.patch(
            "core.mastrao_idle_close.RoomManagement.room_sid",
            return_value=None,
        ),
        mock.patch(
            "core.mastrao_idle_close.sign_idle_meeting_close",
            return_value=("aaa.bbb.ccc", {}),
        ),
        mock.patch(
            "core.mastrao_idle_close.post_core_json", return_value=response
        ) as post,
    ):
        deliver_idle_close(candidate.pk)

    candidate.refresh_from_db()
    assert candidate.state == models.MastraoIdleCloseCandidate.State.DELIVERED
    assert candidate.delivered_at is not None
    assert post.call_args.kwargs["body"] == {"idle_close_assertion": "aaa.bbb.ccc"}


@pytest.mark.django_db(transaction=True)
def test_new_provider_generation_is_deleted_while_canonical_close_continues(settings):
    """A post-timeout generation cannot cancel canonical closure."""

    settings.CELERY_ENABLED = False
    binding = _binding()
    candidate = _observed_idle_close(binding)
    assert candidate is not None
    response = {
        "version": 1,
        "matter_ref": "matter_idle_close_012345",
        "meeting_ref": binding.meeting_ref,
        "room_ref": binding.room_ref,
        "state": "ending",
        "state_version": 2,
        "requested_at": int(time.time()),
    }

    with (
        mock.patch(
            "core.mastrao_idle_close.RoomManagement.room_sid",
            return_value="RM_recreated_after_timeout",
        ),
        mock.patch("core.mastrao_idle_close.RoomManagement.delete_room") as delete,
        mock.patch(
            "core.mastrao_idle_close.sign_idle_meeting_close",
            return_value=("aaa.bbb.ccc", {}),
        ),
        mock.patch(
            "core.mastrao_idle_close.post_core_json", return_value=response
        ) as post,
    ):
        deliver_idle_close(candidate.pk)

    candidate.refresh_from_db()
    assert candidate.state == models.MastraoIdleCloseCandidate.State.DELIVERED
    delete.assert_called_once_with(str(binding.room_id))
    post.assert_called_once()


@pytest.mark.django_db(transaction=True)
def test_same_provider_generation_retries_before_canonical_close(settings):
    """Provider list lag cannot close a room generation still reported alive."""

    settings.CELERY_ENABLED = False
    binding = _binding()
    candidate = _observed_idle_close(binding)
    assert candidate is not None

    with (
        mock.patch(
            "core.mastrao_idle_close.RoomManagement.room_sid",
            return_value=candidate.room_sid,
        ),
        mock.patch("core.mastrao_idle_close.post_core_json") as post,
        pytest.raises(IdleCloseRetryable),
    ):
        deliver_idle_close(candidate.pk)

    candidate.refresh_from_db()
    assert candidate.state == models.MastraoIdleCloseCandidate.State.PENDING
    post.assert_not_called()


@pytest.mark.django_db(transaction=True)
def test_worker_redelivery_resumes_a_claim_after_process_loss(settings):
    """An acknowledged-late task can resume a durable delivering claim."""

    settings.CELERY_ENABLED = False
    binding = _binding()
    candidate = _observed_idle_close(binding)
    assert candidate is not None
    candidate.state = models.MastraoIdleCloseCandidate.State.DELIVERING
    candidate.save(update_fields=["state", "updated_at"])
    response = {
        "version": 1,
        "matter_ref": "matter_idle_close_012345",
        "meeting_ref": binding.meeting_ref,
        "room_ref": binding.room_ref,
        "state": "ending",
        "state_version": 2,
        "requested_at": int(time.time()),
    }

    with (
        mock.patch("core.mastrao_idle_close.RoomManagement.room_sid") as room_sid,
        mock.patch(
            "core.mastrao_idle_close.sign_idle_meeting_close",
            return_value=("aaa.bbb.ccc", {}),
        ),
        mock.patch("core.mastrao_idle_close.post_core_json", return_value=response),
    ):
        deliver_idle_close(candidate.pk)

    room_sid.assert_not_called()
    candidate.refresh_from_db()
    assert candidate.state == models.MastraoIdleCloseCandidate.State.DELIVERED


@pytest.mark.django_db(transaction=True)
def test_worker_start_recovers_persisted_idle_close_claims(settings):
    """Worker startup re-enqueues claims persisted before broker delivery."""

    settings.CELERY_ENABLED = False
    binding = _binding()
    candidate = _observed_idle_close(binding)
    assert candidate is not None

    with mock.patch("core.tasks.idle_close.process_idle_close.delay") as enqueue:
        recover_idle_close_tasks()

    enqueue.assert_called_once_with(str(candidate.pk))
