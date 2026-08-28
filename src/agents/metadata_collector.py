"""Metadata agent that extracts metadata from active room."""

import asyncio
import base64
import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO
from typing import List, Optional
from urllib import request
from urllib.parse import urlparse

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from dotenv import load_dotenv
from livekit import api, rtc
from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    AutoSubscribe,
    JobContext,
    JobProcess,
    JobRequest,
    RoomIO,
    WorkerPermissions,
    cli,
    utils,
)
from livekit.agents import (
    room_io as lk_room_io,
)
from livekit.plugins import silero
from minio import Minio
from minio.error import S3Error

from exceptions import MissingConfigError
from observability import configure_sentry, set_job_context
from tasks import done_callback

load_dotenv()

logger = logging.getLogger("metadata-collector")

AGENT_NAME = os.getenv("METADATA_COLLECTOR_AGENT_NAME", "metadata-collector")
MAX_PARTICIPANTS = 500
MAX_EVENTS = 200_000
MAX_CORE_RESPONSE_BYTES = 32_768
CORE_HTTP_OK = 200
CORE_CALLBACK_TIMEOUT_SECONDS = 10
ARTIFACT_RECEIPT_TYPE = "mastrao.meeting-speaker-evidence-artifact-receipt"
ARTIFACT_RECEIPT_JOSE_TYPE = "mastrao-meeting-speaker-evidence-artifact-receipt+jws"
ARTIFACT_RECEIPT_SUFFIX = ".receipt.json"


def _digest(*parts: str) -> str:
    """Return a stable local SHA-256 digest for sensitive room material."""
    value = "\0".join(parts).encode("utf-8")
    return hashlib.sha256(value).hexdigest()


def _base64url_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _base64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _canonical_json(value) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        ).encode("utf-8")
        + b"\n"
    )


def _required_env(name: str) -> str:
    value = os.getenv(name, "")
    if not value:
        raise MissingConfigError
    return value


def _metadata_dict(raw_metadata: str):
    try:
        metadata = json.loads(raw_metadata)
    except (TypeError, json.JSONDecodeError):
        return None
    return metadata if isinstance(metadata, dict) else None


def _metadata_str(metadata: dict, name: str):
    value = metadata.get(name)
    return value if isinstance(value, str) and value else None


def _metadata_int(metadata: dict, name: str):
    value = metadata.get(name)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _safe_http_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise MissingConfigError
    return value


def _sign_artifact_receipt(claims: dict) -> str:
    jwk = json.loads(_required_env("MASTRAO_RECORDING_RECEIPT_PRIVATE_JWK"))
    private_key = Ed25519PrivateKey.from_private_bytes(_base64url_decode(jwk["d"]))
    protected = _base64url_encode(
        _canonical_json(
            {
                "alg": "EdDSA",
                "kid": _required_env("MASTRAO_RECORDING_RECEIPT_KEY_ID"),
                "typ": ARTIFACT_RECEIPT_JOSE_TYPE,
            }
        )
    )
    encoded = _base64url_encode(_canonical_json(claims))
    signature = private_key.sign(f"{protected}.{encoded}".encode("ascii"))
    return f"{protected}.{encoded}.{_base64url_encode(signature)}"


def _participant_ref(recording_id: str, participant_identity: str) -> str:
    """Return an opaque participant reference scoped to one recording."""
    return f"participant_{_digest(recording_id, participant_identity)[:32]}"


def _event_id(recording_id: str, event_type: str, participant_ref: str, at: datetime):
    """Return a deterministic bounded event id without leaking identity/name."""
    material = f"{recording_id}\0{event_type}\0{participant_ref}\0{at.isoformat()}"
    return f"event_{_digest(material)[:32]}"


def prewarm(proc: JobProcess):
    """Preload voice activity detection model."""
    configure_sentry(AGENT_NAME)
    proc.userdata["vad"] = silero.VAD.load()


server = AgentServer(
    permissions=WorkerPermissions(
        can_publish=False,
        can_publish_data=False,
        can_subscribe=True,
        hidden=True,
    ),
)
server.setup_fnc = prewarm


