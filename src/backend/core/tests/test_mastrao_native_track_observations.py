"""Real signed RTC intake, private Core transport and PostgreSQL delivery fencing."""

# ruff: noqa: PLR0913, PLR0917  # pytest injects independently scoped fixtures.

# Shared RTC fixtures and LiveKit's generated enum members are intentional.
# pylint: disable=missing-function-docstring,redefined-outer-name,unused-import,no-member
# pylint: disable=unused-argument
# pylint: disable=too-many-arguments,too-many-positional-arguments

import copy
import hashlib
import json
from datetime import timedelta
from types import SimpleNamespace
from unittest import mock
from uuid import uuid4

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone

import jwt
import psycopg
import pytest
import requests
import responses
from livekit import api

from core import models
from core.mastrao_native_track_observations import (
    MAX_BATCH,
    OBSERVATION_JOSE,
    OBSERVATION_PATH,
    OBSERVATION_TYPE,
    _claim_next,
    _finish,
    reconcile_native_track_observations,
)
from core.mastrao_recording_reconciler import reconcile_native_recordings
from core.tasks.native_capture import process_native_track_observations
from core.tests.test_mastrao_room_adapter import _jwk_pair
from core.tests.test_mastrao_rtc_observations import (
    _post,
    binding,
    event,
    isolated_settings,
)

pytestmark = pytest.mark.django_db(transaction=True)
CORE_ENDPOINT = "http://127.0.0.1:59999" + OBSERVATION_PATH
ENVELOPE_FIELDS = {
    "version",
    "type",
    "issuer",
    "audience",
    "issued_at",
    "expires_at",
    "jti",
}
FACT_FIELDS = {
    "organization_external_id",
    "meeting_ref",
    "room_ref",
    "provider_binding_digest",
    "observation_ref",
    "payload_digest",
    "event_id",
    "observed_at",
    "room_sid",
    "participant_sid",
    "track_sid",
}


@pytest.fixture(autouse=True)
def export_settings(settings):
    private, jwk = _jwk_pair()
    settings.MASTRAO_CORE_NATIVE_TRACK_OBSERVATION_ENDPOINT = CORE_ENDPOINT
    settings.MASTRAO_RECORDING_RECEIPT_PRIVATE_JWK = jwk["private"]
    settings.MASTRAO_RECORDING_RECEIPT_KEY_ID = "observation-fixture"
    settings.MASTRAO_RECORDING_RECEIPT_ISSUER = "meet-fixture"
    settings.MASTRAO_RECORDING_RECEIPT_AUDIENCE = "core-fixture"
    settings.MASTRAO_NATIVE_PREENTRY_ENABLED = False
    settings.MASTRAO_NATIVE_CAPTURE_START_ENABLED = False
    settings.MASTRAO_NATIVE_SOURCE_TRANSFER_ENABLED = False
    settings.MASTRAO_NATIVE_ASR_ENABLED = False
    settings.CELERY_ENABLED = False
    settings.ROOM_TELEPHONY_ENABLED = False
    settings.ROOMKIT_ENABLED = False
    with mock.patch("core.services.livekit_events.LobbyService.clear_room_cache"):
        yield private.public_key()


@pytest.fixture
def recording(binding):
    return models.MastraoRecordingBinding.objects.create(
        room_binding=binding,
        organization_external_id="organization_exact_fixture",
        meeting_ref=binding.meeting_ref,
        room_ref=binding.room_ref,
        recording_ref="recording_observation_fixture",
        provider_binding_digest=binding.provider_binding_digest,
        policy_ref="policy_fixture",
        notice_version="notice_fixture",
        notice_digest="c" * 64,
        retention_expires_at=timezone.now() + timedelta(days=1),
    )


