"""Pure signed-contract regressions; runnable without PostgreSQL or provider peers."""

import base64
import copy
import hashlib
import json
import time
import unittest
from types import SimpleNamespace

from django.conf import settings
from django.test import override_settings

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core.mastrao_native_capture_contract import (
    EFFECT_TYPE as NATIVE_TYPE,
    JOSE_TYPE as NATIVE_JOSE,
    PROFILE_DIGEST,
    arguments_digest,
    verify_native_start,
)
from core.mastrao_recording_contract import (
    START_EFFECT_JOSE_TYPE,
    START_EFFECT_TYPE,
    RecordingContractRefused,
    verify_recording_start_effect,
)
from core.mastrao_speaker_evidence_contract import (
    CAPTURE_EFFECT_JOSE_TYPE,
    CAPTURE_EFFECT_TYPE,
    verify_speaker_evidence_capture_effect,
)
from core.mastrao_transcription_contract import (
    MANAGED_PROFILE_BINDINGS,
    NORMAL_ASR_PROFILE_REF,
    NORMAL_AUTHORITY_FIELDS,
    SUBMIT_EFFECT_JOSE_TYPE,
    SUBMIT_EFFECT_TYPE,
    TranscriptionContractRefused,
    _submit_arguments,
    build_transcript_artifact_receipt_claims,
    build_transcription_egress_request_claims,
    verify_transcription_submit_effect,
)


def _canonical(value):
    return (
        json.dumps(
            value, sort_keys=True, separators=(",", ":"),
            ensure_ascii=False, allow_nan=False,
        ).encode() + b"\n"
    )

def _digest(value):
    return hashlib.sha256(_canonical(value)).hexdigest()

def _b64(value):
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()

def _signer(settings):
    key = Ed25519PrivateKey.generate()
    settings.MASTRAO_RECORDING_EFFECT_KEY_ID = "persistent-test-key"
    settings.MASTRAO_RECORDING_EFFECT_PUBLIC_JWK = json.dumps(
        {"kty": "OKP", "crv": "Ed25519", "x": _b64(key.public_key().public_bytes_raw())}
    )
    settings.MASTRAO_RECORDING_EFFECT_ISSUER = "core-persistent-test"
    settings.MASTRAO_RECORDING_EFFECT_AUDIENCE = "meet-persistent-test"
    settings.MASTRAO_RECORDING_RECEIPT_ISSUER = "meet-persistent-test"
    settings.MASTRAO_RECORDING_RECEIPT_AUDIENCE = "core-persistent-test"

    def sign(payload, kind):
        header = _b64(_canonical({"alg": "EdDSA", "kid": "persistent-test-key", "typ": kind}))
        body = _b64(_canonical(payload))
        signed = f"{header}.{body}"
        return f"{signed}.{_b64(key.sign(signed.encode()))}"

    return sign

def _recording(persistent):
    now = int(time.time())
    payload = {
        "version": 1,
        "type": START_EFFECT_TYPE,
        "issuer": "core-persistent-test",
        "audience": "meet-persistent-test",
        "operation": "start_room_composite_recording",
        "operation_version": 1,
        "organization_external_id": "organization_persistent_test",
        "meeting_ref": "meeting_persistent_test",
        "room_ref": "room_persistent_test",
        "recording_ref": "recording_persistent_test",
        "provider_binding_digest": "a" * 64,
        "effect_key": "effect_persistent_test",
        "policy_ref": "policy_persistent_test",
        "notice_version": "notice_persistent_test",
        "notice_digest": "b" * 64,
        "purpose": "meeting_recording",
        "scope": "room_composite_audio_video_screen",
        "retention_expires_at": now + 3600,
        "resolve_only": False,
        "claim_id": "claim_persistent_test",
        "issued_at": now,
        "expires_at": now + 30,
        "jti": "request_persistent_test",
    }
    arguments = {
        "version": 1,
        "operation": "start",
        "recording_ref": payload["recording_ref"],
        "meeting_ref": payload["meeting_ref"],
        "room_ref": payload["room_ref"],
        "provider_binding_digest": payload["provider_binding_digest"],
    }
    if persistent:
        payload.update(operation_version=2, retention_mode="until_deleted", retention_expires_at=None)
        arguments.update(operation_version=2, retention_mode="until_deleted", retention_expires_at=None)
    payload["arguments_digest"] = _digest(arguments)
    return payload

