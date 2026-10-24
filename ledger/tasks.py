import logging

from celery import shared_task
from django.db import transaction
from django.utils import timezone

from .models import OutboxEvent, Withdrawal

log = logging.getLogger(__name__)


@shared_task
def publish_outbox(batch_size=100):
    """Relay unpublished outbox events (placeholder publisher: marks them published)."""
    with transaction.atomic():
        events = list(
            OutboxEvent.objects.select_for_update(skip_locked=True)
            .filter(published_at__isnull=True)
            .order_by("id")[:batch_size]
        )
        for ev in events:
            ev.published_at = timezone.now()
            ev.save(update_fields=["published_at"])
    return len(events)


@shared_task(bind=True, max_retries=6, retry_backoff=True, retry_backoff_max=300, retry_jitter=True)
def submit_payout(self, withdrawal_id):
    """Outgoing call to the provider's payout API, retried with exponential backoff."""
    from provider import api as provider_api

    wd = Withdrawal.objects.get(pk=withdrawal_id)
    if wd.status != Withdrawal.Status.PENDING:
        return wd.provider_ref
    try:
        return provider_api.create_payout(
            reference=f"wd-{wd.id}",
            withdrawal_id=wd.id,
            amount=wd.amount,
            currency=wd.currency,
            bank_account=wd.bank_account,
        )
    except provider_api.ProviderUnavailable as exc:
        log.warning("provider unavailable, retrying", extra={"withdrawal_id": wd.id, "retry": self.request.retries})
        raise self.retry(exc=exc)


@shared_task
def expire_holds():
    """Celery beat: release authorisations past their expiry."""
    from .holds import expire_due_holds

    return expire_due_holds()
