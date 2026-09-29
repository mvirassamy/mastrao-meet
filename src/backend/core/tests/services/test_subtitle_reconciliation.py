"""Tests for the durable subtitle agent reconciler."""
# pylint: disable=redefined-outer-name

import asyncio
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from unittest import mock

from django.core.management import call_command
from django.db import close_old_connections, transaction

import pytest
from livekit import api

from core.factories import RoomFactory
from core.models import RoomSubtitleControl
from core.services.subtitle_control import ensure_subtitle_control
from core.services.subtitle_reconciliation import (
    SUBTITLE_STATUS_TOPIC,
    SubtitleConvergenceBusy,
    SubtitleReconciliationAmbiguous,
    _ProviderResult,
    observe_subtitle_agent,
    publish_subtitle_snapshot,
    reconcile_subtitle_control,
    request_subtitle_stop,
)

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(autouse=True)
def enable_subtitles(settings):
    """Keep the reconciler tests on the non-kill-switch path."""
    settings.ROOM_SUBTITLE_ENABLED = True


def _dispatch(
    dispatch_id,
    *,
    room_sid=None,
    generation=None,
    provider="legacy",
    agent_name="multi-user-transcriber",
):
    metadata = None
    if room_sid is not None and generation is not None:
        metadata = json.dumps(
            {
                "agentName": agent_name,
                "generation": generation,
                "provider": provider,
                "roomSid": room_sid,
            }
        )
    return mock.Mock(id=dispatch_id, agent_name=agent_name, metadata=metadata)


@pytest.fixture
def mock_livekit_client():
    """Provide a fake LiveKit client with explicit async provider methods."""
    with mock.patch("core.utils.create_livekit_client") as create_client:
        client = mock.AsyncMock()
        client.agent_dispatch.list_dispatch = mock.AsyncMock(return_value=[])

        async def create_dispatch(request):
            # LiveKit lists a created dispatch with the exact metadata it got.
            created = mock.Mock(
                id="AD_created",
                agent_name=request.agent_name,
                metadata=request.metadata,
            )
            client.agent_dispatch.list_dispatch.return_value = [
                *client.agent_dispatch.list_dispatch.return_value,
                created,
            ]
            return created

        client.agent_dispatch.create_dispatch = mock.AsyncMock(
            side_effect=create_dispatch
        )
        client.agent_dispatch.delete_dispatch = mock.AsyncMock()
        client.room.send_data = mock.AsyncMock()
        create_client.return_value = client
        yield client


def _turn_on(control):
    control.desired_state = RoomSubtitleControl.DesiredState.ON
    control.public_state = RoomSubtitleControl.PublicState.STARTING
    control.save(update_fields=["desired_state", "public_state", "updated_at"])
    return control


def test_agent_join_and_leave_update_legacy_presence_and_identity():
    """Participant observations update the durable legacy readiness signal."""
    room = RoomFactory()
    control = _turn_on(ensure_subtitle_control(room, room_sid="RM_agent_presence"))

    joined = observe_subtitle_agent(
        control.room_sid,
        participant_identity="agent-identity-a",
        present=True,
    )
    assert joined.agent_present is True
    assert joined.session_id == "agent-identity-a"
    assert joined.public_state == RoomSubtitleControl.PublicState.LIVE

    left = observe_subtitle_agent(
        control.room_sid,
        participant_identity="agent-identity-a",
        present=False,
    )
    assert left.agent_present is False
    assert left.session_id is None
    assert left.public_state == RoomSubtitleControl.PublicState.RECONNECTING


def test_late_leave_from_another_agent_does_not_clear_current_identity():
    """A stale participant-left event cannot clear the current agent session."""
    room = RoomFactory()
    control = _turn_on(ensure_subtitle_control(room, room_sid="RM_identity_guard"))
    observe_subtitle_agent(
        control.room_sid,
        participant_identity="current-agent",
        present=True,
    )

    observed = observe_subtitle_agent(
        control.room_sid,
        participant_identity="old-agent",
        present=False,
    )

    assert observed.agent_present is True
    assert observed.session_id == "current-agent"


