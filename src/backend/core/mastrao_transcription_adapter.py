"""Private adapter for exact canonical Mastrao transcription effects."""

import json

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from core import models
from core.mastrao_core_http import post_core_json
from core.mastrao_recording_contract import compact_digest
from core.mastrao_transcription_artifact import (
    extract_verified_audio_file,
    load_result_recovery,
    map_speakers,
    persist_result_recovery,
    persist_transcript,
    recover_persisted_transcript,
    recovery_object_ref,
)
from core.mastrao_transcription_attempt import (
    bind_egress_grant,
    bind_terminal_provenance,
    cas_sending,
    cleanup_attempt_recovery,
    mark_pre_egress_failure,
    mark_rate_limited,
    mark_result,
    mark_terminal,
    mark_unknown,
    may_call_provider,
    may_replay_gateway,
    predeclare_object,
    prepare_attempt,
)
from core.mastrao_transcription_contract import (
    TranscriptionContractRefused,
    TranscriptionPipelineFailed,
    build_submit_receipt_claims,
    build_transcript_artifact_receipt_claims,
    build_transcription_egress_request_claims,
    build_transcription_failure_receipt_claims,
    build_transcription_terminal_receipt_claims,
    sign_submit_receipt,
    sign_transcript_artifact_receipt,
    sign_transcription_egress_request,
    sign_transcription_failure_receipt,
    sign_transcription_terminal_receipt,
    verify_transcription_egress_grant,
    verify_transcription_submit_effect,
)
from core.mastrao_transcription_pipeline import publish_transcription_job
from core.mastrao_transcription_worker import (
    _validated_transcript,
    transcribe_extracted,
)

MAX_BODY_BYTES = 32_768
SUBMITTED_OBSERVATION = "submitted"


def _safe_response(payload, status=200):
    return JsonResponse(
        payload,
        status=status,
        headers={
            "Cache-Control": "private, no-store",
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
        },
    )


def _read_effect(request):
    declared = request.headers.get("content-length")
    if (
        request.content_type != "application/json"
        or declared is None
        or not declared.isdecimal()
        or int(declared) > MAX_BODY_BYTES
        or len(request.body) > MAX_BODY_BYTES
    ):
        raise TranscriptionContractRefused()
    try:
        body = json.loads(request.body)
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as error:
        raise TranscriptionContractRefused() from error
    if not isinstance(body, dict) or set(body) != {"transcription_submit_effect"}:
        raise TranscriptionContractRefused()
    return verify_transcription_submit_effect(body["transcription_submit_effect"])


def _recording_binding(effect):
    """Bind the effect to one exact finalized, verified recording artifact."""

    binding = (
        models.MastraoRecordingBinding.objects.select_for_update(of=("self",))
        .select_related("room_binding")
        .filter(
            organization_external_id=effect["organization_external_id"],
            meeting_ref=effect["meeting_ref"],
            room_ref=effect["room_ref"],
            recording_ref=effect["recording_ref"],
            provider_binding_digest=effect["provider_binding_digest"],
            artifact_ref=effect["recording_artifact_ref"],
            state=models.MastraoRecordingBinding.State.FINALIZED,
        )
        .first()
    )
    if (
        binding is None
        or binding.object_ref is None
        or binding.byte_size is None
        or binding.checksum_digest != effect["recording_checksum_digest"]
    ):
        raise TranscriptionContractRefused()
    return binding


def _exact_effect(existing, effect):
    if (
        existing.effect_key != effect["effect_key"]
        or existing.operation != "transcribe"
        or existing.arguments_digest != effect["arguments_digest"]
        or existing.effect_jti != effect["jti"]
    ):
        raise TranscriptionContractRefused(status=409)
    return existing


