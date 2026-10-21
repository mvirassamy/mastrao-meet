"""Real Django/PostgreSQL delivery state; Core/gateway peers explicitly simulated."""

# Imported pytest fixtures and generated LiveKit protobuf members are resolved dynamically.
# pylint: disable=missing-function-docstring,redefined-outer-name,unused-argument,unused-import

import base64
import hashlib
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from unittest.mock import Mock, patch
from uuid import uuid4

from django.db import close_old_connections, transaction
from django.utils import timezone

import pytest
import requests

from core import models
from core.mastrao_core_http import read_bounded_core_json
from core.mastrao_native_asr_client import (
    NativeAsrDeliveryRefused,
    prepare_native_asr,
    transcribe_native_asr,
)
from core.mastrao_native_asr_worker import (
    _claim,
    _diagnostic_fields,
    _finish,
    _reserve_dispatch,
    dispatch_native_asr,
    process_native_asr_source,
    schedule_native_asr,
)
from core.mastrao_native_source_transfer import _claim as claim_source
from core.mastrao_native_source_transfer import _finish as finish_source
from core.mastrao_recording_contract import RecordingContractRefused
from core.tests.test_mastrao_native_asr_retry import gateway_response
from core.tests.test_mastrao_native_capture import (
    _post,
    binding,
    effect,
    host,
    isolated_binding_settings,
    provider,
    receipt,
    signer,
)
from core.tests.test_mastrao_native_source_transfer import ended_source

pytestmark = pytest.mark.django_db(transaction=True)
PRIVATE_CANARY = (
    "PRIVATE_CANARY message args URL header token body grant receipt transcript"
)


def worker_warning(caplog):
    records = [
        record
        for record in caplog.records
        if record.name == "core.mastrao_native_asr_worker"
    ]
    assert len(records) == 1
    assert records[0].exc_info is None
    return records[0]


@pytest.fixture
def asr_intent(client, signer, effect, provider, settings):
    settings.MASTRAO_NATIVE_ASR_ENABLED = True
    assert _post(client, signer, effect).status_code == 200
    intent = models.MastraoNativeCaptureStart.objects.get()
    intent.source_receipt = {
        "source_ref": str(uuid4()),
        "capture_ref": str(intent.capture_ref),
        "epoch_ref": str(intent.epoch_id),
    }
    intent.save()
    return intent


@pytest.fixture
def peers(asr_intent):
    core_receipt = {
        "core": {
            "state": "verified",
            "result_ref": str(uuid4()),
            "source_ref": asr_intent.source_receipt["source_ref"],
            "attempt_ref": "native_attempt_fixture",
        },
        "fingerprint": "a" * 64,
    }
    with (
        patch(
            "core.mastrao_native_asr_worker.prepare_native_asr",
            return_value=({}, b"audio"),
        ) as prepare,
        patch(
            "core.mastrao_native_asr_worker.transcribe_native_asr", return_value={}
        ) as transcribe,
        patch(
            "core.mastrao_native_asr_worker.upload_native_asr_result",
            return_value=core_receipt,
        ) as upload,
        patch("core.mastrao_native_asr_worker.ack_native_asr") as ack,
    ):
        yield prepare, transcribe, upload, ack


def process_intent(intent):
    reservation = _reserve_dispatch(intent.pk)
    if reservation is None:
        return False
    return process_native_asr_source(*reservation)


def test_worker_persists_core_receipt_before_ack_without_video_gate(asr_intent, peers):
    prepare, transcribe, upload, ack = peers

    def assert_durable(value):
        asr_intent.refresh_from_db()
        assert asr_intent.asr_receipt == value
        assert value == upload.return_value
        assert not asr_intent.asr_acknowledged

    ack.side_effect = assert_durable
    assert process_intent(asr_intent)
    assert (
        prepare.call_count
        == transcribe.call_count
        == upload.call_count
        == ack.call_count
        == 1
    )
    asr_intent.refresh_from_db()
    assert asr_intent.asr_acknowledged
    assert asr_intent.asr_claim is None
    assert not process_intent(asr_intent)
    assert transcribe.call_count == 1


