"""Locust load test for the ledger API.

Each simulated user registers, logs in (JWT), opens an NGN wallet, funds it through the mock provider
(checkout -> signed webhook -> deposit), then mixes transfers, reads, history and hold/capture.

    locust -f loadtest/locustfile.py --headless -u 50 -r 10 -t 60s --host http://localhost:8000

Afterwards run `python manage.py reconcile` — status must be "clean" (no drift, every currency sums to 0).
"""

import random
import threading
import uuid

from locust import HttpUser, between, task

WALLETS: list[int] = []
_lock = threading.Lock()


def key():
    return uuid.uuid4().hex


class LedgerUser(HttpUser):
    wait_time = between(0.05, 0.2)

    def on_start(self):
        name = f"lt_{uuid.uuid4().hex[:10]}"
        pw = "load-test-pass"
        self.client.post("/api/auth/register/", json={"username": name, "password": pw}, name="auth/register")
        tok = self.client.post("/api/auth/token/", json={"username": name, "password": pw}, name="auth/token").json()
        self.client.headers["Authorization"] = f"Bearer {tok['access']}"
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
