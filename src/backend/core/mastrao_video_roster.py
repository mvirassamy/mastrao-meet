"""Authorize the first video provider call against a fresh, bound SFU roster.

This snapshot does not freeze membership or revoke preissued roomJoin tokens.
The SFU can accept a new connection between authorization and provider start.
"""

# SDK protobuf members and exact security predicates are deliberately explicit.
# pylint: disable=no-member,too-many-boolean-expressions

import asyncio
import time
from urllib.parse import urlparse
from uuid import uuid4

from django.conf import settings

from asgiref.sync import async_to_sync
from livekit import api

from core import models, utils
from core.mastrao_core_http import post_core_json, validate_core_endpoint
from core.mastrao_recording_contract import (
    RecordingContractRefused,
    _sign,
    _validate_ref,
)
from core.mastrao_room_contract import DIGEST

START_AUTHORIZATION_PATH = "/internal/v1/meetings/recording/start-authorization"
SESSION_STATUS_PATH = "/internal/v1/meetings/recording/session-status"
ROSTER_JOSE_TYPE = "mastrao-meeting-video-roster+jws"
MAX_SNAPSHOT_SECONDS = 10
MAX_PARTICIPANTS = 100
MAX_COMPACT_BYTES = 16_384
RESPONSE_FIELDS = {"version", "authorized", "start_status", "decision_lock"}
MEDIA_BINDING_ATTRIBUTE = "mastrao.media_token_binding_ref"


@async_to_sync
async def _snapshot(room_name):
    """Read the canonical SFU room incarnation and current participants."""
    client = utils.create_livekit_client(settings.LIVEKIT_CONFIGURATION)
    try:
        async with asyncio.timeout(MAX_SNAPSHOT_SECONDS):
            rooms = await client.room.list_rooms(
                api.ListRoomsRequest(names=[room_name])
            )
            if (
                len(rooms.rooms) != 1
                or rooms.rooms[0].name != room_name
                or not rooms.rooms[0].sid
            ):
                raise RecordingContractRefused(status=503)
            response = await client.room.list_participants(
                api.ListParticipantsRequest(room=room_name)
            )
            return rooms.rooms[0].sid, response.participants
    except (api.TwirpError, OSError, TimeoutError) as error:
        raise RecordingContractRefused(status=503) from error
    finally:
        await client.aclose()


def _humans(participants):
    humans = []
    identities, sids = set(), set()
    for participant in participants:
        if (
            participant.permission.recorder
            or participant.kind == api.ParticipantInfo.EGRESS
        ):
            continue
        if (
            participant.kind != api.ParticipantInfo.STANDARD
            or participant.permission.agent
            or not participant.identity
            or not participant.sid
            or participant.identity in identities
            or participant.sid in sids
        ):
            raise RecordingContractRefused(status=503)
        identities.add(participant.identity)
        sids.add(participant.sid)
        humans.append(participant)
        if len(humans) > MAX_PARTICIPANTS:
            raise RecordingContractRefused(status=503)
    if not humans:
        raise RecordingContractRefused(status=503)
    return humans


def _participant_claim(binding, participant, connection):
    issued = connection.media_token_binding
    if (
        connection.correlation != "correlated"
        or connection.ended
        or issued is None
        or issued.room_binding_id != binding.pk
        or issued.rtc_identity != participant.identity
        or participant.attributes.get(MEDIA_BINDING_ATTRIBUTE) != str(issued.pk)
        or bool(issued.host_grant_id) == bool(issued.guest_grant_id)
    ):
        raise RecordingContractRefused(status=503)
    if issued.host_grant_id:
        grant = issued.host_grant
        kind, reference = "host", grant.identity.host_ref
        if not grant.identity.user.is_active:
            raise RecordingContractRefused(status=503)
    else:
        grant = issued.guest_grant
        kind, reference = "guest", grant.guest_ref
        if (
            grant.admission_state != models.MastraoGuestGrant.AdmissionState.ALLOWED
            or grant.decision_allow is not True
            or grant.decision_confirmed_at is None
            or not grant.decision_receipt_digest
        ):
            raise RecordingContractRefused(status=503)
    if (
        grant.room_binding_id != binding.pk
        or grant.meeting_ref != binding.meeting_ref
        or grant.room_ref != binding.room_ref
        or grant.provider_binding_digest != binding.provider_binding_digest
        or grant.grant_digest != issued.grant_digest
        or grant.session_nonce_digest != issued.session_nonce_digest
    ):
        raise RecordingContractRefused(status=503)
    # Expiry bounds joining, not the lifetime of this established connection.
    claim = {
        "participant_kind": kind,
        "participant_ref": reference,
        "grant_ref": grant.grant_ref,
        "participant_session_digest": issued.session_nonce_digest,
        "participant_grant_digest": issued.grant_digest,
    }
    for name in ("participant_ref", "grant_ref"):
        _validate_ref(claim, name)
    for name in ("participant_session_digest", "participant_grant_digest"):
        if not isinstance(claim[name], str) or not DIGEST.fullmatch(claim[name]):
            raise RecordingContractRefused(status=503)
    return claim


