"""Recording artifact finalization and session-bound download proofs."""

import hashlib
import io
import time
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from unittest import mock
from urllib.parse import urlencode

from django.db import transaction as django_transaction
from django.test import Client
from django.utils import timezone

import pytest

from core import models
from core.factories import RoomFactory, UserFactory
from core.mastrao_recording_access import RETRY_COOKIE, SESSION_KEY
from core.mastrao_recording_artifact import (
    _prepare_artifact_receipt,
    finalize_mastrao_artifact,
)
from core.mastrao_recording_contract import RecordingContractRefused
from core.mastrao_recording_session import _sync_binding
from core.models import RoomAccessLevel
from core.tests import test_mastrao_recording_consent as consent_proofs

recording_rollout_settings = consent_proofs.recording_rollout_settings


def _artifact_access(suffix=""):
    owner = UserFactory()
    room = RoomFactory(access_level=RoomAccessLevel.RESTRICTED)
    room_binding = models.MastraoRoomBinding.objects.create(
        effect_key=f"effect_0123456789abcdef{suffix}",
        arguments_digest="a" * 64,
        meeting_ref=f"meeting_0123456789abcdef{suffix}",
        room_ref=f"room_0123456789abcdef{suffix}",
        owner_ref=f"owner_0123456789abcdef{suffix}",
        room=room,
        owner=owner,
        provider_binding_digest="b" * 64,
    )
    recording = models.Recording.objects.create(
        room=room,
        status=models.RecordingStatusChoices.SAVED,
        mode=models.RecordingModeChoices.SCREEN_RECORDING,
    )
    binding = models.MastraoRecordingBinding.objects.create(
        room_binding=room_binding,
        recording=recording,
        organization_external_id="organization_0123456789",
        meeting_ref=room_binding.meeting_ref,
        room_ref=room_binding.room_ref,
        recording_ref=f"recording_0123456789abcdef{suffix}",
        provider_binding_digest=room_binding.provider_binding_digest,
        policy_ref="policy_0123456789abcdef",
        notice_version="notice_0123456789abcdef",
        notice_digest="c" * 64,
        retention_expires_at=timezone.now() + timedelta(days=30),
        state=models.MastraoRecordingBinding.State.FINALIZED,
        artifact_ref=f"artifact_0123456789abcdef{suffix}",
        object_ref=f"recordings/recording_0123456789abcdef{suffix}.mp4",
        byte_size=3,
        checksum_algorithm="sha256",
        checksum_digest=hashlib.sha256(b"mp4").hexdigest(),
    )
    retry = f"retry_0123456789abcdefghijklmnopqrstuvwxyz{suffix}"
    retry_digest = hashlib.sha256(retry.encode()).hexdigest()
    access = models.MastraoRecordingArtifactAccess.objects.create(
        recording_binding=binding,
        grant_jti=f"request_0123456789abcdef{suffix}",
        grant_digest=hashlib.sha256(f"grant{suffix}".encode()).hexdigest(),
        artifact_ref=binding.artifact_ref,
        subject_external_id_digest="e" * 64,
        platform_session_digest="f" * 64,
        retry_cookie_digest=retry_digest,
        expires_at=timezone.now() + timedelta(minutes=1),
    )
    return access, retry


def _client_for_access(access, retry, stage="ready"):
    client = Client()
    session = client.session
    session[SESSION_KEY] = {
        "access_id": str(access.id),
        "retry_digest": access.retry_cookie_digest,
        "expires_at": int(access.expires_at.timestamp()),
        "stage": stage,
    }
    session.save()
    client.cookies[RETRY_COOKIE] = retry
    return client


