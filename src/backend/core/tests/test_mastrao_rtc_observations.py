"""Signed webhook HTTP requests and real PostgreSQL durable read-back."""

import base64
import copy
import hashlib
import json
from unittest import mock
from uuid import uuid4

from django.db import DatabaseError, connection
from django.utils import timezone

import psycopg
import pytest
from livekit import api

from core import models
from core.factories import RoomFactory, UserFactory

pytestmark = pytest.mark.django_db
ENDPOINT = "/api/v1.0/rooms/webhooks-livekit/"


@pytest.fixture(autouse=True)
def isolated_settings(settings):
    settings.MASTRAO_MEDIA_TOKEN_BINDING_ENABLED = True
    settings.LIVEKIT_WEBHOOK_EVENTS_FILTER_REGEX = ""
    settings.LIVEKIT_CONFIGURATION = {
        "api_key": "test_api_key",
        "api_secret": "test_api_secret_padded_to_32bytes!",
        "url": "http://127.0.0.1:7880",
    }
    settings.CACHES = {
        "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}
    }


@pytest.fixture
def binding():
    return models.MastraoRoomBinding.objects.create(
        effect_key="room_rtc_fixture",
        arguments_digest="a" * 64,
        meeting_ref="meeting_rtc_fixture",
        room_ref="room_rtc_fixture",
        owner_ref="owner_rtc_fixture",
        provider_binding_digest="b" * 64,
        room=RoomFactory(name="room_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"),
        owner=UserFactory(),
    )


@pytest.fixture
def event(binding):
    return {
        "id": "EV_fixture",
        "event": "participant_joined",
        "createdAt": str(int(timezone.now().timestamp())),
        "room": {"name": str(binding.room_id), "sid": "RM_fixture"},
        "participant": {
            "sid": "PA_fixture",
            "identity": "guest_fixture",
            "name": "Private name must not be stored",
            "attributes": {"mastrao.media_token_binding_ref": str(uuid4())},
        },
    }


def _post(client, settings, event, *, body=None, authenticated=True):
    encoded = json.dumps(event)
    signer = api.AccessToken(
        settings.LIVEKIT_CONFIGURATION["api_key"],
        settings.LIVEKIT_CONFIGURATION["api_secret"],
    )
    signer.claims.sha256 = base64.b64encode(
        hashlib.sha256(encoded.encode()).digest()
    ).decode()
    headers = {"HTTP_AUTHORIZATION": signer.to_jwt()} if authenticated else {}
    return client.post(
        ENDPOINT, data=body or encoded, content_type="application/json", **headers
    )


@pytest.mark.parametrize(
    "event_type",
    ["participant_joined", "participant_left", "track_published", "track_unpublished"],
)
@pytest.mark.django_db(transaction=True)
def test_real_signature_and_body_hash_before_durable_ack(
    client, settings, event, event_type
):
    event["event"] = event_type
    if event_type.startswith("track_"):
        event["track"] = {"sid": "TR_audio", "type": "AUDIO", "source": "MICROPHONE"}
    response = _post(client, settings, event)
    assert response.status_code == 200
    row = models.MastraoRtcObservation.objects.get(event_id=event["id"])
    assert row.event_type == event_type
    assert row.room_binding.room_id.hex == event["room"]["name"].replace("-", "")
    assert row.participant_sid == "PA_fixture"
    assert row.rtc_identity == "guest_fixture"
    assert row.payload_digest == hashlib.sha256(json.dumps(event).encode()).hexdigest()
    assert (
        str(row.token_binding_ref)
        == event["participant"]["attributes"]["mastrao.media_token_binding_ref"]
    )
    assert "Private name" not in str(row.__dict__)
    assert not models.MastraoMediaTokenBinding.objects.exists()
    independent = psycopg.connect(**connection.get_connection_params())
    try:
        with independent.cursor() as cursor:
            cursor.execute(
                "SELECT payload_digest FROM meet_mastrao_rtc_observation WHERE event_id = %s",
                [event["id"]],
            )
            assert cursor.fetchone() == (row.payload_digest,)
    finally:
        independent.close()
    if event_type.startswith("track_"):
        assert row.track_sid == "TR_audio"
        assert row.track_type == api.TrackType.AUDIO
        assert row.track_source == api.TrackSource.MICROPHONE