def _bound_roster(binding, room_sid, participants):
    humans = _humans(participants)
    connections = models.MastraoRtcConnection.objects.filter(
        room_binding=binding,
        room_sid=room_sid,
        participant_sid__in=[participant.sid for participant in humans],
    ).select_related(
        "media_token_binding__host_grant__identity__user",
        "media_token_binding__guest_grant",
    )
    by_sid = {}
    for connection in connections:
        if connection.participant_sid in by_sid:
            raise RecordingContractRefused(status=503)
        by_sid[connection.participant_sid] = connection
    claims, references = [], set()
    for participant in humans:
        connection = by_sid.get(participant.sid)
        if connection is None:
            raise RecordingContractRefused(status=503)
        claim = _participant_claim(binding, participant, connection)
        reference = (
            claim["participant_kind"],
            claim["participant_ref"],
            claim["participant_session_digest"],
        )
        if reference in references:
            raise RecordingContractRefused(status=503)
        references.add(reference)
        claims.append(claim)
    return claims


def _core_endpoint():
    # Recording callbacks share the explicitly configured private Core origin.
    configured = validate_core_endpoint(
        settings.MASTRAO_CORE_RECORDING_SESSION_STATUS_ENDPOINT,
        SESSION_STATUS_PATH,
        RecordingContractRefused,
    )
    return urlparse(configured)._replace(path=START_AUTHORIZATION_PATH).geturl()


def snapshot_video_roster(binding):
    """Bind a fresh server snapshot for either the host click or first dispatch."""
    if binding.closing_at is not None or hasattr(binding, "closure"):
        raise RecordingContractRefused(status=503)
    observed_at = int(time.time())
    room_sid, participants = _snapshot(str(binding.room_id))
    participants = _bound_roster(binding, room_sid, participants)
    if not 0 <= int(time.time()) - observed_at <= MAX_SNAPSHOT_SECONDS:
        raise RecordingContractRefused(status=503)
    return {"observed_at": observed_at, "participants": participants}


def _validate_authorization(response):
    if (
        set(response) != RESPONSE_FIELDS
        or not isinstance(response["version"], int)
        or isinstance(response["version"], bool)
        or response["version"] != 1
        or not isinstance(response["authorized"], bool)
        or not isinstance(response["start_status"], str)
        or not isinstance(response["decision_lock"], str)
        or response["start_status"] not in {"pending", "refused", "authorized"}
        or response["decision_lock"]
        not in {"open", "start_in_progress", "started", "stopped"}
    ):
        raise RecordingContractRefused(status=503)
    if response["authorized"]:
        if (
            response["start_status"] != "authorized"
            or response["decision_lock"] != "start_in_progress"
        ):
            raise RecordingContractRefused(status=503)
    elif (
        response["start_status"] not in {"pending", "refused"}
        or response["decision_lock"] != "open"
    ):
        # Only a definite refusal with an open Core lock permits a new click.
        raise RecordingContractRefused(status=503)
    return response


def authorize_video_start(effect, recording_binding, recording):
    """Return Core's strict decision before any first provider invocation."""
    endpoint = _core_endpoint()
    binding = recording_binding.room_binding
    if (
        binding.closing_at is not None
        or hasattr(binding, "closure")
        or str(binding.room_id) != str(recording.room_id)
        or binding.meeting_ref != effect["meeting_ref"]
        or binding.room_ref != effect["room_ref"]
        or binding.provider_binding_digest != effect["provider_binding_digest"]
    ):
        raise RecordingContractRefused(status=503)
    for name in ("meeting_ref", "room_ref", "recording_ref", "effect_key", "claim_id"):
        _validate_ref(effect, name)
    started = time.monotonic()
    snapshot = snapshot_video_roster(binding)
    issued_at = int(time.time())
    if not 0 <= issued_at - snapshot["observed_at"] <= MAX_SNAPSHOT_SECONDS:
        raise RecordingContractRefused(status=503)
    payload = {
        "version": 1,
        "type": "mastrao.meet-video-roster",
        "issuer": settings.MASTRAO_RECORDING_RECEIPT_ISSUER,
        "audience": settings.MASTRAO_RECORDING_RECEIPT_AUDIENCE,
        **{
            name: effect[name]
            for name in (
                "organization_external_id",
                "meeting_ref",
                "room_ref",
                "recording_ref",
                "effect_key",
                "claim_id",
            )
        },
        **snapshot,
        "issued_at": issued_at,
        "expires_at": issued_at + 30,
        "jti": f"videoroster_{uuid4().hex}",
    }
    compact = _sign(payload, ROSTER_JOSE_TYPE)
    # Core's compact-JWS envelope is bounded even when the roster has <=100 humans.
    if len(compact) > MAX_COMPACT_BYTES:
        raise RecordingContractRefused(status=503)
    remaining = MAX_SNAPSHOT_SECONDS - (time.monotonic() - started)
    if remaining <= 0:
        raise RecordingContractRefused(status=503)
    response = post_core_json(
        endpoint=endpoint,
        expected_path=START_AUTHORIZATION_PATH,
        body={"roster_assertion": compact},
        timeout=min(settings.MASTRAO_CORE_RECORDING_TIMEOUT_SECONDS, remaining),
        refusal=RecordingContractRefused,
        expected_fields=RESPONSE_FIELDS,
        client_error_status=503,
    )
    decision = _validate_authorization(response)
    if decision["authorized"] and time.monotonic() - started > MAX_SNAPSHOT_SECONDS:
        raise RecordingContractRefused(status=503)
    return decision
