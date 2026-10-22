"""Signed room closure is durable, scoped and monotonic; not media coverage."""

# Imported fixtures exercise the actual signature verifier and PostgreSQL models.
# pylint: disable=missing-function-docstring,redefined-outer-name,unused-import

import copy
import hashlib
import itertools
import json
from unittest import mock
from uuid import uuid4

from django.core.exceptions import ValidationError
from django.db import DatabaseError, connection

import psycopg
import pytest

from core import models
from core.factories import RoomFactory, UserFactory
from core.tests.test_mastrao_rtc_observations import (
    _post,
    binding,
    event,
    isolated_settings,
)

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def local_room_cleanup(settings):
    settings.ROOM_TELEPHONY_ENABLED = False
    settings.ROOMKIT_ENABLED = False
    # LocmemCache has no iter_keys; cache cleanup is outside RTC persistence.
    with mock.patch("core.services.livekit_events.LobbyService.clear_room_cache"):
        yield


def _finished(event):
    return {
        "id": str(uuid4()),
        "event": "room_finished",
        "createdAt": event["createdAt"],
        "room": copy.deepcopy(event["room"]),
    }


def _published(event):
    result = copy.deepcopy(event)
    result.update(id=str(uuid4()), event="track_published")
    result["track"] = {
        "sid": "TR_audio",
        "type": "AUDIO",
        "source": "MICROPHONE",
    }
    return result


@pytest.mark.django_db(transaction=True)
def test_room_only_closure_commits_before_ack_without_fabricating_connection(
    client, settings, event
):
    finished = _finished(event)
    with mock.patch("core.mastrao_rtc_observations.wake_native_admissions") as wake:
        response = _post(client, settings, finished)
        assert response.status_code == 200, response.content
    wake.assert_not_called()
    row = models.MastraoRtcObservation.objects.get(event_id=finished["id"])
    assert row.event_type == "room_finished"
    assert row.room_sid == "RM_fixture"
    assert row.participant_sid == row.rtc_identity == row.track_sid == ""
    assert row.token_binding_ref is row.track_type is row.track_source is None
    assert not models.MastraoRtcConnection.objects.exists()
    assert not models.MastraoRtcTrackEpoch.objects.exists()
    digest = hashlib.sha256(json.dumps(finished).encode()).hexdigest()
    with psycopg.connect(**connection.get_connection_params()) as independent:
        with independent.cursor() as cursor:
            cursor.execute(
                "SELECT payload_digest, participant_sid, rtc_identity "
                "FROM meet_mastrao_rtc_observation WHERE event_id = %s",
                [finished["id"]],
            )
            assert cursor.fetchone() == (digest, "", "")


@pytest.mark.parametrize("fault", ["unsigned", "body_changed"])
def test_room_closure_requires_signature_and_exact_body(client, settings, event, fault):
    finished = _finished(event)
    kwargs = {"authenticated": False}
    expected_status = 401
    if fault == "body_changed":
        kwargs = {"body": json.dumps({**finished, "id": str(uuid4())})}
        expected_status = 400
    assert _post(client, settings, finished, **kwargs).status_code == expected_status
    assert not models.MastraoRtcObservation.objects.exists()


@pytest.mark.parametrize("field", ["id", "createdAt", "room_sid"])
def test_incomplete_room_closure_is_refused(client, settings, event, field):
    finished = _finished(event)
    if field == "room_sid":
        finished["room"].pop("sid")
    else:
        finished.pop(field)
    assert _post(client, settings, finished).status_code == 400
    assert not models.MastraoRtcObservation.objects.exists()


@pytest.mark.parametrize("field", ["participant_sid", "rtc_identity"])
def test_participant_events_still_require_participant_fields(
    client, settings, event, field
):
    assert _post(client, settings, event).status_code == 200
    row = models.MastraoRtcObservation.objects.get()
    original = getattr(row, field)
    setattr(row, field, "")
    with pytest.raises(ValidationError) as error:
        row.save()
    assert field in error.value.message_dict
    row.refresh_from_db()
    assert getattr(row, field) == original


def test_room_closure_does_not_retain_incidental_participant_data(
    client, settings, event
):
    finished = _finished(event)
    finished["participant"] = event["participant"]
    assert _post(client, settings, finished).status_code == 200
    row = models.MastraoRtcObservation.objects.get()
    assert row.participant_sid == row.rtc_identity == ""
    assert row.token_binding_ref is None
    assert not models.MastraoRtcConnection.objects.exists()