@pytest.mark.usefixtures("db")
def test_artifact_access_bootstrap_consumes_only_from_its_own_origin(settings):
    """Consume the access grant only through the same-site bootstrap flow."""

    access, _retry = _artifact_access()
    binding = access.recording_binding
    access.delete()
    now = int(time.time())
    grant = {
        "organization_external_id": binding.organization_external_id,
        "meeting_ref": binding.meeting_ref,
        "recording_ref": binding.recording_ref,
        "artifact_ref": binding.artifact_ref,
        "subject_external_id": "subject_0123456789abcdef",
        "platform_session_digest": "f" * 64,
        "issued_at": now,
        "expires_at": now + 60,
        "jti": "recordingaccess_0123456789abcdef",
    }
    settings.MASTRAO_MEETING_RECORDING_ENABLED = True
    settings.MASTRAO_MEETING_RECORDING_START_ENABLED = False
    settings.MASTRAO_MEETING_RECORDING_ARTIFACT_ACCESS_ENABLED = True
    settings.MASTRAO_PLATFORM_ORIGIN = "http://platform.test"
    compact = "header.payload.signature"
    client = Client()
    with mock.patch(
        "core.mastrao_recording_access.verify_recording_access_grant",
        return_value=grant,
    ):
        refused_bootstrap = Client().post(
            "/recordings/access/",
            urlencode({"recording_access_grant": compact}),
            content_type="application/x-www-form-urlencoded",
            HTTP_ORIGIN="null",
            HTTP_SEC_FETCH_SITE="cross-site",
        )
        assert refused_bootstrap.status_code == 404
        bootstrap = client.post(
            "/recordings/access/",
            urlencode({"recording_access_grant": compact}),
            content_type="application/x-www-form-urlencoded",
            HTTP_ORIGIN="null",
            HTTP_SEC_FETCH_SITE="same-site",
        )
        assert bootstrap.status_code == 200
        assert 'lang="fr"' in bootstrap.content.decode()
        assert 'role="status"' in bootstrap.content.decode()
        assert "Vérification de l’intégrité" in bootstrap.content.decode()
        consumed = client.post(
            "/recordings/access/",
            urlencode({"stage": "consume", "recording_access_grant": compact}),
            content_type="application/x-www-form-urlencoded",
            HTTP_ORIGIN="null",
            HTTP_SEC_FETCH_SITE="same-site",
        )
        assert consumed.status_code == 303
        assert consumed["Location"] == "/recordings/download/current"
        refused = client.post(
            "/recordings/access/",
            urlencode({"stage": "consume", "recording_access_grant": compact}),
            content_type="application/x-www-form-urlencoded",
            HTTP_ORIGIN=settings.MASTRAO_PLATFORM_ORIGIN,
            HTTP_SEC_FETCH_SITE="cross-site",
        )
        assert refused.status_code == 404


@pytest.mark.usefixtures("db")
def test_artifact_access_shutdown_is_independent_from_capture_rollback(settings):
    """Refuse artifact access independently from disabling capture."""

    access, _retry = _artifact_access()
    access.delete()
    settings.MASTRAO_MEETING_RECORDING_START_ENABLED = False
    settings.MASTRAO_MEETING_RECORDING_ARTIFACT_ACCESS_ENABLED = False

    response = Client().post(
        "/recordings/access/",
        urlencode({"recording_access_grant": "header.payload.signature"}),
        content_type="application/x-www-form-urlencoded",
        HTTP_ORIGIN="null",
        HTTP_SEC_FETCH_SITE="same-site",
    )

    assert response.status_code == 404
    assert response["Content-Type"].startswith("text/html")
    assert "réessayez depuis votre dossier Mastrao" in response.content.decode()


