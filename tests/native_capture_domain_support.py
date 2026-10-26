"""Explicit native metadata fixtures and real signed local room-close receipts."""

# ruff: noqa: PLC0415 -- Core models must load after settings.configure().

import sys
from pathlib import Path


def configure_domain_tests(socket_directory=None):
    """Accept only the disposable socket directory emitted by the Core helper."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src/backend"))
    database = {"ENGINE": "django.db.backends.dummy"}
    if socket_directory is not None:
        directory = Path(socket_directory).resolve(strict=True)
        if directory.parent != Path("/tmp") or not directory.name.startswith(
            "mastrao-shared-runtime-"
        ):
            raise ValueError("Use the existing Core disposable PostgreSQL helper")
        database = {
            "ENGINE": "django.db.backends.postgresql",
            "HOST": str(directory),
            "NAME": "postgres",
            "USER": "postgres",
            "OPTIONS": {"connect_timeout": 5},
        }
    from django.conf import settings

    settings.configure(
        INSTALLED_APPS=["django.contrib.auth", "django.contrib.contenttypes", "core"],
        AUTH_USER_MODEL="core.User",
        DATABASES={"default": database},
        USE_TZ=True,
        TIME_ZONE="UTC",
        LANGUAGES=[("en", "English")],
        LANGUAGE_CODE="en",
        RESOURCE_DEFAULT_ACCESS_LEVEL="public",
        ROOM_TELEPHONY_ENABLED=False,
        ROOMKIT_ENABLED=False,
        CELERY_ENABLED=False,
        CACHES={
            "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}
        },
        SECRET_KEY="synthetic-domain-tests-only",
    )
    import django

    django.setup()


def configure_synthetic_signing():
    """Generate an ephemeral local fixture key; never read application credentials."""
    import base64
    import json

    from django.conf import settings

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    private = Ed25519PrivateKey.generate()
    settings.MASTRAO_RECORDING_RECEIPT_PRIVATE_JWK = json.dumps(
        {
            "kty": "OKP",
            "crv": "Ed25519",
            "d": base64.urlsafe_b64encode(private.private_bytes_raw())
            .rstrip(b"=")
            .decode(),
        }
    )
    settings.MASTRAO_RECORDING_RECEIPT_ISSUER = "synthetic-meet-domain"
    settings.MASTRAO_RECORDING_RECEIPT_AUDIENCE = "synthetic-core-domain"
    settings.MASTRAO_RECORDING_RECEIPT_KEY_ID = "synthetic-domain-key"
    settings.MASTRAO_MEETING_INTEGRATION_CONFIGURED = True
    settings.MASTRAO_CORE_NATIVE_CAPTURE_DOMAIN_ENDPOINT = (
        "http://127.0.0.1:59999/internal/v1/meetings/capture/native/domain"
    )
    settings.MASTRAO_CORE_RECORDING_TIMEOUT_SECONDS = 5
    return private.public_key()


def synthetic_rtc_binding():
    """Persist synthetic local rows, without claims of signed/physical RTC intake."""
    from datetime import timedelta
    from uuid import uuid4

    from django.utils import timezone

    from core import models

    reference = uuid4().hex
    user = models.User.objects.create(sub=reference, password="!synthetic-unusable")
    room = models.Room.objects.create(name=f"synthetic_rtc_{reference}")
    binding = models.MastraoRoomBinding.objects.create(
        room=room,
        owner=user,
        effect_key=f"room_effect_{reference}",
        arguments_digest="a" * 64,
        meeting_ref=f"meeting_{reference}",
        room_ref=f"room_{reference}",
        owner_ref=f"owner_{reference}",
        provider_binding_digest="b" * 64,
    )
    recording = models.MastraoRecordingBinding.objects.create(
        room_binding=binding,
        organization_external_id="synthetic_exact_organization",
        meeting_ref=binding.meeting_ref,
        room_ref=binding.room_ref,
        recording_ref=f"recording_{reference}",
        provider_binding_digest="b" * 64,
        policy_ref=f"policy_{reference}",
        notice_version=f"notice_{reference}",
        notice_digest="c" * 64,
        retention_expires_at=timezone.now() + timedelta(days=1),
    )
    return binding, recording


def synthetic_rtc_publication(
    binding, *, track="TR_synthetic", event="track_published"
):
    from uuid import uuid4

    from core import models

    return models.MastraoRtcObservation.objects.create(
        room_binding=binding,
        event_id=f"EV_{uuid4().hex}",
        payload_digest="d" * 64,
        event_type=event,
        event_time_seconds=1,
        room_sid="RM_synthetic",
        participant_sid="PA_synthetic",
        rtc_identity="synthetic_rtc_only",
        track_sid=track,
        track_type=0,
        track_source=2,
    )


def synthetic_rtc_capture(binding):
    from uuid import uuid4

    from core import models

    publication = synthetic_rtc_publication(binding)
    connection = models.MastraoRtcConnection.objects.create(
        room_binding=binding,
        room_sid=publication.room_sid,
        participant_sid=publication.participant_sid,
        correlation="missing_join",
        ended=True,
    )
    epoch = models.MastraoRtcTrackEpoch.objects.create(
        connection=connection,
        track_sid=publication.track_sid,
        first_publication=publication,
        ended=True,
        conflict=True,
    )
    capture = models.MastraoNativeCaptureStart.objects.create(
        epoch=epoch,
        capture_ref=uuid4(),
        effect_key=f"capture_{uuid4().hex}",
        arguments_digest="e" * 64,
        organization_external_id="synthetic_exact_organization",
    )
    return publication, epoch, capture


def apply_synthetic_room_close(binding, recording):
    """Real signed close view; synthetic Core effect and SDK/cache boundaries."""
    import base64
    import json
    import time
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, patch

    from django.conf import settings
    from django.test import RequestFactory

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from core.mastrao_room_close_adapter import close_mastrao_room
    from core.mastrao_room_close_contract import (
        ROOM_CLOSE_EFFECT_JOSE_TYPE,
        ROOM_CLOSE_EFFECT_TYPE,
        _sign,
        close_arguments,
    )
    from core.mastrao_room_contract import _sha256_canonical

    settings.MASTRAO_ROOM_RECEIPT_PRIVATE_JWK = (
        settings.MASTRAO_RECORDING_RECEIPT_PRIVATE_JWK
    )
    settings.MASTRAO_ROOM_RECEIPT_ISSUER = settings.MASTRAO_RECORDING_RECEIPT_ISSUER
    settings.MASTRAO_ROOM_RECEIPT_AUDIENCE = settings.MASTRAO_RECORDING_RECEIPT_AUDIENCE
    settings.MASTRAO_ROOM_RECEIPT_KEY_ID = settings.MASTRAO_RECORDING_RECEIPT_KEY_ID
    private_jwk = json.loads(settings.MASTRAO_ROOM_RECEIPT_PRIVATE_JWK)
    private = Ed25519PrivateKey.from_private_bytes(
        base64.urlsafe_b64decode(private_jwk["d"] + "==")
    )
    public_jwk = {
        "kty": "OKP",
        "crv": "Ed25519",
        "x": base64.urlsafe_b64encode(private.public_key().public_bytes_raw())
        .rstrip(b"=")
        .decode(),
    }
    settings.MASTRAO_ROOM_EFFECT_PUBLIC_JWK = json.dumps(public_jwk)
    settings.MASTRAO_ROOM_EFFECT_ISSUER = "synthetic-close-core"
    settings.MASTRAO_ROOM_EFFECT_AUDIENCE = "synthetic-close-meet"
    settings.MASTRAO_ROOM_EFFECT_KEY_ID = settings.MASTRAO_ROOM_RECEIPT_KEY_ID
    now = int(time.time())
    effect = {
        "version": 1,
        "type": ROOM_CLOSE_EFFECT_TYPE,
        "issuer": settings.MASTRAO_ROOM_EFFECT_ISSUER,
        "audience": settings.MASTRAO_ROOM_EFFECT_AUDIENCE,
        "operation": "close_private_room",
        "operation_version": 1,
        "organization_external_id": recording.organization_external_id,
        "meeting_ref": binding.meeting_ref,
        "room_ref": binding.room_ref,
        "provider_binding_digest": binding.provider_binding_digest,
        "close_ref": f"close_synthetic_{binding.pk.hex}",
        "effect_key": f"close_effect_synthetic_{binding.pk.hex}",
        "issued_at": now,
        "expires_at": now + 30,
        "jti": f"close_effect_synthetic_{binding.pk.hex}",
    }
    effect["arguments_digest"] = _sha256_canonical(close_arguments(effect))
    sdk = SimpleNamespace(
        room=SimpleNamespace(delete_room=AsyncMock()), aclose=AsyncMock()
    )
    body = json.dumps({"room_close_effect": _sign(effect, ROOM_CLOSE_EFFECT_JOSE_TYPE)})
    receipts = []
    with (
        patch(
            "core.services.room_management.utils.create_livekit_client",
            return_value=sdk,
        ),
        patch("core.mastrao_room_close_adapter.LobbyService.clear_room_cache") as cache,
    ):
        for _ in range(2):
            response = close_mastrao_room(
                RequestFactory().post(
                    "/internal/close", body, content_type="application/json"
                )
            )
            if response.status_code != 200:
                raise AssertionError(
                    f"Real Python room close refused: {response.status_code}"
                )
            receipts.append(json.loads(response.content)["room_close_receipt"])
    if (
        receipts[0] != receipts[1]
        or sdk.room.delete_room.await_count != 1
        or cache.call_count != 1
    ):
        raise AssertionError(
            "Room close replay repeated effects or changed its receipt"
        )
    return {
        "receipt": receipts[0],
        "replay_receipt": receipts[1],
        "sdk_deletes": sdk.room.delete_room.await_count,
        "sdk_closes": sdk.aclose.await_count,
        "cache_clears": cache.call_count,
    }


def _prepare_legacy_compact_baseline(binding):
    """Simulate a pre-fix persisted revision without changing the real receipt."""
    from unittest.mock import patch

    from core import mastrao_native_capture_domain as domain

    original_closure = domain._closure_snapshot

    def historical_compact_closure(facts):
        snapshot = original_closure(facts)
        snapshot["receipt_digest"] = facts.closure["receipt_digest"]
        return snapshot

    with patch.object(
        domain, "_closure_snapshot", side_effect=historical_compact_closure
    ):
        return domain.prepare_native_capture_domain(binding.pk)


def export_synthetic_domain_proof(*, baseline_kind="none"):
    """Export fresh real-producer JWS for a parent-owned Core integration bridge.

    Call configure_domain_tests(socket_directory) first, using the existing Core
    disposable PostgreSQL helper. Everything here is an explicit synthetic RTC
    fixture. No provider, media, Core service, application settings or env file.
    The returned public key is sufficient to verify signatures; no private key
    or application credential leaves this process. Consume within 30 seconds.
    """
    import base64
    import json
    from importlib.metadata import version

    from django.conf import settings
    from django.core.management import call_command
    from django.utils import timezone

    from core import mastrao_native_capture_domain as domain
    from core import models

    call_command("migrate", verbosity=0, interactive=False)
    public_key = configure_synthetic_signing()
    binding, recording = synthetic_rtc_binding()
    publication, epoch, capture = synthetic_rtc_capture(binding)
    terminal = synthetic_rtc_publication(binding, event="track_unpublished")
    now = timezone.now()
    for observation in (publication, terminal):
        observation.event_time_seconds = int(now.timestamp())
        observation.payload_digest = domain._sha256_canonical(
            {
                "synthetic_event_id": observation.event_id,
                "event_type": observation.event_type,
            }
        )
        observation.save()
    models.MastraoRtcConnection.objects.filter(pk=epoch.connection_id).update(
        correlation="correlated"
    )
    epoch.conflict = False
    epoch.native_admission_capture_ref = capture.capture_ref
    epoch.save()
    capture.provider_job_ref = f"EG_SyntheticExport{capture.capture_ref.hex}"
    capture.observed_status = 3
    capture.stop_requested_at = now
    capture.stop_reason = "room_closed"
    capture.drained_at = now
    # Metadata fixture only: no media object is created or claimed verified.
    capture.source_manifest = {
        "version": 1,
        "format": "native_track_egress_ogg_opus_v1",
        "object_ref": f"recordings/{capture.capture_ref}.ogg",
        "started_at": "1000000000",
        "ended_at": "2000000000",
        "duration": "1000000000",
        "size": 1234,
        "coverage": "epoch_only",
    }
    capture.save()
    baseline = None
    if baseline_kind == "before_close":
        baseline = domain.prepare_native_capture_domain(binding.pk)
    close_proof = apply_synthetic_room_close(binding, recording)
    closure = models.MastraoRoomClosure.objects.get(room_binding=binding)
    close_proof["persisted_claims"] = closure.receipt_claims
    close_proof["compact_digest"] = closure.receipt_digest
    if baseline_kind == "legacy_compact":
        baseline = _prepare_legacy_compact_baseline(binding)
    first = domain.prepare_native_capture_domain(binding.pk)
    old_claim = domain.claim_native_capture_domain(first.pk)
    # The first inventory contains the publication and its terminal fact.
    # Reach exactly 129 observations, retaining the original immutable members.
    models.MastraoRtcObservation.objects.bulk_create(
        [
            models.MastraoRtcObservation(
                room_binding=binding,
                event_id=f"EV_synthetic_export_{binding.pk.hex}_{index}",
                payload_digest="e" * 64,
                event_type="track_published",
                event_time_seconds=int(now.timestamp()),
                room_sid="RM_synthetic",
                participant_sid="PA_synthetic",
                track_sid=f"TR_synthetic_export_{index}",
                track_type=0,
                track_source=2,
            )
            for index in range(domain.MAX_OBSERVATIONS - 1)
        ]
    )
    invalidation = domain.prepare_native_capture_domain(binding.pk)
    replay = domain.prepare_native_capture_domain(binding.pk)
    if replay.snapshot != invalidation.snapshot:
        raise AssertionError("Synthetic invalidation replay changed durable content")
    if domain.finish_native_capture_domain(old_claim, acknowledged=True):
        raise AssertionError("Old inventory claim acknowledged newer invalidation")

    def exported(cursor):
        body = domain._wire_body(cursor.snapshot)
        return {
            "snapshot": cursor.snapshot,
            "request": body,
            "signed_body_bytes": len(json.dumps(body).encode()),
        }

    # Sign at the very end; test-suite duration cannot expire these fixtures.
    return {
        "fixture": "synthetic_rtc_hf24_python_producer_v1",
        "versions": {
            name: version(name)
            for name in ("Django", "PyJWT", "psycopg", "livekit-api")
        },
        "close": close_proof,
        "baseline": exported(baseline) if baseline is not None else None,
        "limitations": "Synthetic RTC and manifest metadata; Core must seed its own exact authorities. No media/ASR/physical RTC proof.",
        "trust": {
            "issuer": settings.MASTRAO_RECORDING_RECEIPT_ISSUER,
            "audience": settings.MASTRAO_RECORDING_RECEIPT_AUDIENCE,
            "kid": settings.MASTRAO_RECORDING_RECEIPT_KEY_ID,
            "public_jwk": {
                "kty": "OKP",
                "crv": "Ed25519",
                "x": base64.urlsafe_b64encode(public_key.public_bytes_raw())
                .rstrip(b"=")
                .decode(),
            },
        },
        "core_seed": {
            "organization_external_id": recording.organization_external_id,
            "meeting_ref": binding.meeting_ref,
            "room_ref": binding.room_ref,
            "provider_binding_digest": binding.provider_binding_digest,
            "recording_ref": recording.recording_ref,
            "policy_ref": recording.policy_ref,
            "notice_version": recording.notice_version,
            "notice_digest": recording.notice_digest,
            "retention_expires_at": int(recording.retention_expires_at.timestamp()),
            "close_ref": closure.close_ref,
        },
        "inventory": exported(first),
        "invalidation": exported(invalidation),
        "invalidation_retry": exported(replay),
        "old_inventory_retry": exported(first),
    }