@pytest.fixture
def core(export_settings):
    """Verify real Ed25519 signatures and exercise the shared strict HTTP client."""
    fixture = SimpleNamespace(
        calls=[], ledger={}, override=None, status=200, lose_next_ack=False
    )

    def respond(request):
        body = json.loads(request.body)
        assert set(body) == {"observation"}
        compact = body["observation"]
        assert jwt.get_unverified_header(compact) == {
            "alg": "EdDSA",
            "kid": "observation-fixture",
            "typ": OBSERVATION_JOSE,
        }
        claims = jwt.decode(compact, export_settings, algorithms=["EdDSA"])
        assert set(claims) == ENVELOPE_FIELDS | FACT_FIELDS
        assert claims["type"] == OBSERVATION_TYPE
        assert claims["issuer"] == "meet-fixture"
        assert claims["audience"] == "core-fixture"
        assert claims["expires_at"] - claims["issued_at"] == 30
        assert (
            claims["issued_at"]
            <= int(timezone.now().timestamp())
            < claims["expires_at"]
        )
        facts = {key: claims[key] for key in FACT_FIELDS}
        prior = fixture.ledger.setdefault(claims["observation_ref"], facts)
        assert prior == facts
        fixture.calls.append(claims)
        # A Core write may have committed even when its HTTP response is lost.
        if fixture.lose_next_ack:
            fixture.lose_next_ack = False
            raise requests.ConnectionError("synthetic lost acknowledgement")
        receipt = {
            "version": 1,
            "observation_ref": claims["observation_ref"],
            "state": "recorded",
        }
        if fixture.override is not None:
            receipt = fixture.override
        return fixture.status, {"Content-Type": "application/json"}, json.dumps(receipt)

    with responses.RequestsMock(assert_all_requests_are_fired=False) as transport:
        transport.add_callback(responses.POST, CORE_ENDPOINT, callback=respond)
        yield fixture


def _publication(event, **changes):
    published = copy.deepcopy(event)
    published.update(id=str(uuid4()), event="track_published", **changes)
    published["track"] = {"sid": "TR_audio", "type": "AUDIO", "source": "MICROPHONE"}
    return published


def _ingest(client, settings, event):
    response = _post(client, settings, event)
    assert response.status_code == 200, response.content
    return models.MastraoRtcObservation.objects.get(event_id=event["id"])


def _make_retry_due(observation):
    models.MastraoRtcObservation.objects.filter(pk=observation.pk).update(
        native_observation_next_at=timezone.now() - timedelta(seconds=1)
    )


def test_signed_fact_only_exact_binding_and_webhook_replay(
    client, settings, event, recording, core
):
    published = _publication(event)
    observation = _ingest(client, settings, published)
    original = models.MastraoRtcObservation.objects.values().get(pk=observation.pk)
    assert reconcile_native_track_observations() == 1
    assert reconcile_native_track_observations() == 0
    _ingest(client, settings, published)
    assert reconcile_native_track_observations() == 0
    assert len(core.calls) == len(core.ledger) == 1
    assert {key: core.calls[0][key] for key in FACT_FIELDS} == {
        "organization_external_id": recording.organization_external_id,
        "meeting_ref": recording.meeting_ref,
        "room_ref": recording.room_ref,
        "provider_binding_digest": recording.provider_binding_digest,
        "observation_ref": str(observation.pk),
        "payload_digest": hashlib.sha256(json.dumps(published).encode()).hexdigest(),
        "event_id": published["id"],
        "observed_at": int(published["createdAt"]),
        "room_sid": published["room"]["sid"],
        "participant_sid": published["participant"]["sid"],
        "track_sid": "TR_audio",
    }
    after = models.MastraoRtcObservation.objects.values().get(pk=observation.pk)
    for key in original:
        if not key.startswith("native_observation_"):
            assert after[key] == original[key]
    # Signed event-ID reuse cannot replace the fact already sent.
    published["participant"]["sid"] = "PA_other"
    assert _post(client, settings, published).status_code == 409
    assert (
        models.MastraoRtcObservation.objects.get().payload_digest
        == original["payload_digest"]
    )


