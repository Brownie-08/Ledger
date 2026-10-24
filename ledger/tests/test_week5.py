import json

import pytest
from rest_framework.test import APIClient

from ledger import holds, services, withdrawals
from ledger.models import Account, AuditLog, ReconciliationReport, Withdrawal
from ledger.reconciliation import run_reconciliation
from provider import api as provider_api
from provider.models import ProviderTransaction

pytestmark = pytest.mark.django_db


def post(c, url, data, key):
    return c.post(url, data, format="json", HTTP_IDEMPOTENCY_KEY=key)


@pytest.fixture
def jwt_client():
    """Register + log in through the real endpoints; returns an APIClient carrying the Bearer token."""

    def _make(username, password="s3cret-pass"):
        anon = APIClient()
        assert anon.post("/api/auth/register/", {"username": username, "password": password}).status_code == 201
        tok = anon.post("/api/auth/token/", {"username": username, "password": password}).data
        c = APIClient()
        c.credentials(HTTP_AUTHORIZATION=f"Bearer {tok['access']}")
        return c, tok

    return _make


@pytest.fixture
def staff(django_user_model):
    c = APIClient()
    c.force_authenticate(django_user_model.objects.create_user("ops", password="x", is_staff=True))
    return c


def fund(wallet_id, amount):
    services.deposit(Account.objects.get(pk=wallet_id), amount, f"seed:{wallet_id}:{amount}")


# ---------------------------------------------------------------- auth / permissions


def test_unauthenticated_rejected():
    c = APIClient()
    assert c.get("/api/wallets/").status_code == 401
    assert post(c, "/api/transfers/", {}, "k").status_code == 401


def test_register_token_refresh(jwt_client):
    c, tok = jwt_client("ada")
    r = APIClient().post("/api/auth/token/refresh/", {"refresh": tok["refresh"]})
    assert r.status_code == 200 and "access" in r.data
    assert APIClient().post("/api/auth/token/", {"username": "ada", "password": "wrong"}).status_code == 401
    dup = APIClient().post("/api/auth/register/", {"username": "ada", "password": "s3cret-pass"})
    assert dup.status_code == 400


def test_wallet_created_for_self_even_if_other_owner_given(jwt_client, django_user_model):
    c, _ = jwt_client("ada")
    other = django_user_model.objects.create_user("eve")
    r = post(c, "/api/wallets/", {"currency": "NGN", "owner": other.id}, "w1")
    assert r.status_code == 201
    assert Account.objects.get(pk=r.data["id"]).owner.username == "ada"


def test_users_only_see_their_own_wallets(jwt_client):
    ada, _ = jwt_client("ada")
    bob, _ = jwt_client("bob")
    a = post(ada, "/api/wallets/", {"currency": "NGN"}, "w")
    b = post(bob, "/api/wallets/", {"currency": "NGN"}, "w")  # same key, different user: no collision
    assert a.status_code == b.status_code == 201 and a.data["id"] != b.data["id"]
    assert [w["id"] for w in ada.get("/api/wallets/").data] == [a.data["id"]]
    assert bob.get(f"/api/wallets/{a.data['id']}/").status_code == 404
    assert bob.get(f"/api/wallets/{a.data['id']}/transactions/").status_code == 404


def test_cannot_spend_from_someone_elses_wallet(jwt_client):
    ada, _ = jwt_client("ada")
    bob, _ = jwt_client("bob")
    a = post(ada, "/api/wallets/", {"currency": "NGN"}, "w").data["id"]
    b = post(bob, "/api/wallets/", {"currency": "NGN"}, "w").data["id"]
    fund(a, 1000)
    r = post(bob, "/api/transfers/", {"source_wallet": a, "destination_wallet": b, "amount": 100}, "steal")
    assert r.status_code == 404
    assert post(bob, f"/api/wallets/{a}/withdrawals/", {"amount": 1, "bank_account": "1"}, "x").status_code == 404
    assert post(bob, f"/api/wallets/{a}/holds/", {"merchant_wallet": b, "amount": 1}, "h").status_code == 404
    ok = post(ada, "/api/transfers/", {"source_wallet": a, "destination_wallet": b, "amount": 100}, "pay")
    assert ok.status_code == 201
    assert Account.objects.get(pk=a).balance == 900


