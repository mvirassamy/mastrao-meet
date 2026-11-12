"""Persistent SQL projections, native terminal fencing and short download sessions."""

import io
import json
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from django.contrib.sessions.backends.signed_cookies import SessionStore
from django.db import IntegrityError, connection, transaction
from django.utils import timezone

import pytest

from core import models
from core.factories import RecordingFactory
from core.mastrao_native_asr_worker import _eligible, _finish
from core.mastrao_native_authorization import _validate_authorization
from core.mastrao_native_capture_contract import arguments_digest
from core.mastrao_native_capture_drain import _stop_reason
from core.mastrao_native_source_transfer import _eligible_sources
from core.mastrao_recording_access import _stream_once
from core.mastrao_recording_contract import RecordingContractRefused
from core.mastrao_recording_session import _validate_status
from core.mastrao_recording_session import _sync_binding
from core.mastrao_speaker_evidence_adapter import _capture_metadata
from core.mastrao_retention import active_retention, retention_claims
from core.mastrao_transcription_adapter import (
    _effect_from_local,
    _prepare_transcription,
)
from core.tests.test_mastrao_native_capture import _post
from core.tests.test_mastrao_normal_post_asr import _normal_effect, normal_settings
from core.tests.test_mastrao_persistent_contracts import _recording
from core.tests.test_mastrao_transcription import _finalized_recording_binding

pytest_plugins = (
    "core.tests.test_mastrao_native_capture",
    "core.tests.test_mastrao_native_source_transfer",
    "core.tests.test_mastrao_native_asr",
)
pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def persistent_capture(client, signer, effect, provider):
    effect.update(version=2, retention_mode="until_deleted", retention_expires_at=None)
    effect["arguments_digest"] = arguments_digest(effect)
    assert _post(client, signer, effect).status_code == 200
    intent = models.MastraoNativeCaptureStart.objects.get()
    assert intent.retention_mode == "until_deleted"
    assert intent.retention_expires_at is None
    assert intent.receipt_claims["version"] == 1
    return intent


def test_new_capture_continues_but_old_unknown_stops(persistent_capture):
    intent = persistent_capture
    future = timezone.now() + timedelta(days=3650)
    assert _stop_reason(intent, future) == ""
    intent.retention_mode = "bounded"
    assert _stop_reason(intent, future) == "retention_unknown"
    intent.retention_expires_at = timezone.now() - timedelta(seconds=1)
    assert _stop_reason(intent, future) == "retention_expired"


def test_persistent_capture_still_stops_when_room_closes(persistent_capture):
    intent = persistent_capture
    intent.epoch.connection.room_binding.closing_at = timezone.now()
    assert _stop_reason(intent, timezone.now()) == "room_closed"


def test_persistent_sources_are_eligible_without_bypassing_epoch_fences(persistent_capture):
    intent = persistent_capture
    intent.drained_at = timezone.now()
    intent.observed_status = 3
    intent.source_manifest = {"synthetic": True}
    intent.save()
    now = timezone.now()
    assert _eligible_sources(now).filter(pk=intent.pk).exists()
    intent.retention_mode = "bounded"
    intent.save(update_fields=["retention_mode"])
    assert not _eligible_sources(now).filter(pk=intent.pk).exists()
    intent.retention_mode = "until_deleted"
    intent.save(update_fields=["retention_mode"])
    models.MastraoRtcTrackEpoch.objects.filter(pk=intent.epoch_id).update(conflict=True)
    assert not _eligible_sources(now).filter(pk=intent.pk).exists()


@pytest.mark.parametrize("outcome", ["rejected", "failed"])
def test_terminal_persistent_asr_never_retries_forever(persistent_capture, outcome):
    intent = persistent_capture
    intent.source_receipt = {"source_ref": str(uuid4())}
    intent.asr_claim = uuid4()
    intent.asr_claim_until = timezone.now() + timedelta(seconds=30)
    intent.save()
    assert _eligible(timezone.now()).filter(pk=intent.pk).exists()
    with patch("core.mastrao_native_asr_worker.schedule_native_asr"):
        assert _finish(intent, False, RecordingContractRefused(status=422, outcome=outcome))
    intent.refresh_from_db()
    assert not intent.asr_acknowledged
    assert intent.asr_error == "native_asr_rejected"
    assert intent.asr_next_at is not None
    assert not _eligible(timezone.now() + timedelta(days=3650)).filter(pk=intent.pk).exists()
    with connection.cursor() as cursor:
        cursor.execute("SELECT SUM(pending_count) FROM meet_mastrao_native_asr_queue_metrics")
        assert cursor.fetchone()[0] == 0


@pytest.mark.parametrize("outcome", ["retry", "unknown"])
def test_persistent_transient_asr_keeps_existing_retry_policy(persistent_capture, outcome):
    intent = persistent_capture
    intent.source_receipt = {"source_ref": str(uuid4())}
    intent.asr_claim = uuid4()
    intent.asr_claim_until = timezone.now() + timedelta(seconds=30)
    intent.asr_attempts = 1
    intent.save()
    with patch("core.mastrao_native_asr_worker.schedule_native_asr"):
        assert _finish(intent, False, RecordingContractRefused(status=503, outcome=outcome))
    intent.refresh_from_db()
    assert not intent.asr_acknowledged
    assert intent.asr_attempts == 0
    assert intent.asr_next_at > timezone.now()
    assert _eligible(intent.asr_next_at + timedelta(seconds=1)).filter(pk=intent.pk).exists()


