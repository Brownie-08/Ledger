"""Nightly reconciliation: our ledger vs the provider's records, plus internal integrity checks."""

from collections import defaultdict

from django.db.models import Sum
from django.utils import timezone

from provider.models import ProviderTransaction

from .models import Account, Entry, ReconciliationReport, Transaction, Withdrawal


def _ledger_deposits():
    """provider_ref -> credited amount for every provider-originated deposit in the ledger."""
    out = {}
    for txn in Transaction.objects.filter(type="deposit", idempotency_key__startswith="charge:"):
        ref = txn.idempotency_key.split(":", 1)[1]
        out[ref] = sum(txn.entries.filter(account__type=Account.Type.USER_WALLET).values_list("amount", flat=True))
    return out


def integrity_issues():
    issues = []
    sums = dict(Entry.objects.values_list("account_id").annotate(s=Sum("amount")))
    for acc in Account.objects.only("id", "balance"):
        if acc.balance != sums.get(acc.id, 0):
            issues.append(
                {"kind": "balance_drift", "account_id": acc.id, "cached": acc.balance, "ledger": sums.get(acc.id, 0)}
            )
    for row in Entry.objects.values("currency").annotate(s=Sum("amount")):
        if row["s"] != 0:
            issues.append({"kind": "currency_not_zero", "currency": row["currency"], "sum": row["s"]})
    return issues


def run_reconciliation():
    started = timezone.now()
    mismatches = []

    # 1) Charges: provider succeeded <-> ledger deposit
    ledger = _ledger_deposits()
    provider_charges = {
        p.provider_ref: p
        for p in ProviderTransaction.objects.filter(kind="charge", status=ProviderTransaction.Status.SUCCEEDED)
    }
    for ref, p in provider_charges.items():
        if ref not in ledger:
            mismatches.append({"kind": "charge_missing_in_ledger", "provider_ref": ref, "amount": p.amount})
        elif ledger[ref] != p.amount:
            mismatches.append(
                {"kind": "charge_amount_mismatch", "provider_ref": ref, "provider": p.amount, "ledger": ledger[ref]}
            )
    for ref, amount in ledger.items():
        if ref not in provider_charges:
            mismatches.append({"kind": "deposit_missing_at_provider", "provider_ref": ref, "amount": amount})

    # 2) Payouts: provider outcome <-> withdrawal status
    payouts = {p.withdrawal_id: p for p in ProviderTransaction.objects.filter(kind="payout")}
    expected = {"succeeded": Withdrawal.Status.POSTED, "failed": Withdrawal.Status.FAILED}
    for wd in Withdrawal.objects.all():
        p = payouts.get(wd.id)
        if p is None:
            if wd.status != Withdrawal.Status.PENDING:
                mismatches.append(
                    {"kind": "withdrawal_missing_at_provider", "withdrawal_id": wd.id, "status": wd.status}
                )
            continue
        if p.amount != wd.amount:
            mismatches.append(
                {"kind": "payout_amount_mismatch", "withdrawal_id": wd.id, "provider": p.amount, "ledger": wd.amount}
            )
        if p.status in expected and wd.status != expected[p.status]:
            mismatches.append(
                {"kind": "payout_status_mismatch", "withdrawal_id": wd.id, "provider": p.status, "ledger": wd.status}
            )

    # 3) Internal integrity
    mismatches += integrity_issues()

    counts = defaultdict(int)
    for m in mismatches:
        counts[m["kind"]] += 1
    return ReconciliationReport.objects.create(
        status=ReconciliationReport.Status.MISMATCHES if mismatches else ReconciliationReport.Status.CLEAN,
        started_at=started,
        finished_at=timezone.now(),
        summary={
            "provider_charges": len(provider_charges),
            "ledger_provider_deposits": len(ledger),
            "withdrawals": Withdrawal.objects.count(),
            "accounts": Account.objects.count(),
            "mismatch_counts": dict(counts),
        },
        mismatches=mismatches,
    )
