"""Durable delivery of native sources, with Core-owned attempts and transcripts."""

# Generated LiveKit protobuf members and fail-closed contract checks are intentional here.
# pylint: disable=broad-exception-caught,import-outside-toplevel,missing-function-docstring

import logging
from datetime import timedelta
from uuid import UUID, uuid4

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import connection, transaction
from django.db.models import Count, F, Min, Q
from django.utils import timezone

import requests

from core import models
from core.mastrao_native_asr_client import (
    ack_native_asr,
    prepare_native_asr,
    transcribe_native_asr,
    upload_native_asr_result,
)
from core.mastrao_recording_contract import RecordingContractRefused

logger = logging.getLogger(__name__)

READY_QUEUE = "mastrao-native-asr-ready"
RETRY_QUEUE = "mastrao-native-asr-retry"
DISPATCH_LOCK = 0x4D415352


def _capacity_limits():
    global_limit = settings.MASTRAO_NATIVE_ASR_GLOBAL_LIMIT
    tenant_limit = settings.MASTRAO_NATIVE_ASR_TENANT_LIMIT
    meeting_limit = settings.MASTRAO_NATIVE_ASR_MEETING_LIMIT
    if not (2 <= tenant_limit <= global_limit and 1 <= meeting_limit <= tenant_limit):
        raise ImproperlyConfigured(
            "Native ASR capacity requires 2 <= tenant <= global and "
            "1 <= meeting <= tenant"
        )
    return global_limit, tenant_limit, meeting_limit


def _retrying():
    return Q(asr_attempts__gt=0) | ~Q(asr_error="") | Q(asr_receipt__isnull=False)


def _queue_for(intent):
    if intent.asr_attempts or intent.asr_error or intent.asr_receipt is not None:
        return RETRY_QUEUE
    return READY_QUEUE


def _lock_dispatch_capacity():
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(%s)", [DISPATCH_LOCK])


def _eligible(now):
    return models.MastraoNativeCaptureStart.objects.filter(
        source_receipt__isnull=False,
        asr_acknowledged=False,
        asr_next_at__lte=now,
        retention_expires_at__gt=now,
        epoch__conflict=False,
        epoch__connection__correlation="correlated",
    ).filter(Q(asr_claim_until__isnull=True) | Q(asr_claim_until__lte=now))


@transaction.atomic
def _claim(capture_start_id, dispatch_ref):
    _lock_dispatch_capacity()
    now = timezone.now()
    intent = (
        _eligible(now)
        .filter(
            pk=capture_start_id,
            asr_dispatch_ref=dispatch_ref,
        )
        .select_for_update(skip_locked=True, of=("self",))
        .first()
    )
    if intent is None:
        return None
    if not _has_capacity(intent, now):
        return None
    intent.asr_claim = uuid4()
    intent.asr_claim_until = now + timedelta(seconds=180)
    intent.asr_attempts += 1
    intent.asr_dispatch_ref = None
    intent.asr_dispatch_until = None
    intent.save(
        update_fields=[
            "asr_claim",
            "asr_claim_until",
            "asr_attempts",
            "asr_dispatch_ref",
            "asr_dispatch_until",
            "updated_at",
        ]
    )
    return intent


def _owned(intent):
    return models.MastraoNativeCaptureStart.objects.filter(
        pk=intent.pk,
        asr_claim=intent.asr_claim,
        asr_claim_until__gt=timezone.now(),
        asr_acknowledged=False,
        retention_expires_at__gt=timezone.now(),
        epoch__conflict=False,
        epoch__connection__correlation="correlated",
    )


def _fresh(intent):
    if not _owned(intent).exists():
        raise RecordingContractRefused(status=503)


