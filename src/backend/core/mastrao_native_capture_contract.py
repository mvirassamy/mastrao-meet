"""Versioned native-audio effects; neither room consent nor RTC facts authorize these."""

# Generated LiveKit protobuf members and fail-closed contract checks are intentional here.
# pylint: disable=too-many-boolean-expressions,unidiomatic-typecheck

import re
from uuid import UUID

from django.conf import settings

from core.mastrao_recording_contract import (
    RecordingContractRefused,
    _key,
    _sign,
    _validate_ref,
    _validate_time,
    _verify,
)
from core.mastrao_room_contract import DIGEST, _sha256_canonical

JOSE_TYPE = "mastrao-native-microphone-start-effect+jws"
EFFECT_TYPE = "mastrao.core-native-microphone-start-effect"
RECEIPT_JOSE_TYPE = "mastrao-native-microphone-start-receipt+jws"
PROFILE = {
    "version": 1,
    "engine": "livekit_track_composite",
    "container": "hls_mpegts",
    "codec": "aac",
    "bitrate_kbps": 32,
    "frequency_hz": 48000,
    "segment_seconds": 6,
}
PROFILE_DIGEST = _sha256_canonical(PROFILE)
AUTHORITY_FIELDS = {
    "organization_external_id",
    "meeting_ref",
    "room_ref",
    "provider_binding_digest",
    "capture_ref",
    "epoch_ref",
    "media_token_binding_ref",
    "room_sid",
    "participant_sid",
    "track_sid",
    "grant_ref",
    "grant_digest",
    "session_nonce_digest",
    "participant_kind",
    "participant_ref",
    "policy_ref",
    "notice_version",
    "notice_digest",
    "consent_snapshot_digest",
    "purpose",
    "scope",
    "retention_expires_at",
    "profile_digest",
}
FIELDS = AUTHORITY_FIELDS | {
    "version",
    "type",
    "issuer",
    "audience",
    "effect_key",
    "arguments_digest",
    "issued_at",
    "expires_at",
    "jti",
    "resolve_only",
}


def arguments_digest(effect):
    """All authority predicates survive retries with a freshly signed envelope."""
    return _sha256_canonical({key: effect[key] for key in sorted(AUTHORITY_FIELDS)})


def verify_native_start(compact):
    """Require Core's explicit native purpose, exact epoch and bounded validity."""
    try:
        effect = _verify(compact, JOSE_TYPE, FIELDS)
    except (ValueError, TypeError, RecursionError) as error:
        raise RecordingContractRefused() from error
    _validate_time(effect)
    if (
        type(effect["version"]) is not int
        or effect["version"] != 1
        or effect["type"] != EFFECT_TYPE
        or not settings.MASTRAO_RECORDING_EFFECT_ISSUER
        or not settings.MASTRAO_RECORDING_EFFECT_AUDIENCE
        or effect["issuer"] != settings.MASTRAO_RECORDING_EFFECT_ISSUER
        or effect["audience"] != settings.MASTRAO_RECORDING_EFFECT_AUDIENCE
        or effect["purpose"] != "meeting_transcription_source_audio"
        or effect["scope"] != "consented_microphone_track_epoch"
        or effect["profile_digest"] != PROFILE_DIGEST
        or effect["participant_kind"] not in ("host", "guest")
        or type(effect["resolve_only"]) is not bool
        or type(effect["retention_expires_at"]) is not int
        or effect["retention_expires_at"] <= 0
        or (
            not effect["resolve_only"]
            and effect["retention_expires_at"] <= effect["expires_at"]
        )
    ):
        raise RecordingContractRefused()
    for name in (
        "meeting_ref",
        "room_ref",
        "effect_key",
        "grant_ref",
        "participant_ref",
        "policy_ref",
        "notice_version",
        "jti",
    ):
        _validate_ref(effect, name)
    organization = effect["organization_external_id"]
    if not isinstance(organization, str) or not re.fullmatch(
        r"[A-Za-z0-9._:-]{1,160}", organization
    ):
        raise RecordingContractRefused()
    for name in (
        "provider_binding_digest",
        "arguments_digest",
        "grant_digest",
        "session_nonce_digest",
        "notice_digest",
        "consent_snapshot_digest",
    ):
        if not isinstance(effect[name], str) or not DIGEST.fullmatch(effect[name]):
            raise RecordingContractRefused()
    for name in ("capture_ref", "epoch_ref", "media_token_binding_ref"):
        try:
            if str(UUID(effect[name])) != effect[name]:
                raise ValueError
        except (ValueError, TypeError, AttributeError) as error:
            raise RecordingContractRefused() from error
    for name, prefix in (
        ("room_sid", "RM"),
        ("participant_sid", "PA"),
        ("track_sid", "TR"),
    ):
        if not isinstance(effect[name], str) or not re.fullmatch(
            prefix + r"_[A-Za-z0-9]{1,96}", effect[name]
        ):
            raise RecordingContractRefused()
    if effect["arguments_digest"] != arguments_digest(effect):
        raise RecordingContractRefused()
    return effect


def sign_native_receipt(claims):
    """Sign only persisted, bounded claims; never expose provider request contents."""
    return _sign(claims, RECEIPT_JOSE_TYPE)


def require_native_receipt_signer():
    """Check acknowledgement configuration before committing permission to send."""
    if not (
        settings.MASTRAO_RECORDING_RECEIPT_ISSUER
        and settings.MASTRAO_RECORDING_RECEIPT_AUDIENCE
        and settings.MASTRAO_RECORDING_RECEIPT_KEY_ID
    ):
        raise RecordingContractRefused(status=503)
    _key("MASTRAO_RECORDING_RECEIPT_PRIVATE_JWK", private=True)