@transaction.atomic
def _prepare_transcription(effect):
    recording_binding = _recording_binding(effect)
    transcription_binding = (
        models.MastraoTranscriptionBinding.objects.select_for_update(of=("self",))
        .filter(transcription_ref=effect["transcription_ref"])
        .first()
    )
    if transcription_binding:
        stored_identity = (
            transcription_binding.transcription_ref,
            transcription_binding.artifact_ref,
            transcription_binding.artifact_checksum_digest,
            transcription_binding.artifact_byte_size,
            transcription_binding.recording_binding_id,
            transcription_binding.contract_operation_version,
        )
        expected_identity = (
            effect["transcription_ref"],
            effect["recording_artifact_ref"],
            effect["recording_checksum_digest"],
            recording_binding.byte_size,
            recording_binding.pk,
            effect.get("operation_version", 1),
        )
        if stored_identity != expected_identity:
            raise TranscriptionContractRefused(status=409)
        if effect.get("operation_version", 1) in {2, 3} and any(
            getattr(transcription_binding, name) != effect[name]
            for name in (
                "asr_profile_ref",
                "asr_profile_digest",
                "asr_provider_ref",
                "requested_model_ref",
                "request_config_digest",
                "normalization_version",
                "processing_region_ref",
                "data_control_ref",
            )
        ):
            raise TranscriptionContractRefused(status=409)
        if effect.get("operation_version", 1) == 3 and any(
            getattr(transcription_binding, name) != effect[name]
            for name in (
                "campaign_ref",
                "authorized_cost_ceiling_micros",
                "currency",
                "tariff_catalog_version",
            )
        ):
            raise TranscriptionContractRefused(status=409)
    else:
        if not (
            settings.MASTRAO_MEETING_RECORDING_ENABLED
            and settings.MASTRAO_MEETING_TRANSCRIPTION_ENABLED
        ):
            raise TranscriptionContractRefused()
        transcription_binding = models.MastraoTranscriptionBinding.objects.create(
            recording_binding=recording_binding,
            organization_external_id=effect["organization_external_id"],
            meeting_ref=effect["meeting_ref"],
            room_ref=effect["room_ref"],
            recording_ref=effect["recording_ref"],
            transcription_ref=effect["transcription_ref"],
            artifact_ref=effect["recording_artifact_ref"],
            provider_binding_digest=effect["provider_binding_digest"],
            contract_operation_version=effect.get("operation_version", 1),
            asr_profile_ref=effect.get("asr_profile_ref"),
            asr_profile_digest=effect.get("asr_profile_digest"),
            asr_provider_ref=effect.get("asr_provider_ref"),
            requested_model_ref=effect.get("requested_model_ref"),
            request_config_digest=effect.get("request_config_digest"),
            normalization_version=effect.get("normalization_version"),
            processing_region_ref=effect.get("processing_region_ref"),
            data_control_ref=effect.get("data_control_ref"),
            campaign_ref=effect.get("campaign_ref"),
            authorized_cost_ceiling_micros=effect.get("authorized_cost_ceiling_micros"),
            currency=effect.get("currency"),
            tariff_catalog_version=effect.get("tariff_catalog_version"),
            artifact_checksum_digest=effect["recording_checksum_digest"],
            artifact_byte_size=recording_binding.byte_size,
        )
    existing = (
        models.MastraoTranscriptionEffect.objects.select_for_update()
        .filter(transcription_binding=transcription_binding, operation="transcribe")
        .first()
    )
    if existing:
        return transcription_binding, _exact_effect(existing, effect)
    if effect["resolve_only"]:
        raise TranscriptionContractRefused()
    created = models.MastraoTranscriptionEffect.objects.create(
        transcription_binding=transcription_binding,
        effect_key=effect["effect_key"],
        arguments_digest=effect["arguments_digest"],
        effect_jti=effect["jti"],
        state=models.MastraoTranscriptionEffect.State.APPLYING,
    )
    transcription_binding.state = models.MastraoTranscriptionBinding.State.PROCESSING
    transcription_binding.save(update_fields=["state", "updated_at"])
    return transcription_binding, created


def _assert_transcription_authority(transcription_binding):
    """Re-read recording and transcription authority before egress or commit."""

    with transaction.atomic():
        recording = models.MastraoRecordingBinding.objects.select_for_update().get(
            pk=transcription_binding.recording_binding_id
        )
        locked = models.MastraoTranscriptionBinding.objects.select_for_update().get(
            pk=transcription_binding.pk
        )
        if recording.state != models.MastraoRecordingBinding.State.FINALIZED:
            raise TranscriptionContractRefused(status=404, outcome="deleted")
        if locked.state == models.MastraoTranscriptionBinding.State.FAILED:
            raise TranscriptionContractRefused(status=409, outcome="conflict")
        if not (
            settings.MASTRAO_MEETING_RECORDING_ENABLED
            and settings.MASTRAO_MEETING_TRANSCRIPTION_ENABLED
        ):
            raise TranscriptionContractRefused(status=404, outcome="deleted")
        return locked


