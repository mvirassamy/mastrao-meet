"""Correlate authenticated facts without authorizing capture or guessing names.

The ingestion caller holds the room row lock. Correlation is monotonic when a
conflict or terminal fact arrives. RTC SIDs, not names or delivery timestamps,
identify connection/track epochs. Reusing a token on distinct connections is
quarantined; the consumer must obtain a fresh Core authority before media work.
"""

from django.utils import timezone

from core import models

CONFLICTS = frozenset({"conflicting_join", "token_reused", "identity_conflict"})


def _connection_facts(connection):
    return models.MastraoRtcObservation.objects.filter(
        room_binding_id=connection.room_binding_id,
        room_sid=connection.room_sid,
        participant_sid=connection.participant_sid,
    )


def _matching_receipt(connection, join):
    if join.token_binding_ref is None:
        return None
    receipt = models.MastraoMediaTokenBinding.objects.filter(
        pk=join.token_binding_ref,
        room_binding_id=connection.room_binding_id,
        rtc_identity=join.rtc_identity,
    ).first()
    if receipt is None:
        return None
    # Webhook time has second precision; it is NOT a media timestamp. Expiry
    # gates the join only, never the length of an already established call.
    if not (
        int(receipt.issued_at.timestamp())
        <= join.event_time_seconds
        < int(receipt.expires_at.timestamp())
    ):
        return None
    return receipt


def _correlate_join(connection):
    if connection.correlation in CONFLICTS:
        return
    joins = _connection_facts(connection).filter(event_type="participant_joined")
    variants = list(
        joins.values_list("token_binding_ref", "rtc_identity").distinct()[:2]
    )
    if len(variants) > 1:
        connection.correlation = "conflicting_join"
        return
    join = joins.order_by("event_time_seconds", "event_id").first()
    if join is None:
        return
    receipt = _matching_receipt(connection, join)
    if receipt is None:
        connection.correlation = "unmatched_issuance"
        return
    connection.media_token_binding = receipt
    if (
        _connection_facts(connection)
        .exclude(rtc_identity=receipt.rtc_identity)
        .exists()
    ):
        connection.correlation = "identity_conflict"
        return
    others = models.MastraoRtcConnection.objects.filter(
        media_token_binding=receipt
    ).exclude(pk=connection.pk)
    if others.exists():
        others.update(correlation="token_reused", updated_at=timezone.now())
        connection.correlation = "token_reused"
        return
    connection.correlation = "correlated"


def _update_track(connection, observation):
    if not observation.track_sid:
        return
    epoch, _ = models.MastraoRtcTrackEpoch.objects.get_or_create(
        connection=connection, track_sid=observation.track_sid
    )
    if observation.event_type == "track_unpublished":
        epoch.ended = True
    else:
        publications = _connection_facts(connection).filter(
            track_sid=observation.track_sid, event_type="track_published"
        )
        shapes = publications.values_list("track_type", "track_source").distinct()[:2]
        if len(shapes) > 1:
            epoch.conflict = True
        if epoch.first_publication_id is None:
            epoch.first_publication = observation
    # An out-of-order publish can fill provenance but cannot reopen the epoch.
    epoch.ended = epoch.ended or connection.ended
    epoch.save()


def correlate_verified_observation(observation):
    """Project facts atomically inside the room-locked ingestion transaction."""
    connection, _ = models.MastraoRtcConnection.objects.get_or_create(
        room_binding_id=observation.room_binding_id,
        room_sid=observation.room_sid,
        participant_sid=observation.participant_sid,
    )
    _correlate_join(connection)
    connection.ended = connection.ended or observation.event_type == "participant_left"
    connection.save()
    _update_track(connection, observation)
    if connection.ended:
        models.MastraoRtcTrackEpoch.objects.filter(
            connection=connection, ended=False
        ).update(ended=True, updated_at=timezone.now())
