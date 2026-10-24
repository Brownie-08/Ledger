from datetime import timedelta

import pytest
from django.utils import timezone

from ledger import holds, services
from ledger.models import Account, Entry, Hold, Transaction


def bal(acc):
    acc.refresh_from_db()
    return acc.balance


def ledger_sums_to_zero():
    from django.db.models import Sum

    return all(r["s"] == 0 for r in Entry.objects.values("currency").annotate(s=Sum("amount")))


# --- holds -----------------------------------------------------------------------


def test_hold_reduces_available_not_balance(make_wallet):
    w, m = make_wallet("ada", balance=10_000), make_wallet("shop")
    holds.place_hold(w, m, 3_000, "h1")
    w.refresh_from_db()
    assert (w.balance, w.held_amount, w.available_balance) == (10_000, 3_000, 7_000)


def test_hold_blocks_spending_held_funds(make_wallet):
    w, m, b = make_wallet("ada", balance=10_000), make_wallet("shop"), make_wallet("bola")
    holds.place_hold(w, m, 8_000, "h1")
    with pytest.raises(services.InsufficientFunds):
        services.transfer(w, b, 2_001, "t1")
    with pytest.raises(services.InsufficientFunds):
        holds.place_hold(w, m, 2_001, "h2")
    services.transfer(w, b, 2_000, "t2")


def test_full_capture(make_wallet):
    w, m = make_wallet("ada", balance=10_000), make_wallet("shop")
    h = holds.capture_hold(holds.place_hold(w, m, 3_000, "h1").id)
    w.refresh_from_db()
    assert h.status == "captured" and h.captured_amount == 3_000
    assert (w.balance, w.held_amount) == (7_000, 0) and bal(m) == 3_000
    assert holds.capture_hold(h.id).capture_transaction_id == h.capture_transaction_id  # idempotent


def test_partial_capture_releases_remainder(make_wallet):
    w, m = make_wallet("ada", balance=10_000), make_wallet("shop")
    h = holds.capture_hold(holds.place_hold(w, m, 3_000, "h1").id, 1_200)
    w.refresh_from_db()
    assert (w.balance, w.held_amount, w.available_balance) == (8_800, 0, 8_800) and bal(m) == 1_200
    assert h.captured_amount == 1_200


def test_capture_more_than_hold_rejected(make_wallet):
    w, m = make_wallet("ada", balance=10_000), make_wallet("shop")
    h = holds.place_hold(w, m, 3_000, "h1")
    with pytest.raises(services.LedgerError):
        holds.capture_hold(h.id, 3_001)


def test_release(make_wallet):
    w, m = make_wallet("ada", balance=10_000), make_wallet("shop")
    h = holds.release_hold(holds.place_hold(w, m, 3_000, "h1").id)
    w.refresh_from_db()
    assert h.status == "released" and w.held_amount == 0 and w.balance == 10_000
    with pytest.raises(holds.HoldNotActive):
        holds.capture_hold(h.id)


def test_expiry_task_releases_due_holds(make_wallet):
    from ledger.tasks import expire_holds

    w, m = make_wallet("ada", balance=10_000), make_wallet("shop")
    old = holds.place_hold(w, m, 1_000, "h1", expires_in=timedelta(seconds=1))
    fresh = holds.place_hold(w, m, 2_000, "h2")
    Hold.objects.filter(pk=old.pk).update(expires_at=timezone.now() - timedelta(minutes=1))
    assert expire_holds.delay().get() == 1
    old.refresh_from_db()
    fresh.refresh_from_db()
    w.refresh_from_db()
    assert old.status == "expired" and fresh.status == "active" and w.held_amount == 2_000
    with pytest.raises(holds.HoldNotActive):
        holds.capture_hold(old.id)


def test_expired_but_not_yet_swept_hold_cannot_be_captured(make_wallet):
    w, m = make_wallet("ada", balance=10_000), make_wallet("shop")
    h = holds.place_hold(w, m, 1_000, "h1")
    Hold.objects.filter(pk=h.pk).update(expires_at=timezone.now() - timedelta(seconds=1))
    with pytest.raises(holds.HoldNotActive):
        holds.capture_hold(h.id)


def test_hold_currency_mismatch(make_wallet):
    w, m = make_wallet("ada", balance=10_000), make_wallet("shop", "USD")
    with pytest.raises(services.CurrencyMismatch):
        holds.place_hold(w, m, 1, "h1")


# --- refunds / reversals -------------------------------------------------------


def test_partial_then_full_refund_of_transfer(make_wallet):
    a, b = make_wallet("a", balance=10_000), make_wallet("b")
    t = services.transfer(a, b, 4_000, "t")
    services.refund_transaction(t.id, 1_500, "r1")
    assert (bal(a), bal(b)) == (7_500, 2_500)
    services.refund_transaction(t.id, 2_500, "r2")
    assert (bal(a), bal(b)) == (10_000, 0)
    with pytest.raises(services.NotRefundable):
        services.refund_transaction(t.id, 1, "r3")
    assert services.refund_transaction(t.id, 1_500, "r1").type == "refund"  # replay by key


