from celery import shared_task
from django.db import transaction
from django.utils import timezone

from .models import OutboxEvent


@shared_task
def publish_outbox(batch_size=100):
    """Relay unpublished outbox events. Week 3 will push these to a broker/webhooks."""
    with transaction.atomic():
        events = list(
            OutboxEvent.objects.select_for_update(skip_locked=True)
            .filter(published_at__isnull=True)
            .order_by("id")[:batch_size]
        )
        for ev in events:
            # Placeholder publish: real delivery comes later.
            ev.published_at = timezone.now()
            ev.save(update_fields=["published_at"])
    return len(events)
