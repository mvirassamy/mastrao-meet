"""Durable intake after LiveKit's signature AND exact-body hash verification.

This inbox records facts, including late leave/unpublish events. Arrival order
does not establish media order, and a webhook timestamp is not an audio clock.
Public token markers remain untrusted hints for the next correlation step.
No names, arbitrary attributes, bearer tokens or raw payloads are retained.
"""

import hashlib
import re
from uuid import UUID

from django.conf import settings
from django.db import transaction

from core import models
from core.mastrao_native_admission import wake_native_admissions
from core.mastrao_rtc_correlation import correlate_verified_observation

PARTICIPANT_EVENTS = frozenset({"participant_joined", "participant_left"})
TRACK_EVENTS = frozenset({"track_published", "track_unpublished"})
OBSERVED_EVENTS = PARTICIPANT_EVENTS | TRACK_EVENTS
MEDIA_BINDING_ATTRIBUTE = "mastrao.media_token_binding_ref"


class RtcObservationConflict(Exception):
    """A verified event ID was reused with a different exact payload."""


class InvalidRtcObservation(Exception):
    """A canonical-room event is missing required server facts."""


def _uuid_or_none(value):
    try:
        result = UUID(value)
        return result if str(result) == value else None
    except (ValueError, TypeError, AttributeError):
        return None


def _sid(value, prefix):
    if not re.fullmatch(rf"{prefix}_[A-Za-z0-9_-]{{1,120}}", value):
        raise InvalidRtcObservation("Invalid server reference")
    return value


def _facts(event):
    participant = event.participant
    # Documented UUIDs and older EV-prefixed IDs are both opaque server keys.
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", event.id):
        raise InvalidRtcObservation("Invalid event reference")
    if not participant.identity or len(participant.identity) > 255:
        raise InvalidRtcObservation("Invalid RTC identity")
    if event.created_at <= 0:
        raise InvalidRtcObservation("Missing server event time")
    facts = {
        "event_id": event.id,
        "event_type": event.event,
        "event_time_seconds": event.created_at,
        "room_sid": _sid(event.room.sid, "RM"),
        "participant_sid": _sid(participant.sid, "PA"),
        "rtc_identity": participant.identity,
        "token_binding_ref": _uuid_or_none(
            participant.attributes.get(MEDIA_BINDING_ATTRIBUTE)
        ),
    }
    if event.event in TRACK_EVENTS:
        if not event.HasField("track"):
            raise InvalidRtcObservation("Missing published track")
        facts.update(
            track_sid=_sid(event.track.sid, "TR"),
            track_type=event.track.type,
            track_source=event.track.source,
        )
    return facts


def record_verified_rtc_observation(event, verified_body):
    """Called only behind WebhookReceiver; commit before acknowledging delivery.

    Unknown/noncanonical rooms and flag-off requests retain existing behavior.
    Serialize facts per room; a retry leaves the original row untouched. A DB error
    propagates rather than acknowledging an event that was not persisted.
    """
    if not settings.MASTRAO_MEDIA_TOKEN_BINDING_ENABLED:
        return
    if event.event not in OBSERVED_EVENTS:
        return
    room_id = _uuid_or_none(event.room.name)
    if room_id is None:
        return
    _persist_observation(event, verified_body, room_id)


@transaction.atomic
def _persist_observation(event, verified_body, room_id):
    """Only enter a transaction after the opt-in and event filters."""
    binding = (
        models.MastraoRoomBinding.objects.select_for_update()
        .filter(room_id=room_id)
        .first()
    )
    if binding is None:
        return
    facts = _facts(event)
    digest = hashlib.sha256(verified_body).hexdigest()
    existing = models.MastraoRtcObservation.objects.filter(event_id=event.id).first()
    if existing is not None:
        if existing.payload_digest != digest or existing.room_binding_id != binding.pk:
            raise RtcObservationConflict("Conflicting RTC event ID")
        return
    observation = models.MastraoRtcObservation.objects.create(
        room_binding=binding, payload_digest=digest, **facts
    )
    correlate_verified_observation(observation)
    connection = models.MastraoRtcConnection.objects.get(
        room_binding=binding,
        room_sid=observation.room_sid,
        participant_sid=observation.participant_sid,
    )
    transaction.on_commit(lambda: wake_native_admissions(connection.pk))
