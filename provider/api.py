"""Mock PSP 'API'. Called in-process to keep the demo self-contained; treat each function
as an HTTP call to an external provider (it can fail with ProviderUnavailable)."""

import uuid

from django.db import transaction

from .models import ProviderEvent, ProviderTransaction

# Mock bank rules: accounts ending in 0000 are rejected by the bank.
FAILING_ACCOUNT_SUFFIX = "0000"


class ProviderUnavailable(Exception):
    """Transient error (timeout / 5xx). Callers should retry."""


# Test hook: number of upcoming calls that should fail transiently.
_outage = {"remaining": 0}


def simulate_outage(calls: int):
    _outage["remaining"] = calls


def _maybe_fail():
    if _outage["remaining"] > 0:
        _outage["remaining"] -= 1
        raise ProviderUnavailable("provider timed out")


def _emit(event_type, data):
    from .tasks import deliver_webhook

    ev = ProviderEvent.objects.create(
        event_id=f"evt_{uuid.uuid4().hex[:20]}", type=event_type, payload={"type": event_type, "data": data}
    )
    ev.payload["id"] = ev.event_id
    ev.save(update_fields=["payload"])
    transaction.on_commit(lambda: deliver_webhook.delay(ev.id))
    return ev


def create_charge(*, wallet_id, amount, currency, client_reference=None):
    """A customer paid by card; the provider captures it and notifies us."""
    _maybe_fail()
    client_reference = client_reference or f"chg-{uuid.uuid4().hex[:12]}"
    with transaction.atomic():
        txn, created = ProviderTransaction.objects.get_or_create(
            client_reference=client_reference,
            defaults={
                "kind": ProviderTransaction.Kind.CHARGE,
                "provider_ref": f"ch_{uuid.uuid4().hex[:16]}",
                "wallet_id": wallet_id,
                "amount": amount,
                "currency": currency,
                "status": ProviderTransaction.Status.SUCCEEDED,
            },
        )
        if created:
            _emit(
                "charge.succeeded",
                {"provider_ref": txn.provider_ref, "wallet_id": wallet_id, "amount": amount, "currency": currency},
            )
    return txn


def create_payout(*, reference, withdrawal_id, amount, currency, bank_account):
    """Idempotent on ``reference``. Outcome is reported asynchronously via webhook."""
    _maybe_fail()
    with transaction.atomic():
        txn, created = ProviderTransaction.objects.get_or_create(
            client_reference=reference,
            defaults={
                "kind": ProviderTransaction.Kind.PAYOUT,
                "provider_ref": f"po_{uuid.uuid4().hex[:16]}",
                "withdrawal_id": withdrawal_id,
                "amount": amount,
                "currency": currency,
                "bank_account": bank_account,
            },
        )
        if created:
            data = {"provider_ref": txn.provider_ref, "withdrawal_id": withdrawal_id, "amount": amount}
            if bank_account.endswith(FAILING_ACCOUNT_SUFFIX):
                txn.status = ProviderTransaction.Status.FAILED
                txn.failure_reason = "account_closed"
                _emit("payout.failed", {**data, "reason": "account_closed"})
            else:
                txn.status = ProviderTransaction.Status.SUCCEEDED
                _emit("payout.succeeded", data)
            txn.save()
    return txn.provider_ref