def test_reconcile_dispatches_and_persists_observation(  # pylint: disable=unused-argument
    mock_livekit_client,
):
    """A desired ON row creates one dispatch and records its provider ID."""
    room = RoomFactory()
    control = _turn_on(ensure_subtitle_control(room, room_sid="RM_reconcile"))

    result = reconcile_subtitle_control(control.room_sid)

    assert result.observed_dispatch_ids == ["AD_created"]
    assert result.public_state == RoomSubtitleControl.PublicState.STARTING
    mock_livekit_client.agent_dispatch.create_dispatch.assert_awaited_once()


def test_convergence_adopts_legacy_dispatch_without_creating_another(
    mock_livekit_client,
):
    """Convergence adopts one unannotated legacy dispatch."""

    room = RoomFactory()
    control = _turn_on(ensure_subtitle_control(room, room_sid="RM_legacy_adopt"))
    legacy = _dispatch("AD_legacy")
    mock_livekit_client.agent_dispatch.list_dispatch.return_value = [legacy]

    result = reconcile_subtitle_control(control.room_sid)

    assert result.observed_dispatch_ids == ["AD_legacy"]
    mock_livekit_client.agent_dispatch.create_dispatch.assert_not_called()


def test_convergence_deduplicates_exact_dispatch_metadata(
    mock_livekit_client,
):
    """The deterministic winner remains after duplicate cleanup and relist."""

    room = RoomFactory()
    control = _turn_on(ensure_subtitle_control(room, room_sid="RM_deduplicate"))
    first = _dispatch(
        "AD_0",
        room_sid=control.room_sid,
        generation=control.control_generation,
    )
    duplicate = _dispatch(
        "AD_1",
        room_sid=control.room_sid,
        generation=control.control_generation,
    )
    mock_livekit_client.agent_dispatch.list_dispatch.side_effect = [
        [first, duplicate],
        [first],
    ]

    result = reconcile_subtitle_control(control.room_sid)

    assert result.observed_dispatch_ids == ["AD_0"]
    mock_livekit_client.agent_dispatch.delete_dispatch.assert_called_once_with(
        dispatch_id="AD_1",
        room_name=str(room.id),
    )


def test_sync_convergence_waits_boundedly_for_a_busy_room_lock(settings):
    """A synchronous caller retries contention within its explicit budget."""

    settings.CELERY_ENABLED = False
    room = RoomFactory()
    control = _turn_on(ensure_subtitle_control(room, room_sid="RM_busy_retry"))
    provider_result = _ProviderResult(["AD_busy_retry"])
    with (
        mock.patch(
            "core.services.subtitle_reconciliation._provider_reconcile",
            side_effect=[SubtitleConvergenceBusy(control.room_sid), provider_result],
        ) as provider_reconcile,
        mock.patch("core.services.subtitle_reconciliation.time.sleep") as sleep,
    ):
        result = reconcile_subtitle_control(
            control.room_sid,
            deadline=time.monotonic() + 0.2,
        )

    assert result.observed_dispatch_ids == ["AD_busy_retry"]
    assert provider_reconcile.call_count == 2
    assert sleep.call_count == 1
    assert 0 < sleep.call_args.args[0] <= 0.05


def test_reconcile_uses_list_delete_list_for_duplicate_dispatches(
    mock_livekit_client,
):
    """Cleanup deletes extras, then verifies provider state with a second list."""
    room = RoomFactory()
    control = _turn_on(ensure_subtitle_control(room, room_sid="RM_duplicate"))
    mock_livekit_client.agent_dispatch.list_dispatch.side_effect = [
        [
            _dispatch("AD_keep", room_sid="RM_duplicate", generation=0),
            _dispatch("AD_delete", room_sid="RM_duplicate", generation=-1),
        ],
        [_dispatch("AD_keep", room_sid="RM_duplicate", generation=0)],
    ]

    result = reconcile_subtitle_control(control.room_sid)

    assert result.observed_dispatch_ids == ["AD_keep"]
    mock_livekit_client.agent_dispatch.delete_dispatch.assert_awaited_once_with(
        dispatch_id="AD_delete", room_name=str(room.id)
    )
    assert mock_livekit_client.agent_dispatch.list_dispatch.await_count == 2


