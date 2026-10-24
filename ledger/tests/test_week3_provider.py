import json
import time

import pytest
from django.conf import settings
from rest_framework.test import APIClient

from ledger import services, signing, withdrawals
from ledger.models import Account, Transaction, WebhookEvent, Withdrawal
from provider import api as provider_api
from provider.models import ProviderEvent, ProviderTransaction

URL = "/api/webhooks/provider/"


def signed_post(client, event, secret=None, ts=None):
    body = json.dumps(event).encode()
    sig = signing.sign(body, secret or settings.PROVIDER_WEBHOOK_SECRET, ts)
    return client.post(URL, data=body, content_type="application/json", HTTP_X_PROVIDER_SIGNATURE=sig)


def charge_event(wallet, amount=5000, eid="evt_1", ref="ch_1"):
    return {
        "id": eid,
        "type": "charge.succeeded",
        "data": {"provider_ref": ref, "wallet_id": wallet.id, "amount": amount, "currency": wallet.currency},
    }


# --- signatures ---------------------------------------------------------------


def test_sign_verify_roundtrip():
    body = b'{"a":1}'
    signing.verify(body, signing.sign(body, "s"), "s")


@pytest.mark.parametrize(
    "header,err",
    [(None, "missing"), ("garbage", "malformed"), ("t=1,v1=abc", "tolerance")],
)
def test_verify_rejects_bad_headers(header, err):
    with pytest.raises(signing.InvalidSignature, match=err):
        signing.verify(b"{}", header, "s")


def test_verify_rejects_tampered_body():
    sig = signing.sign(b'{"amount":1}', "s")
    with pytest.raises(signing.InvalidSignature, match="mismatch"):
        signing.verify(b'{"amount":999}', sig, "s")


def test_webhook_rejects_wrong_secret(make_wallet):
    w = make_wallet("ada")
    r = signed_post(APIClient(), charge_event(w), secret="wrong")
    assert r.status_code == 400 and r.json()["error"] == "invalid_signature"
    assert not WebhookEvent.objects.exists()


def test_webhook_rejects_stale_timestamp(make_wallet):
    w = make_wallet("ada")
    r = signed_post(APIClient(), charge_event(w), ts=int(time.time()) - 3600)
    assert r.status_code == 400


# --- dedupe / idempotent processing -----------------------------------------


def test_charge_webhook_credits_wallet_once(make_wallet):
    w = make_wallet("ada")
    c = APIClient()
    assert signed_post(c, charge_event(w)).json() == {"status": "processed"}
    assert signed_post(c, charge_event(w)).json() == {"status": "duplicate"}
    # Same charge re-sent under a *new* event id still cannot double credit.
    assert signed_post(c, charge_event(w, eid="evt_2")).status_code == 200
    w.refresh_from_db()
    assert w.balance == 5000
    assert Transaction.objects.filter(type="deposit").count() == 1


def test_failed_processing_is_rolled_back_and_retryable(make_wallet):
    w = make_wallet("ada", "USD")
    ev = charge_event(w)
    ev["data"]["currency"] = "NGN"  # mismatch -> handler raises
    with pytest.raises(services.CurrencyMismatch):
        signed_post(APIClient(raise_request_exception=True), ev)
    assert not WebhookEvent.objects.filter(provider_event_id="evt_1").exists()


def test_unknown_event_type(make_wallet):
    r = signed_post(APIClient(), {"id": "e", "type": "weird", "data": {}})
    assert r.status_code == 400


# --- provider end-to-end -------------------------------------------------------


def test_simulated_checkout_delivers_signed_webhook(make_wallet, webhook_transport, django_capture_on_commit_callbacks):
    w = make_wallet("ada")
    with django_capture_on_commit_callbacks(execute=True):
        r = APIClient().post(
            "/provider/charges/", {"wallet_id": w.id, "amount": 7500, "currency": "NGN"}, format="json"
        )
    assert r.status_code == 201
    w.refresh_from_db()
    assert w.balance == 7500
    assert webhook_transport[0][1] == 200
    assert ProviderEvent.objects.get().delivered_at is not None