def test_ack_failure_retries_only_ack_and_never_transcribes_again(asr_intent, peers):
    prepare, transcribe, upload, ack = peers
    ack.side_effect = RecordingContractRefused(status=503)
    assert not process_intent(asr_intent)
    asr_intent.refresh_from_db()
    assert asr_intent.asr_receipt == upload.return_value
    assert not asr_intent.asr_acknowledged
    models.MastraoNativeCaptureStart.objects.filter(pk=asr_intent.pk).update(
        asr_next_at=timezone.now() - timedelta(seconds=1)
    )
    ack.side_effect = None
    assert process_intent(asr_intent)
    assert prepare.call_count == transcribe.call_count == upload.call_count == 1
    assert ack.call_count == 2


@pytest.mark.parametrize(
    ("stage", "expected_stage"),
    [
        (0, "prepare"),
        (1, "gateway"),
        (2, "result"),
        (3, "ack"),
    ],
)
def test_upstream_failure_keeps_gateway_cache_and_durable_retry(
    asr_intent, peers, stage, expected_stage, caplog
):
    refusal = RecordingContractRefused(status=503, outcome="unknown")
    refusal.core_http_status = 502
    refusal.core_error_code = "service_unavailable"
    peers[stage].side_effect = refusal
    with caplog.at_level(logging.WARNING, logger="core.mastrao_native_asr_worker"):
        assert not process_intent(asr_intent)
    if stage < 3:
        peers[3].assert_not_called()
    asr_intent.refresh_from_db()
    assert (asr_intent.asr_receipt is None) is (stage < 3)
    assert asr_intent.asr_error == "native_asr_outcome_unknown"
    record = worker_warning(caplog)
    assert record.args == (
        expected_stage,
        asr_intent.capture_ref,
        "contract_refused",
        503,
        502,
        "service_unavailable",
        "unknown",
    )
    assert asr_intent.asr_attempts == 0
    assert asr_intent.asr_retry_streak == 1


@pytest.mark.parametrize(
    ("error", "expected_kind"),
    [
        (RecordingContractRefused(), "contract_refused"),
        (requests.RequestException(PRIVATE_CANARY), "request_error"),
        (ValueError(PRIVATE_CANARY), "invalid_value"),
        (TypeError(PRIVATE_CANARY), "invalid_type"),
        (KeyError(PRIVATE_CANARY), "missing_field"),
        (OSError(PRIVATE_CANARY), "io_error"),
    ],
)
def test_native_asr_diagnostic_error_kinds_are_closed(error, expected_kind):
    assert _diagnostic_fields(error)[0] == expected_kind


@pytest.mark.parametrize("accepted", [100, 599])
def test_native_asr_diagnostic_accepts_bounded_statuses(accepted):
    refusal = RecordingContractRefused(status=accepted)
    refusal.core_http_status = accepted
    assert _diagnostic_fields(refusal)[1:3] == (accepted, accepted)


@pytest.mark.parametrize("rejected", [99, 600, True, "503"])
def test_native_asr_diagnostic_rejects_unbounded_or_non_integer_statuses(rejected):
    refusal = RecordingContractRefused(status=rejected)
    refusal.core_http_status = rejected
    assert _diagnostic_fields(refusal)[1:3] == (None, None)


@pytest.mark.parametrize(
    ("core_code", "outcome"),
    [
        (PRIVATE_CANARY, PRIVATE_CANARY),
        ([PRIVATE_CANARY], [PRIVATE_CANARY]),
        ({"value": PRIVATE_CANARY}, {"value": PRIVATE_CANARY}),
        (True, True),
    ],
)
def test_native_asr_diagnostic_rejects_unknown_or_hostile_closed_fields(
    core_code, outcome
):
    refusal = RecordingContractRefused(status=503)
    refusal.core_error_code = core_code
    refusal.outcome = outcome
    assert _diagnostic_fields(refusal) == (
        "contract_refused",
        503,
        None,
        None,
        None,
    )


