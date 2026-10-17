"""Celery delivery for delayed canonical meeting closure."""

from celery.signals import worker_ready

from core import models
from core.mastrao_idle_close import IdleCloseRetryable, deliver_idle_close
from core.tasks._task import task


@task(
    autoretry_for=(IdleCloseRetryable,),
    retry_kwargs={"countdown": 15},
    max_retries=None,
    acks_late=True,
    reject_on_worker_lost=True,
)
def process_idle_close(candidate_pk):
    """Reconcile one due empty-room close candidate."""

    deliver_idle_close(candidate_pk)


def recover_idle_close_tasks():
    """Re-enqueue durable claims after a worker or broker restart."""

    candidates = models.MastraoIdleCloseCandidate.objects.filter(
        state__in=[
            models.MastraoIdleCloseCandidate.State.PENDING,
            models.MastraoIdleCloseCandidate.State.DELIVERING,
        ]
    ).values_list("pk", flat=True)
    for candidate_pk in candidates.iterator(chunk_size=100):
        process_idle_close.delay(str(candidate_pk))


@worker_ready.connect
def recover_idle_close_tasks_on_worker_start(**_kwargs):
    """Recover durable close delivery whenever a worker becomes ready."""

    recover_idle_close_tasks()