def test_cleanup_timeout_is_ambiguous_and_keeps_retry_budget(mock_livekit_client):
    """A cleanup timeout never claims that the provider is clean."""
    room = RoomFactory()
    control = _turn_on(ensure_subtitle_control(room, room_sid="RM_timeout"))
    control = request_subtitle_stop(room, room_sid=control.room_sid)
    mock_livekit_client.agent_dispatch.list_dispatch.side_effect = [
        [_dispatch("AD_timeout")],
    ]

    async def timeout(*args, **kwargs):
        raise TimeoutError("cleanup timed out")

    mock_livekit_client.agent_dispatch.delete_dispatch.side_effect = timeout

    with pytest.raises(SubtitleReconciliationAmbiguous):
        reconcile_subtitle_control(control.room_sid)

    control.refresh_from_db()
    assert control.public_state == RoomSubtitleControl.PublicState.STOPPING
    assert control.attempts == 1
    assert control.next_retry_at is not None


def test_desired_off_deletes_all_generations_and_legacy_dispatches(
    mock_livekit_client,
):
    """OFF removes every subtitle generation but never another agent."""

    room = RoomFactory()
    control = _turn_on(ensure_subtitle_control(room, room_sid="RM_all_generations"))
    control.pending_since = control.updated_at
    control.save(update_fields=["pending_since", "updated_at"])
    request_subtitle_stop(room, room_sid=control.room_sid)
    mock_livekit_client.agent_dispatch.list_dispatch.side_effect = [
        [
            _dispatch("AD_current", room_sid=control.room_sid, generation=2),
            _dispatch("AD_old", room_sid=control.room_sid, generation=1),
            _dispatch("AD_legacy"),
            _dispatch("AD_other", agent_name="other-agent"),
        ],
        [],
    ]

    result = reconcile_subtitle_control(control.room_sid)

    assert result.public_state == RoomSubtitleControl.PublicState.STOPPED
    assert [
        call.kwargs["dispatch_id"]
        for call in mock_livekit_client.agent_dispatch.delete_dispatch.call_args_list
    ] == [
        "AD_current",
        "AD_old",
        "AD_legacy",
    ]
    assert mock_livekit_client.agent_dispatch.list_dispatch.await_count == 2


def test_create_timeout_but_dispatch_created_then_stop_deletes_it(
    mock_livekit_client,
):
    """A provider create timeout with a late dispatch remains safely stoppable."""

    room = RoomFactory()
    control = _turn_on(ensure_subtitle_control(room, room_sid="RM_timeout_orphan"))
    control.pending_since = control.updated_at
    control.save(update_fields=["pending_since", "updated_at"])
    dispatch = _dispatch(
        "AD_orphan",
        room_sid=control.room_sid,
        generation=control.control_generation,
    )
    provider_dispatches = []

    async def create_with_late_dispatch(*args, **kwargs):
        provider_dispatches.append(dispatch)
        raise asyncio.TimeoutError("response lost after create")

    mock_livekit_client.agent_dispatch.create_dispatch.side_effect = (
        create_with_late_dispatch
    )
    mock_livekit_client.agent_dispatch.list_dispatch.side_effect = [
        [],
        provider_dispatches,
        [],
    ]

    with pytest.raises(SubtitleReconciliationAmbiguous):
        reconcile_subtitle_control(control.room_sid)
    control.refresh_from_db()
    assert control.public_state == RoomSubtitleControl.PublicState.STARTING

    request_subtitle_stop(room, room_sid=control.room_sid)
    result = reconcile_subtitle_control(control.room_sid)

    assert result.public_state == RoomSubtitleControl.PublicState.STOPPED
    mock_livekit_client.agent_dispatch.delete_dispatch.assert_awaited_once_with(
        dispatch_id="AD_orphan", room_name=str(room.id)
    )