def _bind_gateway_result(sending, transcript, usage=None):
    """Persist the recovery object and bind its checksum in one attempt write."""
    recovery_ref, _checksum = persist_result_recovery(sending.attempt_ref, transcript)
    return mark_result(sending, transcript, usage, recovery_ref=recovery_ref)


def _produce_transcript(transcription_binding):
    """Extract, transcribe and persist outside any database transaction."""

    recording_binding = transcription_binding.recording_binding
    local_effect = transcription_binding.effects.filter(operation="transcribe").get()
    try:
        extracted = extract_verified_audio_file(
            recording_binding.object_ref,
            recording_binding.byte_size,
            recording_binding.checksum_digest,
        )
    except TranscriptionContractRefused as error:
        raise TranscriptionPipelineFailed("audio_extraction_failed") from error
    try:
        _assert_transcription_authority(transcription_binding)
        attempt = prepare_attempt(local_effect, extracted)
        recovered = _resume_produced_artifact(transcription_binding, attempt)
        if recovered is not None:
            return recovered
        transcript = _resume_or_transcribe(extracted, attempt, transcription_binding)
        _assert_transcription_authority(transcription_binding)
        transcript = map_speakers(transcript)
        object_ref = predeclare_object(attempt, transcription_binding.transcription_ref)
        if not transcription_binding.object_ref:
            transcription_binding.object_ref = object_ref
            transcription_binding.save(update_fields=["object_ref", "updated_at"])
        return persist_transcript(
            transcription_binding.transcription_ref,
            transcript,
            object_ref=object_ref,
        )
    except TranscriptionContractRefused as error:
        if error.outcome in {"deleted", "conflict"}:
            _discard_late_result(local_effect, error.outcome)
        raise
    finally:
        extracted.close()


def _accepted_recovery_transcript(transcript, extracted, attempt):
    """Accept a durable recovery only when it matches this attempt's audio and engine."""

    if not transcript:
        return None
    try:
        validated = _validated_transcript(transcript)
    except TranscriptionContractRefused:
        return None
    if validated.get("audio_digest") != extracted.sha256:
        return None
    engine = validated.get("engine_ref")
    expected = (
        attempt.requested_model_ref
        if attempt.provider_ref == "fake"
        else f"{attempt.provider_ref}:{attempt.requested_model_ref}"
    )
    if engine != expected:
        return None
    return validated


def _resume_or_transcribe(  # noqa: PLR0912  # pylint: disable=too-many-branches
    extracted, sending, transcription_binding
):
    """Resume a durable result or open one Gateway call for this attempt."""
    if sending.result_checksum:
        recovery_ref = sending.result_recovery_ref or recovery_object_ref(
            sending.attempt_ref
        )
        transcript = _accepted_recovery_transcript(
            load_result_recovery(recovery_ref, sending.result_checksum),
            extracted,
            sending,
        )
        if not transcript:
            raise TranscriptionPipelineFailed("asr_failed", status=409)
        return transcript
    discovered = load_result_recovery(recovery_object_ref(sending.attempt_ref))
    if discovered is not None:
        transcript = _accepted_recovery_transcript(discovered, extracted, sending)
        if not transcript:
            raise TranscriptionPipelineFailed("asr_failed", status=409)
        mark_result(
            sending,
            transcript,
            recovery_ref=recovery_object_ref(sending.attempt_ref),
        )
        return transcript
    if may_replay_gateway(sending):
        grant = _authorize_egress(
            transcription_binding, sending, execution_mode="recover_only"
        )
        return _replay_gateway_result(extracted, sending, grant)
    if not may_call_provider(sending):
        mark_unknown(sending)
        raise TranscriptionPipelineFailed("asr_failed", status=409)
    _assert_transcription_authority(transcription_binding)
    grant = _authorize_egress(
        transcription_binding, sending, execution_mode="send_allowed"
    )
    _assert_transcription_authority(transcription_binding)
    sending = cas_sending(sending)
    try:
        transcript = transcribe_extracted(extracted, sending, egress_grant=grant)
    except TranscriptionContractRefused as error:
        if error.error_code == "ATTEMPT_IN_PROGRESS":
            raise
        if error.outcome == "unknown" or error.status == 409:
            sending = bind_terminal_provenance(sending, error.provenance)
            if error.outcome == "unknown" and not error.provenance:
                mark_unknown(sending)
                raise TranscriptionContractRefused(
                    status=503, outcome="retry"
                ) from error
            mark_terminal(sending, "unknown")
            raise TranscriptionContractRefused(status=409, outcome="unknown") from error
        if error.outcome == "retry" and error.provenance:
            # A completed 429 is a known provider refusal, not unknown egress.
            sending = bind_terminal_provenance(sending, error.provenance)
            mark_rate_limited(sending)
            raise
        if error.outcome == "rejected":
            sending = bind_terminal_provenance(sending, error.provenance)
            mark_terminal(sending, "rejected")
            raise
        sending = bind_terminal_provenance(sending, error.provenance)
        mark_pre_egress_failure(sending)
        if error.outcome in {"retry", "failed_pre_egress"}:
            raise
        raise TranscriptionContractRefused(
            status=503, outcome="failed_pre_egress"
        ) from error
    usage = transcript.pop("_usage", None)
    _bind_gateway_result(sending, transcript, usage)
    return transcript


