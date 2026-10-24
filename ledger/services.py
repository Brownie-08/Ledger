"""Double-entry posting service. The only code path allowed to move money."""

from collections import defaultdict
from dataclasses import dataclass

from django.db import IntegrityError, transaction
from django.db.models import F

from .models import Account, Entry, OutboxEvent, Transaction


class LedgerError(Exception):
    code = "ledger_error"


class UnbalancedTransaction(LedgerError):
    code = "unbalanced"


class CurrencyMismatch(LedgerError):
    code = "currency_mismatch"


class InsufficientFunds(LedgerError):
    code = "insufficient_funds"


class AlreadyReversed(LedgerError):
    code = "already_reversed"


@dataclass(frozen=True)
class EntrySpec:
    account_id: int
    amount: int
    currency: str


def post_transaction(entries, idempotency_key, *, type="generic", reference="", metadata=None):
    """Atomically post a balanced set of entries.

    Idempotent on ``idempotency_key``: a repeat call returns the existing transaction.
    """
    entries = [e if isinstance(e, EntrySpec) else EntrySpec(**e) for e in entries]
    if len(entries) < 2:
        raise UnbalancedTransaction("A transaction needs at least two entries")
    totals = defaultdict(int)
    for e in entries:
        if not isinstance(e.amount, int) or isinstance(e.amount, bool) or e.amount == 0:
            raise LedgerError("Entry amounts must be non-zero integers (minor units)")
        totals[e.currency] += e.amount
    if any(totals.values()):
        raise UnbalancedTransaction(f"Entries do not sum to zero per currency: {dict(totals)}")

    existing = Transaction.objects.filter(idempotency_key=idempotency_key).first()
    if existing:
        return existing

    try:
        with transaction.atomic():
            ids = sorted({e.account_id for e in entries})
            # Lock in ascending id order so concurrent transfers can't deadlock.
            accounts = {a.id: a for a in Account.objects.select_for_update().filter(id__in=ids).order_by("id")}
            if len(accounts) != len(ids):
                raise LedgerError("Unknown account")
            deltas = defaultdict(int)
            for e in entries:
                if accounts[e.account_id].currency != e.currency:
                    raise CurrencyMismatch(f"Account {e.account_id} is {accounts[e.account_id].currency}")
                deltas[e.account_id] += e.amount
            for acc_id, delta in deltas.items():
                acc = accounts[acc_id]
                if acc.type == Account.Type.USER_WALLET and acc.balance + delta < 0:
                    raise InsufficientFunds(f"Account {acc_id} has insufficient funds")

            txn = Transaction.objects.create(
                type=type,
                status=Transaction.Status.POSTED,
                idempotency_key=idempotency_key,
                reference=reference,
                metadata=metadata or {},
            )
            Entry.objects.bulk_create(
                [Entry(transaction=txn, account_id=e.account_id, amount=e.amount, currency=e.currency) for e in entries]
            )
            for acc_id, delta in deltas.items():
                Account.objects.filter(id=acc_id).update(balance=F("balance") + delta, version=F("version") + 1)
            OutboxEvent.objects.create(
                event_type="transaction.posted",
                payload={
                    "transaction_id": txn.id,
                    "type": type,
                    "entries": [
                        {"account_id": e.account_id, "amount": e.amount, "currency": e.currency} for e in entries
                    ],
                },
            )
            return txn
    except IntegrityError:
        # Lost a race on the same idempotency key: return the winner.
        existing = Transaction.objects.filter(idempotency_key=idempotency_key).first()
        if existing:
            return existing
        raise


def reverse_transaction(txn_id, idempotency_key, *, reason=""):
    with transaction.atomic():
        original = Transaction.objects.select_for_update().get(pk=txn_id)
        if original.status == Transaction.Status.REVERSED or hasattr(original, "reversed_by"):
            raise AlreadyReversed(f"Transaction {txn_id} already reversed")
        specs = [EntrySpec(e.account_id, -e.amount, e.currency) for e in original.entries.all()]
        rev = post_transaction(
            specs, idempotency_key, type="reversal", reference=f"reversal:{txn_id}", metadata={"reason": reason}
        )
        Transaction.objects.filter(pk=rev.pk).update(reverses=original)
        # Status change via queryset update: the DB trigger allows posted -> reversed only.
        Transaction.objects.filter(pk=original.pk).update(status=Transaction.Status.REVERSED)
        rev.refresh_from_db()
        return rev


def system_account(type, currency):
    acc, _ = Account.objects.get_or_create(owner=None, type=type, currency=currency)
    return acc


def deposit(wallet, amount, idempotency_key, reference=""):
    cash = system_account(Account.Type.SYSTEM_CASH, wallet.currency)
    return post_transaction(
        [EntrySpec(cash.id, -amount, wallet.currency), EntrySpec(wallet.id, amount, wallet.currency)],
        idempotency_key,
        type="deposit",
        reference=reference,
    )


def transfer(source, destination, amount, idempotency_key, reference=""):
    if source.id == destination.id:
        raise LedgerError("Cannot transfer to the same wallet")
    return post_transaction(
        [EntrySpec(source.id, -amount, source.currency), EntrySpec(destination.id, amount, destination.currency)],
        idempotency_key,
        type="transfer",
        reference=reference,
    )
