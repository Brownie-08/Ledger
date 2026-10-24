import pytest
from django.db import IntegrityError, connection, transaction

from ledger import services
from ledger.models import Account, AppendOnlyError, Entry, OutboxEvent, Transaction
from ledger.services import EntrySpec


def total(currency="NGN"):
    return sum(Account.objects.filter(currency=currency).values_list("balance", flat=True))


def test_deposit_posts_balanced_entries(make_wallet):
    w = make_wallet("ada")
    txn = services.deposit(w, 10_000, "k1")
    w.refresh_from_db()
    assert w.balance == 10_000
    assert sorted(txn.entries.values_list("amount", flat=True)) == [-10_000, 10_000]
    assert txn.status == Transaction.Status.POSTED
    assert total() == 0
    assert OutboxEvent.objects.filter(event_type="transaction.posted").count() == 1


def test_transfer_moves_money(make_wallet):
    a, b = make_wallet("a", balance=5_000), make_wallet("b")
    services.transfer(a, b, 2_000, "t1")
    a.refresh_from_db()
    b.refresh_from_db()
    assert (a.balance, b.balance) == (3_000, 2_000)
    assert a.version == 2 and b.version == 1


def test_unbalanced_rejected(make_wallet):
    a, b = make_wallet("a", balance=100), make_wallet("b")
    with pytest.raises(services.UnbalancedTransaction):
        services.post_transaction([EntrySpec(a.id, -100, "NGN"), EntrySpec(b.id, 90, "NGN")], "x")


def test_single_entry_rejected(make_wallet):
    a = make_wallet("a")
    with pytest.raises(services.UnbalancedTransaction):
        services.post_transaction([EntrySpec(a.id, 0, "NGN")], "x")


def test_zero_or_float_amount_rejected(make_wallet):
    a, b = make_wallet("a", balance=100), make_wallet("b")
    with pytest.raises(services.LedgerError):
        services.post_transaction([EntrySpec(a.id, -1.5, "NGN"), EntrySpec(b.id, 1.5, "NGN")], "x")


def test_currency_mismatch_rejected(make_wallet):
    a, b = make_wallet("a", "NGN", balance=100), make_wallet("b", "USD")
    with pytest.raises(services.CurrencyMismatch):
        services.post_transaction([EntrySpec(a.id, -100, "NGN"), EntrySpec(b.id, 100, "NGN")], "x")


def test_overdraft_rejected_and_nothing_written(make_wallet):
    a, b = make_wallet("a", balance=100), make_wallet("b")
    before = Transaction.objects.count()
    with pytest.raises(services.InsufficientFunds):
        services.transfer(a, b, 101, "t")
    assert Transaction.objects.count() == before
    a.refresh_from_db()
    assert a.balance == 100


def test_system_cash_may_go_negative(make_wallet):
    w = make_wallet("a", balance=500)
    cash = Account.objects.get(type="system_cash", currency="NGN")
    assert cash.balance == -500 and w.balance == 500


def test_service_idempotency_returns_same_txn(make_wallet):
    w = make_wallet("a")
    t1 = services.deposit(w, 100, "same")
    t2 = services.deposit(w, 100, "same")
    w.refresh_from_db()
    assert t1.pk == t2.pk and w.balance == 100


def test_reversal_offsets_entries(make_wallet):
    a, b = make_wallet("a", balance=1_000), make_wallet("b")
    t = services.transfer(a, b, 400, "t")
    rev = services.reverse_transaction(t.id, "rev-t", reason="customer dispute")
    a.refresh_from_db()
    b.refresh_from_db()
    t.refresh_from_db()
    assert (a.balance, b.balance) == (1_000, 0)
    assert t.status == Transaction.Status.REVERSED
    assert rev.reverses_id == t.id and rev.type == "reversal"
    assert sorted(rev.entries.values_list("amount", flat=True)) == [-400, 400]
    with pytest.raises(services.AlreadyReversed):
        services.reverse_transaction(t.id, "rev-t-2")


def test_reversal_blocked_if_funds_already_spent(make_wallet):
    a, b, c = make_wallet("a", balance=1_000), make_wallet("b"), make_wallet("c")
    t = services.transfer(a, b, 400, "t")
    services.transfer(b, c, 400, "t2")
    with pytest.raises(services.InsufficientFunds):
        services.reverse_transaction(t.id, "rev")


def test_model_guards_append_only(make_wallet):
    w = make_wallet("a", balance=100)
    e = Entry.objects.filter(account=w).first()
    with pytest.raises(AppendOnlyError):
        e.save()
    with pytest.raises(AppendOnlyError):
        e.delete()
    t = e.transaction
    t.reference = "changed"
    with pytest.raises(AppendOnlyError):
        t.save()
    with pytest.raises(AppendOnlyError):
        t.delete()


def test_db_trigger_blocks_entry_update_and_delete(make_wallet):
    w = make_wallet("a", balance=100)
    with pytest.raises(Exception, match="append-only"):
        with transaction.atomic():
            Entry.objects.filter(account=w).update(amount=999)
    with pytest.raises(Exception, match="append-only"):
        with transaction.atomic():
            Entry.objects.filter(account=w).delete()


def test_db_trigger_blocks_posted_transaction_edit(make_wallet):
    w = make_wallet("a", balance=100)
    with pytest.raises(Exception, match="immutable"):
        with transaction.atomic():
            Transaction.objects.filter(entries__account=w).update(reference="tampered")


def test_db_deferred_zero_sum_constraint(make_wallet):
    w = make_wallet("a")
    with pytest.raises(IntegrityError, match="unbalanced"):
        with transaction.atomic():
            t = Transaction.objects.create(type="manual", idempotency_key="raw", status="pending")
            Entry.objects.create(transaction=t, account=w, amount=100, currency="NGN")
            with connection.cursor() as cur:
                cur.execute("SET CONSTRAINTS ALL IMMEDIATE")