def test_bounded_terminal_asr_retains_historical_deadline(asr_intent):
    intent = asr_intent
    intent.asr_claim = uuid4()
    intent.asr_claim_until = timezone.now() + timedelta(seconds=30)
    intent.save()
    expiry = intent.retention_expires_at
    with patch("core.mastrao_native_asr_worker.schedule_native_asr"):
        assert _finish(intent, False, RecordingContractRefused(status=422, outcome="rejected"))
    intent.refresh_from_db()
    assert intent.asr_next_at == expiry
    assert not intent.asr_acknowledged
    assert not _eligible(expiry + timedelta(seconds=1)).filter(pk=intent.pk).exists()


@pytest.mark.parametrize("mode,expiry", [
    ("bounded", None), ("unknown", None),
    ("until_deleted", timezone.now() + timedelta(days=1)),
])
def test_recording_database_rejects_incoherent_retention(mode, expiry):
    binding = _finalized_recording_binding()
    with transaction.atomic(), pytest.raises(IntegrityError):
        models.MastraoRecordingBinding.objects.filter(pk=binding.pk).update(
            retention_mode=mode, retention_expires_at=expiry,
        )


def _persistent_recording():
    binding = _finalized_recording_binding()
    binding.retention_mode = "until_deleted"
    binding.retention_expires_at = None
    binding.save()
    return binding


def test_persistent_binding_is_current_and_serializes_only_explicit_mode():
    binding = _persistent_recording()
    assert models.MastraoRecordingBinding.objects.filter(
        active_retention(timezone.now() + timedelta(days=3650)), pk=binding.pk,
    ).exists()
    assert retention_claims(binding) == {
        "retention_mode": "until_deleted", "retention_expires_at": None,
    }


def test_speaker_dispatch_metadata_preserves_explicit_retention():
    effect = {
        "operation_version": 2,
        "meeting_ref": "meeting_persistent_test", "room_ref": "room_persistent_test",
        "recording_ref": "recording_persistent_test", "evidence_ref": "evidence_persistent_test",
        "organization_external_id": "organization_persistent_test",
        "provider_binding_digest": "a" * 64, "policy_ref": "policy_persistent_test",
        "notice_version": "notice_persistent_test", "notice_digest": "b" * 64,
        "retention_mode": "until_deleted", "retention_expires_at": None,
        "recording_started_at_ms": 1000,
    }
    metadata = json.loads(_capture_metadata(SimpleNamespace(id=uuid4()), effect))
    assert metadata["version"] == 2
    assert metadata["retention_mode"] == "until_deleted"
    assert metadata["retention_expires_at"] is None
    effect.pop("retention_mode")
    effect.update(operation_version=1, retention_expires_at=4100000000)
    old = json.loads(_capture_metadata(SimpleNamespace(id=uuid4()), effect))
    assert old["version"] == 1
    assert "retention_mode" not in old
    assert old["retention_expires_at"] == 4100000000


def test_existing_recording_expiry_flag_respects_persistent_binding(settings):
    binding = _persistent_recording()
    recording = RecordingFactory(room=binding.room_binding.room)
    binding.recording = recording
    binding.save()
    settings.RECORDING_EXPIRATION_DAYS = 1
    recording.created_at = timezone.now() - timedelta(days=2)
    assert recording.expired_at is None
    assert not recording.is_expired


def test_session_projection_cannot_upgrade_existing_bounded_recording():
    binding = _finalized_recording_binding()
    binding.artifact_receipt_claims = {"historical": "unchanged"}
    binding.save()
    status = {
        name: getattr(binding, name)
        for name in (
            "organization_external_id", "meeting_ref", "room_ref", "recording_ref",
            "policy_ref", "notice_version", "notice_digest", "purpose", "scope",
        )
    }
    status.update(
        mode="recorded", recording_state="available",
        retention_mode="until_deleted", retention_expires_at=None,
    )
    with pytest.raises(RecordingContractRefused):
        _sync_binding(binding.room_binding.room, status)
    binding.refresh_from_db()
    assert binding.retention_mode == "bounded"
    assert binding.retention_expires_at is not None
    assert binding.artifact_receipt_claims == {"historical": "unchanged"}


