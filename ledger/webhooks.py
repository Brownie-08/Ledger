"""Inbound provider webhooks: verify signature, dedupe on provider_event_id, process atomically."""

import logging

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from . import services, withdrawals
from .audit import audit
from .models import Account, Transaction, WebhookEvent

log = logging.getLogger(__name__)


class UnknownEvent(Exception):
    pass


def _charge_succeeded(data):
    wallet = Account.objects.get(pk=data["wallet_id"], type=Account.Type.USER_WALLET)
    if wallet.currency != data["currency"]:
        raise services.CurrencyMismatch("charge currency does not match wallet")
    # Keyed on the provider's charge ref so even a re-sent event with a new id can't double-credit.
    return services.deposit(wallet, int(data["amount"]), f"charge:{data['provider_ref']}", data["provider_ref"])


def _payout_succeeded(data):
    return withdrawals.settle_withdrawal(data["withdrawal_id"], data["provider_ref"])


def _payout_failed(data):
    return withdrawals.fail_withdrawal(data["withdrawal_id"], data["provider_ref"], data.get("reason", ""))


HANDLERS = {
    "charge.succeeded": _charge_succeeded,
    "payout.succeeded": _payout_succeeded,
    "payout.failed": _payout_failed,
}


def process_event(event: dict) -> str:
    """Returns 'processed' or 'duplicate'. Raises on failure (provider will retry)."""
    handler = HANDLERS.get(event.get("type"))
    if handler is None:
        raise UnknownEvent(event.get("type"))
    try:
        with transaction.atomic():
            record = WebhookEvent.objects.create(provider_event_id=event["id"], event_type=event["type"], payload=event)
            result = handler(event["data"])
            audit(
                f"webhook.{event['type']}",
                transaction=result if isinstance(result, Transaction) else getattr(result, "final_transaction", None),
                obj=record,
                provider_event_id=event["id"],
                provider_ref=event["data"].get("provider_ref"),
            )
            record.processed_at = timezone.now()
            record.save(update_fields=["processed_at"])
    except IntegrityError:
        if WebhookEvent.objects.filter(provider_event_id=event["id"]).exists():
            return "duplicate"
        raise
    log.info("webhook processed", extra={"event_id": event["id"], "event_type": event["type"]})
    return "processed"


def secret():
    return settings.PROVIDER_WEBHOOK_SECRET