def test_lost_ack_retries_same_fact_with_new_envelope_after_backoff(
    client, settings, event, recording, core
):
    observation = _ingest(client, settings, _publication(event))
    core.lose_next_ack = True
    assert reconcile_native_track_observations() == 0
    observation.refresh_from_db()
    assert observation.native_observation_acknowledged_at is None
    assert observation.native_observation_claim is None
    assert observation.native_observation_next_at > timezone.now()
    assert reconcile_native_track_observations() == 0
    assert len(core.calls) == 1
    _make_retry_due(observation)
    # Scheduler recovery does not depend on another event or a Celery wakeup.
    assert reconcile_native_recordings(limit=1) == 1
    assert len(core.calls) == 2
    assert len(core.ledger) == 1
    assert core.calls[0]["jti"] != core.calls[1]["jti"]
    observation.refresh_from_db()
    assert observation.native_observation_acknowledged_at is not None


@pytest.mark.parametrize(
    "receipt",
    [
        {},
        {"version": True, "state": "recorded"},
        {"version": 2, "state": "recorded"},
        {"version": 1, "state": "pending"},
        {"version": 1, "state": "recorded", "extra": False},
        {"version": 1, "state": "recorded", "observation_ref": str(uuid4())},
    ],
)
def test_no_ack_for_malformed_or_mismatched_receipt(
    client, settings, event, recording, core, receipt
):
    observation = _ingest(client, settings, _publication(event))
    core.override = {"observation_ref": str(observation.pk), **receipt}
    assert reconcile_native_track_observations() == 0
    observation.refresh_from_db()
    assert observation.native_observation_acknowledged_at is None
    assert observation.native_observation_next_at > timezone.now()


@pytest.mark.parametrize("status", [400, 403, 404, 409, 500])
def test_refusal_never_permanently_discards_observation(
    client, settings, event, recording, core, status
):
    observation = _ingest(client, settings, _publication(event))
    core.status = status
    assert reconcile_native_track_observations() == 0
    observation.refresh_from_db()
    assert observation.native_observation_acknowledged_at is None
    core.status = 200
    _make_retry_due(observation)
    assert reconcile_native_track_observations() == 1


@pytest.mark.parametrize(
    "field", ["meeting_ref", "room_ref", "provider_binding_digest"]
)
def test_inexact_organization_binding_stays_pending_until_exact(
    client, settings, event, recording, core, field
):
    observation = _ingest(client, settings, _publication(event))
    original = getattr(recording, field)
    wrong = "d" * 64 if field == "provider_binding_digest" else "wrong_binding"
    setattr(recording, field, wrong)
    recording.save(update_fields=[field])
    assert reconcile_native_track_observations() == 0
    assert not core.calls
    observation.refresh_from_db()
    assert observation.native_observation_acknowledged_at is None
    setattr(recording, field, original)
    recording.save(update_fields=[field])
    _make_retry_due(observation)
    assert reconcile_native_track_observations() == 1


def test_missing_organization_binding_recovers_without_new_rtc_event(
    client, settings, event, binding, core
):
    observation = _ingest(client, settings, _publication(event))
    assert reconcile_native_track_observations() == 0
    assert not core.calls
    # Even a closure's authoritative organization is not this contract's binding.
    models.MastraoRoomClosure.objects.create(
        room_binding=binding,
        organization_external_id="organization_must_not_use",
        meeting_ref=binding.meeting_ref,
        room_ref=binding.room_ref,
        provider_binding_digest=binding.provider_binding_digest,
        close_ref="close_fixture",
        effect_key="close_effect_fixture",
        arguments_digest="c" * 64,
        requested_at=timezone.now(),
    )
    _make_retry_due(observation)
    assert reconcile_native_track_observations() == 0
    assert not core.calls
    models.MastraoRecordingBinding.objects.create(
        room_binding=binding,
        organization_external_id="organization_exact_fixture",
        meeting_ref=binding.meeting_ref,
        room_ref=binding.room_ref,
        recording_ref="recording_late",
        provider_binding_digest=binding.provider_binding_digest,
        policy_ref="policy_fixture",
        notice_version="notice_fixture",
        notice_digest="c" * 64,
        retention_expires_at=timezone.now() + timedelta(days=1),
    )
    _make_retry_due(observation)
    assert reconcile_native_recordings(limit=1) == 1
    assert core.calls[0]["organization_external_id"] == "organization_exact_fixture"