def test_refund_of_capture(make_wallet):
    w, m = make_wallet("ada", balance=10_000), make_wallet("shop")
    h = holds.capture_hold(holds.place_hold(w, m, 3_000, "h1").id)
    services.refund_transaction(h.capture_transaction_id, 3_000, "r")
    assert (bal(w), bal(m)) == (10_000, 0)


def test_refund_requires_merchant_funds(make_wallet):
    a, b, c = make_wallet("a", balance=1_000), make_wallet("b"), make_wallet("c")
    t = services.transfer(a, b, 1_000, "t")
    services.transfer(b, c, 1_000, "t2")
    with pytest.raises(services.InsufficientFunds):
        services.refund_transaction(t.id, 500, "r")


def test_deposit_not_refundable(make_wallet):
    a = make_wallet("a", balance=1_000)
    dep = Transaction.objects.get(type="deposit")
    with pytest.raises(services.NotRefundable):
        services.refund_transaction(dep.id, 1, "r")
    assert bal(a) == 1_000


# --- fees ------------------------------------------------------------------------------


def test_transfer_fee_goes_to_fees_account(make_wallet, settings):
    settings.TRANSFER_FEE_BPS = 100  # 1%
    a, b = make_wallet("a", balance=10_000), make_wallet("b")
    t = services.transfer(a, b, 5_000, "t")
    fees = Account.objects.get(type="fees", currency="NGN")
    assert (bal(a), bal(b), fees.balance) == (4_950, 5_000, 50)
    assert t.metadata["fee"] == 50 and t.entries.count() == 3
    assert ledger_sums_to_zero()


def test_fee_min_and_max(settings):
    settings.TRANSFER_FEE_BPS, settings.TRANSFER_FEE_MIN, settings.TRANSFER_FEE_MAX = 50, 10, 1_000
    assert services.compute_fee(100) == 10
    assert services.compute_fee(100_000) == 500
    assert services.compute_fee(10_000_000) == 1_000


def test_fee_counts_toward_overdraft(make_wallet, settings):
    settings.TRANSFER_FEE_BPS = 100
    a, b = make_wallet("a", balance=1_000), make_wallet("b")
    with pytest.raises(services.InsufficientFunds):
        services.transfer(a, b, 1_000, "t")  # needs 1_010


def test_refund_does_not_return_fee(make_wallet, settings):
    settings.TRANSFER_FEE_BPS = 100
    a, b = make_wallet("a", balance=10_000), make_wallet("b")
    t = services.transfer(a, b, 5_000, "t")
    services.refund_transaction(t.id, 5_000, "r")
    assert (bal(a), bal(b)) == (9_950, 0)


# --- FX -------------------------------------------------------------------------------


def test_fx_rates_seeded(db):
    assert services.get_rate("USD", "NGN") == 1500
    assert services.get_rate("NGN", "USD") > 0


def test_fx_transfer_each_currency_sums_to_zero(make_wallet):
    usd, ngn = make_wallet("ada", "USD", balance=10_000), make_wallet("bola", "NGN")
    t = services.transfer(usd, ngn, 1_000, "fx1")  # $10.00 -> NGN 15,000.00
    assert t.type == "fx_transfer" and t.metadata["converted_amount"] == 1_500_000
    assert (bal(usd), bal(ngn)) == (9_000, 1_500_000)
    fx_usd = Account.objects.get(type="fx", currency="USD")
    fx_ngn = Account.objects.get(type="fx", currency="NGN")
    assert (fx_usd.balance, fx_ngn.balance) == (1_000, -1_500_000)
    for cur in ("USD", "NGN"):
        assert sum(t.entries.filter(currency=cur).values_list("amount", flat=True)) == 0
    assert ledger_sums_to_zero()


def test_fx_conversion_rounds_down():
    from decimal import Decimal

    assert services.convert(1, Decimal("0.0006666667")) == 0
    assert services.convert(150_001, Decimal("0.0006666667")) == 100


def test_fx_amount_too_small(make_wallet):
    ngn, usd = make_wallet("a", "NGN", balance=10_000), make_wallet("b", "USD")
    with pytest.raises(services.LedgerError):
        services.transfer(ngn, usd, 1, "fx")


def test_fx_with_fee_in_source_currency(make_wallet, settings):
    settings.TRANSFER_FEE_BPS = 100
    gbp, usd = make_wallet("a", "GBP", balance=10_000), make_wallet("b", "USD")
    t = services.transfer(gbp, usd, 1_000, "fx")
    assert (bal(gbp), bal(usd)) == (8_990, 1_270)
    assert Account.objects.get(type="fees", currency="GBP").balance == 10
    assert t.metadata["rate"] == "1.2700000000"


def test_missing_fx_rate(make_wallet):
    from ledger.models import FxRate

    FxRate.objects.filter(base="GBP", quote="USD").delete()
    gbp, usd = make_wallet("a", "GBP", balance=10_000), make_wallet("b", "USD")
    with pytest.raises(services.NoFxRate):
        services.transfer(gbp, usd, 100, "fx")


def test_fx_transfer_reversal(make_wallet):
    usd, ngn = make_wallet("ada", "USD", balance=10_000), make_wallet("bola", "NGN")
    t = services.transfer(usd, ngn, 1_000, "fx1")
    services.reverse_transaction(t.id, "rev")
    assert (bal(usd), bal(ngn)) == (10_000, 0)
    assert ledger_sums_to_zero()
