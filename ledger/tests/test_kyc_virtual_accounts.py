"""KYC tiers (risk-based onboarding) and mock virtual bank accounts with inbound bank transfers."""

import re

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from ledger import kyc, services, virtual_accounts, withdrawals
from ledger.models import Account, AuditLog, KYCProfile, OutboxEvent, Transaction, VirtualAccount
from ledger.reconciliation import run_reconciliation
from provider import api as provider_api

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("kyc_on")]

KYC = {
    "full_name": "Ada Obi",
    "date_of_birth": "1995-04-12",
    "country": "NG",
    "id_type": "BVN",
    "id_number": "22212345678",
}


def post(c, url, data=None, key=None):
    extra = {"HTTP_IDEMPOTENCY_KEY": key} if key else {}
    return c.post(url, data or {}, format="json", **extra)


@pytest.fixture
def signup():
    def _make(username):
        anon = APIClient()
        assert post(anon, "/api/auth/register/", {"username": username, "password": "s3cret-pass"}).status_code == 201
        c = APIClient()
        c.force_authenticate(get_user_model().objects.get(username=username))
        return c

    return _make


@pytest.fixture
def staff(django_user_model):
    c = APIClient()
    c.force_authenticate(django_user_model.objects.create_user("ops", password="x", is_staff=True))
    return c


def user(name):
    return get_user_model().objects.get(username=name)


def tier1(name, password="x"):
    u, _ = get_user_model().objects.get_or_create(username=name)
    p = kyc.profile_for(u)
    p.tier, p.status = 1, KYCProfile.Status.APPROVED
    p.save()
    return u


def open_wallet(c, currency="NGN", key=None):
    return post(c, "/api/wallets/", {"currency": currency}, key or f"w-{currency}-{id(c)}")


# ------------------------------------------------------------------ signup + tier 0 limits


def test_signup_is_instant_tier0(signup):
    c = signup("ada")
    r = c.get("/api/kyc/")
    assert r.status_code == 200
    assert r.data["tier"] == 0 and r.data["status"] == "none"
    assert r.data["limits"]["max_balance"]["NGN"] == 5_000_000
    w = open_wallet(c)
    assert w.status_code == 201 and w.data["virtual_account"] is None


@pytest.mark.parametrize("currency", ["USD", "GBP"])
def test_tier0_ngn_wallet_only(signup, currency):
    c = signup("ada")
    r = open_wallet(c, currency)
    assert r.status_code == 403 and r.data["error"] == "kyc_required"
    with pytest.raises(services.KycRequired):
        kyc.open_wallet(user("ada"), currency)  # enforced in the service layer too


def test_tier0_max_balance_enforced_in_posting_layer(signup):
    c = signup("ada")
    w = Account.objects.get(pk=open_wallet(c).data["id"])
    services.deposit(w, 5_000_000, "d1")  # exactly at the ₦50,000 cap
    with pytest.raises(services.LimitExceeded):
        services.deposit(w, 1, "d2")
    w.refresh_from_db()
    assert w.balance == 5_000_000


def test_tier0_daily_outflow_limit(signup, make_wallet):
    c = signup("ada")
    w = Account.objects.get(pk=open_wallet(c).data["id"])
    services.deposit(w, 5_000_000, "d1")
    bob = make_wallet("bob")
    assert (
        post(
            c, "/api/transfers/", {"source_wallet": w.id, "destination_wallet": bob.id, "amount": 1_500_000}, "t1"
        ).status_code
        == 201
    )
    r = post(c, "/api/transfers/", {"source_wallet": w.id, "destination_wallet": bob.id, "amount": 600_000}, "t2")
    assert r.status_code == 422 and r.data["error"] == "limit_exceeded"
    # still room for the remaining ₦5,000 today
    assert (
        post(
            c, "/api/transfers/", {"source_wallet": w.id, "destination_wallet": bob.id, "amount": 500_000}, "t3"
        ).status_code
        == 201
    )
    assert kyc.outflow_today(w.id, "NGN") == 2_000_000


def test_tier0_balance_cap_blocks_incoming_transfer(signup, make_wallet):
    c = signup("ada")
    w = Account.objects.get(pk=open_wallet(c).data["id"])
    tier1("rich")
    rich = make_wallet("rich", balance=10_000_000)
    with pytest.raises(services.LimitExceeded):
        services.transfer(rich, w, 5_000_001, "big")