def _asr():
    recording = _recording(True)
    payload = {
        key: value for key, value in recording.items()
        if key not in {"claim_id", "arguments_digest"}
    }
    payload.update(
        type=SUBMIT_EFFECT_TYPE,
        operation="submit_meeting_transcription",
        operation_version=5,
        purpose="meeting_transcription",
        scope="recording_artifact_audio_transcript",
        transcription_ref="transcription_persistent_test",
        recording_artifact_ref="artifact_persistent_test",
        recording_checksum_digest="c" * 64,
        asr_profile_ref=NORMAL_ASR_PROFILE_REF,
        **MANAGED_PROFILE_BINDINGS[NORMAL_ASR_PROFILE_REF],
        authority_version=1,
        authorized_cost_ceiling_micros=198000,
        currency="USD",
        normalization_version="meeting-transcript-v1",
        maximum_audio_seconds=3600,
        maximum_audio_bytes=25000000,
    )
    arguments = {
        "version": 1,
        "operation": "submit",
        **{key: payload[key] for key in (
            "transcription_ref", "recording_ref", "meeting_ref", "room_ref",
            "provider_binding_digest", "asr_profile_ref", "asr_profile_digest",
            "asr_provider_ref", "requested_model_ref", "request_config_digest",
            "normalization_version", "processing_region_ref", "data_control_ref",
            *NORMAL_AUTHORITY_FIELDS,
        )},
        "operation_version": 5,
        "retention_mode": "until_deleted",
        "retention_expires_at": None,
    }
    payload["arguments_digest"] = _digest(arguments)
    return payload

def _native():
    now = int(time.time())
    payload = {
        "version": 2, "type": NATIVE_TYPE,
        "issuer": "core-persistent-test", "audience": "meet-persistent-test",
        "organization_external_id": "organization_persistent_test",
        "meeting_ref": "meeting_persistent_test", "room_ref": "room_persistent_test",
        "provider_binding_digest": "a" * 64, "effect_key": "effect_persistent_test",
        "capture_ref": "00000000-0000-4000-8000-000000000001",
        "epoch_ref": "00000000-0000-4000-8000-000000000002",
        "media_token_binding_ref": "00000000-0000-4000-8000-000000000003",
        "room_sid": "RM_persistent", "participant_sid": "PA_persistent",
        "track_sid": "TR_persistent", "grant_ref": "grant_persistent_test",
        "grant_digest": "b" * 64, "session_nonce_digest": "c" * 64,
        "participant_kind": "host", "participant_ref": "participant_persistent_test",
        "policy_ref": "policy_persistent_test", "policy_version": "policy_version_test",
        "policy_digest": "d" * 64, "policy_authorization_digest": "e" * 64,
        "purpose": "meeting_transcription_source_audio",
        "scope": "authorized_microphone_track_epoch",
        "retention_mode": "until_deleted", "retention_expires_at": None,
        "profile_digest": PROFILE_DIGEST,
        "issued_at": now, "expires_at": now + 30,
        "jti": "claim_persistent_test", "resolve_only": False,
    }
    payload["arguments_digest"] = arguments_digest(payload)
    return payload