def test_all_publications_include_ended_unmatched_conflicting_and_late_epochs(
    client, settings, event, recording, core
):
    first = _publication(event)
    _ingest(client, settings, first)
    # A second shape makes the epoch conflicting without erasing its audio fact.
    video = copy.deepcopy(first)
    video.update(id=str(uuid4()))
    video["track"]["type"] = "VIDEO"
    _ingest(client, settings, video)
    second = copy.deepcopy(first)
    second.update(id=str(uuid4()))
    second["track"]["sid"] = "TR_second"
    _ingest(client, settings, second)
    ended = copy.deepcopy(second)
    ended.update(id=str(uuid4()), event="track_unpublished")
    _ingest(client, settings, ended)
    finished = {
        "id": str(uuid4()),
        "event": "room_finished",
        "createdAt": event["createdAt"],
        "room": event["room"],
    }
    _ingest(client, settings, finished)
    late = copy.deepcopy(second)
    late.update(id=str(uuid4()))
    late["track"]["sid"] = "TR_late"
    _ingest(client, settings, late)
    # Separate verified publications in the same epoch must all be exported.
    replay_shape = copy.deepcopy(first)
    replay_shape.update(id=str(uuid4()))
    _ingest(client, settings, replay_shape)
    recording.state = models.MastraoRecordingBinding.State.FINALIZED
    recording.save()
    recording.room_binding.closing_at = timezone.now()
    recording.room_binding.save()
    epochs_before = list(models.MastraoRtcTrackEpoch.objects.order_by("pk").values())
    connections_before = list(models.MastraoRtcConnection.objects.values())
    assert all(epoch["ended"] for epoch in epochs_before)
    assert any(epoch["conflict"] for epoch in epochs_before)
    assert connections_before[0]["media_token_binding_id"] is None
    assert not models.MastraoHostGrant.objects.exists()
    assert not models.MastraoGuestGrant.objects.exists()
    with (
        mock.patch("core.mastrao_native_capture_adapter.native_request") as capture,
        mock.patch("core.mastrao_recording_adapter._start_provider") as video_start,
        mock.patch("core.mastrao_native_asr_worker.process_native_asr_source") as asr,
    ):
        assert reconcile_native_recordings(limit=20) == 4
    capture.assert_not_called()
    video_start.assert_not_called()
    asr.assert_not_called()
    assert {claim["event_id"] for claim in core.calls} == {
        first["id"],
        second["id"],
        late["id"],
        replay_shape["id"],
    }
    assert (
        list(models.MastraoRtcTrackEpoch.objects.order_by("pk").values())
        == epochs_before
    )
    assert list(models.MastraoRtcConnection.objects.values()) == connections_before
    assert not models.MastraoNativeCaptureStart.objects.exists()
    assert not models.MastraoRecordingEffect.objects.exists()
    recording.refresh_from_db()
    assert recording.state == models.MastraoRecordingBinding.State.FINALIZED


def test_crashed_worker_lease_expires_and_fences_both_old_results(
    client, settings, event, recording, core
):
    observation = _ingest(client, settings, _publication(event))
    first = _claim_next()
    assert _claim_next() is None
    expired_at = timezone.now() - timedelta(seconds=1)
    models.MastraoRtcObservation.objects.filter(pk=observation.pk).update(
        native_observation_claim_until=expired_at
    )
    assert _finish(first, True) == 0
    assert _finish(first, False) == 0
    second = _claim_next()
    assert second.native_observation_claim != first.native_observation_claim
    assert _finish(first, True) == 0
    assert _finish(first, False) == 0
    assert _finish(second, True) == 1
    assert _finish(first, False) == 0
    assert reconcile_native_track_observations() == 0
    assert not core.calls


