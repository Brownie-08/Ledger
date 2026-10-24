import json

import requests
from celery import shared_task
from django.conf import settings
from django.utils import timezone

from ledger.signing import HEADER, sign

from .models import ProviderEvent


class DeliveryFailed(Exception):
    pass


def http_post(url, body: bytes, headers: dict) -> int:
    """Transport (swapped out in tests)."""
    return requests.post(url, data=body, headers=headers, timeout=10).status_code


@shared_task(bind=True, max_retries=8, retry_backoff=True, retry_backoff_max=600, retry_jitter=True)
def deliver_webhook(self, event_pk):
    ev = ProviderEvent.objects.get(pk=event_pk)
    if ev.delivered_at:
        return "already-delivered"
    body = json.dumps(ev.payload, separators=(",", ":")).encode()
    headers = {"Content-Type": "application/json", HEADER: sign(body, settings.PROVIDER_WEBHOOK_SECRET)}
    ev.attempts += 1
    try:
        status = http_post(settings.PROVIDER_WEBHOOK_URL, body, headers)
        if status >= 300:
            raise DeliveryFailed(f"HTTP {status}")
    except (requests.RequestException, DeliveryFailed) as exc:
        ev.last_error = str(exc)
        ev.save(update_fields=["attempts", "last_error"])
        raise self.retry(exc=exc)
    ev.delivered_at = timezone.now()
    ev.save(update_fields=["attempts", "delivered_at"])
    return "delivered"