def test_snapshot_packet_is_reliable_and_runs_after_commit(
    mock_livekit_client, django_capture_on_commit_callbacks
):
    """The status packet is emitted only after the committed snapshot exists."""
    room = RoomFactory()
    control = _turn_on(ensure_subtitle_control(room, room_sid="RM_packet"))
    callback_seen = []

    def capture_send(*args, **kwargs):
        callback_seen.append((args, kwargs))

    mock_livekit_client.room.send_data.side_effect = capture_send

    with django_capture_on_commit_callbacks(execute=True):
        with transaction.atomic():
            observe_subtitle_agent(
                control.room_sid,
                participant_identity="agent-packet",
                present=True,
            )
            assert not callback_seen

    assert callback_seen
    request = callback_seen[0][0][0]
    assert request.kind == 0
    assert request.topic == SUBTITLE_STATUS_TOPIC
    assert request.room == str(room.id)
    payload = json.loads(request.data)
    assert payload["roomSid"] == control.room_sid
    assert payload["state"] == RoomSubtitleControl.PublicState.LIVE
    assert payload["stateVersion"] == 1
    assert payload["eventId"] == f"subtitle-state-{control.room_sid}-1"
    assert payload["occurredAt"]
    assert RoomSubtitleControl.objects.get(pk=control.pk).state_version == 1


def test_create_timeout_remains_starting_and_retryable(mock_livekit_client):
    """A timed-out create never projects degraded or unavailable prematurely."""

    room = RoomFactory()
    control = _turn_on(ensure_subtitle_control(room, room_sid="RM_create_timeout"))

    async def timeout(*args, **kwargs):
        raise asyncio.TimeoutError("create timed out")

    mock_livekit_client.agent_dispatch.create_dispatch.side_effect = timeout

    with pytest.raises(SubtitleReconciliationAmbiguous):
        reconcile_subtitle_control(control.room_sid)

    control.refresh_from_db()
    assert control.public_state == RoomSubtitleControl.PublicState.STARTING
    assert control.public_state != RoomSubtitleControl.PublicState.DEGRADED
    assert control.next_retry_at is not None


def test_failed_packet_publication_schedules_a_bounded_retry(
    mock_livekit_client,
):
    """A packet failure schedules a bounded retry of the latest committed state."""

    room = RoomFactory()
    _turn_on(ensure_subtitle_control(room, room_sid="RM_packet_retry"))
    mock_livekit_client.room.send_data.side_effect = RuntimeError("network down")

    with mock.patch(
        "core.services.subtitle_reconciliation._schedule_snapshot_retry"
    ) as schedule_retry:
        publish_subtitle_snapshot(room.id)

    schedule_retry.assert_called_once_with(room.id, 1)


def test_stop_during_start_reconciles_the_new_off_intent():
    """A stop arriving during provider work fences the stale create result."""

    room = RoomFactory()
    control = _turn_on(ensure_subtitle_control(room, room_sid="RM_stop_during_start"))
    control.observed_dispatch_ids = ["AD_pending"]
    control.save(update_fields=["observed_dispatch_ids", "updated_at"])

    calls = []

    def provider_call(_room_name, room_sid, desired_state, _generation, _provider):
        calls.append(desired_state)
        if len(calls) == 1:
            request_subtitle_stop(room, room_sid=room_sid)
            return _ProviderResult(["AD_orphan"])
        return _ProviderResult([])

    with mock.patch(
        "core.services.subtitle_reconciliation._provider_reconcile",
        side_effect=provider_call,
    ):
        result = reconcile_subtitle_control(control.room_sid)

    assert calls == [
        RoomSubtitleControl.DesiredState.ON,
        RoomSubtitleControl.DesiredState.OFF,
    ]
    assert result.desired_state == RoomSubtitleControl.DesiredState.OFF
    assert result.public_state == RoomSubtitleControl.PublicState.STOPPED