def _finish(intent, acknowledged, error=None):
    now = timezone.now()
    retry_streak = min(intent.asr_retry_streak + 1, 7)
    local_delay = min(30 * 2 ** (retry_streak - 1), 1800)
    values = {
        "asr_acknowledged": acknowledged,
        "asr_claim": None,
        "asr_claim_until": None,
        "asr_dispatch_queue": "",
        "asr_next_at": now + timedelta(seconds=local_delay),
        "asr_retry_streak": 0 if acknowledged else retry_streak,
        "asr_error": "" if acknowledged else "native_asr_delivery_failed",
        "updated_at": now,
    }
    if isinstance(error, RecordingContractRefused):
        if error.status == 429 or error.outcome in {"retry", "unknown"}:
            # Core bounds provider sends. Recovering a refused/uncertain attempt
            # must not consume the local delivery budget before Core can replace it.
            values["asr_attempts"] = F("asr_attempts") - 1
            deadline = getattr(error, "retry_at", None)
            if deadline is not None:
                values["asr_next_at"] = deadline
            if error.outcome == "unknown":
                values["asr_error"] = "native_asr_outcome_unknown"
        elif error.outcome in {"rejected", "failed"}:
            values["asr_next_at"] = intent.retention_expires_at
            values["asr_error"] = "native_asr_rejected"
    if _owned(intent).update(**values) != 1:
        return False
    transaction.on_commit(lambda: schedule_native_asr(limit=1))
    return True


def process_native_asr_source(capture_start_id, dispatch_ref):
    """No synchronous API work, no direct ASR secret, no video-finalization gate."""
    if not settings.MASTRAO_NATIVE_ASR_ENABLED:
        return False
    try:
        capture_start_id = UUID(str(capture_start_id))
        dispatch_ref = UUID(str(dispatch_ref))
    except (ValueError, TypeError, AttributeError):
        return False
    intent = _claim(capture_start_id, dispatch_ref)
    if intent is None:
        return False
    try:
        receipt = intent.asr_receipt
        if receipt is None:
            prepared, audio = prepare_native_asr(intent)
            _fresh(intent)
            result = transcribe_native_asr(prepared, audio)
            _fresh(intent)
            receipt = upload_native_asr_result(intent, prepared, result)
            # The gateway cache cannot be released before durable receipt commit.
            if (
                _owned(intent)
                .filter(asr_receipt__isnull=True)
                .update(asr_receipt=receipt, updated_at=timezone.now())
                != 1
            ):
                raise RecordingContractRefused(status=503)
        _fresh(intent)
        ack_native_asr(receipt)
        return _finish(intent, True)
    except (
        RecordingContractRefused,
        requests.RequestException,
        ValueError,
        TypeError,
        KeyError,
        OSError,
    ) as error:
        _finish(intent, False, error)
        logger.warning("Native ASR delivery failed; durable recovery state retained")
        return False


def _dispatchable(now):
    return _eligible(now).filter(
        Q(asr_dispatch_until__isnull=True) | Q(asr_dispatch_until__lte=now)
    )


def _active_dispatches(now):
    return models.MastraoNativeCaptureStart.objects.filter(
        asr_acknowledged=False,
    ).filter(Q(asr_dispatch_until__gt=now) | Q(asr_claim_until__gt=now))


def _active_retry_counts(active):
    grouped = (
        active.filter(
            Q(asr_dispatch_queue=RETRY_QUEUE) | (Q(asr_dispatch_queue="") & _retrying())
        )
        .values("organization_external_id")
        .annotate(total=Count("pk"))
    )
    tenants = {row["organization_external_id"]: row["total"] for row in grouped}
    return sum(tenants.values()), tenants


def _capacity_counts(active):
    tenants = {}
    meetings = {}
    meeting_field = "epoch__connection__room_binding__meeting_ref"
    grouped = active.values("organization_external_id", meeting_field).annotate(
        total=Count("pk")
    )
    for row in grouped:
        tenant = row["organization_external_id"]
        meeting = (tenant, row[meeting_field])
        tenants[tenant] = tenants.get(tenant, 0) + row["total"]
        meetings[meeting] = meetings.get(meeting, 0) + row["total"]
    return sum(tenants.values()), tenants, meetings


def _within_limits(tenant, meeting, tenants, meetings):
    if tenants.get(tenant, 0) >= settings.MASTRAO_NATIVE_ASR_TENANT_LIMIT:
        return False
    if meetings.get((tenant, meeting), 0) >= settings.MASTRAO_NATIVE_ASR_MEETING_LIMIT:
        return False
    return True


