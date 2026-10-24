#!/usr/bin/env python
"""End-to-end demo against a live server on a brand-new database.

    python scripts/demo.py            # needs local Postgres (POSTGRES_* env vars); no Redis/worker needed

Creates a fresh DB (default: ledger_demo), migrates, starts gunicorn with Celery in eager mode, then drives the
public HTTP API: register -> JWT -> wallets -> provider charge (signed webhook) -> transfer with fee -> FX ->
hold + partial capture -> refund -> failed withdrawal reversal -> reconciliation -> balances that sum to zero.
"""

import hashlib
import hmac
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

import psycopg
import requests

ROOT = Path(__file__).resolve().parent.parent
DB = os.environ.get("DEMO_DB", "ledger_demo")
PORT = int(os.environ.get("DEMO_PORT", "8200"))
BASE = f"http://127.0.0.1:{PORT}"
SECRET = "whsec_demo_secret"
ENV = {
    **os.environ,
    "POSTGRES_DB": DB,
    "DJANGO_SETTINGS_MODULE": "config.settings",
    "CELERY_EAGER": "1",
    "DEBUG": "0",
    "LOG_LEVEL": "WARNING",
    "PROVIDER_WEBHOOK_SECRET": SECRET,
    "PROVIDER_WEBHOOK_URL": f"{BASE}/api/webhooks/provider/",
    "TRANSFER_FEE_BPS": "50",
    "THROTTLE_ANON": "1000/min",
    "SECRET_KEY": "demo-only-secret-key-that-is-long-enough-for-hs256",
}
PY = sys.executable


def h(title):
    print(f"\n=== {title} " + "=" * max(0, 70 - len(title)))


def ngn(minor, cur="NGN"):
    sym = {"NGN": "₦", "USD": "$", "GBP": "£"}[cur]
    return f"{'-' if minor < 0 else ''}{sym}{abs(minor) / 100:,.2f}"


class Api:
    def __init__(self, token=None):
        self.s = requests.Session()
        if token:
            self.s.headers["Authorization"] = f"Bearer {token}"

    def get(self, path):
        r = self.s.get(BASE + path, timeout=30)
        return r

    def post(self, path, body=None, idem=True, expect=None):
        headers = {"Idempotency-Key": uuid.uuid4().hex} if idem is True else ({"Idempotency-Key": idem} if idem else {})
        r = self.s.post(BASE + path, json=body or {}, headers=headers, timeout=30)
        if expect is not None and r.status_code != expect:
            raise SystemExit(f"FAILED {path}: expected {expect}, got {r.status_code} {r.text}")
        return r


def fresh_db():
    conninfo = dict(
        host=os.environ.get("POSTGRES_HOST", "localhost"),
        port=os.environ.get("POSTGRES_PORT", "5432"),
        user=os.environ.get("POSTGRES_USER", "postgres"),
        password=os.environ.get("POSTGRES_PASSWORD", "postgres"),
        dbname="postgres",
        autocommit=True,
    )
    with psycopg.connect(**conninfo) as c:
        c.execute(f'DROP DATABASE IF EXISTS "{DB}" WITH (FORCE)')
        c.execute(f'CREATE DATABASE "{DB}"')
    subprocess.run([PY, "manage.py", "migrate", "-v0"], cwd=ROOT, env=ENV, check=True)
    code = (
        "from django.contrib.auth import get_user_model as g;"
        "g().objects.create_user('ops', password='ops-demo-pass', is_staff=True)"
    )
    subprocess.run([PY, "manage.py", "shell", "-c", code], cwd=ROOT, env=ENV, check=True, stdout=subprocess.DEVNULL)


def start_server():
    proc = subprocess.Popen(
        [PY, "-m", "gunicorn", "config.wsgi:application", "-b", f"127.0.0.1:{PORT}", "-w", "1", "--threads", "8"],
        cwd=ROOT,
        env=ENV,
        stdout=subprocess.DEVNULL,
        stderr=open("/tmp/demo_server.log", "w"),
    )
    for _ in range(50):
        try:
            if requests.get(f"{BASE}/healthz", timeout=1).ok:
                return proc
        except requests.ConnectionError:
            time.sleep(0.2)
    proc.kill()
    raise SystemExit("server did not start; see /tmp/demo_server.log")


def login(name, password):
    anon = Api()
    if name != "ops":
        anon.post("/api/auth/register/", {"username": name, "password": password}, idem=False, expect=201)
    tok = anon.post("/api/auth/token/", {"username": name, "password": password}, idem=False, expect=200).json()
    return Api(tok["access"]), tok


def bal(api, wid):
    w = api.get(f"/api/wallets/{wid}/").json()
    return w


def main():
    h("0. Fresh database + server")
    fresh_db()
    print(f"created database {DB!r}, applied all migrations, created staff user 'ops'")
    server = start_server()
    print(f"gunicorn up at {BASE} (Celery eager: webhooks/payouts run inline)")
    try:
        run()
    finally:
        server.terminate()
        server.wait(10)