def _discard_late_result(local_effect, outcome):
    """Drop a late recovery copy after authority is revoked or conflicts."""
    attempt = (
        models.MastraoTranscriptionProviderAttempt.objects.filter(
            effect=local_effect, generation=1
        )
        .order_by("created_at")
        .first()
    )
    if attempt is None:
        return
    if attempt.grant_semantic_digest:
        mark_terminal(attempt, outcome)
    cleanup_attempt_recovery(attempt)


def _replay_gateway_result(extracted, sending, egress_grant):
    """Replay one Gateway attempt after a paid sending crash, never a second send."""

    try:
        transcript = transcribe_extracted(extracted, sending, egress_grant=egress_grant)
    except TranscriptionContractRefused as error:
        if error.error_code == "ATTEMPT_IN_PROGRESS":
            raise
        if error.outcome == "unknown" or error.status == 409:
            sending = bind_terminal_provenance(sending, error.provenance)
            mark_terminal(sending, "unknown")
            raise TranscriptionContractRefused(status=409, outcome="unknown") from error
        if error.outcome == "retry" and error.provenance:
            sending = bind_terminal_provenance(sending, error.provenance)
            mark_rate_limited(sending)
            raise
        if error.outcome == "rejected":
            sending = bind_terminal_provenance(sending, error.provenance)
            mark_terminal(sending, "rejected")
        raise
    usage = transcript.pop("_usage", None)
    _bind_gateway_result(sending, transcript, usage)
    return transcript


def _authorize_egress(transcription_binding, attempt, execution_mode):
    """Obtain and durably bind Core's exact fresh authorization."""

    if transcription_binding.contract_operation_version < 3:
        return None
    local_effect = attempt.effect
    effect = _effect_from_local(transcription_binding, local_effect)
    claims = build_transcription_egress_request_claims(effect, attempt, execution_mode)
    try:
        result = post_core_json(
            endpoint=settings.MASTRAO_CORE_TRANSCRIPTION_EGRESS_ENDPOINT,
            expected_path="/internal/v1/meetings/transcription/egress/authorize",
            body={
                "transcription_egress_request": sign_transcription_egress_request(
                    claims
                )
            },
            timeout=settings.MASTRAO_CORE_RECORDING_TIMEOUT_SECONDS,
            refusal=TranscriptionContractRefused,
            expected_fields={"transcription_egress_grant"},
            passthrough_statuses=frozenset({404, 409, 503}),
        )
    except TranscriptionContractRefused as error:
        if error.outcome == "failed":
            mark_pre_egress_failure(attempt, "egress_refused")
        raise
    compact_grant = result["transcription_egress_grant"]
    grant = verify_transcription_egress_grant(compact_grant, claims)
    bind_egress_grant(attempt, grant)
    # The caller keeps using this instance to validate Gateway provenance and
    # build the v2 receipt. Refresh the immutable grant binding that was
    # decided under the row lock instead of leaving a stale pre-grant object.
    attempt.refresh_from_db(
        fields=[
            "grant_semantic_digest",
            "authority_version",
            "execution_mode",
            "campaign_ref",
            "authorized_cost_ceiling_micros",
            "tariff_catalog_version",
        ]
    )
    return compact_grant


