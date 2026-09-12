"""Bounded native drain: exact job observation, never another capture start.

Admission rollback must not disable this consumer. A terminal provider status
proves termination only, not complete audio, durable fragments or ASR readiness.
"""

import asyncio
from datetime import timedelta
from uuid import uuid4

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from asgiref.sync import async_to_sync
from livekit import api

from core import models, utils
from core.mastrao_native_capture_adapter import _matches, native_request
from core.mastrao_recording_contract import RecordingContractRefused

TERMINAL = {
    api.EgressStatus.EGRESS_COMPLETE,
    api.EgressStatus.EGRESS_FAILED,
    api.EgressStatus.EGRESS_ABORTED,
    api.EgressStatus.EGRESS_LIMIT_REACHED,
}
RUNNING = {api.EgressStatus.EGRESS_STARTING, api.EgressStatus.EGRESS_ACTIVE}


def _stop_reason(intent, now):
    epoch = intent.epoch
    connection = epoch.connection
    room = connection.room_binding
    if room.closing_at is not None or hasattr(room, "closure"):
        return "room_closed"
    if connection.correlation != "correlated" or epoch.conflict:
        return "quarantined"
    if connection.ended or epoch.ended:
        return "source_ended"
    if intent.retention_expires_at is None:
        return "retention_unknown"
    if intent.retention_expires_at <= now:
        return "retention_expired"
    return ""


@transaction.atomic
def _claim_next():
    now = timezone.now()
    intent = (
        models.MastraoNativeCaptureStart.objects.select_for_update(skip_locked=True)
        .filter(drained_at__isnull=True, next_check_at__lte=now)
        .filter(Q(drain_claim_until__isnull=True) | Q(drain_claim_until__lte=now))
        .order_by("next_check_at", "pk")
        .first()
    )
    if intent is None:
        return None
    reason = _stop_reason(intent, now)
    if reason and intent.stop_requested_at is None:
        intent.stop_requested_at = now
        intent.stop_reason = reason
    intent.drain_claim = uuid4()
    intent.drain_claim_until = now + timedelta(seconds=45)
    intent.save(
        update_fields=[
            "stop_requested_at",
            "stop_reason",
            "drain_claim",
            "drain_claim_until",
            "updated_at",
        ]
    )
    return intent


@async_to_sync
async def _observe_and_stop(intent):
    request = native_request(intent)
    client = None
    try:
        client = utils.create_livekit_client(
            {**settings.LIVEKIT_CONFIGURATION, "failover": False}
        )
        result = await asyncio.wait_for(
            client.egress.list_egress(
                api.ListEgressRequest(room_name=request.room_name)
            ),
            timeout=5,
        )
        matches = [
            job
            for job in result.items
            if _matches(job, request, intent.epoch.connection.room_sid)
        ]
        if len(matches) != 1:
            raise RecordingContractRefused(status=409 if matches else 503)
        job = matches[0]
        if intent.provider_job_ref and job.egress_id != intent.provider_job_ref:
            raise RecordingContractRefused(status=409)
        if job.status not in TERMINAL | RUNNING | {api.EgressStatus.EGRESS_ENDING}:
            raise RecordingContractRefused(status=503)
        if intent.stop_requested_at is not None and job.status in RUNNING:
            # Ignore the stop acknowledgement: only a later List of this exact
            # job may establish terminal state. Lost replies remain retryable.
            await asyncio.wait_for(
                client.egress.stop_egress(
                    api.StopEgressRequest(egress_id=job.egress_id)
                ),
                timeout=5,
            )
        return job
    except RecordingContractRefused:
        raise
    except Exception:  # noqa: BLE001 - upstream errors may expose credentials.
        raise RecordingContractRefused(status=503) from None
    finally:
        if client is not None:
            try:
                await asyncio.wait_for(client.aclose(), timeout=5)
            except Exception:  # noqa: BLE001 - retain uncertainty on close failure.
                raise RecordingContractRefused(status=503) from None


@transaction.atomic
def _finish(intent, job):
    now = timezone.now()
    locked = (
        models.MastraoNativeCaptureStart.objects.select_for_update()
        .filter(pk=intent.pk, drain_claim=intent.drain_claim, drain_claim_until__gt=now)
        .first()
    )
    if locked is None:
        return False
    if job is not None and (
        not locked.provider_job_ref or locked.provider_job_ref == job.egress_id
    ):
        locked.provider_job_ref = job.egress_id
        locked.observed_status = int(job.status)
        if job.status in TERMINAL:
            locked.drained_at = now
    locked.drain_claim = None
    locked.drain_claim_until = None
    locked.next_check_at = now + timedelta(seconds=30)
    locked.save(
        update_fields=[
            "provider_job_ref",
            "observed_status",
            "drained_at",
            "drain_claim",
            "drain_claim_until",
            "next_check_at",
            "updated_at",
        ]
    )
    return locked.drained_at is not None


def reconcile_native_captures(limit=20):
    """Called by the existing recording reconciler, including after flag rollback."""
    drained = 0
    for _ in range(max(0, min(limit, 20))):
        intent = _claim_next()
        if intent is None:
            break
        # _stop_reason has materialized the related objects outside async I/O.
        try:
            job = _observe_and_stop(intent)
        except RecordingContractRefused:
            job = None
        drained += int(_finish(intent, job))
    return drained
