"""Inbound provider webhooks: verify signature, dedupe on provider_event_id, process atomically."""

import logging

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from . import services, withdrawals
from .audit import audit
from .models import Account, Transaction, VirtualAccount, WebhookEvent
from .services import EntrySpec

log = logging.getLogger(__name__)


class UnknownEvent(Exception):
    pass


def _charge_succeeded(data):
    wallet = Account.objects.get(pk=data["wallet_id"], type=Account.Type.USER_WALLET)
    if wallet.currency != data["currency"]:
        raise services.CurrencyMismatch("charge currency does not match wallet")
    # Keyed on the provider's charge ref so even a re-sent event with a new id can't double-credit.
    key = f"charge:{data['provider_ref']}"
    try:
        return services.deposit(wallet, int(data["amount"]), key, data["provider_ref"])
    except (services.LimitExceeded, services.KycRequired) as exc:
        return park_in_suspense(data, key, reason=exc.code, wallet_id=wallet.id)


def park_in_suspense(data, key, *, reason, **meta):
    """Money arrived at the bank but can't be credited: book it to suspense so the ledger still matches the
    bank, and let reconciliation flag it as unallocated until ops resolves it."""
    amount, currency = int(data["amount"]), data["currency"]
    cash = services.system_account(Account.Type.SYSTEM_CASH, currency)
    suspense = services.system_account(Account.Type.SUSPENSE, currency)
    log.warning("inbound funds parked in suspense", extra={"reason": reason, "provider_ref": data["provider_ref"]})
    return services.post_transaction(
        [EntrySpec(cash.id, -amount, currency), EntrySpec(suspense.id, amount, currency)],
        key,
        type="inbound_unallocated",
        reference=data["provider_ref"],
        metadata={"reason": reason, "provider_ref": data["provider_ref"], "amount": amount, **meta},
    )


def _bank_transfer_received(data):
    """Inbound bank transfer to a virtual account number. Unknown/mismatched account -> suspense."""
    key = f"banktx:{data['provider_ref']}"
    va = VirtualAccount.objects.select_related("wallet").filter(account_number=data["account_number"]).first()
    if va is None:
        return park_in_suspense(data, key, reason="unknown_account", account_number=data["account_number"])
    if va.currency != data["currency"]:
        return park_in_suspense(data, key, reason="currency_mismatch", account_number=data["account_number"])
    cash = services.system_account(Account.Type.SYSTEM_CASH, va.currency)
    try:
        return services.post_transaction(
            [
                EntrySpec(cash.id, -int(data["amount"]), va.currency),
                EntrySpec(va.wallet_id, int(data["amount"]), va.currency),
            ],
            key,
            type="bank_transfer_in",
            reference=data["provider_ref"],
            metadata={
                "provider_ref": data["provider_ref"],
                "account_number": va.account_number,
                "sender_name": data.get("sender_name", ""),
                "amount": int(data["amount"]),
            },
        )
    except (services.LimitExceeded, services.KycRequired) as exc:
        return park_in_suspense(data, key, reason=exc.code, wallet_id=va.wallet_id)


def _payout_succeeded(data):
    return withdrawals.settle_withdrawal(data["withdrawal_id"], data["provider_ref"])


def _payout_failed(data):
    return withdrawals.fail_withdrawal(data["withdrawal_id"], data["provider_ref"], data.get("reason", ""))


HANDLERS = {
    "charge.succeeded": _charge_succeeded,
    "payout.succeeded": _payout_succeeded,
    "payout.failed": _payout_failed,
    "bank_transfer.received": _bank_transfer_received,
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