def _has_capacity(intent, now):
    global_limit, tenant_limit, _ = _capacity_limits()
    active = _active_dispatches(now).exclude(pk=intent.pk)
    active_count, tenants, meetings = _capacity_counts(active)
    if active_count >= global_limit:
        return False
    meeting = intent.epoch.connection.room_binding.meeting_ref
    tenant = intent.organization_external_id
    if not _within_limits(tenant, meeting, tenants, meetings):
        return False
    if _queue_for(intent) == RETRY_QUEUE:
        retry_count, retry_tenants = _active_retry_counts(active)
        return (
            retry_count < global_limit - 1
            and retry_tenants.get(tenant, 0) < tenant_limit - 1
        )
    return True


def native_asr_dispatch_metrics(now=None):
    """Report aggregate backlog and capacity, without source or tenant labels."""
    now = now or timezone.now()
    due = _dispatchable(now)
    ready = due.filter(~_retrying()).aggregate(
        count=Count("pk"), oldest=Min("asr_next_at")
    )
    retry = due.filter(_retrying()).aggregate(
        count=Count("pk"), oldest=Min("asr_next_at")
    )

    def age(oldest):
        if oldest is None:
            return 0
        return max(0, int((now - oldest).total_seconds()))

    return {
        "ready_count": ready["count"],
        "retry_count": retry["count"],
        "ready_age_seconds": age(ready["oldest"]),
        "retry_age_seconds": age(retry["oldest"]),
        "active_count": _active_dispatches(now).count(),
    }


def _first_with_capacity(candidates, tenants, meetings, retry_tenants=None):
    identities = candidates.values_list(
        "pk", "organization_external_id", "epoch__connection__room_binding__meeting_ref"
    )
    for capture_start_id, tenant, meeting in identities.iterator(chunk_size=100):
        if not _within_limits(tenant, meeting, tenants, meetings):
            continue
        if (
            retry_tenants is not None
            and retry_tenants.get(tenant, 0)
            >= settings.MASTRAO_NATIVE_ASR_TENANT_LIMIT - 1
        ):
            continue
        intent = (
            candidates.filter(pk=capture_start_id)
            .select_for_update(skip_locked=True, of=("self",))
            .first()
        )
        if intent is not None:
            return intent
    return None


def _fresh_would_starve_retry(
    ready, dispatchable, capacity_counts, retry_counts, global_limit
):
    active_count, tenants, meetings = capacity_counts
    active_retries, retry_tenants = retry_counts
    tenant = ready.organization_external_id
    meeting = ready.epoch.connection.room_binding.meeting_ref
    if active_retries == 0 and active_count >= global_limit - 1:
        return True
    if (
        retry_tenants.get(tenant, 0) == 0
        and tenants.get(tenant, 0) >= settings.MASTRAO_NATIVE_ASR_TENANT_LIMIT - 1
    ):
        tenant_retry = _first_with_capacity(
            dispatchable.filter(_retrying(), organization_external_id=tenant),
            tenants,
            meetings,
            retry_tenants,
        )
        if tenant_retry is not None:
            return True
    if (
        meetings.get((tenant, meeting), 0)
        >= settings.MASTRAO_NATIVE_ASR_MEETING_LIMIT - 1
    ):
        meeting_retry = _first_with_capacity(
            dispatchable.filter(
                _retrying(),
                organization_external_id=tenant,
                epoch__connection__room_binding__meeting_ref=meeting,
            ),
            tenants,
            meetings,
            retry_tenants,
        )
        return meeting_retry is not None
    return False


