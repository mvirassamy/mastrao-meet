"""Build the strict post-room-audio source bundle admitted by Tenant Core."""

from __future__ import annotations

import re
from dataclasses import dataclass
from hashlib import sha256

from room_audio_session import RoomAudioCompletion

MAX_AUDIO_DURATION_MS = 14_400_000
MAX_AUDIO_BYTES = 20_000_000_000
MAX_GAP_COUNT = 100_000
MAX_GENERATION_COUNT = 10_000
PROVIDER_REFERENCE = re.compile(r"^[A-Za-z0-9_-]{8,200}$")
OBJECT_REFERENCE = re.compile(r"^[A-Za-z0-9._/-]{16,1024}$")
BINDING_FIELDS = (
    "organization_external_id",
    "meeting_ref",
    "room_ref",
    "audio_run_ref",
    "policy_ref",
    "policy_digest",
    "notice_digest",
    "provider_binding_digest",
    "retention_expires_at",
)


@dataclass(frozen=True)
class RoomAudioSourceBundle:
    """Canonical manifest and ordered gaps for one completed post run."""

    manifest: dict
    gaps: tuple[dict, ...]


def build_room_audio_source_bundle(
    completion: RoomAudioCompletion,
    *,
    provider_session_ref: str,
    object_ref: str,
) -> RoomAudioSourceBundle:
    """Convert one verified post completion into the shared v2 contract."""
    artifact = completion.artifact
    if completion.effect.get("pipeline") != "post" or artifact is None:
        raise ValueError("post room audio completion with artifact is required")
    if not PROVIDER_REFERENCE.fullmatch(provider_session_ref):
        raise ValueError("provider_session_ref is invalid")
    if not _is_safe_object_ref(object_ref):
        raise ValueError("object_ref is invalid")
    binding = _binding_from_effect(completion.effect)
    _validate_completion_bounds(completion)
    _validate_artifact(artifact)
    gaps = tuple(
        {
            "sequence": sequence,
            "generation": gap.generation,
            "started_at_ms": gap.started_at_ms,
            "ended_at_ms": gap.ended_at_ms,
            "reason": gap.reason,
        }
        for sequence, gap in enumerate(completion.gaps)
    )
    total_gap_ms = sum(gap["ended_at_ms"] - gap["started_at_ms"] for gap in gaps)
    if total_gap_ms > artifact.duration_ms:
        raise ValueError("room audio gaps exceed artifact duration")
    manifest = {
        "version": 2,
        "type": "mastrao.meeting-room-audio-source",
        **binding,
        "pipeline": "post",
        "provider_session_ref": provider_session_ref,
        "object_ref": object_ref,
        "content_type": "audio/ogg",
        "codec": "opus",
        "channel_count": 1,
        "started_at_ms": completion.started_at_ms,
        "ended_at_ms": completion.started_at_ms + artifact.duration_ms,
        "duration_ms": artifact.duration_ms,
        "byte_size": artifact.byte_size,
        "checksum_algorithm": "sha256",
        "checksum_digest": artifact.sha256,
        "generation_count": completion.generation_count,
        "gap_count": len(gaps),
        "total_gap_ms": total_gap_ms,
        "mixer_output_frame_count": completion.mixer.output_frame_count,
        "mixer_dropped_frame_count": completion.mixer.dropped_frame_count,
        "mixer_underrun_frame_count": completion.mixer.track_underrun_frame_count,
        "mixer_queue_peak_frames": completion.mixer.mixer_queue_peak_frames,
        "rtc_sample_count": completion.rtc.sample_count,
        "rtc_collection_error_count": completion.rtc.collection_error_count,
        "rtc_packets_received": completion.rtc.packets_received,
        "rtc_packets_lost": completion.rtc.packets_lost,
        "rtc_concealed_samples": completion.rtc.concealed_samples,
        "rtc_maximum_jitter_ms": completion.rtc.maximum_jitter_ms,
        "rtc_maximum_round_trip_time_ms": (completion.rtc.maximum_round_trip_time_ms),
    }
    return RoomAudioSourceBundle(manifest=manifest, gaps=gaps)


def _binding_from_effect(effect: dict) -> dict:
    missing = [name for name in BINDING_FIELDS if name not in effect]
    if missing:
        raise ValueError("room audio effect binding is incomplete")
    return {name: effect[name] for name in BINDING_FIELDS}


def _validate_completion_bounds(completion: RoomAudioCompletion) -> None:
    if (
        completion.started_at_ms <= 0
        or completion.ended_at_ms < completion.started_at_ms
    ):
        raise ValueError("room audio completion interval is invalid")
    if not 1 <= completion.generation_count <= MAX_GENERATION_COUNT:
        raise ValueError("room audio generation count is invalid")
    if len(completion.gaps) > MAX_GAP_COUNT:
        raise ValueError("room audio gap count exceeds contract")
    previous_ended_at_ms = completion.started_at_ms
    previous_generation = 0
    for gap in completion.gaps:
        if (
            gap.generation <= 0
            or gap.generation > completion.generation_count
            or gap.generation < previous_generation
            or gap.started_at_ms < completion.started_at_ms
            or gap.started_at_ms < previous_ended_at_ms
            or gap.ended_at_ms <= gap.started_at_ms
            or gap.ended_at_ms > completion.ended_at_ms
            or gap.reason != "source_reconnect"
        ):
            raise ValueError("room audio gap is outside the completed run")
        previous_ended_at_ms = gap.ended_at_ms
        previous_generation = gap.generation


def _validate_artifact(artifact) -> None:
    if not 1 <= artifact.duration_ms <= MAX_AUDIO_DURATION_MS:
        raise ValueError("room audio artifact duration is invalid")
    if not 1 <= artifact.byte_size <= MAX_AUDIO_BYTES:
        raise ValueError("room audio artifact size is invalid")
    if not re.fullmatch(r"[a-f0-9]{64}", artifact.sha256):
        raise ValueError("room audio artifact checksum is invalid")
    if artifact.byte_size != len(artifact.content):
        raise ValueError("room audio artifact size does not match content")
    if artifact.sha256 != sha256(artifact.content).hexdigest():
        raise ValueError("room audio artifact checksum does not match content")


def _is_safe_object_ref(object_ref: str) -> bool:
    if not OBJECT_REFERENCE.fullmatch(object_ref):
        return False
    return all(part not in {"", ".", ".."} for part in object_ref.split("/"))