def _resume_produced_artifact(transcription_binding, attempt):
    object_ref = transcription_binding.object_ref or attempt.transcript_object_ref
    if not object_ref:
        return None
    engine_ref = attempt.resolved_model_ref or attempt.requested_model_ref
    return recover_persisted_transcript(
        object_ref,
        transcription_binding.transcription_ref,
        engine_ref,
    )


def _artifact_from_binding(binding):
    """Rebuild the exact persisted artifact facts for idempotent replays."""

    return {
        "transcript_artifact_ref": binding.transcript_artifact_ref,
        "object_ref": binding.object_ref,
        "byte_size": binding.byte_size,
        "checksum_digest": binding.checksum_digest,
        "segment_count": binding.segment_count,
        "engine_ref": binding.engine_ref,
    }


def _effect_from_local(transcription_binding, local_effect):
    recording_binding = transcription_binding.recording_binding
    effect = {
        "organization_external_id": transcription_binding.organization_external_id,
        "meeting_ref": transcription_binding.meeting_ref,
        "room_ref": transcription_binding.room_ref,
        "recording_ref": transcription_binding.recording_ref,
        "transcription_ref": transcription_binding.transcription_ref,
        "recording_artifact_ref": transcription_binding.artifact_ref,
        "provider_binding_digest": transcription_binding.provider_binding_digest,
        "recording_checksum_digest": transcription_binding.artifact_checksum_digest,
        "retention_expires_at": int(recording_binding.retention_expires_at.timestamp()),
        "effect_key": local_effect.effect_key,
        "arguments_digest": local_effect.arguments_digest,
        "resolve_only": False,
        "jti": local_effect.effect_jti,
        "operation_version": transcription_binding.contract_operation_version,
    }
    if transcription_binding.contract_operation_version >= 2:
        effect.update(
            {
                name: getattr(transcription_binding, name)
                for name in (
                    "asr_profile_ref",
                    "asr_profile_digest",
                    "asr_provider_ref",
                    "requested_model_ref",
                    "request_config_digest",
                    "normalization_version",
                    "processing_region_ref",
                    "data_control_ref",
                )
            }
        )
    if transcription_binding.contract_operation_version == 3:
        effect.update(
            {
                name: getattr(transcription_binding, name)
                for name in (
                    "campaign_ref",
                    "authorized_cost_ceiling_micros",
                    "currency",
                    "tariff_catalog_version",
                )
            }
        )
    return effect


def _notify_core_artifact(effect, artifact):
    """Report one exact persisted transcript artifact to Core."""

    attempt = None
    if effect.get("operation_version") == 3:
        attempt = (
            models.MastraoTranscriptionProviderAttempt.objects.filter(
                effect__effect_key=effect["effect_key"], generation=1
            )
            .order_by("created_at")
            .first()
        )
    claims = build_transcript_artifact_receipt_claims(effect, artifact, attempt)
    result = post_core_json(
        endpoint=settings.MASTRAO_CORE_TRANSCRIPTION_ARTIFACT_ENDPOINT,
        expected_path="/internal/v1/meetings/transcription/artifacts/finalize",
        body={
            "transcription_artifact_receipt": sign_transcript_artifact_receipt(claims)
        },
        timeout=settings.MASTRAO_CORE_RECORDING_TIMEOUT_SECONDS,
        refusal=TranscriptionContractRefused,
        expected_fields={"artifactRef", "outcome"},
        passthrough_statuses=frozenset({404, 409, 503}),
    )
    if (
        result["artifactRef"] != claims["artifact_ref"]
        or result["outcome"] != "available"
    ):
        raise TranscriptionContractRefused(status=503, outcome="retry")