@pytest.mark.usefixtures("db")
def test_capture_rollback_preserves_artifact_download(settings):
    """Keep one verified download available after capture is disabled."""

    access, retry = _artifact_access()
    settings.MASTRAO_MEETING_RECORDING_ENABLED = True
    settings.MASTRAO_MEETING_RECORDING_START_ENABLED = False
    settings.MASTRAO_MEETING_RECORDING_ARTIFACT_ACCESS_ENABLED = True
    client = _client_for_access(access, retry)
    prepared = client.get("/recordings/download/current")
    assert prepared.status_code == 303
    access.refresh_from_db()
    assert access.consumed_at is None

    with mock.patch(
        "core.mastrao_recording_access.default_storage.open",
        return_value=io.BytesIO(b"mp4"),
    ):
        streamed = client.get("/recordings/download/current")
        assert streamed.status_code == 200
        assert b"".join(streamed.streaming_content) == b"mp4"
    access.refresh_from_db()
    assert access.consumed_at is not None
    assert client.get("/recordings/download/current").status_code == 404


@pytest.mark.usefixtures("db")
def test_artifact_access_shutdown_revokes_a_prepared_download(settings):
    """Revoke the prepared session and cookie without opening storage."""

    access, retry = _artifact_access()
    client = _client_for_access(access, retry, stage="prepared")
    settings.MASTRAO_MEETING_RECORDING_ENABLED = True
    settings.MASTRAO_MEETING_RECORDING_ARTIFACT_ACCESS_ENABLED = False

    with mock.patch("core.mastrao_recording_access.default_storage.open") as storage:
        response = client.get("/recordings/download/current")

    assert response.status_code == 404
    assert SESSION_KEY not in client.session
    assert response.cookies[RETRY_COOKIE]["path"] == "/recordings/"
    assert response.cookies[RETRY_COOKIE]["max-age"] == 0
    storage.assert_not_called()
    access.refresh_from_db()
    assert access.consumed_at is None


@pytest.mark.usefixtures("db")
def test_artifact_download_rejects_changed_object():
    """Refuse changed bytes without consuming the artifact grant."""

    access, retry = _artifact_access()
    client = _client_for_access(access, retry, stage="prepared")
    with mock.patch(
        "core.mastrao_recording_access.default_storage.open",
        return_value=io.BytesIO(b"changed"),
    ):
        assert client.get("/recordings/download/current").status_code == 404
    access.refresh_from_db()
    assert access.consumed_at is None


@pytest.mark.usefixtures("db")
def test_artifact_download_reads_and_serves_one_verified_stream():
    """Serve the verified bytes from the same single storage stream."""

    access, retry = _artifact_access()
    client = _client_for_access(access, retry, stage="prepared")
    with mock.patch(
        "core.mastrao_recording_access.default_storage.open",
        side_effect=[io.BytesIO(b"mp4"), io.BytesIO(b"changed")],
    ) as storage:
        response = client.get("/recordings/download/current")
        assert response.status_code == 200
        assert b"".join(response.streaming_content) == b"mp4"
    storage.assert_called_once()


@pytest.mark.usefixtures("db")
def test_artifact_download_storage_failure_is_opaque_and_retryable():
    """Keep storage failures opaque and leave the grant retryable."""

    access, retry = _artifact_access()
    client = _client_for_access(access, retry, stage="prepared")
    with mock.patch(
        "core.mastrao_recording_access.default_storage.open",
        side_effect=OSError("storage unavailable"),
    ):
        response = client.get("/recordings/download/current")
    assert response.status_code == 404
    assert response["Content-Type"].startswith("text/html")
    assert "réessayez depuis votre dossier Mastrao" in response.content.decode()
    access.refresh_from_db()
    assert access.consumed_at is None


@pytest.mark.usefixtures("db")
def test_artifact_download_started_before_expiry_finishes_after_expiry():
    """Allow a verified download admitted before the grant expires."""

    access, retry = _artifact_access()
    started_at = timezone.now()
    access.expires_at = started_at + timedelta(seconds=30)
    access.save(update_fields=["expires_at", "updated_at"])
    client = _client_for_access(access, retry, stage="prepared")
    with (
        mock.patch("core.mastrao_recording_access.datetime") as clock,
        mock.patch(
            "core.mastrao_recording_access._open_verified_stream",
            return_value=io.BytesIO(b"mp4"),
        ),
    ):
        clock.now.side_effect = [started_at, started_at + timedelta(minutes=2)]
        response = client.get("/recordings/download/current")
    assert response.status_code == 200
    assert b"".join(response.streaming_content) == b"mp4"
    access.refresh_from_db()
    assert access.consumed_at == started_at + timedelta(minutes=2)