def test_admin_only_endpoints(jwt_client, staff):
    ada, _ = jwt_client("ada")
    a = post(ada, "/api/wallets/", {"currency": "NGN"}, "w").data["id"]
    assert post(ada, f"/api/wallets/{a}/deposit/", {"amount": 10}, "d").status_code == 403
    assert ada.get("/api/admin/reconciliation/").status_code == 403
    assert post(ada, "/api/admin/reconciliation/", {}, "r").status_code == 403
    assert ada.get("/api/admin/audit-logs/").status_code == 403
    assert post(staff, f"/api/wallets/{a}/deposit/", {"amount": 10}, "d").status_code == 201
    txn_id = Account.objects.get(pk=a).entries.get().transaction_id
    assert post(ada, f"/api/transactions/{txn_id}/reverse/", {"reason": "x"}, "rv").status_code == 403
    assert post(staff, f"/api/transactions/{txn_id}/reverse/", {"reason": "x"}, "rv").status_code == 201


def test_hold_capture_only_by_merchant_and_refund_only_by_payee(jwt_client):
    ada, _ = jwt_client("ada")
    shop, _ = jwt_client("shop")
    a = post(ada, "/api/wallets/", {"currency": "NGN"}, "w").data["id"]
    m = post(shop, "/api/wallets/", {"currency": "NGN"}, "w").data["id"]
    fund(a, 1000)
    h = post(ada, f"/api/wallets/{a}/holds/", {"merchant_wallet": m, "amount": 600}, "h").data["id"]
    assert ada.get(f"/api/holds/{h}/").status_code == 200  # customer can view
    assert post(ada, f"/api/holds/{h}/capture/", {}, "c").status_code == 404  # but not capture
    cap = post(shop, f"/api/holds/{h}/capture/", {"amount": 400}, "c")
    assert cap.status_code == 200 and cap.data["status"] == "captured"
    txn = cap.data["capture_transaction"]
    assert post(ada, f"/api/transactions/{txn}/refund/", {"amount": 100}, "r").status_code == 404
    assert post(shop, f"/api/transactions/{txn}/refund/", {"amount": 100}, "r").status_code == 201
    assert Account.objects.get(pk=a).balance == 700


# ---------------------------------------------------------------- throttling


def test_throttling_returns_429(settings, jwt_client):
    ada, _ = jwt_client("ada")
    settings.LEDGER_THROTTLE_RATES = {"anon": "1000/min", "user": "3/min", "money": "1000/min"}
    codes = [ada.get("/api/wallets/").status_code for _ in range(5)]
    assert codes == [200, 200, 200, 429, 429]


def test_money_throttle_is_tighter(settings, jwt_client):
    ada, _ = jwt_client("ada")
    bob, _ = jwt_client("bob")
    a = post(ada, "/api/wallets/", {"currency": "NGN"}, "w").data["id"]
    b = post(bob, "/api/wallets/", {"currency": "NGN"}, "w").data["id"]
    fund(a, 10_000)
    settings.LEDGER_THROTTLE_RATES = {"anon": "1000/min", "user": "1000/min", "money": "2/min"}
    codes = [
        post(ada, "/api/transfers/", {"source_wallet": a, "destination_wallet": b, "amount": 1}, f"t{i}").status_code
        for i in range(3)
    ]
    assert codes == [201, 201, 429]
    assert ada.get("/api/wallets/").status_code == 200  # reads unaffected


def test_anon_throttle(settings):
    settings.LEDGER_THROTTLE_RATES = {"anon": "2/min", "user": "1000/min", "money": "1000/min"}
    codes = [APIClient().post("/api/auth/token/", {"username": "x", "password": "y"}).status_code for _ in range(3)]
    assert codes == [401, 401, 429]


# ---------------------------------------------------------------- audit log


def test_money_moving_actions_are_audited(jwt_client, staff):
    ada, _ = jwt_client("ada")
    bob, _ = jwt_client("bob")
    a = post(ada, "/api/wallets/", {"currency": "NGN"}, "w").data["id"]
    b = post(bob, "/api/wallets/", {"currency": "NGN"}, "w").data["id"]
    post(staff, f"/api/wallets/{a}/deposit/", {"amount": 5000}, "d")
    t = post(ada, "/api/transfers/", {"source_wallet": a, "destination_wallet": b, "amount": 1000}, "t").data
    post(bob, f"/api/transactions/{t['transaction_id']}/refund/", {"amount": 200}, "r")
    h = post(ada, f"/api/wallets/{a}/holds/", {"merchant_wallet": b, "amount": 300}, "h").data["id"]
    post(bob, f"/api/holds/{h}/release/", {}, "rel")
    actions = list(AuditLog.objects.order_by("id").values_list("action", "actor__username"))
    assert actions == [
        ("deposit.manual", "ops"),
        ("transfer", "ada"),
        ("refund", "bob"),
        ("hold.place", "ada"),
        ("hold.release", "bob"),
    ]
    entry = AuditLog.objects.get(action="transfer")
    assert entry.transaction_id == t["transaction_id"] and entry.data["amount"] == 1000
    listed = staff.get("/api/admin/audit-logs/?action=transfer").data["results"]
    assert len(listed) == 1
    with pytest.raises(Exception):  # noqa: B017 - append-only guard
        entry.save()


