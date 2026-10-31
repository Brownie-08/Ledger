"""Broker failures leave retryable database intent instead of stranding a payout or webhook."""

import logging
from datetime import timedelta

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .models import DispatchJob, Withdrawal

log = logging.getLogger(__name__)


def enqueue(kind, object_id):
    job, _ = DispatchJob.objects.get_or_create(kind=kind, object_id=object_id)
    transaction.on_commit(lambda: dispatch_one(job.id))
    return job


def dispatch_one(job_id):
    from provider.models import ProviderEvent
    from provider.tasks import deliver_webhook

    from .tasks import submit_payout

    with transaction.atomic():
        job = DispatchJob.objects.select_for_update(skip_locked=True).filter(pk=job_id).first()
        if not job or job.completed_at:
            return False
        if job.published_at and job.published_at > timezone.now() - timedelta(minutes=5):
            return False
        if job.kind == "payout":
            if not Withdrawal.objects.filter(pk=job.object_id, status="pending").exists():
                job.completed_at = timezone.now()
                job.save(update_fields=["completed_at"])
                return False
            task = submit_payout
        else:
            if not ProviderEvent.objects.filter(pk=job.object_id, delivered_at__isnull=True).exists():
                job.completed_at = timezone.now()
                job.save(update_fields=["completed_at"])
                return False
            task = deliver_webhook
        job.attempts += 1
        try:
            task.delay(job.object_id)
        except Exception as exc:
            # Keep the intent durable. Do not turn a committed financial operation into
            # an ambiguous HTTP 500 because the broker is temporarily unavailable.
            job.last_error = type(exc).__name__
            job.published_at = None
            log.warning("dispatch deferred", extra={"dispatch_id": job.id, "error_type": job.last_error})
        else:
            job.last_error = ""
            job.published_at = timezone.now()
        job.save(update_fields=["attempts", "published_at", "last_error"])
        return job.published_at is not None


def dispatch_due(batch_size=100):
    cutoff = timezone.now() - timedelta(minutes=5)
    ids = list(
        DispatchJob.objects.filter(Q(published_at__isnull=True) | Q(published_at__lt=cutoff), completed_at__isnull=True)
        .order_by("id")
        .values_list("id", flat=True)[:batch_size]
    )
    return sum(dispatch_one(pk) for pk in ids)