def test_native_asr_warning_never_logs_exception_private_values(
    asr_intent, peers, caplog
):
    error = requests.RequestException(PRIVATE_CANARY)
    error.url = f"https://example.invalid/{PRIVATE_CANARY}"
    error.headers = {"Authorization": f"Bearer {PRIVATE_CANARY}"}
    error.body = PRIVATE_CANARY
    error.grant = PRIVATE_CANARY
    error.receipt = PRIVATE_CANARY
    error.transcript = PRIVATE_CANARY
    peers[1].side_effect = error

    with caplog.at_level(logging.WARNING, logger="core.mastrao_native_asr_worker"):
        assert not process_intent(asr_intent)

    record = worker_warning(caplog)
    assert record.args[0] == "gateway"
    assert record.args[2:] == ("request_error", None, None, None, None)
    assert PRIVATE_CANARY not in record.getMessage()
    asr_intent.refresh_from_db()
    assert asr_intent.asr_error == "native_asr_delivery_failed"
    assert asr_intent.asr_attempts == 1
    assert asr_intent.asr_retry_streak == 1


@pytest.mark.parametrize(
    "case",
    [
        (1, "verify_claim_after_prepare", (1, 0, 0, 0)),
        (2, "verify_claim_after_gateway", (1, 1, 0, 0)),
        (3, "verify_claim_before_ack", (1, 1, 1, 0)),
    ],
)
def test_native_asr_logs_each_claim_verification_stage(asr_intent, peers, caplog, case):
    fresh_call, expected_stage, expected_calls = case
    calls = 0

    def fail_selected_fresh(_intent):
        nonlocal calls
        calls += 1
        if calls == fresh_call:
            raise OSError(PRIVATE_CANARY)

    with (
        patch("core.mastrao_native_asr_worker._fresh", side_effect=fail_selected_fresh),
        caplog.at_level(logging.WARNING, logger="core.mastrao_native_asr_worker"),
    ):
        assert not process_intent(asr_intent)

    record = worker_warning(caplog)
    assert record.args[0] == expected_stage
    assert PRIVATE_CANARY not in record.getMessage()
    assert tuple(peer.call_count for peer in peers) == expected_calls


def test_native_asr_logs_receipt_persistence_stage(asr_intent, peers, caplog):
    persistence = Mock()
    persistence.filter.return_value.update.side_effect = OSError(PRIVATE_CANARY)
    finish = Mock()
    finish.update.return_value = 1

    with (
        patch("core.mastrao_native_asr_worker._fresh"),
        patch(
            "core.mastrao_native_asr_worker._owned", side_effect=[persistence, finish]
        ),
        caplog.at_level(logging.WARNING, logger="core.mastrao_native_asr_worker"),
    ):
        assert not process_intent(asr_intent)

    record = worker_warning(caplog)
    assert record.args[0] == "persist_receipt"
    assert PRIVATE_CANARY not in record.getMessage()
    assert tuple(peer.call_count for peer in peers) == (1, 1, 1, 0)


def test_native_asr_logs_finish_stage_without_replaying_upstream(
    asr_intent, peers, caplog
):
    real_finish = _finish
    calls = 0

    def fail_success_finish(intent, acknowledged, error=None):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError(PRIVATE_CANARY)
        return real_finish(intent, acknowledged, error)

    with (
        patch(
            "core.mastrao_native_asr_worker._finish", side_effect=fail_success_finish
        ),
        caplog.at_level(logging.WARNING, logger="core.mastrao_native_asr_worker"),
    ):
        assert not process_intent(asr_intent)

    record = worker_warning(caplog)
    assert record.args[0] == "finish"
    assert PRIVATE_CANARY not in record.getMessage()
    assert tuple(peer.call_count for peer in peers) == (1, 1, 1, 1)
    asr_intent.refresh_from_db()
    assert asr_intent.asr_receipt == peers[2].return_value
    assert not asr_intent.asr_acknowledged
    assert asr_intent.asr_error == "native_asr_delivery_failed"