def test_mixed_admission_state_does_not_filter_observed_denominator(
    client, settings, event, recording, core
):
    first = _publication(event)
    _ingest(client, settings, first)
    admitted_epoch = models.MastraoRtcTrackEpoch.objects.get()
    admitted_epoch.native_admission_capture_ref = uuid4()
    admitted_epoch.save()
    second = _publication(event)
    second["track"]["sid"] = "TR_unadmitted"
    _ingest(client, settings, second)
    terminal = copy.deepcopy(second)
    terminal.update(id=str(uuid4()), event="track_unpublished")
    _ingest(client, settings, terminal)
    assert reconcile_native_recordings(limit=2) == 2
    assert {claim["track_sid"] for claim in core.calls} == {"TR_audio", "TR_unadmitted"}
    admitted_epoch.refresh_from_db()
    assert admitted_epoch.native_admission_capture_ref is not None
    unadmitted = models.MastraoRtcTrackEpoch.objects.get(track_sid="TR_unadmitted")
    assert unadmitted.ended
    assert unadmitted.native_admission_capture_ref is None
    assert not models.MastraoNativeCaptureStart.objects.exists()


@pytest.mark.parametrize("field", ["room_sid", "participant_sid", "track_sid"])
def test_invalid_stored_sid_cannot_be_acknowledged_as_valid_metadata(
    client, settings, event, recording, core, field
):
    observation = _ingest(client, settings, _publication(event))
    models.MastraoRtcObservation.objects.filter(pk=observation.pk).update(
        **{field: "WRONG_opaque"}
    )
    assert reconcile_native_track_observations() == 0
    assert not core.calls
    observation.refresh_from_db()
    assert observation.native_observation_acknowledged_at is None


def test_skip_locked_claim_progresses_past_independently_locked_row(
    client, settings, event
):
    assert connection.vendor == "postgresql"
    first = _ingest(client, settings, _publication(event))
    second = _ingest(client, settings, _publication(event))
    independent = psycopg.connect(**connection.get_connection_params())
    try:
        independent.execute(
            "SELECT id FROM meet_mastrao_rtc_observation WHERE id = %s FOR UPDATE",
            [first.pk],
        )
        claimed = _claim_next()
        assert claimed.pk == second.pk
        assert _claim_next() is None
    finally:
        independent.rollback()
        independent.close()
    assert _claim_next().pk == first.pk


@pytest.mark.parametrize("limit,expected", [(-1, 0), (0, 0), (2, 2), (1000, MAX_BATCH)])
def test_batches_are_bounded_and_old_rows_remain_eligible(
    settings, binding, recording, core, limit, expected
):
    observations = [
        models.MastraoRtcObservation(
            room_binding=binding,
            event_id=f"EV_old_{index}",
            payload_digest="e" * 64,
            event_type="track_published",
            event_time_seconds=1,
            room_sid="RM_old",
            participant_sid="PA_old",
            track_sid=f"TR_{index}",
            track_type=api.TrackType.AUDIO,
            track_source=api.TrackSource.MICROPHONE,
        )
        for index in range(MAX_BATCH + 2)
    ]
    models.MastraoRtcObservation.objects.bulk_create(observations)
    assert reconcile_native_track_observations(limit=limit) == expected
    assert len(core.calls) == expected
    assert (
        models.MastraoRtcObservation.objects.filter(
            native_observation_acknowledged_at__isnull=True
        ).count()
        == MAX_BATCH + 2 - expected
    )