@pytest.mark.django_db(transaction=True)
def test_stop_during_start_across_two_connections(mock_livekit_client, settings):
    """A public stop on another PostgreSQL connection fences a blocked start."""

    settings.CELERY_ENABLED = False
    room = RoomFactory()
    control = _turn_on(ensure_subtitle_control(room, room_sid="RM_two_connections"))
    control.pending_since = control.updated_at
    control.save(update_fields=["pending_since", "updated_at"])
    provider_dispatches = []
    entered = threading.Event()
    release = threading.Event()

    async def list_dispatches(*args, **kwargs):
        return list(provider_dispatches)

    async def blocked_create(*args, **kwargs):
        entered.set()
        assert release.wait(timeout=5)
        dispatch = _dispatch(
            "AD_two_connection",
            room_sid=control.room_sid,
            generation=control.control_generation,
        )
        provider_dispatches.append(dispatch)
        return dispatch

    async def delete_dispatch(dispatch_id, **kwargs):
        provider_dispatches[:] = [
            dispatch for dispatch in provider_dispatches if dispatch.id != dispatch_id
        ]

    mock_livekit_client.agent_dispatch.list_dispatch.side_effect = list_dispatches
    mock_livekit_client.agent_dispatch.create_dispatch.side_effect = blocked_create
    mock_livekit_client.agent_dispatch.delete_dispatch.side_effect = delete_dispatch

    def run_start():
        close_old_connections()
        try:
            return reconcile_subtitle_control(control.room_sid)
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(run_start)
        assert entered.wait(timeout=5)
        stopped = request_subtitle_stop(room, room_sid=control.room_sid)
        assert stopped.public_state == RoomSubtitleControl.PublicState.STOPPING
        release.set()
        result = future.result(timeout=10)

    assert result.desired_state == RoomSubtitleControl.DesiredState.OFF
    assert result.public_state == RoomSubtitleControl.PublicState.STOPPED
    assert not provider_dispatches


def test_old_sid_observation_cannot_reactivate_the_current_sid(mock_livekit_client):
    """A late event for an old provider SID cannot change the current row."""
    room = RoomFactory()
    old = ensure_subtitle_control(room, room_sid="RM_old")
    current = _turn_on(ensure_subtitle_control(room, room_sid="RM_current"))

    observe_subtitle_agent(
        old.room_sid,
        participant_identity="stale-agent",
        present=True,
    )

    current.refresh_from_db()
    old.refresh_from_db()
    assert current.is_current is True
    assert current.agent_present is False
    assert current.public_state == RoomSubtitleControl.PublicState.STARTING
    assert old.is_current is False
    assert old.agent_present is False
    mock_livekit_client.agent_dispatch.create_dispatch.assert_not_called()


def test_room_finished_requests_stop_and_preserves_terminal_reason():
    """Room closure projects OFF and a terminal room-finished state."""
    room = RoomFactory()
    control = _turn_on(ensure_subtitle_control(room, room_sid="RM_finished"))

    stopped = request_subtitle_stop(
        room,
        room_sid=control.room_sid,
        reason_code=RoomSubtitleControl.ReasonCode.ROOM_FINISHED,
    )

    assert stopped.desired_state == RoomSubtitleControl.DesiredState.OFF
    assert stopped.public_state == RoomSubtitleControl.PublicState.STOPPING
    assert stopped.reason_code == RoomSubtitleControl.ReasonCode.ROOM_FINISHED


def test_room_finished_blocks_late_agent_presence():
    """A late join after room closure cannot reactivate subtitle state."""

    room = RoomFactory()
    control = _turn_on(ensure_subtitle_control(room, room_sid="RM_finished_late"))
    request_subtitle_stop(
        room,
        room_sid=control.room_sid,
        reason_code=RoomSubtitleControl.ReasonCode.ROOM_FINISHED,
    )

    observed = observe_subtitle_agent(
        control.room_sid,
        participant_identity="late-agent",
        present=True,
    )

    assert observed.desired_state == RoomSubtitleControl.DesiredState.OFF
    assert observed.public_state == RoomSubtitleControl.PublicState.STOPPING
    assert observed.agent_present is False