def test_unsigned_or_tampered_body_never_persists(client, settings, event):
    assert _post(client, settings, event, authenticated=False).status_code == 401
    assert (
        _post(
            client, settings, event, body=json.dumps({**event, "id": "EV_changed"})
        ).status_code
        == 400
    )
    assert not models.MastraoRtcObservation.objects.exists()


def test_documented_uuid_and_minimal_track_participant(client, settings, event):
    """Track webhooks omit attributes: do not require the token marker there."""
    event.update(id=str(uuid4()), event="track_published")
    event["participant"].pop("attributes")
    event["track"] = {"sid": "TR_audio", "type": "AUDIO", "source": "MICROPHONE"}
    assert _post(client, settings, event).status_code == 200
    row = models.MastraoRtcObservation.objects.get(event_id=event["id"])
    assert row.token_binding_ref is None
    assert row.track_sid == "TR_audio"


def test_duplicate_delivery_does_not_mutate_row(client, settings, event):
    assert _post(client, settings, event).status_code == 200
    first = models.MastraoRtcObservation.objects.get().__dict__.copy()
    assert _post(client, settings, event).status_code == 200
    row = models.MastraoRtcObservation.objects.get()
    assert row.id == first["id"]
    assert row.updated_at == first["updated_at"]
    assert row.payload_digest == first["payload_digest"]


def test_signed_conflicting_id_preserves_first_fact(client, settings, event):
    assert _post(client, settings, event).status_code == 200
    event["participant"]["sid"] = "PA_other"
    assert _post(client, settings, event).status_code == 409
    assert models.MastraoRtcObservation.objects.get().participant_sid == "PA_fixture"


@pytest.mark.parametrize("marker", [None, "not-a-uuid", str(uuid4()).upper()])
def test_untrusted_marker_is_not_required_for_fact_storage(
    client, settings, event, marker
):
    event["participant"]["attributes"] = (
        {} if marker is None else {"mastrao.media_token_binding_ref": marker}
    )
    assert _post(client, settings, event).status_code == 200
    assert models.MastraoRtcObservation.objects.get().token_binding_ref is None


def test_late_out_of_order_events_retained_after_closure(
    client, settings, event, binding
):
    binding.closing_at = timezone.now()
    binding.save()
    unpublished = copy.deepcopy(event)
    unpublished.update(id="EV_unpublish", event="track_unpublished")
    unpublished["track"] = {"sid": "TR_audio", "type": "AUDIO"}
    assert _post(client, settings, unpublished).status_code == 200
    assert _post(client, settings, event).status_code == 200
    assert set(
        models.MastraoRtcObservation.objects.values_list("event_type", flat=True)
    ) == {"participant_joined", "track_unpublished"}
    assert not models.MastraoMediaTokenBinding.objects.exists()


@pytest.mark.parametrize(
    "field", ["id", "createdAt", "participant", "track", "room_sid"]
)
def test_incomplete_canonical_fact_is_not_acknowledged(client, settings, event, field):
    if field == "room_sid":
        event["room"].pop("sid")
    elif field == "track":
        event["event"] = "track_published"
    else:
        event.pop(field)
    assert _post(client, settings, event).status_code == 400
    assert not models.MastraoRtcObservation.objects.exists()


def test_flag_off_retains_existing_behavior(client, settings, event):
    settings.MASTRAO_MEDIA_TOKEN_BINDING_ENABLED = False
    assert _post(client, settings, event).status_code == 200
    assert not models.MastraoRtcObservation.objects.exists()


def test_unknown_room_has_no_canonical_observations(client, settings, event):
    event["room"]["name"] = str(uuid4())
    assert _post(client, settings, event).status_code == 200
    assert not models.MastraoRtcObservation.objects.exists()


def test_post_insert_failure_rolls_back_without_success_response(
    client, settings, event
):
    save = models.MastraoRtcObservation.save

    def failing_save(row, *args, **kwargs):
        save(row, *args, **kwargs)
        raise DatabaseError("synthetic failure after write")

    with mock.patch.object(models.MastraoRtcObservation, "save", failing_save):
        with pytest.raises(DatabaseError, match="synthetic failure after write"):
            _post(client, settings, event)
    assert not models.MastraoRtcObservation.objects.exists()
