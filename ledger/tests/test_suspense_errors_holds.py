"""Suspense allocation / return, human-friendly money in errors, and tier limits on hold placement."""

from io import StringIO

import pytest
from django.core.management import CommandError, call_command
from rest_framework.test import APIClient

from ledger import holds, kyc, services, suspense
from ledger.models import Account, AuditLog, OutboxEvent, SuspenseResolution, Transaction
from ledger.money import fmt
from ledger.reconciliation import run_reconciliation
from provider import api as provider_api

from .conftest import verify

pytestmark = pytest.mark.django_db


def post(c, url, data=None, key=None):
    extra = {"HTTP_IDEMPOTENCY_KEY": key} if key else {}
    return c.post(url, data or {}, format="json", **extra)


@pytest.fixture
def staff(django_user_model):
    u = django_user_model.objects.create_user("ops", password="x", is_staff=True)
    c = APIClient()
    c.force_authenticate(u)
    c.user = u
    return c


@pytest.fixture
def parked(webhook_transport, django_capture_on_commit_callbacks):
    """A real ₦7,500 inbound bank transfer to an unknown account number, parked in suspense."""

    def _park(amount=750_000, currency="NGN", account_number="9999999999"):
        with django_capture_on_commit_callbacks(execute=True):
            provider_api.simulate_bank_transfer(
                account_number=account_number, amount=amount, currency=currency, sender_name="Typo Sender"
            )
        return Transaction.objects.filter(type="inbound_unallocated").latest("id")

    return _park


def suspense_balance(cur="NGN"):
    return Account.objects.get(type=Account.Type.SUSPENSE, currency=cur).balance


# ------------------------------------------------------------------ money formatting


def test_fmt():
    assert fmt(2_000_000, "NGN") == "₦20,000.00 NGN"
    assert fmt(10_000, "USD") == "$100.00 USD"
    assert fmt(5_000, "GBP") == "£50.00 GBP"
    assert fmt(-150, "NGN") == "-₦1.50 NGN"


# ------------------------------------------------------------------ suspense: list / allocate / return


def test_list_requires_staff_and_shows_item(parked, staff):
    txn = parked()
    nobody = APIClient()
    assert nobody.get("/api/admin/suspense/").status_code == 401
    r = staff.get("/api/admin/suspense/")
    assert r.status_code == 200
    [it] = r.json()
    assert it["transaction_id"] == txn.id and it["amount_minor"] == 750_000 and it["currency"] == "NGN"
    assert it["amount_display"] == "₦7,500.00 NGN" and it["reason"] == "unknown_account"
    assert it["sender_name"] == "Typo Sender" and it["account_number"] == "9999999999"


def test_non_staff_cannot_allocate(parked, make_wallet, django_user_model):
    txn = parked()
    w = make_wallet("ada")
    c = APIClient()
    c.force_authenticate(w.owner)
    assert post(c, f"/api/admin/suspense/{txn.id}/allocate/", {"wallet_id": w.id}).status_code == 403
    assert suspense_balance() == 750_000


def test_allocate_moves_money_and_reconciliation_is_clean(parked, make_wallet, staff):
    txn = parked()
    assert run_reconciliation().summary["mismatch_counts"] == {"unallocated_inbound": 1}
    w = make_wallet("ada")
    r = post(staff, f"/api/admin/suspense/{txn.id}/allocate/", {"wallet_id": w.id, "reason": "sender typo"})
    assert r.status_code == 200, r.json()
    body = r.json()
    assert body["action"] == "allocate" and body["wallet_id"] == w.id and body["amount_minor"] == 750_000
    w.refresh_from_db()
    assert w.balance == 750_000 and suspense_balance() == 0
    alloc = Transaction.objects.get(pk=body["transaction_id"])
    assert alloc.type == "suspense_allocation" and alloc.metadata["parked_transaction"] == txn.id
    assert sorted(e.amount for e in alloc.entries.all()) == [-750_000, 750_000]
    assert AuditLog.objects.filter(action="suspense.allocate", actor=staff.user).exists()
    ev = OutboxEvent.objects.get(event_type="suspense.allocated")
    assert ev.payload["wallet_id"] == w.id and ev.payload["amount"] == 750_000
    report = run_reconciliation()
    assert report.status == "clean", report.mismatches
    assert report.summary["unallocated_inbound"] == 0
    assert staff.get("/api/admin/suspense/").json() == []


def test_allocate_is_idempotent_and_conflicts_409(parked, make_wallet, staff):
    txn = parked()
    w, other = make_wallet("ada"), make_wallet("bob")
    r1 = post(staff, f"/api/admin/suspense/{txn.id}/allocate/", {"wallet_id": w.id})
    r2 = post(staff, f"/api/admin/suspense/{txn.id}/allocate/", {"wallet_id": w.id})
    assert r1.json()["transaction_id"] == r2.json()["transaction_id"]
    w.refresh_from_db()
    assert w.balance == 750_000
    assert Transaction.objects.filter(type="suspense_allocation").count() == 1
    r3 = post(staff, f"/api/admin/suspense/{txn.id}/allocate/", {"wallet_id": other.id})
    assert r3.status_code == 409 and r3.json()["error"] == "suspense_invalid_state"
    assert post(staff, f"/api/admin/suspense/{txn.id}/return/").status_code == 409