def test_system_actions_audited(make_wallet, webhook_transport, django_capture_on_commit_callbacks):
    from datetime import timedelta

    from django.utils import timezone

    w = make_wallet("ada", balance=1000)
    m = make_wallet("shop")
    with django_capture_on_commit_callbacks(execute=True):
        provider_api.create_charge(wallet_id=w.id, amount=500, currency="NGN")
    hold = holds.place_hold(w, m, 100, "h1", timedelta(seconds=1))
    holds.expire_due_holds(now=timezone.now() + timedelta(seconds=5))
    hold.refresh_from_db()
    assert hold.status == "expired"
    assert set(AuditLog.objects.values_list("action", flat=True)) == {"webhook.charge.succeeded", "hold.expire"}


# ---------------------------------------------------------------- reconciliation


def test_reconciliation_clean(make_wallet, webhook_transport, django_capture_on_commit_callbacks):
    w = make_wallet("ada")
    with django_capture_on_commit_callbacks(execute=True):
        provider_api.create_charge(wallet_id=w.id, amount=5000, currency="NGN")
    with django_capture_on_commit_callbacks(execute=True):
        withdrawals.create_withdrawal(w, 1000, "0123456789", "wd1")
    with django_capture_on_commit_callbacks(execute=True):
        withdrawals.create_withdrawal(w, 500, "9990000", "wd2")  # fails at provider
    report = run_reconciliation()
    assert report.status == "clean", report.mismatches
    assert report.summary["provider_charges"] == 1 and report.summary["withdrawals"] == 2


def test_reconciliation_detects_mismatches(make_wallet, django_capture_on_commit_callbacks):
    w = make_wallet("ada")
    # Provider says a charge succeeded but its webhook never reached us (no transport → delivery fails silently).
    with django_capture_on_commit_callbacks(execute=False):
        provider_api.create_charge(wallet_id=w.id, amount=7000, currency="NGN")
    # Ledger has a provider-style deposit the provider never heard of.
    services.deposit(w, 300, "charge:ch_ghost", "ch_ghost")
    # Provider paid out but we never got payout.succeeded → withdrawal still pending.
    with django_capture_on_commit_callbacks(execute=False):
        wd = withdrawals.create_withdrawal(w, 100, "0123456789", "wd1")
    with django_capture_on_commit_callbacks(execute=False):  # payout webhook "lost"
        provider_api.create_payout(
            reference=f"wd-{wd.id}", withdrawal_id=wd.id, amount=100, currency="NGN", bank_account="0123456789"
        )
    ProviderTransaction.objects.filter(withdrawal_id=wd.id).update(status="succeeded")
    # Corrupt a cached balance (bypassing services) to simulate drift.
    Account.objects.filter(pk=w.id).update(balance=999_999)

    report = run_reconciliation()
    kinds = sorted(m["kind"] for m in report.mismatches)
    assert report.status == "mismatches"
    assert kinds == [
        "balance_drift",
        "charge_missing_in_ledger",
        "deposit_missing_at_provider",
        "payout_status_mismatch",
    ]
    assert Withdrawal.objects.get(pk=wd.id).status == "pending"


def test_reconciliation_admin_endpoint_and_task(staff, make_wallet):
    make_wallet("ada", balance=100)
    r = post(staff, "/api/admin/reconciliation/", {}, "run1")
    assert r.status_code == 201 and r.data["status"] == "clean"
    assert staff.get("/api/admin/reconciliation/").data["results"][0]["id"] == r.data["id"]
    assert staff.get(f"/api/admin/reconciliation/{r.data['id']}/").data["summary"]["accounts"] >= 1

    from ledger.tasks import nightly_reconciliation

    rid = nightly_reconciliation.delay().get()
    assert ReconciliationReport.objects.get(pk=rid).status == "clean"


def test_reconcile_management_command(make_wallet, capsys):
    from django.core.management import call_command

    make_wallet("ada", balance=100)
    call_command("reconcile")
    assert json.loads(capsys.readouterr().out)["status"] == "clean"