def test_existing_receipt_failure_logs_ack_without_replaying_upstream(
    asr_intent, peers, caplog
):
    receipt = peers[2].return_value
    models.MastraoNativeCaptureStart.objects.filter(pk=asr_intent.pk).update(
        asr_receipt=receipt
    )
    peers[3].side_effect = OSError(PRIVATE_CANARY)

    with caplog.at_level(logging.WARNING, logger="core.mastrao_native_asr_worker"):
        assert not process_intent(asr_intent)

    record = worker_warning(caplog)
    assert record.args[0] == "ack"
    assert tuple(peer.call_count for peer in peers) == (0, 0, 0, 1)
    assert PRIVATE_CANARY not in record.getMessage()


def test_rate_limit_waits_until_provider_deadline(asr_intent, peers):
    now = timezone.now()
    refusal = NativeAsrDeliveryRefused(
        status=429, outcome="rejected", retry_at=now + timedelta(seconds=120)
    )
    peers[1].side_effect = refusal

    assert not process_intent(asr_intent)
    asr_intent.refresh_from_db()
    assert asr_intent.asr_next_at >= now + timedelta(seconds=120)

    with patch(
        "core.mastrao_native_asr_worker.timezone.now",
        return_value=now + timedelta(seconds=60),
    ):
        assert not process_intent(asr_intent)

    asr_intent.refresh_from_db()
    assert asr_intent.asr_attempts == 0
    peers[1].assert_called_once()
    peers[2].assert_not_called()
    peers[3].assert_not_called()


def test_rejected_terminal_is_not_replayed_to_exhaust_local_budget(asr_intent, peers):
    peers[1].side_effect = RecordingContractRefused(status=502, outcome="rejected")
    assert not process_intent(asr_intent)

    with patch(
        "core.mastrao_native_asr_worker.timezone.now",
        return_value=timezone.now() + timedelta(minutes=1),
    ):
        assert not process_intent(asr_intent)

    asr_intent.refresh_from_db()
    assert asr_intent.asr_attempts == 1
    assert not asr_intent.asr_acknowledged
    assert asr_intent.asr_receipt is None
    peers[0].assert_called_once()
    peers[1].assert_called_once()
    peers[2].assert_not_called()
    peers[3].assert_not_called()


def test_core_failed_survives_http_client_and_stops_worker_dispatch(
    asr_intent, settings
):
    settings.MASTRAO_CORE_NATIVE_ASR_PREPARE_ENDPOINT = (
        "http://cabinet-core/internal/v1/meetings/capture/native/asr/prepare"
    )
    models.MastraoNativeCaptureStart.objects.filter(pk=asr_intent.pk).update(
        asr_attempts=13, asr_retry_streak=7
    )
    response = gateway_response(409, {"code": "runtime_busy", "outcome": "failed"})
    with (
        patch("requests.Session.post", return_value=response) as core_post,
        patch("core.mastrao_native_asr_worker.transcribe_native_asr") as transcribe,
        patch("core.mastrao_native_asr_worker.upload_native_asr_result") as upload,
        patch("core.mastrao_native_asr_worker.ack_native_asr") as ack,
    ):
        assert not process_intent(asr_intent)
        core_post.assert_called_once()
        asr_intent.refresh_from_db()
        assert asr_intent.asr_next_at == asr_intent.retention_expires_at
        assert asr_intent.asr_error == "native_asr_rejected"
        assert asr_intent.asr_attempts == 14
        assert asr_intent.asr_claim is None
        assert not asr_intent.asr_acknowledged
        assert asr_intent.asr_receipt is None
        for now in (
            timezone.now() + timedelta(seconds=2),
            asr_intent.retention_expires_at,
        ):
            with patch("core.mastrao_native_asr_worker.timezone.now", return_value=now):
                assert not process_intent(asr_intent)
                assert schedule_native_asr(limit=1) == 0
        core_post.assert_called_once()
        transcribe.assert_not_called()
        upload.assert_not_called()
        ack.assert_not_called()