def test_room_finished_not_found_is_confirmed_stopped(mock_livekit_client):
    """A deleted LiveKit room confirms cleanup after room_finished."""

    room = RoomFactory()
    control = _turn_on(ensure_subtitle_control(room, room_sid="RM_finished_404"))
    request_subtitle_stop(
        room,
        room_sid=control.room_sid,
        reason_code=RoomSubtitleControl.ReasonCode.ROOM_FINISHED,
    )
    mock_livekit_client.agent_dispatch.list_dispatch.side_effect = api.TwirpError(
        msg="room not found",
        code="not_found",
        status=404,
    )

    result = reconcile_subtitle_control(control.room_sid)

    assert result.public_state == RoomSubtitleControl.PublicState.STOPPED
    assert result.desired_state == RoomSubtitleControl.DesiredState.OFF
    mock_livekit_client.agent_dispatch.delete_dispatch.assert_not_awaited()


def test_reconcile_retries_are_bounded_without_beat(mock_livekit_client, settings):
    """Provider failures persist a bounded retry and do not require Celery beat."""
    room = RoomFactory()
    control = _turn_on(ensure_subtitle_control(room, room_sid="RM_retries"))
    mock_livekit_client.agent_dispatch.list_dispatch.side_effect = TimeoutError(
        "provider timeout"
    )
    settings.CELERY_ENABLED = False

    with pytest.raises(SubtitleReconciliationAmbiguous):
        reconcile_subtitle_control(control.room_sid)

    control.refresh_from_db()
    assert control.attempts == 1
    assert control.next_retry_at is not None


def test_kill_switch_converges_an_on_intent_to_off(mock_livekit_client, settings):
    """The kill switch disables provider state without creating a dispatch."""
    room = RoomFactory()
    control = _turn_on(ensure_subtitle_control(room, room_sid="RM_kill_switch"))
    settings.ROOM_SUBTITLE_ENABLED = False

    result = reconcile_subtitle_control(control.room_sid)

    assert result.desired_state == RoomSubtitleControl.DesiredState.OFF
    assert result.public_state == RoomSubtitleControl.PublicState.INACTIVE
    assert result.reason_code == RoomSubtitleControl.ReasonCode.PROVIDER_UNAVAILABLE
    mock_livekit_client.agent_dispatch.create_dispatch.assert_not_called()


def test_kill_switch_command_filters_provider_and_room(  # pylint: disable=unused-argument
    mock_livekit_client, settings, capsys
):
    """The operational command only converges the selected provider and room."""
    settings.ROOM_SUBTITLE_ENABLED = True
    selected_room = RoomFactory()
    other_room = RoomFactory()
    selected = _turn_on(
        ensure_subtitle_control(selected_room, room_sid="RM_kill_selected")
    )
    selected.provider = "openai"
    selected.save(update_fields=["provider", "updated_at"])
    other = _turn_on(ensure_subtitle_control(other_room, room_sid="RM_kill_other"))
    other.provider = "mistral"
    other.save(update_fields=["provider", "updated_at"])

    call_command(
        "subtitles_kill_switch",
        provider="openai",
        room_sid=selected.room_sid,
    )

    selected.refresh_from_db()
    other.refresh_from_db()
    assert selected.desired_state == RoomSubtitleControl.DesiredState.OFF
    assert other.desired_state == RoomSubtitleControl.DesiredState.ON
    assert "converged=1 failed=0" in capsys.readouterr().out


def test_openai_kill_switch_does_not_touch_legacy_sessions(mock_livekit_client, capsys):
    """Provider-scoped shutdown leaves legacy subtitle sessions unchanged."""

    room = RoomFactory()
    control = _turn_on(ensure_subtitle_control(room, room_sid="RM_legacy"))
    control.provider = "legacy"
    control.save(update_fields=["provider", "updated_at"])

    call_command("subtitles_kill_switch", provider="openai")

    control.refresh_from_db()
    assert control.desired_state == RoomSubtitleControl.DesiredState.ON
    assert "converged=0 failed=0" in capsys.readouterr().out
    mock_livekit_client.agent_dispatch.list_dispatch.assert_not_awaited()