@pytest.mark.usefixtures("db")
def test_artifact_download_started_after_expiry_is_refused():
    """Refuse an expired grant before opening storage."""

    access, retry = _artifact_access()
    access.expires_at = timezone.now() - timedelta(seconds=1)
    access.save(update_fields=["expires_at", "updated_at"])
    client = _client_for_access(access, retry, stage="prepared")
    with mock.patch("core.mastrao_recording_access.default_storage.open") as storage:
        response = client.get("/recordings/download/current")
    assert response.status_code == 404
    storage.assert_not_called()
    access.refresh_from_db()
    assert access.consumed_at is None


@pytest.mark.usefixtures("db")
def test_artifact_download_rejects_old_cookie_and_other_browser():
    """Refuse stale cookies and cookies without their bound browser session."""

    access, retry = _artifact_access()
    wrong_cookie = _client_for_access(access, f"{retry}-old")
    assert wrong_cookie.get("/recordings/download/current").status_code == 404

    other_browser = Client()
    other_browser.cookies[RETRY_COOKIE] = retry
    assert other_browser.get("/recordings/download/current").status_code == 404


@pytest.mark.usefixtures("db")
def test_artifact_download_rechecks_retention_at_stream_time():
    """Recheck recording retention before streaming the artifact."""

    access, retry = _artifact_access()
    binding = access.recording_binding
    binding.retention_expires_at = timezone.now() - timedelta(seconds=1)
    binding.save(update_fields=["retention_expires_at", "updated_at"])
    client = _client_for_access(access, retry, stage="prepared")
    with mock.patch("core.mastrao_recording_access.default_storage.open") as storage:
        assert client.get("/recordings/download/current").status_code == 404
    storage.assert_not_called()


@pytest.mark.usefixtures("db")
def test_artifact_finalization_verifies_and_replays_persisted_metadata(settings):
    """Persist verified artifact metadata before notifying Core."""

    access, _retry = _artifact_access()
    binding = access.recording_binding
    binding.artifact_ref = None
    binding.object_ref = None
    binding.state = models.MastraoRecordingBinding.State.PROCESSING
    binding.save()
    recording = binding.recording
    payload = b"verified-room-composite-mp4"
    settings.MASTRAO_RECORDING_STORAGE_BINDING_DIGEST = "1" * 64
    settings.MASTRAO_RECORDING_REGION_REF = "fr-par"
    settings.MASTRAO_RECORDING_ENCRYPTION_REF = "sse-s3"
    settings.MASTRAO_RECORDING_LIFECYCLE_POLICY_REF = "retention-30-days"
    settings.MASTRAO_CORE_RECORDING_ARTIFACT_ENDPOINT = (
        "http://cabinet-core:3911/internal/v1/meetings/recording/artifacts/finalize"
    )
    with (
        mock.patch(
            "core.mastrao_recording_artifact.default_storage.open",
            return_value=io.BytesIO(payload),
        ),
        mock.patch(
            "core.mastrao_recording_artifact.sign_artifact_receipt",
            return_value="receipt.payload.signature",
        ),
        mock.patch("core.mastrao_recording_artifact.post_core_json") as post_core_json,
    ):
        post_core_json.side_effect = lambda **kwargs: {
            "artifactRef": kwargs["body"]
            and models.MastraoRecordingBinding.objects.get(pk=binding.pk).artifact_ref
        }
        finalize_mastrao_artifact(recording)
    binding.refresh_from_db()
    assert binding.state == models.MastraoRecordingBinding.State.FINALIZED
    assert binding.byte_size == len(payload)
    assert binding.checksum_digest == hashlib.sha256(payload).hexdigest()
    assert binding.artifact_receipt_claims["content_type"] == "video/mp4"
    post_core_json.assert_called_once()