@dataclass
class MetadataEvent:
    """A single timestamped event recorded during a meeting."""

    participant_ref: str
    type: str
    timestamp: datetime
    data_digest: Optional[str] = None

    def serialize(self, recording_id: str, recording_started_at_ms: int) -> dict:
        """Return a JSON-serializable dictionary representation of the event."""
        wall_clock_ms = int(self.timestamp.timestamp() * 1000)
        at_ms = max(0, wall_clock_ms - recording_started_at_ms)
        event_id = _event_id(
            recording_id,
            self.type,
            self.participant_ref,
            self.timestamp,
        )
        payload = {
            "event_id": event_id,
            "at_ms": at_ms,
            "type": self.type,
            "participant_ref": self.participant_ref,
        }
        payload["event_digest"] = self.data_digest or _digest(
            recording_id,
            event_id,
            self.type,
            self.participant_ref,
            str(at_ms),
        )
        return payload


class VADAgent(Agent):
    """Agent that monitors voice activity for a specific participant."""

    def __init__(self, participant_ref: str, events: List):
        """Initialize with a participant identity and shared events list."""
        super().__init__(
            instructions="not-needed",
        )
        self.participant_ref = participant_ref
        self.events = events

    async def on_enter(self) -> None:
        """Initialize VAD monitoring for this participant."""

        @self.session.on("user_state_changed")
        def on_user_state(event):
            timestamp = datetime.now(timezone.utc)

            if event.new_state == "speaking":
                event = MetadataEvent(
                    participant_ref=self.participant_ref,
                    type="speech_start",
                    timestamp=timestamp,
                )
                self.events.append(event)

            elif event.old_state == "speaking":
                event = MetadataEvent(
                    participant_ref=self.participant_ref,
                    type="speech_end",
                    timestamp=timestamp,
                )
                self.events.append(event)