def test_webhook_delivery_retries_until_success(make_wallet, monkeypatch, run_with_retries):
    from django.test import Client

    import provider.tasks

    w = make_wallet("ada")
    statuses = iter([503, 500])

    def flaky(url, body, headers):
        code = next(statuses, None)
        if code:
            return code
        sig = headers[signing.HEADER]
        return Client().post(URL, data=body, content_type="application/json", HTTP_X_PROVIDER_SIGNATURE=sig).status_code

    monkeypatch.setattr(provider.tasks, "http_post", flaky)
    monkeypatch.setattr(provider.tasks.deliver_webhook, "delay", lambda *a: None)
    provider_api.create_charge(wallet_id=w.id, amount=100, currency="NGN")
    ev = ProviderEvent.objects.get()
    _, attempts = run_with_retries(provider.tasks.deliver_webhook, [ev.pk])
    ev.refresh_from_db()
    assert attempts == 3 and ev.attempts == 3 and ev.delivered_at and ev.last_error == "HTTP 500"
    w.refresh_from_db()
    assert w.balance == 100
    assert provider.tasks.deliver_webhook.retry_backoff is True


# --- withdrawals -----------------------------------------------------------------


def _withdraw(w, amount, account, key, capture):
    with capture(execute=True):
        return withdrawals.create_withdrawal(w, amount, account, key)


def test_withdrawal_success(make_wallet, webhook_transport, django_capture_on_commit_callbacks):
    w = make_wallet("ada", balance=10_000)
    wd = _withdraw(w, 4_000, "0123456789", "wd1", django_capture_on_commit_callbacks)
    wd.refresh_from_db()
    w.refresh_from_db()
    assert wd.status == Withdrawal.Status.POSTED and wd.provider_ref.startswith("po_")
    assert w.balance == 6_000
    assert Account.objects.get(type="suspense", currency="NGN").balance == 0
    assert ProviderTransaction.objects.get(kind="payout").status == "succeeded"


def test_withdrawal_failure_is_reversed(make_wallet, webhook_transport, django_capture_on_commit_callbacks):
    w = make_wallet("ada", balance=10_000)
    wd = _withdraw(w, 4_000, "1234560000", "wd1", django_capture_on_commit_callbacks)
    wd.refresh_from_db()
    w.refresh_from_db()
    assert wd.status == Withdrawal.Status.FAILED and wd.failure_reason == "account_closed"
    assert w.balance == 10_000
    assert wd.hold_transaction.status == Transaction.Status.REVERSED
    assert Account.objects.get(type="suspense", currency="NGN").balance == 0


def test_withdrawal_pending_moves_funds_to_suspense(make_wallet, monkeypatch):
    import ledger.tasks

    monkeypatch.setattr(ledger.tasks.submit_payout, "delay", lambda *a: None)
    w = make_wallet("ada", balance=1_000)
    wd = withdrawals.create_withdrawal(w, 600, "0123456789", "wd1")
    w.refresh_from_db()
    assert wd.status == "pending" and w.balance == 400
    assert Account.objects.get(type="suspense", currency="NGN").balance == 600


def test_withdrawal_overdraft_rejected(make_wallet):
    from ledger.services import InsufficientFunds

    w = make_wallet("ada", balance=100)
    with pytest.raises(InsufficientFunds):
        withdrawals.create_withdrawal(w, 101, "0123456789", "wd1")
    assert not Withdrawal.objects.exists()


def test_payout_retries_with_backoff_on_provider_outage(
    make_wallet, webhook_transport, monkeypatch, run_with_retries, django_capture_on_commit_callbacks
):
    import ledger.tasks

    monkeypatch.setattr(ledger.tasks.submit_payout, "delay", lambda *a: None)
    w = make_wallet("ada", balance=1_000)
    wd = withdrawals.create_withdrawal(w, 500, "0123456789", "wd1")
    provider_api.simulate_outage(2)
    with django_capture_on_commit_callbacks(execute=True):
        _, attempts = run_with_retries(ledger.tasks.submit_payout, [wd.id])
    wd.refresh_from_db()
    assert attempts == 3 and wd.status == "posted"
    assert ProviderTransaction.objects.filter(kind="payout").count() == 1
    assert ledger.tasks.submit_payout.retry_backoff is True and ledger.tasks.submit_payout.max_retries == 6


def test_settle_is_idempotent(make_wallet, monkeypatch):
    import ledger.tasks

    monkeypatch.setattr(ledger.tasks.submit_payout, "delay", lambda *a: None)
    w = make_wallet("ada", balance=1_000)
    wd = withdrawals.create_withdrawal(w, 600, "0123456789", "wd1")
    withdrawals.settle_withdrawal(wd.id, "po_x")
    withdrawals.settle_withdrawal(wd.id, "po_x")
    withdrawals.fail_withdrawal(wd.id, "po_x")  # late contradictory event ignored
    assert Transaction.objects.filter(type="withdrawal_settle").count() == 1
    w.refresh_from_db()
    assert w.balance == 400
