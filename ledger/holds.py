"""Holds / authorisations: reserve -> capture (full or partial) | release | expire."""

from datetime import timedelta

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from . import services
from .models import Account, Hold
from .services import EntrySpec, LedgerError


class HoldNotActive(LedgerError):
    code = "hold_not_active"


def _lock(*ids):
    return {a.id: a for a in Account.objects.select_for_update().filter(id__in=sorted(set(ids))).order_by("id")}


def place_hold(wallet, merchant, amount, idempotency_key, expires_in=timedelta(days=7), reference=""):
    existing = Hold.objects.filter(idempotency_key=idempotency_key).first()
    if existing:
        return existing
    if amount <= 0:
        raise LedgerError("Hold amount must be positive")
    with transaction.atomic():
        accs = _lock(wallet.id, merchant.id)
        acc = accs[wallet.id]
        if acc.currency != accs[merchant.id].currency:
            raise services.CurrencyMismatch("Hold and merchant currencies differ")
        if acc.available_balance < amount:
            raise services.InsufficientFunds(f"Account {acc.id} has insufficient available funds")
        Account.objects.filter(id=acc.id).update(held_amount=F("held_amount") + amount, version=F("version") + 1)
        return Hold.objects.create(
            account=acc,
            merchant=accs[merchant.id],
            amount=amount,
            currency=acc.currency,
            idempotency_key=idempotency_key,
            reference=reference,
            expires_at=timezone.now() + expires_in,
        )


def _close(hold, status):
    Account.objects.filter(id=hold.account_id).update(
        held_amount=F("held_amount") - hold.amount, version=F("version") + 1
    )
    hold.status = status
    hold.save()


def capture_hold(hold_id, amount=None):
    """Capture ``amount`` (default: full). Any remainder is released immediately."""
    with transaction.atomic():
        hold = Hold.objects.select_for_update().get(pk=hold_id)
        if hold.status == Hold.Status.CAPTURED:
            return hold  # idempotent replay
        if hold.status != Hold.Status.ACTIVE or hold.expires_at <= timezone.now():
            raise HoldNotActive(f"Hold {hold_id} is {hold.status}")
        amount = hold.amount if amount is None else amount
        if not 0 < amount <= hold.amount:
            raise LedgerError(f"Capture amount must be between 1 and {hold.amount}")
        _lock(hold.account_id, hold.merchant_id)
        _close(hold, Hold.Status.CAPTURED)  # frees the reservation before posting the debit
        hold.capture_transaction = services.post_transaction(
            [EntrySpec(hold.account_id, -amount, hold.currency), EntrySpec(hold.merchant_id, amount, hold.currency)],
            f"capture:{hold.id}",
            type="capture",
            reference=hold.reference,
            metadata={"hold_id": hold.id, "source": hold.account_id, "destination": hold.merchant_id, "amount": amount},
        )
        hold.captured_amount = amount
        hold.save()
        return hold


def release_hold(hold_id, status=Hold.Status.RELEASED):
    with transaction.atomic():
        hold = Hold.objects.select_for_update().get(pk=hold_id)
        if hold.status in (Hold.Status.RELEASED, Hold.Status.EXPIRED):
            return hold
        if hold.status != Hold.Status.ACTIVE:
            raise HoldNotActive(f"Hold {hold_id} is {hold.status}")
        _lock(hold.account_id)
        _close(hold, status)
        return hold


def expire_due_holds(now=None):
    now = now or timezone.now()
    ids = list(Hold.objects.filter(status=Hold.Status.ACTIVE, expires_at__lte=now).values_list("id", flat=True))
    for hold_id in ids:
        release_hold(hold_id, status=Hold.Status.EXPIRED)
    return len(ids)