class MetadataCollector:
    """Collect meeting events across all participants in a room.

    Creates one AgentSession per participant to capture VAD events
    (speech start/end), and listens for connection, disconnection,
    and rename events. Persists only sanitized evidence as JSON to S3
    on shutdown. Chat and transcript content are intentionally excluded.
    """

    def __init__(self, ctx: JobContext, recording_metadata: str):
        """Initialize metadata agent."""
        self.minio_client = Minio(
            endpoint=os.getenv("AWS_S3_ENDPOINT_URL"),
            access_key=os.getenv("AWS_S3_ACCESS_KEY_ID"),
            secret_key=os.getenv("AWS_S3_SECRET_ACCESS_KEY"),
            secure=os.getenv("AWS_S3_SECURE_ACCESS", "False").lower() == "true",
        )

        if (bucket_name := os.getenv("AWS_STORAGE_BUCKET_NAME")) is not None:
            self.bucket_name = bucket_name
        else:
            raise MissingConfigError

        self.ctx = ctx
        self._sessions: dict[str, AgentSession] = {}
        self._tasks: set[asyncio.Task] = set()
        self.recording_id = recording_metadata
        self.recording_ref = f"recording_{_digest(self.recording_id)[:32]}"
        self.evidence_ref = None
        self.provider_binding_digest = None
        self.meeting_ref = None
        self.room_ref = None
        self.organization_external_id = None
        self.policy_ref = None
        self.notice_version = None
        self.notice_digest = None
        self.retention_expires_at = None
        self.recording_started_at_ms = 0

        output_folder = os.getenv("AWS_S3_OUTPUT_FOLDER", "metadata")
        self.output_filename = f"{output_folder}/{recording_metadata}-metadata.json"
        self._apply_signed_metadata(recording_metadata)

        # Storage for events
        self.events = []
        self.participants = {}

        logger.info("MetadataCollector initialized")

    def _apply_signed_metadata(self, recording_metadata: str):
        """Load opaque Core-provided metadata when this is a signed capture."""
        metadata = _metadata_dict(recording_metadata)
        if metadata is None:
            return
        self.recording_id = _metadata_str(metadata, "recording_id") or self.recording_id
        self.recording_ref = (
            _metadata_str(metadata, "recording_ref") or self.recording_ref
        )
        self.meeting_ref = _metadata_str(metadata, "meeting_ref")
        self.room_ref = _metadata_str(metadata, "room_ref")
        self.evidence_ref = _metadata_str(metadata, "evidence_ref")
        self.organization_external_id = _metadata_str(
            metadata,
            "organization_external_id",
        )
        self.provider_binding_digest = _metadata_str(
            metadata,
            "provider_binding_digest",
        )
        self.policy_ref = _metadata_str(metadata, "policy_ref")
        self.notice_version = _metadata_str(metadata, "notice_version")
        self.notice_digest = _metadata_str(metadata, "notice_digest")
        self.retention_expires_at = _metadata_int(metadata, "retention_expires_at")
        self.recording_started_at_ms = (
            _metadata_int(metadata, "recording_started_at_ms") or 0
        )
        object_ref = _metadata_str(metadata, "object_ref")
        if object_ref and object_ref.startswith("mastrao-speaker-evidence/"):
            self.output_filename = object_ref

    def start(self):
        """Start listening for room-level events."""
        self.ctx.room.on("participant_disconnected", self.on_participant_disconnected)
        self.ctx.room.on("participant_name_changed", self.on_participant_name_changed)

        logger.info("Started listening for participant events")

    def save(self):
        """Serialize collected events and upload as JSON to S3."""
        logger.info("Persisting metadata...")

        participants = []
        for participant_ref, participant in self.participants.items():
            participants.append(
                {
                    "participant_ref": participant_ref,
                    "participant_kind": participant["participant_kind"],
                    "participant_session_digest": participant[
                        "participant_session_digest"
                    ],
                    "declared_label_digest": participant.get("declared_label_digest"),
                }
            )

        sorted_events = sorted(self.events, key=lambda e: e.timestamp)

        events = [
            event.serialize(self.recording_id, self.recording_started_at_ms)
            for event in sorted_events[:MAX_EVENTS]
        ]
        event_times = [event["at_ms"] for event in events]
        timeline_started_at_ms = min(event_times, default=0)
        timeline_ended_at_ms = max(event_times, default=0)

        payload = {
            "version": 1,
            "recording_ref": self.recording_ref,
            "recording_started_at_ms": self.recording_started_at_ms,
            "timeline_started_at_ms": timeline_started_at_ms,
            "timeline_ended_at_ms": timeline_ended_at_ms,
            "participants": participants[:MAX_PARTICIPANTS],
            "events": events,
        }
        if self.evidence_ref is not None:
            payload["evidence_ref"] = self.evidence_ref
        if self.meeting_ref is not None:
            payload["meeting_ref"] = self.meeting_ref
        if self.room_ref is not None:
            payload["room_ref"] = self.room_ref

        data = json.dumps(payload, indent=2).encode("utf-8")
        stream = BytesIO(data)

        try:
            self.minio_client.put_object(
                self.bucket_name,
                self.output_filename,
                stream,
                length=len(data),
                content_type="application/json",
            )
            logger.info(
                "Uploaded speaker meeting metadata",
            )
            if self.evidence_ref is not None:
                self.notify_core_artifact(payload, data)
        except S3Error:
            logger.exception(
                "Failed to upload meeting metadata",
            )

    def _artifact_receipt_claims(self, payload: dict, data: bytes):
        """Build the signed Core receipt for one uploaded speaker evidence artifact."""
        if any(
            value is None
            for value in (
                self.organization_external_id,
                self.provider_binding_digest,
                self.policy_ref,
                self.notice_version,
                self.notice_digest,
                self.retention_expires_at,
            )
        ):
            raise MissingConfigError
        now = int(time.time())
        checksum_digest = hashlib.sha256(data).hexdigest()
        artifact_ref = (
            f"speakerartifact_{_digest(payload['evidence_ref'], checksum_digest)[:32]}"
        )
        return {
            "version": 1,
            "type": ARTIFACT_RECEIPT_TYPE,
            "issuer": _required_env("MASTRAO_RECORDING_RECEIPT_ISSUER"),
            "audience": _required_env("MASTRAO_RECORDING_RECEIPT_AUDIENCE"),
            "operation": "confirm_meeting_speaker_evidence_artifact",
            "operation_version": 1,
            "organization_external_id": self.organization_external_id,
            "meeting_ref": payload["meeting_ref"],
            "room_ref": payload["room_ref"],
            "recording_ref": payload["recording_ref"],
            "evidence_ref": payload["evidence_ref"],
            "provider_binding_digest": self.provider_binding_digest,
            "policy_ref": self.policy_ref,
            "notice_version": self.notice_version,
            "notice_digest": self.notice_digest,
            "purpose": "meeting_speaker_evidence",
            "scope": "recording_roster_vad_timeline",
            "retention_expires_at": self.retention_expires_at,
            "artifact_ref": artifact_ref,
            "object_ref": self.output_filename,
            "byte_size": len(data),
            "checksum_digest": checksum_digest,
            "participant_count": len(payload["participants"]),
            "event_count": len(payload["events"]),
            "timeline_started_at_ms": payload["timeline_started_at_ms"],
            "timeline_ended_at_ms": payload["timeline_ended_at_ms"],
            "region_ref": _required_env("MASTRAO_RECORDING_REGION_REF"),
            "encryption_ref": _required_env("MASTRAO_RECORDING_ENCRYPTION_REF"),
            "lifecycle_policy_ref": _required_env(
                "MASTRAO_RECORDING_LIFECYCLE_POLICY_REF"
            ),
            "issued_at": now,
            "expires_at": now + 30,
            "jti": f"speakerartifact_{_digest(payload['evidence_ref'], str(now))[:32]}",
        }

    def notify_core_artifact(self, payload: dict, data: bytes):
        """Notify Core that the speaker evidence artifact is durably available."""
        endpoint = _safe_http_url(
            _required_env("MASTRAO_CORE_SPEAKER_EVIDENCE_ARTIFACT_ENDPOINT")
        )
        receipt = _sign_artifact_receipt(self._artifact_receipt_claims(payload, data))
        body = json.dumps(
            {"speaker_evidence_artifact_receipt": receipt},
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        self.minio_client.put_object(
            self.bucket_name,
            f"{self.output_filename}{ARTIFACT_RECEIPT_SUFFIX}",
            BytesIO(body),
            length=len(body),
            content_type="application/json",
        )
        core_request = request.Request(  # noqa: S310
            endpoint,
            data=body,
            method="POST",
            headers={"content-type": "application/json"},
        )
        with request.urlopen(  # noqa: S310
            core_request,
            timeout=CORE_CALLBACK_TIMEOUT_SECONDS,
        ) as response:
            response_body = response.read(MAX_CORE_RESPONSE_BYTES + 1)
            if (
                response.status != CORE_HTTP_OK
                or len(response_body) > MAX_CORE_RESPONSE_BYTES
            ):
                raise RuntimeError("speaker_evidence_artifact_refused")

    async def aclose(self):
        """Close all sessions and cleanup resources."""
        logger.info("Closing all VAD monitoring sessions…")

        await utils.aio.cancel_and_wait(*self._tasks)

        await asyncio.gather(
            *[self._close_session(session) for session in self._sessions.values()],
            return_exceptions=True,
        )

        self.ctx.room.off("participant_disconnected", self.on_participant_disconnected)
        self.ctx.room.off("participant_name_changed", self.on_participant_name_changed)

        logger.info("All VAD sessions closed")
        self.save()

    async def on_participant_entrypoint(
        self, ctx: JobContext, participant: rtc.RemoteParticipant
    ):
        """Handle new participant by starting a VAD monitoring session."""
        if participant.identity in self._sessions:
            logger.debug("VAD session already exists for participant")
            return
        participant_ref = _participant_ref(self.recording_id, participant.identity)

        self.events.append(
            MetadataEvent(
                participant_ref=participant_ref,
                type="participant_connected",
                timestamp=datetime.now(timezone.utc),
            )
        )

        self.participants[participant_ref] = {
            "participant_kind": "unknown",
            "participant_session_digest": _digest(
                self.recording_id,
                participant.identity,
                "session",
            ),
            "declared_label_digest": _digest(
                self.recording_id,
                participant.name or "",
                "label",
            ),
        }

        logger.info("New participant connected")
        try:
            session = await self._start_session(participant)
            self._sessions[participant.identity] = session
        except Exception:
            logger.exception("Failed to start VAD session")

    def on_participant_disconnected(self, participant: rtc.RemoteParticipant):
        """Handle participant disconnection by closing VAD monitoring."""
        participant_ref = _participant_ref(self.recording_id, participant.identity)
        self.events.append(
            MetadataEvent(
                participant_ref=participant_ref,
                type="participant_disconnected",
                timestamp=datetime.now(timezone.utc),
            )
        )

        session = self._sessions.pop(participant.identity, None)
        if session is None:
            logger.debug("No VAD session found for participant")
            return

        logger.info("Participant disconnected")
        task = asyncio.create_task(self._close_session(session))
        self._tasks.add(task)
        task.add_done_callback(
            done_callback(
                logger,
                self._tasks,
                "close VAD session",
                on_success=lambda _: logger.info(
                    "VAD session closed (remaining sessions: %d)",
                    len(self._sessions),
                ),
            )
        )

    def on_participant_name_changed(self, participant: rtc.RemoteParticipant):
        """Record a sanitized participant label change."""
        participant_ref = _participant_ref(self.recording_id, participant.identity)
        logger.info("Participant label changed")
        self.participants.setdefault(
            participant_ref,
            {
                "participant_kind": "unknown",
                "participant_session_digest": _digest(
                    self.recording_id,
                    participant.identity,
                    "session",
                ),
            },
        )["declared_label_digest"] = _digest(
            self.recording_id,
            participant.name or "",
            "label",
        )
        self.events.append(
            MetadataEvent(
                participant_ref=participant_ref,
                type="participant_renamed",
                timestamp=datetime.now(timezone.utc),
                data_digest=_digest(self.recording_id, participant.name or "", "label"),
            )
        )

    async def _start_session(self, participant: rtc.RemoteParticipant) -> AgentSession:
        """Create and start VAD monitoring session for participant."""
        if participant.identity in self._sessions:
            return self._sessions[participant.identity]

        # Create session with VAD only - no STT, LLM, or TTS
        session = AgentSession(
            vad=self.ctx.proc.userdata["vad"],
            turn_detection="vad",
            user_away_timeout=30.0,
        )

        # Set up room IO to receive audio from this specific participant
        room_io = RoomIO(
            agent_session=session,
            room=self.ctx.room,
            participant=participant,
            options=lk_room_io.RoomOptions(
                audio_input=lk_room_io.AudioInputOptions(),
                text_input=False,
                audio_output=False,
                text_output=False,
            ),
        )

        await room_io.start()
        await session.start(
            agent=VADAgent(
                participant_ref=_participant_ref(
                    self.recording_id,
                    participant.identity,
                ),
                events=self.events,
            )
        )

        return session

    async def _close_session(self, session: AgentSession) -> None:
        """Close and cleanup VAD monitoring session."""
        try:
            await session.aclose()
        except Exception:
            logger.exception("Error closing session")


async def handle_job_request(job_req: JobRequest) -> None:
    """Accept or reject the job request based on agent presence in the room."""
    room_name = job_req.room.name
    recording_id = job_req.job.metadata
    agent_identity = f"{AGENT_NAME}-{room_name}"

    async with api.LiveKitAPI() as lk:
        try:
            resp = await lk.room.list_participants(
                list=api.ListParticipantsRequest(room=room_name)
            )
            already_present = any(
                p.kind == rtc.ParticipantKind.PARTICIPANT_KIND_AGENT
                and p.identity == agent_identity
                for p in resp.participants
            )
            if already_present:
                logger.info("Agent already in the room '%s' — reject", room_name)
                await job_req.reject()
            else:
                logger.info(
                    "Accept job for '%s' — identity=%s", room_name, agent_identity
                )
                await job_req.accept(identity=agent_identity, metadata=recording_id)
        except Exception:
            logger.exception("Error treating the job for '%s'", room_name)
            await job_req.reject()


@server.rtc_session(agent_name=AGENT_NAME, on_request=handle_job_request)
async def entrypoint(ctx: JobContext):
    """Initialize and run the metadata collector."""
    set_job_context(room=ctx.room.name, job_id=ctx.job.id)

    logger.info("Starting metadata agent in room: %s", ctx.room.name)
    recording_id = ctx.job.metadata
    metadata_collector = MetadataCollector(ctx, recording_id)
    metadata_collector.start()

    ctx.add_participant_entrypoint(metadata_collector.on_participant_entrypoint)

    await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)

    async def cleanup():
        logger.info("Shutting down metadata collector...")
        await metadata_collector.aclose()

    ctx.add_shutdown_callback(cleanup)


if __name__ == "__main__":
    # Initialize Sentry for the worker process. Each job runs in its own
    # (forked) process and re-initializes Sentry via prewarm().
    configure_sentry(AGENT_NAME)
    cli.run_app(server)
