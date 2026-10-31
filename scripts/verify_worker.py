"""Real Redis + separate Celery process + signed HTTP webhook verification.

Requires a disposable PostgreSQL database whose name ends in _verification.
No existing database is dropped, reset, or flushed. Results exclude credentials.
"""

import json
import os
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")


def wait_for(predicate, label, timeout=45):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.2)
    raise AssertionError(f"Timed out: {label}")


def main():
    import django

    django.setup()
    from django.conf import settings
    from django.contrib.auth import get_user_model
    from django.core.management import call_command
    from redis import Redis

    from ledger import services, signing, withdrawals
    from ledger.models import Account, KYCProfile, Transaction
    from ledger.reconciliation import run_reconciliation
    from provider.api import create_charge
    from provider.models import ProviderEvent

    if not settings.DATABASES["default"]["NAME"].endswith("_verification") or settings.CELERY_TASK_ALWAYS_EAGER:
        raise SystemExit("Use a dedicated *_verification database and CELERY_EAGER=0")
    assert Redis.from_url(settings.CELERY_BROKER_URL).ping()
    call_command("migrate", verbosity=0)
    local = ROOT / ".local"
    local.mkdir(exist_ok=True)
    env = {**os.environ, "PROVIDER_WEBHOOK_URL": "http://127.0.0.1:8212/", "CELERY_EAGER": "0"}
    responses = []

    class Gate(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            if not responses:
                responses.append(503)
                self.send_response(503)
                self.end_headers()
                return
            response = requests.post(
                "http://127.0.0.1:8211/api/webhooks/provider/",
                data=body,
                headers={"Content-Type": "application/json", signing.HEADER: self.headers[signing.HEADER]},
                timeout=10,
            )
            responses.append(response.status_code)
            self.send_response(response.status_code)
            self.end_headers()
            self.wfile.write(response.content)

    gateway = ThreadingHTTPServer(("127.0.0.1", 8212), Gate)
    thread = threading.Thread(target=gateway.serve_forever, daemon=True)
    thread.start()
    processes = []
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    with (local / "worker-verification.log").open("w") as log:
        try:
            processes.append(
                subprocess.Popen(
                    [sys.executable, "manage.py", "runserver", "127.0.0.1:8211", "--noreload"],
                    cwd=ROOT,
                    env=env,
                    stdout=log,
                    stderr=log,
                    creationflags=flags,
                )
            )
            processes.append(
                subprocess.Popen(
                    [
                        sys.executable,
                        "-m",
                        "celery",
                        "-A",
                        "config",
                        "worker",
                        "--pool=solo",
                        "--concurrency=1",
                        "-Q",
                        settings.CELERY_TASK_DEFAULT_QUEUE,
                        "--without-gossip",
                        "--without-mingle",
                        "--without-heartbeat",
                        "-l",
                        "INFO",
                    ],
                    cwd=ROOT,
                    env=env,
                    stdout=log,
                    stderr=log,
                    creationflags=flags,
                )
            )

            def healthy():
                try:
                    return requests.get("http://127.0.0.1:8211/healthz", timeout=1).ok
                except requests.RequestException:
                    return False

            wait_for(healthy, "HTTP API")
            user = get_user_model().objects.create_user(f"worker_{uuid.uuid4().hex[:8]}")
            KYCProfile.objects.create(user=user, tier=1, status="approved")
            wallet = Account.objects.create(owner=user, currency="NGN")
            services.deposit(wallet, 10000, f"worker:seed:{wallet.id}")
            charge = create_charge(wallet_id=wallet.id, amount=3000, currency="NGN")
            event = ProviderEvent.objects.get(payload__data__provider_ref=charge.provider_ref)
            wait_for(lambda: ProviderEvent.objects.get(pk=event.id).delivered_at, "webhook retry delivery")
            event.refresh_from_db()
            assert event.attempts >= 2 and responses[:2] == [503, 200]
            wallet.refresh_from_db()
            assert wallet.balance == 13000
            raw = json.dumps(event.payload).encode()
            for _ in range(2):
                response = requests.post(
                    "http://127.0.0.1:8211/api/webhooks/provider/",
                    data=raw,
                    headers={
                        "Content-Type": "application/json",
                        signing.HEADER: signing.sign(raw, settings.PROVIDER_WEBHOOK_SECRET),
                    },
                    timeout=10,
                )
                assert response.status_code == 200 and response.json()["status"] == "duplicate"
            failed = withdrawals.create_withdrawal(wallet, 1000, "1234560000", f"worker:failed:{wallet.id}")

            def finalised(withdrawal):
                withdrawal.refresh_from_db()
                return withdrawal.status != "pending"

            wait_for(lambda: finalised(failed), "failed payout reversal")
            assert failed.status == "failed"
            wallet.refresh_from_db()
            assert wallet.balance == 13000
            succeeded = withdrawals.create_withdrawal(wallet, 500, "1234567890", f"worker:success:{wallet.id}")
            wait_for(lambda: finalised(succeeded), "successful payout settlement")
            assert succeeded.status == "posted"
            wallet.refresh_from_db()
            assert wallet.balance == 12500
            assert Transaction.objects.filter(reverses_id=failed.hold_transaction_id).count() == 1
            report = run_reconciliation()
            assert report.status == "clean", report.mismatches
            evidence = {
                "broker": "real Redis",
                "worker": "separate Celery solo process",
                "eager": False,
                "initial_webhook_http": responses[:2],
                "webhook_attempts": event.attempts,
                "duplicate_event_deliveries": 2,
                "failed_withdrawal": failed.status,
                "successful_withdrawal": succeeded.status,
                "final_balance_minor": wallet.balance,
                "reconciliation": report.status,
            }
            (local / "worker-verification.json").write_text(json.dumps(evidence, indent=2))
            print(json.dumps(evidence, indent=2))
        finally:
            gateway.shutdown()
            for process in reversed(processes):
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


if __name__ == "__main__":
    main()
