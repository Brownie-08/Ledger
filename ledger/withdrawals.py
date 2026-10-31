"""Withdrawals: wallet -> suspense while pending, suspense -> system_cash on success, reversed on failure."""

from django.db import transaction

from . import services
from .models import Account, Withdrawal
from .services import EntrySpec


def create_withdrawal(wallet, amount, bank_account, idempotency_key):
    from .dispatch import enqueue

    suspense = services.system_account(Account.Type.SUSPENSE, wallet.currency)
    with transaction.atomic():
        hold_txn = services.post_transaction(
            [EntrySpec(wallet.id, -amount, wallet.currency), EntrySpec(suspense.id, amount, wallet.currency)],
            idempotency_key,
            type="withdrawal_hold",
            metadata={"bank_account": bank_account, "amount": amount},
        )
        wd, created = Withdrawal.objects.get_or_create(
            hold_transaction=hold_txn,
            defaults={"wallet": wallet, "amount": amount, "currency": wallet.currency, "bank_account": bank_account},
        )
        if created:
            enqueue("payout", wd.id)
    return wd


def settle_withdrawal(withdrawal_id, provider_ref):
    with transaction.atomic():
        wd = Withdrawal.objects.select_for_update().get(pk=withdrawal_id)
        if wd.status != Withdrawal.Status.PENDING:
            return wd  # idempotent: already finalised
        suspense = services.system_account(Account.Type.SUSPENSE, wd.currency)
        cash = services.system_account(Account.Type.SYSTEM_CASH, wd.currency)
        wd.final_transaction = services.post_transaction(
            [EntrySpec(suspense.id, -wd.amount, wd.currency), EntrySpec(cash.id, wd.amount, wd.currency)],
            f"withdrawal-settle:{wd.id}",
            type="withdrawal_settle",
            reference=provider_ref,
            metadata={"withdrawal_id": wd.id},
        )
        wd.status = Withdrawal.Status.POSTED
        wd.provider_ref = provider_ref
        wd.save()
        return wd


def fail_withdrawal(withdrawal_id, provider_ref, reason=""):
    with transaction.atomic():
        wd = Withdrawal.objects.select_for_update().get(pk=withdrawal_id)
        if wd.status != Withdrawal.Status.PENDING:
            return wd
        wd.final_transaction = services.reverse_transaction(
            wd.hold_transaction_id, f"withdrawal-fail:{wd.id}", reason=reason or "payout failed"
        )
        wd.status = Withdrawal.Status.FAILED
        wd.provider_ref = provider_ref
        wd.failure_reason = reason
        wd.save()
        return wd