def test_recovery_preserves_budget_and_prepares_authorized_successor(asr_intent, peers):
    prepare, transcribe, upload, ack = peers
    consumed = {"metadata": {"attempt_ref": "consumed_native_attempt"}}
    prepare.return_value = consumed, b"audio"
    transcribe.side_effect = NativeAsrDeliveryRefused(status=503, outcome="unknown")

    for _ in range(9):
        models.MastraoNativeCaptureStart.objects.filter(pk=asr_intent.pk).update(
            asr_next_at=timezone.now() - timedelta(seconds=1)
        )
        assert not process_intent(asr_intent)
        asr_intent.refresh_from_db()
        assert asr_intent.asr_attempts == 0
        assert asr_intent.asr_error == "native_asr_outcome_unknown"
    upload.assert_not_called()
    ack.assert_not_called()

    deadline = timezone.now() + timedelta(seconds=120)
    transcribe.side_effect = NativeAsrDeliveryRefused(
        status=429, outcome="rejected", retry_at=deadline
    )
    models.MastraoNativeCaptureStart.objects.filter(pk=asr_intent.pk).update(
        asr_next_at=timezone.now() - timedelta(seconds=1)
    )
    assert not process_intent(asr_intent)
    asr_intent.refresh_from_db()
    assert asr_intent.asr_next_at == deadline
    assert not process_intent(asr_intent)

    successor = {"metadata": {"attempt_ref": "successor_native_attempt"}}
    prepare.return_value = successor, b"audio"
    transcribe.side_effect = None
    upload.return_value["core"]["attempt_ref"] = successor["metadata"]["attempt_ref"]
    with patch(
        "core.mastrao_native_asr_worker.timezone.now",
        return_value=deadline + timedelta(seconds=1),
    ):
        assert process_intent(asr_intent)

    transcribe.assert_called_with(successor, b"audio")
    upload.assert_called_once()
    ack.assert_called_once()
    asr_intent.refresh_from_db()
    assert asr_intent.asr_attempts == 1
    assert asr_intent.asr_acknowledged
    assert not asr_intent.asr_error


def test_stale_lease_cannot_finish_or_ack_another_workers_result(asr_intent, peers):
    first = _claim(*_reserve_dispatch(asr_intent.pk))
    assert first is not None
    assert _claim(asr_intent.pk, uuid4()) is None
    models.MastraoNativeCaptureStart.objects.filter(pk=asr_intent.pk).update(
        asr_claim_until=timezone.now() - timedelta(seconds=1)
    )
    second = _claim(*_reserve_dispatch(asr_intent.pk))
    assert second.asr_claim != first.asr_claim
    assert not _finish(first, True)
    assert _finish(second, False)
    peers[3].assert_not_called()


def test_lease_loss_before_core_receipt_commit_preserves_gateway_copy(
    asr_intent, peers
):
    def lose_claim(*_args):
        models.MastraoNativeCaptureStart.objects.filter(pk=asr_intent.pk).update(
            asr_claim=uuid4()
        )
        return peers[2].return_value

    peers[2].side_effect = lose_claim
    assert not process_intent(asr_intent)
    peers[3].assert_not_called()
    asr_intent.refresh_from_db()
    assert asr_intent.asr_receipt is None


def test_disabled_or_unverified_source_is_not_scheduled(asr_intent, peers, settings):
    settings.MASTRAO_NATIVE_ASR_ENABLED = False
    assert not process_intent(asr_intent)
    settings.MASTRAO_NATIVE_ASR_ENABLED = True
    models.MastraoNativeCaptureStart.objects.filter(pk=asr_intent.pk).update(
        source_receipt=None
    )
    assert not process_intent(asr_intent)
    settings.CELERY_ENABLED = False
    assert schedule_native_asr() == 0
    peers[0].assert_not_called()


