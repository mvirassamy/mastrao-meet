"""Exact issuance + signed webhook route + persisted connection/epoch fixtures."""

# Imported pytest fixtures and generated LiveKit protobuf members are resolved dynamically.
# pylint: disable=missing-function-docstring,redefined-outer-name,unused-import

import copy
from uuid import uuid4

from django.contrib.auth.models import AnonymousUser
from django.db import DatabaseError, connection
from django.utils import timezone

import psycopg
import pytest

from core import models
from core.factories import RoomFactory
from core.mastrao_media_token_binding import generate_guest_media_config
from core.tests.test_mastrao_media_token_binding import (
    _claims,
    _host_config,
    binding,
    guest,
    host,
    isolated_binding_settings,
)
from core.tests.test_mastrao_rtc_observations import _post

pytestmark = pytest.mark.django_db


def _join(receipt, participant_sid="PA_host"):
    return {
        "id": str(uuid4()),
        "event": "participant_joined",
        "createdAt": str(int(receipt.issued_at.timestamp())),
        "room": {"sid": "RM_fixture", "name": str(receipt.room_binding.room_id)},
        "participant": {
            "sid": participant_sid,
            "identity": receipt.rtc_identity,
            "name": "Identical display name",
            "attributes": {"mastrao.media_token_binding_ref": str(receipt.pk)},
        },
    }


def _event(join, event_type="track_published", track_sid="TR_audio"):
    event = copy.deepcopy(join)
    event.update(id=str(uuid4()), event=event_type)
    event["participant"].pop("attributes")
    if event_type.startswith("track_"):
        event["track"] = {"sid": track_sid, "type": "AUDIO", "source": "MICROPHONE"}
    return event


@pytest.fixture
def receipt(host):
    claims = _claims(_host_config(host))
    return models.MastraoMediaTokenBinding.objects.get(
        pk=claims["attributes"]["mastrao.media_token_binding_ref"]
    )


def _assert_post(client, settings, event):
    assert _post(client, settings, event).status_code == 200


def test_host_and_admitted_guest_same_name_remain_distinct(
    client, settings, receipt, guest
):
    # Upstream Core guest authorization is a fixture here; no provider/network call.
    config = generate_guest_media_config(
        guest,
        "f" * 64,
        room_id=str(guest.room_binding.room_id),
        user=AnonymousUser(),
        username="Identical display name",
        participant_id=guest.guest_ref,
        expires_at=guest.expires_at,
    )
    guest_receipt = models.MastraoMediaTokenBinding.objects.get(
        pk=_claims(config)["attributes"]["mastrao.media_token_binding_ref"]
    )
    for issued, sid in [(receipt, "PA_host"), (guest_receipt, "PA_guest")]:
        join = _join(issued, sid)
        _assert_post(client, settings, join)
        _assert_post(client, settings, _event(join))
    rows = list(
        models.MastraoRtcTrackEpoch.objects.select_related(
            "connection__media_token_binding"
        )
    )
    assert len(rows) == 2
    assert {row.connection.media_token_binding_id for row in rows} == {
        receipt.id,
        guest_receipt.id,
    }
    assert all(row.connection.correlation == "correlated" for row in rows)
    assert all(not row.ended and not row.conflict for row in rows)


@pytest.mark.parametrize("mismatch", ["marker", "identity", "before", "expired"])
def test_join_requires_exact_receipt(client, settings, receipt, mismatch):
    join = _join(receipt)
    if mismatch == "marker":
        join["participant"]["attributes"]["mastrao.media_token_binding_ref"] = str(
            uuid4()
        )
    elif mismatch == "identity":
        join["participant"]["identity"] = "different_identity"
    elif mismatch == "before":
        join["createdAt"] = str(int(receipt.issued_at.timestamp()) - 1)
    else:
        join["createdAt"] = str(int(receipt.expires_at.timestamp()))
    _assert_post(client, settings, join)
    connection = models.MastraoRtcConnection.objects.get()
    assert connection.media_token_binding_id is None
    assert connection.correlation == "unmatched_issuance"


def test_join_does_not_use_delivery_time_as_token_expiry(client, settings, receipt):
    receipt.issued_at -= timezone.timedelta(hours=2)
    receipt.expires_at -= timezone.timedelta(hours=2)
    receipt.save()
    _assert_post(client, settings, _join(receipt))
    assert models.MastraoRtcConnection.objects.get().correlation == "correlated"


def test_same_marker_in_other_room_is_not_correlated(client, settings, receipt):
    other = models.MastraoRoomBinding.objects.create(
        effect_key="other_room_correlation_fixture",
        arguments_digest="a" * 64,
        meeting_ref="other_meeting",
        room_ref="other_room",
        owner_ref="other_owner",
        provider_binding_digest="b" * 64,
        room=RoomFactory(),
        owner=receipt.room_binding.owner,
    )
    join = _join(receipt)
    join["room"]["name"] = str(other.room_id)
    _assert_post(client, settings, join)
    candidate = models.MastraoRtcConnection.objects.get()
    assert candidate.room_binding_id == other.id
    assert candidate.media_token_binding_id is None
    assert candidate.correlation == "unmatched_issuance"


