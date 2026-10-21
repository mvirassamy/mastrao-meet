"""Forward live consent to Core without retaining choices or authorizing media."""

# Strict wire schemas exclude bool integers and keep exact binding guards together.
# pylint: disable=too-many-boolean-expressions,unidiomatic-typecheck

import re
import time
from uuid import uuid4

from django.conf import settings

from core.mastrao_core_http import post_core_json
from core.mastrao_guest_contract import GUEST_BOOTSTRAP_TYPE
from core.mastrao_guest_grant import active_guest_grant, active_guest_lifecycle_grant
from core.mastrao_host_contract import EXTERNAL_ID, HOST_GRANT_TYPE
from core.mastrao_host_grant import active_host_close_grant, active_host_grant
from core.mastrao_recording_contract import (
    RecordingContractRefused,
    _sign,
    compact_digest,
)
from core.mastrao_recording_session import _participant
from core.mastrao_room_contract import DIGEST, OPAQUE_REFERENCE

CONSENT_PURPOSE = "meeting_live_transcription"
CONSENT_SCOPE = "live_microphone_audio_transcript"
NOTICE_VERSION = "live_microphone_fr_v1"
NOTICE_TEXT = (
    "Votre microphone sera transmis à OpenAI, avec le modèle gpt-live-transcribe, "
    "pour transcrire en direct votre piste audio et afficher les sous-titres. "
    "Les segments de transcription définitifs seront conservés dans le contexte "
    "de la réunion jusqu’à l’échéance indiquée et pourront être réutilisés pour "
    "produire sa synthèse. Ce choix n’autorise ni l’enregistrement vidéo ni la "
    "transcription différée par lot. Vous pouvez refuser ou retirer votre "
    "consentement ; aucun nouvel audio ne sera alors envoyé pour cette "
    "transcription en direct."
)
NOTICE_DIGEST = "19a76d62a39b5c4c3211d17720908bf474fe3080eec010ea0c0470db8c40d249"
OFFER_PATH = "/internal/v1/meetings/transcripts/live/consent/offer"
DECISION_PATH = "/internal/v1/meetings/transcripts/live/consent/decision"
OFFER_JOSE = "mastrao-live-transcript-consent-offer+jws"
DECISION_JOSE = "mastrao-live-transcript-consent-decision+jws"
OFFER_TYPE = "mastrao.meet-live-transcript-consent-offer"
DECISION_TYPE = "mastrao.meet-live-transcript-consent-decision"
MAX_SAFE_INTEGER = 9_007_199_254_740_991
COMPACT_JWS = re.compile(r"[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+")
SNAPSHOT_FIELDS = {
    "consent_ref",
    "policy_ref",
    "notice_version",
    "notice_digest",
    "purpose",
    "scope",
    "retention_expires_at",
}
OFFER_FIELDS = SNAPSHOT_FIELDS | {
    "version",
    "organization_external_id",
    "meeting_ref",
    "room_ref",
    "text",
    "decision",
    "capture_authorized",
}


def _matches(pattern, value):
    return isinstance(value, str) and pattern.fullmatch(value) is not None


def _future_retention(value):
    return type(value) is int and int(time.time()) < value <= MAX_SAFE_INTEGER


def _local_grant(request, room, kind, *, include_closed):
    if kind == "host":
        if include_closed:
            return active_host_close_grant(request, room)
        return active_host_grant(request, room)
    if kind == "guest":
        if include_closed:
            return active_guest_lifecycle_grant(request, room)
        return active_guest_grant(request, room)
    raise RecordingContractRefused()


def _validate_room_binding(claims, room_binding, grant):
    for name in ("meeting_ref", "room_ref", "provider_binding_digest"):
        value = claims.get(name)
        if value != getattr(room_binding, name) or value != getattr(grant, name):
            raise RecordingContractRefused()


def _consent_participant(request, room, *, include_closed=False):
    """Bind the verified bearer to the exact local grant and room projection."""
    room_binding = getattr(room, "mastrao_binding", None)
    if room_binding is None:
        raise RecordingContractRefused()
    if include_closed:
        participant = _participant(request, room, include_closed=True)
    else:
        participant = _participant(request, room)
    claims = participant["claims"]
    kind = participant["kind"]
    grant = _local_grant(request, room, kind, include_closed=include_closed)
    if grant is None:
        raise RecordingContractRefused()
    if kind == "host":
        claim_type = HOST_GRANT_TYPE
        ref_field = "host_ref"
        local_ref = grant.identity.host_ref
    else:
        claim_type = GUEST_BOOTSTRAP_TYPE
        ref_field = "guest_ref"
        local_ref = grant.guest_ref

    compact = participant["compact"]
    if not _matches(COMPACT_JWS, compact):
        raise RecordingContractRefused()
    digest = compact_digest(compact)
    if (
        claims.get("type") != claim_type
        or claims.get(ref_field) != participant["ref"]
        or participant["ref"] != local_ref
        or participant["session_digest"] != grant.session_nonce_digest
        or claims.get("grant_ref") != grant.grant_ref
        or digest != grant.grant_digest
        or grant.room_binding_id != room_binding.pk
    ):
        raise RecordingContractRefused()
    _validate_room_binding(claims, room_binding, grant)
    if kind == "host" and (
        claims.get("platform_session_ref") != grant.platform_session_ref
    ):
        raise RecordingContractRefused()
    if kind == "guest" and (
        claims.get("organization_external_id") != grant.organization_external_id
    ):
        raise RecordingContractRefused()
    expires_at = int(grant.expires_at.timestamp())
    if (
        claims.get("credential_digest") != grant.credential_digest
        or claims.get("expires_at") != expires_at
    ):
        raise RecordingContractRefused()

    binding = {
        name: claims.get(name)
        for name in (
            "organization_external_id",
            "meeting_ref",
            "room_ref",
            "provider_binding_digest",
            "grant_ref",
        )
    }
    binding.update(
        participant_kind=kind,
        participant_ref=participant["ref"],
        grant_digest=digest,
        participant_session_digest=participant["session_digest"],
    )
    if (
        not _matches(EXTERNAL_ID, binding["organization_external_id"])
        or any(
            not _matches(OPAQUE_REFERENCE, binding[name])
            for name in ("meeting_ref", "room_ref", "grant_ref", "participant_ref")
        )
        or any(
            not _matches(DIGEST, binding[name])
            for name in (
                "provider_binding_digest",
                "grant_digest",
                "participant_session_digest",
            )
        )
    ):
        raise RecordingContractRefused()
    return participant, binding