@transaction.atomic
def _reserve_dispatch(capture_start_id=None, exclude_ids=()):
    global_limit, _, _ = _capacity_limits()
    _lock_dispatch_capacity()
    now = timezone.now()
    active = _active_dispatches(now)
    active_count, tenants, meetings = _capacity_counts(active)
    if active_count >= global_limit:
        return None
    active_retries, retry_tenants = _active_retry_counts(active)
    dispatchable = _dispatchable(now).exclude(pk__in=exclude_ids)
    backlog_retry = None
    if active_retries < global_limit - 1:
        backlog_retry = _first_with_capacity(
            dispatchable.filter(_retrying()),
            tenants,
            meetings,
            retry_tenants,
        )
    candidates = dispatchable
    if capture_start_id is not None:
        candidates = candidates.filter(pk=capture_start_id)
    candidates = candidates.order_by("asr_next_at", "pk")
    ready = _first_with_capacity(candidates.filter(~_retrying()), tenants, meetings)
    retry = backlog_retry
    if capture_start_id is not None and active_retries < global_limit - 1:
        retry = _first_with_capacity(
            candidates.filter(_retrying()),
            tenants,
            meetings,
            retry_tenants,
        )
    if (
        capture_start_id is not None
        and ready is not None
        and backlog_retry is not None
        and _fresh_would_starve_retry(
            ready,
            dispatchable,
            (active_count, tenants, meetings),
            (active_retries, retry_tenants),
            global_limit,
        )
    ):
        return None
    if (
        capture_start_id is None
        and ready is not None
        and retry is not None
        and backlog_retry is not None
        and _fresh_would_starve_retry(
            ready,
            dispatchable,
            (active_count, tenants, meetings),
            (active_retries, retry_tenants),
            global_limit,
        )
    ):
        intent = retry
    elif retry is not None and active_retries == 0:
        intent = retry
    else:
        intent = ready or retry
    if intent is None:
        return None
    dispatch_ref = intent.asr_dispatch_ref or uuid4()
    intent.asr_dispatch_ref = dispatch_ref
    intent.asr_dispatch_until = now + timedelta(minutes=5)
    intent.asr_dispatch_queue = _queue_for(intent)
    intent.save(
        update_fields=[
            "asr_dispatch_ref",
            "asr_dispatch_until",
            "asr_dispatch_queue",
            "updated_at",
        ]
    )
    return intent.pk, dispatch_ref


def _publish_dispatch(capture_start_id, dispatch_ref):
    from core.tasks.native_capture import (  # noqa: PLC0415  # pylint: disable=cyclic-import
        process_native_asr,
    )

    queue = (
        models.MastraoNativeCaptureStart.objects.filter(
            pk=capture_start_id, asr_dispatch_ref=dispatch_ref
        )
        .values_list("asr_dispatch_queue", flat=True)
        .first()
    )
    if queue not in {READY_QUEUE, RETRY_QUEUE}:
        return
    try:
        process_native_asr.apply_async(
            args=[str(capture_start_id), str(dispatch_ref)],
            queue=queue,
        )
    except Exception:  # noqa: BLE001 - broker errors may include credentials
        models.MastraoNativeCaptureStart.objects.filter(
            pk=capture_start_id, asr_dispatch_ref=dispatch_ref
        ).update(asr_dispatch_until=None)
        logger.warning("Native ASR dispatch unavailable; pending rows retained")


def _dispatch_one(capture_start_id=None, exclude_ids=()):
    reservation = _reserve_dispatch(capture_start_id, exclude_ids)
    if reservation is None:
        return None
    transaction.on_commit(lambda: _publish_dispatch(*reservation))
    return reservation[0]


def dispatch_native_asr(capture_start_id):
    if not settings.MASTRAO_NATIVE_ASR_ENABLED or not settings.CELERY_ENABLED:
        return False
    return _dispatch_one(capture_start_id) is not None


def schedule_native_asr(limit=20):
    if not settings.MASTRAO_NATIVE_ASR_ENABLED or not settings.CELERY_ENABLED:
        return 0
    dispatched_ids = set()
    for _ in range(limit):
        capture_start_id = _dispatch_one(exclude_ids=dispatched_ids)
        if capture_start_id is None:
            break
        dispatched_ids.add(capture_start_id)
    metrics = native_asr_dispatch_metrics()
    logger.info(
        "Native ASR dispatch ready_count=%d retry_count=%d ready_age_seconds=%d "
        "retry_age_seconds=%d active_count=%d dispatched_count=%d",
        metrics["ready_count"],
        metrics["retry_count"],
        metrics["ready_age_seconds"],
        metrics["retry_age_seconds"],
        metrics["active_count"],
        len(dispatched_ids),
    )
    return len(dispatched_ids)