def test_allocate_currency_mismatch_and_bad_targets(parked, make_wallet, staff):
    txn = parked()
    usd = make_wallet("ada", "USD")
    r = post(staff, f"/api/admin/suspense/{txn.id}/allocate/", {"wallet_id": usd.id})
    assert r.status_code == 422 and r.json()["error"] == "currency_mismatch"
    fees = services.system_account(Account.Type.FEES, "NGN")
    assert post(staff, f"/api/admin/suspense/{txn.id}/allocate/", {"wallet_id": fees.id}).status_code == 404
    normal = Transaction.objects.exclude(type="inbound_unallocated").first() or txn
    if normal.id != txn.id:
        assert post(staff, f"/api/admin/suspense/{normal.id}/allocate/", {"wallet_id": usd.id}).status_code == 422
    assert suspense_balance() == 750_000


def test_allocate_respects_kyc_limits_unless_overridden(parked, make_wallet, staff):
    txn = parked(amount=6_000_000)  # ₦60,000 > tier-0 max balance ₦50,000
    w = make_wallet("ada", tier=0)
    r = post(staff, f"/api/admin/suspense/{txn.id}/allocate/", {"wallet_id": w.id})
    assert r.status_code == 422
    b = r.json()
    assert b["error"] == "limit_exceeded" and b["limit"] == "max_balance"
    assert b["limit_minor"] == 5_000_000 and b["amount_minor"] == 6_000_000 and b["currency"] == "NGN"
    assert "₦50,000.00 NGN" in b["detail"]
    # Override needs a reason...
    r = post(staff, f"/api/admin/suspense/{txn.id}/allocate/", {"wallet_id": w.id, "override_limits": True})
    assert r.status_code == 422
    # ...and is recorded everywhere.
    r = post(
        staff,
        f"/api/admin/suspense/{txn.id}/allocate/",
        {"wallet_id": w.id, "override_limits": True, "reason": "verified by phone; KYC in progress"},
    )
    assert r.status_code == 200 and r.json()["override_limits"] is True
    w.refresh_from_db()
    assert w.balance == 6_000_000
    alloc = Transaction.objects.get(type="suspense_allocation")
    assert alloc.metadata["admin_override_limits"] is True
    log = AuditLog.objects.get(action="suspense.allocate")
    assert log.data["override_limits"] is True and "phone" in log.data["reason"]
    assert run_reconciliation().status == "clean"


def test_return_to_sender(parked, staff):
    txn = parked()
    cash_before = services.system_account(Account.Type.SYSTEM_CASH, "NGN").balance
    r = post(staff, f"/api/admin/suspense/{txn.id}/return/", {"reason": "no such customer"})
    assert r.status_code == 200 and r.json()["action"] == "return"
    assert suspense_balance() == 0
    assert services.system_account(Account.Type.SYSTEM_CASH, "NGN").balance == cash_before + 750_000
    ret = Transaction.objects.get(type="suspense_return")
    assert ret.metadata["mock_refund_ref"].startswith("rf_")
    assert OutboxEvent.objects.filter(event_type="suspense.returned").exists()
    assert AuditLog.objects.filter(action="suspense.return").exists()
    assert run_reconciliation().status == "clean"
    # idempotent
    assert post(staff, f"/api/admin/suspense/{txn.id}/return/").json()["transaction_id"] == ret.id


def test_service_rejects_non_parked_transaction(make_wallet):
    w = make_wallet("ada", balance=100)
    txn = Transaction.objects.get(type="deposit")
    with pytest.raises(services.LedgerError):
        suspense.allocate(txn.id, w, None)
    with pytest.raises(services.LedgerError):
        suspense.return_to_sender(txn.id, None)


def test_management_command(parked, make_wallet, staff):
    a, b = parked(), parked(amount=100_00)
    w = make_wallet("ada")
    out = StringIO()
    call_command("suspense", "list", stdout=out)
    assert '"amount_display": "\\u20a67,500.00 NGN"' in out.getvalue() or "7,500.00 NGN" in out.getvalue()
    out = StringIO()
    call_command("suspense", "allocate", str(a.id), "--wallet", str(w.id), "--actor", "ops", stdout=out)
    assert '"action": "allocate"' in out.getvalue()
    call_command("suspense", "return", str(b.id), "--reason", "unknown", stdout=StringIO())
    assert SuspenseResolution.objects.count() == 2
    assert AuditLog.objects.get(action="suspense.allocate").actor == staff.user
    assert run_reconciliation().status == "clean"
    with pytest.raises(CommandError):
        call_command("suspense", "allocate", str(a.id), stdout=StringIO())  # no --wallet
    with pytest.raises(CommandError):
        call_command("suspense", "allocate", str(a.id), "--wallet", str(w.id), "--actor", "nobody")
    with pytest.raises(CommandError):
        call_command("suspense", "allocate", str(a.id), "--wallet", "999999")
    with pytest.raises(CommandError, match="suspense_invalid_state"):
        call_command("suspense", "return", str(a.id), stdout=StringIO())
    with pytest.raises(CommandError):
        call_command("suspense", "return", stdout=StringIO())