def test_tier0_no_withdrawals_service_layer(signup):
    c = signup("ada")
    w = Account.objects.get(pk=open_wallet(c).data["id"])
    services.deposit(w, 100_000, "d1")
    r = post(c, f"/api/wallets/{w.id}/withdrawals/", {"amount": 1000, "bank_account": "0123456789"}, "wd1")
    assert r.status_code == 403 and r.data["error"] == "kyc_required"
    with pytest.raises(services.KycRequired):
        withdrawals.create_withdrawal(w, 1000, "0123456789", "wd2")


def test_tier0_no_fx(make_wallet):
    ngn = make_wallet("ada", balance=1_000_000)
    tier1("ada")
    usd = make_wallet("ada", "USD")
    KYCProfile.objects.filter(user__username="ada").update(tier=0)  # e.g. downgraded
    with pytest.raises(services.KycRequired):
        services.transfer(ngn, usd, 150_000, "fx1")


def test_refund_and_reversal_credits_exempt_from_cap(signup, make_wallet):
    c = signup("ada")
    w = Account.objects.get(pk=open_wallet(c).data["id"])
    services.deposit(w, 5_000_000, "d1")
    bob = make_wallet("bob")
    t = services.transfer(w, bob, 1_000_000, "t1")
    services.deposit(w, 1_000_000, "d2")  # back at the cap
    services.reverse_transaction(t.id, "rev1")  # returning her own money must still work
    w.refresh_from_db()
    assert w.balance == 7_000_000 - 1_000_000


# ------------------------------------------------------------------ submit / review / permissions


def test_submit_validation(signup):
    c = signup("ada")
    assert post(c, "/api/kyc/", {**KYC, "id_number": "123"}).status_code == 400
    assert post(c, "/api/kyc/", {**KYC, "country": "GB"}).status_code == 400  # BVN is NG-only
    assert post(c, "/api/kyc/", {**KYC, "date_of_birth": "2015-01-01"}).status_code == 400  # under 18
    ok = post(c, "/api/kyc/", {**KYC, "country": "GB", "id_type": "PASSPORT", "id_number": "123456789"})
    assert ok.status_code == 201


def test_submit_then_admin_approves(signup, staff):
    c = signup("ada")
    r = post(c, "/api/kyc/", KYC)
    assert r.status_code == 201 and r.data["status"] == "pending" and r.data["tier"] == 0
    assert r.data["id_number"] == "*******5678"  # masked in responses
    queue = staff.get("/api/admin/kyc/")
    assert [p["username"] for p in queue.data["results"]] == ["ada"]
    a = post(staff, f"/api/admin/kyc/{r.data['id']}/approve/")
    assert a.status_code == 200 and a.data["tier"] == 1 and a.data["status"] == "approved"
    assert a.data["reviewer"] == "ops" and a.data["reviewed_at"]
    assert staff.get("/api/admin/kyc/").data["results"] == []
    assert AuditLog.objects.filter(action="kyc.approve", object_id=str(r.data["id"])).exists()
    ev = OutboxEvent.objects.get(event_type="kyc.approved")
    assert ev.payload["user_id"] == user("ada").id
    # now USD is allowed and comes with a virtual account
    usd = open_wallet(c, "USD")
    assert usd.status_code == 201 and usd.data["virtual_account"]["scheme"] == "aba"
    assert post(c, "/api/kyc/", KYC).status_code == 409  # already approved


def test_reject_then_resubmit(signup, staff):
    c = signup("ada")
    pid = post(c, "/api/kyc/", KYC).data["id"]
    assert post(staff, f"/api/admin/kyc/{pid}/reject/", {}).status_code == 400  # reason required
    r = post(staff, f"/api/admin/kyc/{pid}/reject/", {"reason": "name does not match BVN"})
    assert r.status_code == 200 and r.data["status"] == "rejected" and r.data["tier"] == 0
    assert c.get("/api/kyc/").data["rejection_reason"] == "name does not match BVN"
    assert OutboxEvent.objects.get(event_type="kyc.rejected").payload["reason"] == "name does not match BVN"
    assert AuditLog.objects.filter(action="kyc.reject").count() == 1
    assert post(staff, f"/api/admin/kyc/{pid}/approve/").status_code == 409  # not pending
    again = post(c, "/api/kyc/", {**KYC, "full_name": "Adaeze Obi"})
    assert again.status_code == 201 and again.data["status"] == "pending" and again.data["rejection_reason"] == ""
    assert post(staff, f"/api/admin/kyc/{pid}/approve/").status_code == 200