def test_asr5_persists_exact_signed_binding_and_reconstructs_it(normal_settings, settings):
    binding = _finalized_recording_binding()
    effect = _normal_effect(binding, settings, operation_version=5,
                            retention_mode="until_deleted", retention_expires_at=None)
    binding.retention_mode = "until_deleted"
    binding.retention_expires_at = None
    binding.save()
    transcript, local = _prepare_transcription(effect)
    assert transcript.contract_operation_version == 5
    rebuilt = _effect_from_local(transcript, local)
    assert rebuilt["retention_mode"] == "until_deleted"
    assert rebuilt["retention_expires_at"] is None
    assert rebuilt["arguments_digest"] == effect["arguments_digest"]
    binding.retention_mode = "bounded"
    binding.retention_expires_at = timezone.now() + timedelta(days=1)
    binding.save()
    transcript.refresh_from_db()
    with pytest.raises(RecordingContractRefused):
        _effect_from_local(transcript, local)


def test_bounded_recording_cannot_authorize_persistent_asr(normal_settings, settings):
    binding = _finalized_recording_binding()
    effect = _normal_effect(binding, settings, operation_version=5,
                            retention_mode="until_deleted", retention_expires_at=None)
    with pytest.raises(RecordingContractRefused):
        _prepare_transcription(effect)
    assert not models.MastraoTranscriptionBinding.objects.exists()


def _access(binding, *, expired=False):
    lifetime = timedelta(seconds=30)
    if expired:
        lifetime = -lifetime
    return models.MastraoRecordingArtifactAccess.objects.create(
        recording_binding=binding, grant_jti="persistent_test_grant",
        grant_digest="d" * 64, artifact_ref=binding.artifact_ref,
        subject_external_id_digest="e" * 64, platform_session_digest="f" * 64,
        retry_cookie_digest="a" * 64, expires_at=timezone.now() + lifetime,
    )


@pytest.mark.parametrize("expired", [False, True])
def test_persistent_video_download_keeps_short_session_and_one_use(expired):
    binding = _persistent_recording()
    access = _access(binding, expired=expired)
    request = SimpleNamespace(session=SessionStore(), is_secure=lambda: True)
    with patch(
        "core.mastrao_recording_access._open_verified_stream",
        return_value=io.BytesIO(b"video"),
    ) as opened:
        if expired:
            with pytest.raises(RecordingContractRefused):
                _stream_once(request, {"access_id": str(access.pk)}, "a" * 64)
            opened.assert_not_called()
            return
        response = _stream_once(request, {"access_id": str(access.pk)}, "a" * 64)
        assert response.status_code == 200
        response.close()
        with pytest.raises(RecordingContractRefused):
            _stream_once(request, {"access_id": str(access.pk)}, "a" * 64)
    access.refresh_from_db()
    assert access.consumed_at is not None


def test_download_rechecks_content_retention_after_object_verification():
    binding = _persistent_recording()
    access = _access(binding)
    stream = io.BytesIO(b"video")

    def revoke(*_args):
        models.MastraoRecordingBinding.objects.filter(pk=binding.pk).update(
            retention_mode="bounded", retention_expires_at=timezone.now() - timedelta(seconds=1),
        )
        return stream

    request = SimpleNamespace(session=SessionStore(), is_secure=lambda: True)
    with patch("core.mastrao_recording_access._open_verified_stream", side_effect=revoke):
        with pytest.raises(RecordingContractRefused):
            _stream_once(request, {"access_id": str(access.pk)}, "a" * 64)
    assert stream.closed
    access.refresh_from_db()
    assert access.consumed_at is None


def test_native_authorization_accepts_only_explicit_persistence():
    result = {
        "version": 1, "capture_authorized": False,
        "authorization": {
            "policy_ref": "policy_persistent_test", "policy_version": "policy_version_test",
            "policy_digest": "a" * 64,
            "purpose": "meeting_transcription_source_audio",
            "scope": "authorized_microphone_track_epoch",
            "retention_mode": "until_deleted", "retention_expires_at": None,
        },
    }
    assert _validate_authorization(result) == result
    del result["authorization"]["retention_mode"]
    with pytest.raises(RecordingContractRefused):
        _validate_authorization(result)

def test_session_status_keeps_persistent_mode_and_tenant_boundary(settings):
    settings.MASTRAO_RECORDING_NOTICE_VERSION = "notice_persistent_test"
    settings.MASTRAO_RECORDING_NOTICE_DIGEST = "b" * 64
    status = {name: value for name, value in _recording(True).items() if name in {
        "version", "organization_external_id", "meeting_ref", "room_ref",
        "recording_ref", "policy_ref", "notice_version", "notice_digest",
        "purpose", "scope", "retention_mode", "retention_expires_at",
    }}
    status.update(
        mode="recorded", recording_state="active", decision="accepted",
        transcription_mode="disabled", video={
            "consultation_source": "present", "decision": "accepted",
            "decision_basis": "explicit", "start_status": "authorized",
            "decision_lock": "started", "started_at": 1000,
            "start_requested": True, "start_available": False,
        },
    )
    participant = {"claims": {name: status[name] for name in (
        "organization_external_id", "meeting_ref", "room_ref",
    )}}
    room = SimpleNamespace(mastrao_binding=SimpleNamespace(room_ref=status["room_ref"]))
    assert _validate_status(status, participant, room) == status
    status["organization_external_id"] = "other-tenant"
    with pytest.raises(RecordingContractRefused):
        _validate_status(status, participant, room)