@pytest.mark.parametrize("switch", ["integration", "endpoint"])
def test_disabled_export_preserves_pending_for_later_recovery(
    client, settings, event, recording, core, switch
):
    observation = _ingest(client, settings, _publication(event))
    if switch == "integration":
        settings.MASTRAO_MEETING_INTEGRATION_CONFIGURED = False
    else:
        settings.MASTRAO_CORE_NATIVE_TRACK_OBSERVATION_ENDPOINT = ""
    assert reconcile_native_track_observations() == 0
    assert not core.calls
    settings.MASTRAO_MEETING_INTEGRATION_CONFIGURED = True
    settings.MASTRAO_CORE_NATIVE_TRACK_OBSERVATION_ENDPOINT = CORE_ENDPOINT
    assert process_native_track_observations(str(observation.pk)) == 1


@pytest.mark.parametrize("eager", [False, True])
def test_intake_never_runs_synchronous_export(
    client, settings, event, recording, eager
):
    settings.CELERY_ENABLED = eager
    settings.CELERY_TASK_ALWAYS_EAGER = eager
    with mock.patch("core.mastrao_native_track_observations.post_core_json") as post:
        observation = _ingest(client, settings, _publication(event))
    post.assert_not_called()
    observation.refresh_from_db()
    assert observation.native_observation_acknowledged_at is None


def test_wakeup_runs_after_commit_broker_loss_preserves_fact(
    client, settings, event, recording, core
):
    settings.CELERY_ENABLED = True
    settings.CELERY_TASK_ALWAYS_EAGER = False

    def unavailable(observation_id):
        assert not connection.in_atomic_block
        assert models.MastraoRtcObservation.objects.filter(pk=observation_id).exists()
        raise RuntimeError("synthetic unavailable broker")

    with mock.patch(
        "core.tasks.native_capture.process_native_track_observations.delay",
        side_effect=unavailable,
    ) as enqueue:
        _ingest(client, settings, _publication(event))
    enqueue.assert_called_once()
    assert not core.calls
    assert reconcile_native_recordings(limit=1) == 1


@pytest.mark.parametrize("seconds", [1, 9_007_199_254_740_991])
def test_event_clock_is_reported_fact_including_clock_skew(
    client, settings, event, recording, core, seconds
):
    published = _publication(event, createdAt=str(seconds))
    # The SID prefix is followed by up to 120 characters (123 total).
    published["room"]["sid"] = "RM_" + "r" * 120
    published["participant"]["sid"] = "PA_" + "p" * 120
    published["track"]["sid"] = "TR_" + "t" * 120
    _ingest(client, settings, published)
    assert reconcile_native_track_observations() == 1
    assert core.calls[0]["observed_at"] == seconds


@pytest.mark.parametrize(
    "field,value",
    [
        ("sid", "RM_" + "r" * 121),
        ("sid", "invalid/room"),
        ("sid", "PA_wrong"),
        ("createdAt", "0"),
        ("createdAt", "9007199254740992"),
    ],
)
def test_intake_rejects_facts_outside_export_contract(
    client, settings, event, field, value
):
    published = _publication(event)
    if field == "sid":
        published["room"]["sid"] = value
    else:
        published[field] = value
    assert _post(client, settings, published).status_code == 400
    assert not models.MastraoRtcObservation.objects.exists()


def test_migration_makes_existing_publication_pending_without_new_event(
    binding, recording, core
):
    """Upgrade an actual pre-0061 fact rather than simulating new-model defaults."""
    executor = MigrationExecutor(connection)
    latest = executor.loader.graph.leaf_nodes("core")
    previous = [("core", "0060_subtitle_draining_generation")]
    executor.migrate(previous)
    try:
        old_apps = executor.loader.project_state(previous).apps
        old_observations = old_apps.get_model("core", "MastraoRtcObservation")
        observation = old_observations.objects.create(
            room_binding_id=binding.pk,
            event_id="EV_before_migration",
            payload_digest="e" * 64,
            event_type="track_published",
            event_time_seconds=1,
            room_sid="RM_old",
            participant_sid="PA_old",
            track_sid="TR_old",
            track_type=0,
            track_source=2,
        )
    finally:
        MigrationExecutor(connection).migrate(latest)
    assert reconcile_native_recordings(limit=1) == 1
    assert core.calls[0]["observation_ref"] == str(observation.pk)