def test_only_staff_can_review(signup):
    ada = signup("ada")
    eve = signup("eve")
    pid = post(ada, "/api/kyc/", KYC).data["id"]
    assert eve.get("/api/admin/kyc/").status_code == 403
    assert post(eve, f"/api/admin/kyc/{pid}/approve/").status_code == 403
    assert post(ada, f"/api/admin/kyc/{pid}/approve/").status_code == 403  # not even your own
    assert post(eve, f"/api/admin/kyc/{pid}/reject/", {"reason": "spite"}).status_code == 403
    assert APIClient().get("/api/kyc/").status_code == 401
    assert KYCProfile.objects.get(pk=pid).status == "pending"


def test_admin_list_filter(signup, staff):
    post(signup("ada"), "/api/kyc/", KYC)
    signup("bob")
    assert len(staff.get("/api/admin/kyc/?status=all").data["results"]) == 2
    assert [p["username"] for p in staff.get("/api/admin/kyc/?status=none").data["results"]] == ["bob"]


# ------------------------------------------------------------------ virtual accounts


def test_backfill_virtual_account_on_approval(signup, staff):
    c = signup("ada")
    wid = open_wallet(c).data["id"]
    assert c.get(f"/api/wallets/{wid}/").data["virtual_account"] is None
    pid = post(c, "/api/kyc/", KYC).data["id"]
    post(staff, f"/api/admin/kyc/{pid}/approve/")
    va = c.get(f"/api/wallets/{wid}/").data["virtual_account"]
    assert va["scheme"] == "nuban" and va["bank_name"] == "Ledger MFB (mock)" and va["mock"] is True
    assert va["account_name"] == "Ada Obi"
    assert virtual_accounts.is_valid_nuban(va["account_number"])
    assert OutboxEvent.objects.get(event_type="kyc.approved").payload["virtual_accounts"] == [va["account_number"]]
    assert c.get("/api/wallets/").data[0]["virtual_account"]["account_number"] == va["account_number"]


def test_account_number_formats(make_wallet):
    tier1("ada")
    ngn = virtual_accounts.issue(make_wallet("ada", "NGN"))
    gbp = virtual_accounts.issue(make_wallet("ada", "GBP"))
    usd = virtual_accounts.issue(make_wallet("ada", "USD"))
    assert re.fullmatch(r"\d{10}", ngn.account_number) and virtual_accounts.is_valid_nuban(ngn.account_number)
    assert re.fullmatch(r"\d{6}", gbp.sort_code) and re.fullmatch(r"\d{8}", gbp.account_number)
    assert re.fullmatch(r"\d{9}", usd.routing_number) and virtual_accounts.is_valid_aba(usd.routing_number)
    assert re.fullmatch(r"\d{12}", usd.account_number)
    assert {ngn.bank_name, gbp.bank_name, usd.bank_name} == {
        "Ledger MFB (mock)",
        "Ledger Bank UK (mock)",
        "Ledger Bank US (mock)",
    }
    assert virtual_accounts.issue(Account.objects.get(pk=ngn.wallet_id)) == ngn  # idempotent


def test_known_check_digits():
    # CBN NUBAN example from the spec: bank 011, serial 000001457 -> check digit 9
    assert virtual_accounts.nuban_check_digit("011", "000001457") == "9"
    assert virtual_accounts.is_valid_aba("011000015")  # well-known valid ABA (Federal Reserve Boston)
    assert not virtual_accounts.is_valid_aba("011000016")


def test_numbers_unique_and_collision_checked(make_wallet, monkeypatch):
    numbers = {virtual_accounts.issue(make_wallet(f"u{i}")).account_number for i in range(60)}
    assert len(numbers) == 60
    # Force a collision: the next wallet's first candidate equals an existing number -> next attempt used.
    w = make_wallet("late")
    taken = VirtualAccount.objects.first().account_number
    real = virtual_accounts.candidate

    def colliding(wallet, attempt):
        return {"scheme": "nuban", "account_number": taken} if attempt == 0 else real(wallet, attempt)

    monkeypatch.setattr(virtual_accounts, "candidate", colliding)
    va = virtual_accounts.issue(w)
    assert va.account_number != taken and va.account_number == real(w, 1)["account_number"]


def test_generation_is_deterministic_per_wallet(make_wallet):
    w = make_wallet("ada")
    assert virtual_accounts.candidate(w, 0) == virtual_accounts.candidate(w, 0)
    assert virtual_accounts.candidate(w, 0) != virtual_accounts.candidate(w, 1)


# ------------------------------------------------------------------ inbound bank transfers


def bank_transfer(account_number, amount, currency="NGN", **kw):
    return provider_api.simulate_bank_transfer(
        account_number=account_number, amount=amount, currency=currency, sender_name="Mama Ada", **kw
    )