class PersistentContractTests(unittest.TestCase):
    """Real Ed25519 signatures and independent authority dictionaries."""

    def setUp(self):
        names = (
            "EFFECT_KEY_ID", "EFFECT_PUBLIC_JWK", "EFFECT_ISSUER", "EFFECT_AUDIENCE",
            "RECEIPT_ISSUER", "RECEIPT_AUDIENCE", "STORAGE_BINDING_DIGEST",
            "REGION_REF", "ENCRYPTION_REF", "LIFECYCLE_POLICY_REF",
        )
        configured = {f"MASTRAO_RECORDING_{name}": "test" for name in names}
        self.overrides = override_settings(**configured)
        self.overrides.enable()
        self.addCleanup(self.overrides.disable)
        self.signer = _signer(settings)

    def test_recording_variants_preserve_exact_signed_claims(self):
        for persistent in (False, True):
            with self.subTest(persistent=persistent):
                payload = _recording(persistent)
                before = copy.deepcopy(payload)
                compact = self.signer(payload, START_EFFECT_JOSE_TYPE)
                self.assertEqual(verify_recording_start_effect(compact), before)
                self.assertEqual(payload, before)
                self.assertEqual(self.signer(payload, START_EFFECT_JOSE_TYPE), compact)

    def test_recording_persistent_schema_and_authority_are_closed(self):
        changes = (
            {"retention_mode": "bounded"}, {"retention_mode": "unknown"},
            {"retention_expires_at": 4100000000}, {"retention_expires_at": False},
            {"operation_version": 1}, {"operation_version": True},
            {"unexpected": "field"}, {"arguments_digest": "f" * 64},
            {"expires_at": 1}, {"expires_at": int(time.time()) + 120},
        )
        for change in changes:
            with self.subTest(change=change):
                payload = {**_recording(True), **change}
                with self.assertRaises(RecordingContractRefused):
                    verify_recording_start_effect(self.signer(payload, START_EFFECT_JOSE_TYPE))

    def test_recording_null_without_mode_does_not_upgrade_old_schema(self):
        for persistent in (False, True):
            with self.subTest(persistent=persistent):
                payload = _recording(persistent)
                payload.pop("retention_mode", None)
                payload["retention_expires_at"] = None
                with self.assertRaises(RecordingContractRefused):
                    verify_recording_start_effect(self.signer(payload, START_EFFECT_JOSE_TYPE))

    def test_asr5_has_exact_persistent_authority_and_short_egress(self):
        payload = _asr()
        compact = self.signer(payload, SUBMIT_EFFECT_JOSE_TYPE)
        self.assertEqual(verify_transcription_submit_effect(compact), payload)
        self.assertEqual(_digest(_submit_arguments(payload)), payload["arguments_digest"])
        attempt = SimpleNamespace(
            attempt_ref="attempt_persistent_test", audio_sha256="d" * 64,
            input_bytes=100, audio_duration_ms=1000,
        )
        request = build_transcription_egress_request_claims(payload, attempt, "send_allowed")
        self.assertEqual(request["operation_version"], 2)
        self.assertEqual(request["expires_at"] - request["issued_at"], 30)
        self.assertNotIn("retention_mode", request)

    def test_asr5_rejects_downgrade_and_substituted_limits(self):
        changes = (
            {"operation_version": 4}, {"retention_mode": "bounded"},
            {"retention_expires_at": 4100000000}, {"maximum_audio_bytes": True},
            {"maximum_audio_bytes": 1}, {"unexpected": 1},
        )
        for change in changes:
            with self.subTest(change=change):
                payload = {**_asr(), **change}
                with self.assertRaises(RecordingContractRefused):
                    verify_transcription_submit_effect(self.signer(payload, SUBMIT_EFFECT_JOSE_TYPE))

    def test_asr4_remains_bounded_without_an_added_mode(self):
        payload = _asr()
        payload.pop("retention_mode")
        payload.update(operation_version=4, retention_expires_at=4100000000)
        arguments = _submit_arguments(payload)
        self.assertNotIn("retention_mode", arguments)
        self.assertNotIn("retention_expires_at", arguments)
        self.assertEqual(arguments["operation_version"], 4)
        payload["arguments_digest"] = _digest(arguments)
        before = copy.deepcopy(payload)
        compact = self.signer(payload, SUBMIT_EFFECT_JOSE_TYPE)
        self.assertEqual(verify_transcription_submit_effect(compact), before)
        self.assertEqual(self.signer(payload, SUBMIT_EFFECT_JOSE_TYPE), compact)

    def test_asr_retention_refusal_keeps_transcription_error_family(self):
        payload = _asr()
        payload["retention_expires_at"] = True
        compact = self.signer(payload, SUBMIT_EFFECT_JOSE_TYPE)
        with self.assertRaises(TranscriptionContractRefused):
            verify_transcription_submit_effect(compact)

    def test_asr_artifact3_retains_provider_provenance_and_token_ttl(self):
        effect = _asr()
        effect["transcription_ref"] = "transcription_" + "1" * 32
        effect["arguments_digest"] = _digest(_submit_arguments(effect))
        now = int(time.time())
        attempt = SimpleNamespace(
            grant_semantic_digest="e" * 64, authority_version=1,
            attempt_ref="attempt_persistent_test", provider_ref="mistral",
            requested_model_ref=effect["requested_model_ref"], audio_sha256="f" * 64,
            usage_audio_seconds=1, estimated_cost_micros=1,
            currency="USD", tariff_catalog_version=effect["tariff_catalog_version"],
            provider_observed_model_ref=None, provider_request_ref_digest=None,
            provider_egress_opened_at=now - 2, provider_completed_at=now - 1,
        )
        artifact = {
            "transcript_artifact_ref": "transcriptartifact_persistent_test",
            "object_ref": f"mastrao-transcripts/{effect['transcription_ref']}.json",
            "byte_size": 100, "checksum_digest": "a" * 64, "segment_count": 1,
        }
        with override_settings(MASTRAO_RECORDING_STORAGE_BINDING_DIGEST="c" * 64):
            claims = build_transcript_artifact_receipt_claims(effect, artifact, attempt)
        self.assertEqual(claims["operation_version"], 3)
        self.assertEqual(claims["retention_mode"], "until_deleted")
        self.assertIsNone(claims["retention_expires_at"])
        self.assertEqual(claims["grant_semantic_digest"], "e" * 64)
        self.assertEqual(claims["provider_egress_opened_at"], now - 2)
        self.assertEqual(claims["provider_completed_at"], now - 1)
        self.assertEqual(claims["expires_at"] - claims["issued_at"], 30)

    def test_speaker_capture2_binds_mode_in_digest(self):
        payload = _recording(True)
        payload.pop("resolve_only")
        payload.pop("claim_id")
        payload.update(
            type=CAPTURE_EFFECT_TYPE, operation="capture_meeting_speaker_evidence",
            purpose="meeting_speaker_evidence", scope="recording_roster_vad_timeline",
            evidence_ref="evidence_persistent_test", recording_started_at_ms=1000,
        )
        payload["arguments_digest"] = _digest({
            "version": 1, "operation": "capture_meeting_speaker_evidence",
            "operation_version": 2,
            **{name: payload[name] for name in (
                "meeting_ref", "room_ref", "recording_ref", "evidence_ref",
                "provider_binding_digest", "purpose", "scope", "retention_mode",
                "retention_expires_at",
            )},
        })
        compact = self.signer(payload, CAPTURE_EFFECT_JOSE_TYPE)
        self.assertEqual(verify_speaker_evidence_capture_effect(compact), payload)
        payload["operation_version"] = 1
        with self.assertRaises(RecordingContractRefused):
            verify_speaker_evidence_capture_effect(self.signer(payload, CAPTURE_EFFECT_JOSE_TYPE))

    def test_native2_requires_fresh_effect_despite_persistent_content(self):
        payload = _native()
        authority = {
            name: value for name, value in payload.items()
            if name not in {
                "version", "type", "issuer", "audience", "effect_key",
                "arguments_digest", "issued_at", "expires_at", "jti", "resolve_only",
            }
        }
        self.assertEqual(payload["arguments_digest"], _digest(authority))
        self.assertEqual(verify_native_start(self.signer(payload, NATIVE_JOSE)), payload)
        payload["expires_at"] = 1
        with self.assertRaises(RecordingContractRefused):
            verify_native_start(self.signer(payload, NATIVE_JOSE))

    def test_native1_keeps_finite_authority_and_original_signed_bytes(self):
        payload = _native()
        payload.pop("retention_mode")
        payload.update(version=1, retention_expires_at=4100000000)
        authority = {
            name: value for name, value in payload.items()
            if name not in {
                "version", "type", "issuer", "audience", "effect_key",
                "arguments_digest", "issued_at", "expires_at", "jti", "resolve_only",
            }
        }
        payload["arguments_digest"] = _digest(authority)
        compact = self.signer(payload, NATIVE_JOSE)
        self.assertEqual(verify_native_start(compact), payload)
        self.assertEqual(self.signer(payload, NATIVE_JOSE), compact)

    def test_native2_schema_is_closed(self):
        changes = (
            {"version": 1}, {"retention_expires_at": 4100000000},
            {"retention_mode": "bounded"}, {"retention_mode": None}, {"unexpected": 1},
        )
        for change in changes:
            with self.subTest(change=change):
                payload = {**_native(), **change}
                with self.assertRaises(RecordingContractRefused):
                    verify_native_start(self.signer(payload, NATIVE_JOSE))