def test_exact_duplicate_closure_preserves_first_fact(client, settings, event):
    finished = _finished(event)
    assert _post(client, settings, finished).status_code == 200
    first = models.MastraoRtcObservation.objects.get()
    assert _post(client, settings, finished).status_code == 200
    second = models.MastraoRtcObservation.objects.get()
    assert (second.pk, second.updated_at, second.payload_digest) == (
        first.pk,
        first.updated_at,
        first.payload_digest,
    )


def test_conflicting_event_id_cannot_close_an_open_epoch(client, settings, event):
    publication = _published(event)
    assert _post(client, settings, publication).status_code == 200
    finished = _finished(event)
    finished["id"] = publication["id"]
    assert _post(client, settings, finished).status_code == 409
    assert models.MastraoRtcObservation.objects.get().event_type == "track_published"
    assert not models.MastraoRtcConnection.objects.get().ended
    assert not models.MastraoRtcTrackEpoch.objects.get().ended


@pytest.mark.parametrize(
    "order", list(itertools.permutations(["join", "publish", "finish"]))
)
def test_late_facts_cannot_reopen_a_finished_room(client, settings, event, order):
    facts = {"join": event, "publish": _published(event), "finish": _finished(event)}
    closed = False
    for kind in order:
        assert _post(client, settings, facts[kind]).status_code == 200
        closed = closed or kind == "finish"
        if closed:
            assert not models.MastraoRtcConnection.objects.filter(ended=False).exists()
            assert not models.MastraoRtcTrackEpoch.objects.filter(ended=False).exists()
    assert models.MastraoRtcObservation.objects.count() == 3
    assert models.MastraoRtcConnection.objects.get().ended
    assert models.MastraoRtcTrackEpoch.objects.get().ended


def test_old_sid_closes_only_its_own_incarnation_and_retains_late_publications(
    client, settings, event
):
    old = _published(event)
    current = _published(event)
    current["room"]["sid"] = "RM_current"
    for fact in (old, current, _finished(event)):
        assert _post(client, settings, fact).status_code == 200
    late = _published(event)
    late["participant"]["sid"] = "PA_late"
    late["track"]["sid"] = "TR_late"
    assert _post(client, settings, late).status_code == 200
    assert (
        models.MastraoRtcConnection.objects.filter(room_sid="RM_fixture").count() == 2
    )
    assert not models.MastraoRtcConnection.objects.filter(
        room_sid="RM_fixture", ended=False
    ).exists()
    assert not models.MastraoRtcTrackEpoch.objects.filter(
        connection__room_sid="RM_fixture", ended=False
    ).exists()
    assert not models.MastraoRtcConnection.objects.get(room_sid="RM_current").ended
    assert not models.MastraoRtcTrackEpoch.objects.get(
        connection__room_sid="RM_current"
    ).ended


def test_same_sid_in_another_binding_is_not_closed(client, settings, event):
    other = models.MastraoRoomBinding.objects.create(
        effect_key="other_room_closure_fixture",
        arguments_digest="a" * 64,
        meeting_ref="other_meeting_closure_fixture",
        room_ref="other_room_closure_fixture",
        owner_ref="other_owner_closure_fixture",
        provider_binding_digest="b" * 64,
        room=RoomFactory(),
        owner=UserFactory(),
    )
    publication = _published(event)
    publication["room"]["name"] = str(other.room_id)
    assert _post(client, settings, publication).status_code == 200
    assert _post(client, settings, _finished(event)).status_code == 200
    assert not models.MastraoRtcConnection.objects.get(room_binding=other).ended
    assert not models.MastraoRtcTrackEpoch.objects.get().ended


def test_projection_failure_rolls_back_terminal_fact_before_ack(
    client, settings, event
):
    with mock.patch(
        "core.mastrao_rtc_observations.correlate_verified_observation",
        side_effect=DatabaseError("synthetic terminal projection failure"),
    ):
        with pytest.raises(
            DatabaseError, match="synthetic terminal projection failure"
        ):
            _post(client, settings, _finished(event))
    assert not models.MastraoRtcObservation.objects.exists()
    assert not models.MastraoRtcConnection.objects.exists()