@pytest.mark.usefixtures("db")
def test_artifact_inspection_runs_outside_database_transaction(settings):
    """Inspect storage outside the transaction that prepares the receipt."""

    access, _retry = _artifact_access()
    binding = access.recording_binding
    binding.artifact_ref = None
    binding.object_ref = None
    binding.state = models.MastraoRecordingBinding.State.PROCESSING
    binding.retention_expires_at = datetime.fromtimestamp(
        int(binding.retention_expires_at.timestamp()), tz=UTC
    )
    binding.save()
    binding.refresh_from_db()
    settings.MASTRAO_RECORDING_STORAGE_BINDING_DIGEST = "1" * 64
    settings.MASTRAO_RECORDING_REGION_REF = "fr-par"
    settings.MASTRAO_RECORDING_ENCRYPTION_REF = "sse-s3"
    settings.MASTRAO_RECORDING_LIFECYCLE_POLICY_REF = "retention-30-days"
    transaction_depth = 0
    inspection_depths = []
    real_atomic = django_transaction.atomic
    status = {
        "mode": "recorded",
        "organization_external_id": binding.organization_external_id,
        "meeting_ref": binding.meeting_ref,
        "room_ref": binding.room_ref,
        "recording_ref": binding.recording_ref,
        "policy_ref": binding.policy_ref,
        "notice_version": binding.notice_version,
        "notice_digest": binding.notice_digest,
        "purpose": binding.purpose,
        "scope": binding.scope,
        "retention_expires_at": int(binding.retention_expires_at.timestamp()),
        "recording_state": "processing",
    }

    @contextmanager
    def tracked_atomic(*args, **kwargs):
        nonlocal transaction_depth
        transaction_depth += 1
        try:
            with real_atomic(*args, **kwargs):
                yield
        finally:
            transaction_depth -= 1

    def inspect(_object_ref):
        inspection_depths.append(transaction_depth)
        _sync_binding(binding.room_binding.room, status)
        return 4, hashlib.sha256(b"mp4!").hexdigest()

    with (
        mock.patch(
            "core.mastrao_recording_artifact.transaction.atomic",
            side_effect=tracked_atomic,
        ),
        mock.patch(
            "core.mastrao_recording_artifact._inspect_object", side_effect=inspect
        ),
    ):
        _prepare_artifact_receipt(binding.recording)

    assert inspection_depths == [0]


@pytest.mark.usefixtures("db")
def test_artifact_finalization_rejects_recording_version_race(settings):
    """Reject inspected bytes if the recording changes before receipt creation."""

    access, _retry = _artifact_access()
    binding = access.recording_binding
    binding.artifact_ref = None
    binding.object_ref = None
    binding.state = models.MastraoRecordingBinding.State.PROCESSING
    binding.save()
    recording = binding.recording
    settings.MASTRAO_RECORDING_STORAGE_BINDING_DIGEST = "1" * 64
    settings.MASTRAO_RECORDING_REGION_REF = "fr-par"
    settings.MASTRAO_RECORDING_ENCRYPTION_REF = "sse-s3"
    settings.MASTRAO_RECORDING_LIFECYCLE_POLICY_REF = "retention-30-days"

    def mutate_recording(_object_ref):
        models.Recording.objects.filter(pk=recording.pk).update(
            status=models.RecordingStatusChoices.EXTERNAL_PROCESS_SUCCESSFUL,
            updated_at=timezone.now() + timedelta(seconds=1),
        )
        return 4, hashlib.sha256(b"mp4!").hexdigest()

    with mock.patch(
        "core.mastrao_recording_artifact._inspect_object",
        side_effect=mutate_recording,
    ):
        try:
            _prepare_artifact_receipt(recording)
        except RecordingContractRefused as error:
            assert error.status == 409
        else:
            raise AssertionError("A changed recording version must invalidate the hash")

    binding.refresh_from_db()
    assert binding.artifact_receipt_claims == {}
    assert binding.artifact_ref is None