def test_inbound_bank_transfer_credits_wallet(make_wallet, webhook_transport, django_capture_on_commit_callbacks):
    tier1("ada")
    w = make_wallet("ada")
    va = virtual_accounts.issue(w)
    with django_capture_on_commit_callbacks(execute=True):
        r = APIClient().post(
            "/provider/bank-transfers/",
            {"account_number": va.account_number, "amount": 2_500_000, "currency": "NGN", "sender_name": "Mama Ada"},
            format="json",
        )
    assert r.status_code == 201
    assert webhook_transport[0][1] == 200 and webhook_transport[0][2] == {"status": "processed"}
    w.refresh_from_db()
    assert w.balance == 2_500_000
    txn = Transaction.objects.get(type="bank_transfer_in")
    assert txn.metadata["sender_name"] == "Mama Ada"
    assert AuditLog.objects.filter(action="webhook.bank_transfer.received").exists()
    assert run_reconciliation().status == "clean"


def test_inbound_bank_transfer_dedupe(make_wallet, webhook_transport, django_capture_on_commit_callbacks):
    from provider.models import ProviderEvent
    from provider.tasks import deliver_webhook

    tier1("ada")
    w = make_wallet("ada")
    va = virtual_accounts.issue(w)
    with django_capture_on_commit_callbacks(execute=True):
        bank_transfer(va.account_number, 100_000, client_reference="bt-1")
        bank_transfer(va.account_number, 100_000, client_reference="bt-1")  # provider-side idempotent
    ev = ProviderEvent.objects.get()
    ProviderEvent.objects.filter(pk=ev.pk).update(delivered_at=None)
    deliver_webhook.apply(args=[ev.pk])  # same event re-delivered
    assert [c[2]["status"] for c in webhook_transport] == ["processed", "duplicate"]
    w.refresh_from_db()
    assert w.balance == 100_000


def test_unknown_account_goes_to_suspense_and_is_flagged(webhook_transport, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        bt = bank_transfer("9999999999", 750_000)
    assert webhook_transport[0][1] == 200
    suspense = Account.objects.get(type=Account.Type.SUSPENSE, currency="NGN")
    assert suspense.balance == 750_000
    txn = Transaction.objects.get(type="inbound_unallocated")
    assert txn.metadata["reason"] == "unknown_account" and txn.metadata["account_number"] == "9999999999"
    report = run_reconciliation()
    assert report.status == "mismatches"
    assert report.summary["mismatch_counts"] == {"unallocated_inbound": 1}
    m = report.mismatches[0]
    assert m["provider_ref"] == bt.provider_ref and m["amount"] == 750_000
    # the provider's record still matches the ledger (it's booked, just not to a customer)
    assert "charge_missing_in_ledger" not in report.summary["mismatch_counts"]


def test_inbound_over_tier_limit_goes_to_suspense(make_wallet, webhook_transport, django_capture_on_commit_callbacks):
    # A tier-0 wallet with a VA (approved then downgraded) can't exceed its cap even via bank transfer.
    tier1("ada")
    w = make_wallet("ada")
    va = virtual_accounts.issue(w)
    KYCProfile.objects.filter(user__username="ada").update(tier=0)
    with django_capture_on_commit_callbacks(execute=True):
        bank_transfer(va.account_number, 6_000_000)
    w.refresh_from_db()
    assert w.balance == 0
    assert Transaction.objects.get(type="inbound_unallocated").metadata["reason"] == "limit_exceeded"


def test_card_charge_over_tier0_cap_goes_to_suspense(signup, webhook_transport, django_capture_on_commit_callbacks):
    c = signup("ada")
    wid = open_wallet(c).data["id"]
    with django_capture_on_commit_callbacks(execute=True):
        provider_api.create_charge(wallet_id=wid, amount=6_000_000, currency="NGN")
    assert webhook_transport[0][1] == 200
    assert Account.objects.get(pk=wid).balance == 0
    report = run_reconciliation()
    assert report.summary["mismatch_counts"] == {"unallocated_inbound": 1}


def test_currency_mismatch_goes_to_suspense(make_wallet, webhook_transport, django_capture_on_commit_callbacks):
    tier1("ada")
    va = virtual_accounts.issue(make_wallet("ada"))
    with django_capture_on_commit_callbacks(execute=True):
        bank_transfer(va.account_number, 1_000, currency="USD")
    assert Transaction.objects.get(type="inbound_unallocated").metadata["reason"] == "currency_mismatch"


def test_enforcement_can_be_disabled(settings, signup):
    settings.KYC_ENFORCED = False
    c = signup("ada")
    assert open_wallet(c, "USD").status_code == 201