# ------------------------------------------------------------------ human-friendly money in errors


def test_insufficient_funds_error_body(make_wallet):
    a = make_wallet("ada", balance=5_000)
    b = make_wallet("bob")
    c = APIClient()
    c.force_authenticate(a.owner)
    r = post(c, "/api/transfers/", {"source_wallet": a.id, "destination_wallet": b.id, "amount": 10_000}, "t1")
    body = r.json()
    assert r.status_code == 422 and body["error"] == "insufficient_funds"
    assert body["amount_minor"] == 10_000 and body["available_minor"] == 5_000 and body["currency"] == "NGN"
    assert "₦100.00 NGN" in body["detail"] and "₦50.00 NGN" in body["detail"]


def test_insufficient_funds_on_hold_usd(make_wallet):
    a = make_wallet("ada", "USD", balance=5_000)
    m = make_wallet("shop", "USD")
    with pytest.raises(services.InsufficientFunds) as ei:
        holds.place_hold(a, m, 10_000, "h1")
    assert "$100.00 USD" in str(ei.value) and "$50.00 USD" in str(ei.value)
    assert ei.value.fields == {"account_id": a.id, "amount_minor": 10_000, "available_minor": 5_000, "currency": "USD"}


def test_daily_limit_error_body_gbp(make_wallet):
    # Tier-0 GBP isn't openable via the API, but the limit text must format any currency.
    with pytest.raises(services.LimitExceeded) as ei:
        kyc._check_daily(0, 1, "GBP", 5_000, 0)
    assert "£11.00 GBP" in str(ei.value) and "£50.00 GBP" in str(ei.value)
    assert ei.value.fields["limit_minor"] == 1_100 and ei.value.fields["used_minor"] == 0


def test_limit_exceeded_api_body(make_wallet):
    a = make_wallet("ada", balance=3_000_000, tier=0)
    b = make_wallet("bob")
    c = APIClient()
    c.force_authenticate(a.owner)
    r = post(c, "/api/transfers/", {"source_wallet": a.id, "destination_wallet": b.id, "amount": 2_500_000}, "t1")
    body = r.json()
    assert r.status_code == 422 and body["error"] == "limit_exceeded" and body["limit"] == "daily_outflow"
    assert body["amount_minor"] == 2_500_000 and body["limit_minor"] == 2_000_000 and body["currency"] == "NGN"
    assert body["detail"] == (
        "Daily outflow limit for tier 0 is ₦20,000.00 NGN (used ₦0.00 NGN, requested ₦25,000.00 NGN)"
    )


# ------------------------------------------------------------------ holds vs daily limit


def test_tier0_hold_over_daily_limit_rejected_at_placement(make_wallet):
    a = make_wallet("ada", balance=4_000_000, tier=0)
    m = make_wallet("shop")
    with pytest.raises(services.LimitExceeded) as ei:
        holds.place_hold(a, m, 2_500_000, "h1")
    assert ei.value.fields["limit"] == "daily_outflow"
    a.refresh_from_db()
    assert a.held_amount == 0


def test_tier0_active_holds_and_spend_count_together(make_wallet):
    a = make_wallet("ada", balance=4_000_000, tier=0)
    m, b = make_wallet("shop"), make_wallet("bob")
    holds.place_hold(a, m, 1_200_000, "h1")  # ₦12,000 reserved
    services.transfer(a, b, 500_000, "t1")  # ₦5,000 spent -> 17,000 committed
    with pytest.raises(services.LimitExceeded) as ei:
        holds.place_hold(a, m, 400_000, "h2")  # would be 21,000 > 20,000
    assert ei.value.fields["used_minor"] == 1_700_000
    holds.place_hold(a, m, 300_000, "h3")  # exactly 20,000: allowed


def test_released_hold_frees_daily_headroom_and_capture_still_checked(make_wallet):
    a = make_wallet("ada", balance=4_000_000, tier=0)
    m = make_wallet("shop")
    h = holds.place_hold(a, m, 2_000_000, "h1")
    holds.release_hold(h.id)
    h2 = holds.place_hold(a, m, 2_000_000, "h2")
    holds.capture_hold(h2.id, 1_500_000)
    with pytest.raises(services.LimitExceeded):
        holds.place_hold(a, m, 600_000, "h3")  # 15,000 captured today + 6,000


def test_tier1_hold_limits_and_kyc_off(make_wallet, settings):
    a = make_wallet("ada", balance=10_000_000)
    m = make_wallet("shop")
    holds.place_hold(a, m, 9_000_000, "h1")  # ₦90,000: fine at tier 1
    verify(a.owner, 0)
    settings.KYC_ENFORCED = False
    holds.place_hold(a, m, 500_000, "h2")  # limits off