def test_same_sid_with_another_issuance_is_quarantined(client, settings, receipt, host):
    _assert_post(client, settings, _join(receipt))
    _host_config(host)
    other = models.MastraoMediaTokenBinding.objects.exclude(pk=receipt.pk).get()
    _assert_post(client, settings, _join(other))
    candidate = models.MastraoRtcConnection.objects.get()
    assert candidate.media_token_binding_id == receipt.pk
    assert candidate.correlation == "conflicting_join"


@pytest.mark.django_db(transaction=True)
def test_connection_and_epoch_are_committed_before_http_ack(client, settings, receipt):
    join = _join(receipt)
    _assert_post(client, settings, join)
    publication = _event(join)
    _assert_post(client, settings, publication)
    with psycopg.connect(**connection.get_connection_params()) as independent:
        with independent.cursor() as cursor:
            cursor.execute(
                "SELECT c.correlation, c.media_token_binding_id, e.ended, e.conflict "
                "FROM meet_mastrao_rtc_track_epoch e "
                "JOIN meet_mastrao_rtc_connection c ON c.id = e.connection_id "
                "JOIN meet_mastrao_rtc_observation o ON o.id = e.first_publication_id "
                "WHERE o.event_id = %s",
                [publication["id"]],
            )
            assert cursor.fetchone() == ("correlated", receipt.pk, False, False)


def test_publication_before_join_is_pending_then_correlated(client, settings, receipt):
    join = _join(receipt)
    publication = _event(join)
    _assert_post(client, settings, publication)
    epoch = models.MastraoRtcTrackEpoch.objects.get()
    assert epoch.connection.correlation == "missing_join"
    _assert_post(client, settings, join)
    epoch.refresh_from_db()
    assert epoch.connection.correlation == "correlated"
    assert epoch.connection.media_token_binding_id == receipt.pk


def test_exact_retry_preserves_epoch_identity(client, settings, receipt):
    join = _join(receipt)
    publication = _event(join)
    _assert_post(client, settings, join)
    _assert_post(client, settings, publication)
    epoch = models.MastraoRtcTrackEpoch.objects.get()
    _assert_post(client, settings, publication)
    assert models.MastraoRtcTrackEpoch.objects.get().id == epoch.id


def test_reused_token_quarantines_both_connections(client, settings, receipt):
    first, second = _join(receipt), _join(receipt, "PA_reused")
    _assert_post(client, settings, first)
    _assert_post(client, settings, second)
    assert list(
        models.MastraoRtcConnection.objects.values_list("correlation", flat=True)
    ) == ["token_reused", "token_reused"]
    _assert_post(client, settings, _event(first))
    assert not models.MastraoRtcConnection.objects.exclude(
        correlation="token_reused"
    ).exists()


def test_new_issuance_and_sid_create_distinct_epochs(client, settings, receipt, host):
    first = _join(receipt)
    _assert_post(client, settings, first)
    _assert_post(client, settings, _event(first))
    _assert_post(client, settings, _event(first, "participant_left"))
    _host_config(host)
    next_receipt = models.MastraoMediaTokenBinding.objects.exclude(pk=receipt.pk).get()
    second = _join(next_receipt, "PA_new")
    _assert_post(client, settings, second)
    _assert_post(client, settings, _event(second))
    assert models.MastraoRtcTrackEpoch.objects.count() == 2
    assert models.MastraoRtcTrackEpoch.objects.filter(ended=True).count() == 1
    assert (
        models.MastraoRtcConnection.objects.filter(correlation="correlated").count()
        == 2
    )


@pytest.mark.parametrize("terminal", ["participant_left", "track_unpublished"])
def test_terminal_before_publication_never_reopens(client, settings, receipt, terminal):
    join = _join(receipt)
    _assert_post(client, settings, _event(join, terminal))
    _assert_post(client, settings, _event(join))
    _assert_post(client, settings, join)
    assert models.MastraoRtcTrackEpoch.objects.get().ended is True


def test_track_identity_conflict_is_monotonic(client, settings, receipt):
    join = _join(receipt)
    _assert_post(client, settings, join)
    changed = _event(join)
    changed["participant"]["identity"] = "other_identity"
    _assert_post(client, settings, changed)
    _assert_post(client, settings, _event(join))
    assert models.MastraoRtcConnection.objects.get().correlation == "identity_conflict"


def test_conflicting_track_shape_cannot_be_reset(client, settings, receipt):
    join = _join(receipt)
    _assert_post(client, settings, join)
    _assert_post(client, settings, _event(join))
    changed = _event(join)
    changed["track"]["type"] = "VIDEO"
    _assert_post(client, settings, changed)
    _assert_post(client, settings, _event(join))
    assert models.MastraoRtcTrackEpoch.objects.get().conflict is True


def test_projection_failure_rolls_back_ingress(client, settings, receipt, monkeypatch):
    def fail(*_args, **_kwargs):
        raise DatabaseError("synthetic projection failure")

    monkeypatch.setattr(models.MastraoRtcConnection, "save", fail)
    with pytest.raises(DatabaseError, match="synthetic projection failure"):
        _post(client, settings, _join(receipt))
    assert not models.MastraoRtcObservation.objects.exists()
    assert not models.MastraoRtcConnection.objects.exists()
