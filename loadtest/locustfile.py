"""Locust load test for the ledger API.

Each simulated user registers, logs in (JWT), opens an NGN wallet, funds it through the mock provider
(checkout -> signed webhook -> deposit), then mixes transfers, reads, history and hold/capture.

    locust -f loadtest/locustfile.py --headless -u 50 -r 10 -t 60s --host http://localhost:8000

Seeding is KYC-compatible: each user submits KYC over the API and is then approved to tier 1 through the
service layer (``ledger.kyc.approve``, the same code the admin endpoint runs), so the run works with
``KYC_ENFORCED=1``. The locust process therefore needs DB access (same POSTGRES_* env as the server).

Afterwards run `python manage.py reconcile` — status must be "clean" (no drift, every currency sums to 0).
"""

import os
import random
import sys
import threading
import uuid
from pathlib import Path

from locust import HttpUser, between, task

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django  # noqa: E402

django.setup()
from django.contrib.auth import get_user_model  # noqa: E402
from django.db import close_old_connections, connection  # noqa: E402

from ledger import kyc  # noqa: E402

WALLETS: list[int] = []
_lock = threading.Lock()


def key():
    return uuid.uuid4().hex


KYC_DATA = {"full_name": "Load Tester", "date_of_birth": "1990-01-01", "country": "NG", "id_type": "NIN"}


def approve_to_tier1(username):
    """Admin approval via the service layer (audited, outbox event, virtual account backfill)."""
    close_old_connections()
    try:
        reviewer, _ = get_user_model().objects.get_or_create(username="loadtest-ops", defaults={"is_staff": True})
        user = get_user_model().objects.get(username=username)
        kyc.approve(kyc.profile_for(user).pk, reviewer)
    finally:
        connection.close()


class LedgerUser(HttpUser):
    wait_time = between(0.05, 0.2)

    def on_start(self):
        name = f"lt_{uuid.uuid4().hex[:10]}"
        pw = "load-test-pass"
        self.client.post("/api/auth/register/", json={"username": name, "password": pw}, name="auth/register")
        tok = self.client.post("/api/auth/token/", json={"username": name, "password": pw}, name="auth/token").json()
        self.client.headers["Authorization"] = f"Bearer {tok['access']}"
        self.client.post(
            "/api/kyc/", json={**KYC_DATA, "id_number": f"{random.randrange(10**10, 10**11)}"}, name="kyc [submit]"
        )
        approve_to_tier1(name)
        r = self.client.post(
            "/api/wallets/", json={"currency": "NGN"}, headers={"Idempotency-Key": key()}, name="wallets [create]"
        )
        self.wallet = r.json()["id"]
        self.client.post(
            "/provider/charges/",
            json={"wallet_id": self.wallet, "amount": 50_000_000, "currency": "NGN"},
            name="provider/charges (-> webhook deposit)",
        )
        with _lock:
            WALLETS.append(self.wallet)

    def _other(self):
        with _lock:
            others = [w for w in WALLETS if w != self.wallet]
        return random.choice(others) if others else None

    @task(6)
    def transfer(self):
        dst = self._other()
        if dst is None:
            return
        self.client.post(
            "/api/transfers/",
            json={"source_wallet": self.wallet, "destination_wallet": dst, "amount": random.randint(100, 50_000)},
            headers={"Idempotency-Key": key()},
            name="transfers",
        )

    @task(3)
    def balance(self):
        self.client.get(f"/api/wallets/{self.wallet}/", name="wallets/{id}")

    @task(2)
    def history(self):
        self.client.get(f"/api/wallets/{self.wallet}/transactions/?page_size=20", name="wallets/{id}/transactions")

    @task(1)
    def hold_and_capture(self):
        dst = self._other()
        if dst is None:
            return
        # Short-lived authorisation (left to the expiry job) + read-back; exercises held_amount locking.
        r = self.client.post(
            f"/api/wallets/{self.wallet}/holds/",
            json={"merchant_wallet": dst, "amount": 1_000, "expires_in_seconds": 30},
            headers={"Idempotency-Key": key()},
            name="holds [place]",
        )
        if r.status_code == 201 and random.random() < 0.5:
            self.client.get(f"/api/holds/{r.json()['id']}/", name="holds/{id}")