def test_prepare_accepts_scoped_large_response_but_refuses_changed_audio(
    asr_intent, settings
):
    settings.MASTRAO_CORE_NATIVE_ASR_PREPARE_ENDPOINT = (
        "http://127.0.0.1:9000/internal/v1/meetings/capture/native/asr/prepare"
    )
    audio = b"OggS" + b"x" * 30000
    prepared = {
        "version": 1,
        "source_ref": asr_intent.source_receipt["source_ref"],
        "capture_ref": str(asr_intent.capture_ref),
        "epoch_ref": str(asr_intent.epoch_id),
        "metadata": {
            "provider": "mistral",
            "audio_codec": "ogg",
            "audio_sha256": hashlib.sha256(audio).hexdigest(),
        },
        "native_source_egress_grant": "signed-fixture-placeholder",
        "audio_base64": base64.b64encode(audio).decode(),
    }
    with patch(
        "core.mastrao_native_asr_client.post_core_json", return_value=prepared
    ) as core:
        assert prepare_native_asr(asr_intent)[1] == audio
        assert core.call_args.kwargs["maximum_response_bytes"] == 24 * 1024**2
        prepared["metadata"]["audio_sha256"] = "f" * 64
        with pytest.raises(RecordingContractRefused):
            prepare_native_asr(asr_intent)


def test_gateway_receives_the_core_ogg_contract(settings):
    settings.MASTRAO_NATIVE_ASR_GATEWAY_ENDPOINT = (
        "http://asr-gateway/v1/native/transcribe"
    )
    settings.MASTRAO_NATIVE_ASR_GATEWAY_AUTH_TOKEN = "native-gateway-fixture-only-token"
    audio = b"OggS" + b"audio"
    prepared = {
        "metadata": {
            "audio_sha256": hashlib.sha256(audio).hexdigest(),
            "audio_codec": "ogg",
        },
        "native_source_egress_grant": "signed-fixture-placeholder",
    }
    result = {
        "outcome": "succeeded",
        "transcript": {
            "audio_digest": prepared["metadata"]["audio_sha256"],
        },
    }
    with (
        patch("core.mastrao_native_asr_client.requests.Session") as session,
        patch(
            "core.mastrao_native_asr_client.read_bounded_core_json",
            return_value=result,
        ),
    ):
        session.return_value.__enter__.return_value.post.return_value.status_code = 200
        assert transcribe_native_asr(prepared, audio) == result

    request = session.return_value.__enter__.return_value.post
    assert request.call_args.kwargs["files"]["audio"] == (
        "source.ogg",
        audio,
        "audio/ogg",
    )


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://api.eu.mistral.ai/v1/native/transcribe",
        "http://asr-gateway/v1/transcribe",
        "http://user:password@asr-gateway/v1/native/transcribe",
        "http://asr-gateway/v1/native/transcribe?x=1",
    ],
)
def test_client_never_targets_direct_provider_or_legacy_gateway(endpoint, settings):
    settings.MASTRAO_NATIVE_ASR_GATEWAY_ENDPOINT = endpoint
    settings.MASTRAO_NATIVE_ASR_GATEWAY_AUTH_TOKEN = "native-gateway-fixture-only-token"
    with (
        patch("core.mastrao_native_asr_client.requests.Session") as session,
        pytest.raises(RecordingContractRefused),
    ):
        transcribe_native_asr({}, b"audio")
    session.assert_not_called()


def test_large_core_bound_is_opt_in_and_closes_oversized_streams():
    raw = json.dumps({"audio_base64": "x" * 30000}).encode()

    def response():
        item = Mock(status_code=200, headers={})
        item.iter_content.return_value = [raw]
        return item

    small = response()
    with pytest.raises(RecordingContractRefused):
        read_bounded_core_json(small, RecordingContractRefused)
    small.close.assert_called_once()
    assert (
        len(
            read_bounded_core_json(
                response(), RecordingContractRefused, maximum_bytes=40000
            )["audio_base64"]
        )
        == 30000
    )