@pytest.mark.usefixtures("db")
def test_artifact_finalization_replays_receipt_after_core_failure(settings):
    """Retry the persisted receipt only after verifying the original bytes."""

    access, _retry = _artifact_access()
    binding = access.recording_binding
    binding.artifact_ref = None
    binding.object_ref = None
    binding.state = models.MastraoRecordingBinding.State.PROCESSING
    binding.save()
    recording = binding.recording
    settings.MASTRAO_RECORDING_STORAGE_BINDING_DIGEST = "1" * 64
    settings.MASTRAO_RECORDING_REGION_REF = "fr-par"
    settings.MASTRAO_RECORDING_ENCRYPTION_REF = "sse-s3"
    settings.MASTRAO_RECORDING_LIFECYCLE_POLICY_REF = "retention-30-days"
    settings.MASTRAO_CORE_RECORDING_ARTIFACT_ENDPOINT = (
        "http://cabinet-core:3911/internal/v1/meetings/recording/artifacts/finalize"
    )
    with (
        mock.patch(
            "core.mastrao_recording_artifact.default_storage.open",
            return_value=io.BytesIO(b"verified-room-composite-mp4"),
        ),
        mock.patch(
            "core.mastrao_recording_artifact.sign_artifact_receipt",
            return_value="receipt.payload.signature",
        ),
        mock.patch(
            "core.mastrao_recording_artifact.post_core_json",
            side_effect=RecordingContractRefused(status=503),
        ),
    ):
        try:
            finalize_mastrao_artifact(recording)
        except RecordingContractRefused:
            pass
        else:
            raise AssertionError("Core failure should remain visible")

    binding.refresh_from_db()
    first_claims = dict(binding.artifact_receipt_claims)
    assert binding.state == models.MastraoRecordingBinding.State.PROCESSING
    assert first_claims["artifact_ref"] == binding.artifact_ref
    expired_claims = {
        **first_claims,
        "issued_at": int(time.time()) - 31,
        "expires_at": int(time.time()) - 1,
    }
    binding.artifact_receipt_claims = expired_claims
    binding.save(update_fields=["artifact_receipt_claims", "updated_at"])

    with (
        mock.patch(
            "core.mastrao_recording_artifact.default_storage.open",
            return_value=io.BytesIO(b"changed-room-composite-mp4"),
        ),
        mock.patch("core.mastrao_recording_artifact.post_core_json") as post_core_json,
    ):
        with pytest.raises(RecordingContractRefused) as replay_error:
            finalize_mastrao_artifact(recording)
    assert replay_error.value.status == 409
    post_core_json.assert_not_called()

    with (
        mock.patch(
            "core.mastrao_recording_artifact.default_storage.open",
            return_value=io.BytesIO(b"verified-room-composite-mp4"),
        ) as storage_open,
        mock.patch(
            "core.mastrao_recording_artifact.sign_artifact_receipt",
            return_value="receipt.payload.signature",
        ),
        mock.patch(
            "core.mastrao_recording_artifact.post_core_json",
            return_value={"artifactRef": binding.artifact_ref},
        ),
    ):
        finalize_mastrao_artifact(recording)
    storage_open.assert_called_once()
    binding.refresh_from_db()
    assert binding.state == models.MastraoRecordingBinding.State.FINALIZED
    assert binding.artifact_receipt_claims["jti"] != first_claims["jti"]
    assert (
        binding.artifact_receipt_claims["artifact_ref"] == first_claims["artifact_ref"]
    )
    assert (
        binding.artifact_receipt_claims["checksum_digest"]
        == first_claims["checksum_digest"]
    )