def _notify_core_failure(effect, failure_code):
    """Report one pipeline failure to Core. A 503 must be retried."""

    if effect.get("operation_version") == 3:
        attempt = (
            models.MastraoTranscriptionProviderAttempt.objects.filter(
                effect__effect_key=effect["effect_key"], generation=1
            )
            .order_by("created_at")
            .first()
        )
        if (
            attempt is not None
            and not attempt.grant_semantic_digest
            and attempt.last_safe_error_code == "egress_refused"
        ):
            return {"state": "failed", "outcome": "failed"}
        if attempt is not None and not attempt.grant_semantic_digest:
            raise TranscriptionContractRefused(status=409, outcome="conflict")
        if attempt is not None and attempt.grant_semantic_digest:
            outcome = attempt.terminal_outcome
            if not outcome and attempt.state == attempt.State.FAILED_PRE_EGRESS:
                outcome = "failed_pre_egress"
                attempt = mark_terminal(attempt, outcome)
            if outcome:
                claims = build_transcription_terminal_receipt_claims(
                    effect, attempt, outcome
                )
                result = post_core_json(
                    endpoint=settings.MASTRAO_CORE_TRANSCRIPTION_TERMINAL_ENDPOINT,
                    expected_path="/internal/v1/meetings/transcription/terminal",
                    body={
                        "transcription_terminal_receipt": sign_transcription_terminal_receipt(
                            claims
                        )
                    },
                    timeout=settings.MASTRAO_CORE_RECORDING_TIMEOUT_SECONDS,
                    refusal=TranscriptionContractRefused,
                    expected_fields={"transcriptionRef", "state", "outcome"},
                    passthrough_statuses=frozenset({404, 409, 503}),
                )
                if result["outcome"] == "available" and result["state"] == "available":
                    return result
                if result["outcome"] != "failed" or result["state"] != "failed":
                    raise TranscriptionContractRefused(status=503, outcome="retry")
                return result
    claims = build_transcription_failure_receipt_claims(effect, failure_code)
    result = post_core_json(
        endpoint=settings.MASTRAO_CORE_TRANSCRIPTION_FAILURE_ENDPOINT,
        expected_path="/internal/v1/meetings/transcription/failures",
        body={
            "transcription_failure_receipt": sign_transcription_failure_receipt(claims)
        },
        timeout=settings.MASTRAO_CORE_RECORDING_TIMEOUT_SECONDS,
        refusal=TranscriptionContractRefused,
        expected_fields={"transcriptionRef", "state", "outcome"},
        passthrough_statuses=frozenset({404, 409, 503}),
    )
    if result["outcome"] == "available" and result["state"] == "available":
        return result
    if result["outcome"] != "failed" or result["state"] != "failed":
        raise TranscriptionContractRefused(status=503, outcome="retry")
    return result


def _persist_submit_receipt(local_effect, effect):
    if local_effect.receipt_claims:
        return local_effect.receipt_claims
    claims = build_submit_receipt_claims(effect, SUBMITTED_OBSERVATION)
    local_effect.receipt_claims = claims
    local_effect.receipt_digest = compact_digest(sign_submit_receipt(claims))
    local_effect.provider_observation = SUBMITTED_OBSERVATION
    local_effect.save(
        update_fields=[
            "receipt_claims",
            "receipt_digest",
            "provider_observation",
            "updated_at",
        ]
    )
    return claims


def _apply_transcription(effect):
    """Reserve the effect, return the submitted receipt, publish Celery."""

    _transcription_binding, local_effect = _prepare_transcription(effect)
    if local_effect.state == models.MastraoTranscriptionEffect.State.FAILED:
        raise TranscriptionContractRefused(status=409)
    claims = _persist_submit_receipt(local_effect, effect)
    if (
        local_effect.dispatch_state
        != models.MastraoTranscriptionEffect.DispatchState.COMPLETED
    ):
        publish_transcription_job(local_effect.pk)
    return sign_submit_receipt(claims)


@csrf_exempt
@require_POST
def transcribe_mastrao_recording(request):
    """Claim one exact signed submit effect and return a signed submit receipt."""

    try:
        effect = _read_effect(request)
        return _safe_response(
            {"transcription_submit_receipt": _apply_transcription(effect)}
        )
    except TranscriptionContractRefused as error:
        return _safe_response(
            {"message": "Not found" if error.status == 404 else "Unavailable"},
            error.status,
        )
    except (IntegrityError, ValidationError):
        return _safe_response({"message": "Unavailable"}, 409)