def run():
    h("1. Register users + get JWTs")
    ada, tok = login("ada", "ada-demo-pass")
    bob, _ = login("bob", "bob-demo-pass")
    shop, _ = login("shop", "shop-demo-pass")
    ops, _ = login("ops", "ops-demo-pass")
    print(f"ada/bob/shop registered; ada's access token: {tok['access'][:24]}… (refresh issued too)")
    print("unauthenticated GET /api/wallets/ ->", requests.get(f"{BASE}/api/wallets/").status_code)

    h("2. Create wallets")
    a_ngn = ada.post("/api/wallets/", {"currency": "NGN"}, expect=201).json()["id"]
    a_usd = ada.post("/api/wallets/", {"currency": "USD"}, expect=201).json()["id"]
    b_ngn = bob.post("/api/wallets/", {"currency": "NGN"}, expect=201).json()["id"]
    s_ngn = shop.post("/api/wallets/", {"currency": "NGN"}, expect=201).json()["id"]
    print(f"ada NGN #{a_ngn}, ada USD #{a_usd}, bob NGN #{b_ngn}, shop NGN #{s_ngn}")
    print("bob reading ada's wallet ->", bob.get(f"/api/wallets/{a_ngn}/").status_code, "(hidden)")

    h("3. Deposit via provider checkout -> HMAC-signed webhook")
    for who, wid, amt in [("ada", a_ngn, 1_000_000_00), ("bob", b_ngn, 50_000_00)]:
        r = Api().post("/provider/charges/", {"wallet_id": wid, "amount": amt, "currency": "NGN"}, idem=False)
        print(
            f"{who}: provider charge {r.json()['provider_ref']} {ngn(amt)} -> wallet balance "
            f"{ngn(bal(ada if who == 'ada' else bob, wid)['balance'])}"
        )
    # Replay + forgery, done by hand with the shared secret:
    sys.path.insert(0, str(ROOT))
    os.environ.update(POSTGRES_DB=DB, DJANGO_SETTINGS_MODULE="config.settings")
    import django

    django.setup()
    from provider.models import ProviderEvent

    ev = ProviderEvent.objects.filter(type="charge.succeeded").order_by("id").first().payload
    body = json.dumps(ev).encode()
    ts = int(time.time())
    sig = hmac.new(SECRET.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    hdr = {"Content-Type": "application/json", "X-Provider-Signature": f"t={ts},v1={sig}"}
    r = requests.post(f"{BASE}/api/webhooks/provider/", data=body, headers=hdr)
    print(f"re-delivered event {ev['id']} with valid signature -> {r.status_code} {r.json()}")
    bad = {**hdr, "X-Provider-Signature": f"t={ts},v1={'0' * 64}"}
    r = requests.post(f"{BASE}/api/webhooks/provider/", data=body, headers=bad)
    print(f"same event with forged signature -> {r.status_code} {r.json()['error']}")
    print(f"ada balance still {ngn(bal(ada, a_ngn)['balance'])} (no double credit)")

    h("4. Transfer with fee (0.5%)")
    r = ada.post(
        "/api/transfers/",
        {"source_wallet": a_ngn, "destination_wallet": b_ngn, "amount": 100_000_00, "reference": "rent"},
        idem="demo-transfer-1",
        expect=201,
    ).json()
    print(f"ada -> bob {ngn(100_000_00)}; fee {ngn(r['metadata']['fee'])}; entries:")
    for e in r["entries"]:
        print(f"   account #{e['account_id']:<3} {ngn(e['amount']):>16}")
    r2 = ada.post(
        "/api/transfers/",
        {"source_wallet": a_ngn, "destination_wallet": b_ngn, "amount": 100_000_00, "reference": "rent"},
        idem="demo-transfer-1",
    )
    print(
        f"retry with same Idempotency-Key -> {r2.status_code}, "
        f"Idempotent-Replayed={r2.headers.get('Idempotent-Replayed')}, "
        f"same txn: {r2.json()['transaction_id'] == r['transaction_id']} (#{r['transaction_id']})"
    )
    print(
        "bob trying to spend from ada's wallet ->",
        bob.post("/api/transfers/", {"source_wallet": a_ngn, "destination_wallet": b_ngn, "amount": 1}).status_code,
    )

    h("5. FX transfer NGN -> USD")
    print(
        "rates:",
        ", ".join(
            f"{x['base']}/{x['quote']}={x['rate']}"
            for x in ada.get("/api/fx-rates/").json()
            if x["base"] in ("USD", "NGN") and x["quote"] in ("USD", "NGN")
        ),
    )
    r = ada.post(
        "/api/transfers/", {"source_wallet": a_ngn, "destination_wallet": a_usd, "amount": 150_000_00}, expect=201
    ).json()
    m = r["metadata"]
    print(
        f"ada NGN -> ada USD: {ngn(150_000_00)} @ {m['rate']} = {ngn(m['converted_amount'], 'USD')} "
        f"(fee {ngn(m['fee'])}); ada USD balance {ngn(bal(ada, a_usd)['balance'], 'USD')}"
    )

    h("6. Hold (authorisation) + partial capture + refund")
    hold = ada.post(
        f"/api/wallets/{a_ngn}/holds/",
        {"merchant_wallet": s_ngn, "amount": 20_000_00, "expires_in_seconds": 3600},
        expect=201,
    ).json()
    w = bal(ada, a_ngn)
    print(
        f"hold #{hold['id']} {ngn(20_000_00)}: ada balance {ngn(w['balance'])}, held {ngn(w['held_amount'])}, "
        f"available {ngn(w['available_balance'])}"
    )
    print("ada trying to capture her own hold ->", ada.post(f"/api/holds/{hold['id']}/capture/").status_code)
    cap = shop.post(f"/api/holds/{hold['id']}/capture/", {"amount": 15_000_00}, expect=200).json()
    w = bal(ada, a_ngn)
    print(
        f"shop captured {ngn(cap['captured_amount'])} (status {cap['status']}); remainder released -> "
        f"ada held {ngn(w['held_amount'])}, balance {ngn(w['balance'])}"
    )
    ref = shop.post(
        f"/api/transactions/{cap['capture_transaction']}/refund/",
        {"amount": 5_000_00, "reason": "item returned"},
        expect=201,
    ).json()
    print(
        f"shop refunded {ngn(5_000_00)} (txn #{ref['transaction_id']}); shop balance "
        f"{ngn(bal(shop, s_ngn)['balance'])}, ada balance {ngn(bal(ada, a_ngn)['balance'])}"
    )
    over = shop.post(f"/api/transactions/{cap['capture_transaction']}/refund/", {"amount": 10_000_01})
    print(f"refund beyond remaining {ngn(10_000_00)} -> {over.status_code} {over.json()['error']}")

    h("7. Withdrawals: one succeeds, one fails and is reversed")
    before = bal(bob, b_ngn)["balance"]
    ok = bob.post(
        f"/api/wallets/{b_ngn}/withdrawals/", {"amount": 30_000_00, "bank_account": "0123456789"}, expect=201
    ).json()
    print(
        f"withdrawal #{ok['id']} {ngn(30_000_00)} to 0123456789 -> "
        f"{bob.get(f'/api/withdrawals/{ok["id"]}/').json()['status']}"
    )
    bad = bob.post(
        f"/api/wallets/{b_ngn}/withdrawals/", {"amount": 20_000_00, "bank_account": "5550000"}, expect=201
    ).json()
    wd = bob.get(f"/api/withdrawals/{bad['id']}/").json()
    print(
        f"withdrawal #{bad['id']} {ngn(20_000_00)} to 5550000 -> {wd['status']} ({wd.get('failure_reason')}); "
        f"funds returned from suspense"
    )
    print(f"bob balance {ngn(before)} -> {ngn(bal(bob, b_ngn)['balance'])} (only the successful {ngn(30_000_00)} left)")

    h("8. Reconciliation (admin)")
    print("ada calling admin endpoint ->", ada.post("/api/admin/reconciliation/").status_code)
    rep = ops.post("/api/admin/reconciliation/", expect=201).json()
    print(f"report #{rep['id']}: status={rep['status'].upper()} summary={json.dumps(rep['summary'])}")

    h("9. Every account + per-currency zero-sum check")
    from django.db.models import Sum

    from ledger.models import Account, AuditLog, Entry

    print(f"{'acct':>4}  {'type':<12} {'owner':<6} {'cur':<4} {'balance':>18} {'SUM(entries)':>18}")
    for acc in Account.objects.select_related("owner").order_by("currency", "id"):
        s = Entry.objects.filter(account=acc).aggregate(s=Sum("amount"))["s"] or 0
        owner = acc.owner.username if acc.owner else "-"
        print(
            f"{acc.id:>4}  {acc.type:<12} {owner:<6} {acc.currency:<4} {ngn(acc.balance, acc.currency):>18} "
            f"{ngn(s, acc.currency):>18}{'  <-- DRIFT' if s != acc.balance else ''}"
        )
    for row in Entry.objects.values("currency").annotate(s=Sum("amount"), n=Sum(1)).order_by("currency"):
        print(
            f"SUM(all {row['currency']} entries) = {row['s']}  ({row['n']} entries)  "
            f"{'OK' if row['s'] == 0 else 'BROKEN'}"
        )
    print(
        "\naudit log:",
        ", ".join(f"{a}×{n}" for a, n in AuditLog.objects.values_list("action").annotate(n=Sum(1)).order_by("action")),
    )
    print(
        "request id round-trip:",
        requests.get(f"{BASE}/healthz", headers={"X-Request-ID": "demo-123"}).headers["X-Request-ID"],
    )
    metrics = requests.get(f"{BASE}/metrics").text
    print("metrics:", "; ".join(ln for ln in metrics.splitlines() if ln.startswith("ledger_transactions_posted_total")))
    print("\nDEMO COMPLETE")


if __name__ == "__main__":
    main()