def _envelope(assertion_type):
    now = int(time.time())
    issuer = settings.MASTRAO_RECORDING_RECEIPT_ISSUER
    audience = settings.MASTRAO_RECORDING_RECEIPT_AUDIENCE
    if not _matches(EXTERNAL_ID, issuer) or not _matches(EXTERNAL_ID, audience):
        raise RecordingContractRefused(status=503)
    return {
        "version": 1,
        "type": assertion_type,
        "issuer": issuer,
        "audience": audience,
        "issued_at": now,
        "expires_at": now + 30,
        "jti": f"liveconsent_{uuid4().hex}",
    }


def _validate_offer(result, binding):
    """Accept only the canonical flat notice; consent never enables capture."""
    if not isinstance(result, dict) or set(result) != OFFER_FIELDS:
        raise RecordingContractRefused(status=503)
    if (
        type(result["version"]) is not int
        or result["version"] != 1
        or result["capture_authorized"] is not False
        or result["notice_version"] != NOTICE_VERSION
        or result["notice_digest"] != NOTICE_DIGEST
        or result["text"] != NOTICE_TEXT
        or result["purpose"] != CONSENT_PURPOSE
        or result["scope"] != CONSENT_SCOPE
        or not isinstance(result["decision"], str)
        or result["decision"] not in {"absent", "accepted", "refused", "withdrawn"}
        or not _future_retention(result["retention_expires_at"])
        or any(
            result[name] != binding[name]
            for name in ("organization_external_id", "meeting_ref", "room_ref")
        )
        or any(
            not _matches(OPAQUE_REFERENCE, result[name])
            for name in ("consent_ref", "policy_ref")
        )
    ):
        raise RecordingContractRefused(status=503)
    return result


def offer_live_consent(request, room):
    """Read Core's live notice for this participant, without storing the offer."""
    participant, binding = _consent_participant(request, room)
    assertion = {**binding, **_envelope(OFFER_TYPE)}
    result = post_core_json(
        endpoint=settings.MASTRAO_CORE_LIVE_CONSENT_OFFER_ENDPOINT,
        expected_path=OFFER_PATH,
        body={
            "request": _sign(assertion, OFFER_JOSE),
            "participant_grant": participant["compact"],
        },
        timeout=settings.MASTRAO_CORE_RECORDING_TIMEOUT_SECONDS,
        refusal=RecordingContractRefused,
        passthrough_statuses={409},
    )
    return _validate_offer(result, binding)


def record_live_consent(  # noqa: PLR0913  # pylint: disable=too-many-arguments
    request,
    room,
    *,
    decision_ref,
    decision,
    consent_ref,
    policy_ref,
    notice_version,
    notice_digest,
    retention_expires_at,
):
    """Sign the displayed snapshot unchanged; Core owns decisions and conflicts."""
    if (
        not isinstance(decision, str)
        or decision not in {"accepted", "refused", "withdrawn"}
        or any(
            not _matches(OPAQUE_REFERENCE, value)
            for value in (decision_ref, consent_ref, policy_ref)
        )
        or notice_version != NOTICE_VERSION
        or notice_digest != NOTICE_DIGEST
        or not _future_retention(retention_expires_at)
    ):
        raise RecordingContractRefused()
    snapshot = {
        "consent_ref": consent_ref,
        "policy_ref": policy_ref,
        "notice_version": notice_version,
        "notice_digest": notice_digest,
        "purpose": CONSENT_PURPOSE,
        "scope": CONSENT_SCOPE,
        "retention_expires_at": retention_expires_at,
    }
    participant, binding = _consent_participant(
        request, room, include_closed=decision == "withdrawn"
    )
    assertion = {
        **binding,
        **_envelope(DECISION_TYPE),
        **snapshot,
        "decision_ref": decision_ref,
        "decision": decision,
    }
    result = post_core_json(
        endpoint=settings.MASTRAO_CORE_LIVE_CONSENT_DECISION_ENDPOINT,
        expected_path=DECISION_PATH,
        body={
            "request": _sign(assertion, DECISION_JOSE),
            "participant_grant": participant["compact"],
        },
        timeout=settings.MASTRAO_CORE_RECORDING_TIMEOUT_SECONDS,
        refusal=RecordingContractRefused,
        passthrough_statuses={409},
    )
    _validate_offer(result, binding)
    if any(result[name] != snapshot[name] for name in SNAPSHOT_FIELDS):
        raise RecordingContractRefused(status=503)
    return result
