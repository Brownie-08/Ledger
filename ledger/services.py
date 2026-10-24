"""Double-entry posting service. The only code path allowed to move money."""

from collections import defaultdict
from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import F

from .metrics import LEDGER_TRANSACTIONS
from .models import Account, Entry, FxRate, OutboxEvent, Refund, Transaction


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


class NoFxRate(LedgerError):
    code = "no_fx_rate"


class NotRefundable(LedgerError):
    code = "not_refundable"


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
                # Available = balance - active holds; only debits are checked.
                if acc.type == Account.Type.USER_WALLET and delta < 0 and acc.balance - acc.held_amount + delta < 0:
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
            transaction.on_commit(lambda: LEDGER_TRANSACTIONS.labels(type).inc())
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


def compute_fee(amount):
    """Configurable transfer fee: basis points with optional min/max (all minor units)."""
    bps = int(getattr(settings, "TRANSFER_FEE_BPS", 0))
    fee = max(amount * bps // 10_000, int(getattr(settings, "TRANSFER_FEE_MIN", 0)))
    cap = int(getattr(settings, "TRANSFER_FEE_MAX", 0))
    return min(fee, cap) if cap else fee


def get_rate(base, quote):
    try:
        return FxRate.objects.get(base=base, quote=quote).rate
    except FxRate.DoesNotExist:
        raise NoFxRate(f"No FX rate for {base}->{quote}")


def convert(amount, rate):
    """Convert minor units (all supported currencies have 2 decimals); always round down."""
    return int((Decimal(amount) * Decimal(rate)).quantize(Decimal(1), rounding=ROUND_DOWN))


def transfer(source, destination, amount, idempotency_key, reference=""):
    """Wallet-to-wallet transfer with fee; converts via per-currency FX accounts if currencies differ.

    Same currency:  src -(amount+fee) | dst +amount | fees +fee
    Cross currency: src -(amount+fee) A | fees_A +fee A | fx_A +amount A | fx_B -converted B | dst +converted B
    Every currency still nets to zero inside the transaction.
    """
    if source.id == destination.id:
        raise LedgerError("Cannot transfer to the same wallet")
    a = source.currency
    fee = compute_fee(amount)
    meta = {"source": source.id, "destination": destination.id, "amount": amount, "fee": fee}
    specs = [EntrySpec(source.id, -(amount + fee), a)]
    if fee:
        specs.append(EntrySpec(system_account(Account.Type.FEES, a).id, fee, a))
    if destination.currency == a:
        specs.append(EntrySpec(destination.id, amount, a))
        txn_type = "transfer"
    else:
        b = destination.currency
        rate = get_rate(a, b)
        converted = convert(amount, rate)
        if converted <= 0:
            raise LedgerError("Amount too small to convert")
        specs += [
            EntrySpec(system_account(Account.Type.FX, a).id, amount, a),
            EntrySpec(system_account(Account.Type.FX, b).id, -converted, b),
            EntrySpec(destination.id, converted, b),
        ]
        meta.update({"rate": str(rate), "converted_amount": converted, "to_currency": b})
        txn_type = "fx_transfer"
    return post_transaction(specs, idempotency_key, type=txn_type, reference=reference, metadata=meta)


def refund_transaction(txn_id, amount, idempotency_key, reason=""):
    """Full or partial refund of a same-currency transfer or hold capture (fees are not refunded)."""
    existing = Transaction.objects.filter(idempotency_key=idempotency_key).first()
    if existing:
        return existing
    with transaction.atomic():
        original = Transaction.objects.select_for_update().get(pk=txn_id)
        if original.type not in ("transfer", "capture") or original.status != Transaction.Status.POSTED:
            raise NotRefundable(f"Transaction {txn_id} ({original.type}/{original.status}) cannot be refunded")
        meta = original.metadata
        refunded = sum(original.refunds.values_list("amount", flat=True))
        if amount <= 0 or amount > meta["amount"] - refunded:
            raise NotRefundable(f"Refund exceeds refundable amount ({meta['amount'] - refunded})")
        currency = original.entries.get(account_id=meta["destination"]).currency
        txn = post_transaction(
            [EntrySpec(meta["destination"], -amount, currency), EntrySpec(meta["source"], amount, currency)],
            idempotency_key,
            type="refund",
            reference=f"refund:{txn_id}",
            metadata={"refund_of": txn_id, "amount": amount, "reason": reason},
        )
        Refund.objects.create(original=original, transaction=txn, amount=amount)
        return txn
