import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from django.db import connection
from rest_framework.test import APIClient

from ledger import services
from ledger.models import AuditLog, IdempotencyKey, Transaction


@pytest.mark.django_db
def test_explorer_is_participant_scoped_and_redacts_counterparty(make_wallet):
    a = make_wallet("alice-private", balance=10000)
    b = make_wallet("bob-private")
    stranger = make_wallet("stranger")
    posting = services.transfer(a, b, 1000, "private-transfer")
    client = APIClient()
    assert client.get(f"/api/transactions/{posting.id}/").status_code == 401
    client.force_authenticate(stranger.owner)
    assert client.get(f"/api/transactions/{posting.id}/").status_code == 404
    assert client.get("/api/transactions/").data["results"] == []
    for participant in [a.owner, b.owner]:
        client.force_authenticate(participant)
        response = client.get(f"/api/transactions/{posting.id}/")
        assert response.status_code == 200
        assert response.data["totals"] == {"NGN": 0}
        assert len(response.data["entries"]) == 2
        assert "alice-private" not in str(response.data) and "bob-private" not in str(response.data)
        assert "idempotency_key" not in response.data and "metadata" not in response.data


@pytest.mark.django_db
def test_explorer_cursor_pagination_does_not_duplicate_transaction(make_wallet):
    a = make_wallet("a", balance=1000)
    b = make_wallet("b")
    for i in range(3):
        services.transfer(a, b, 1, f"p-{i}")
    client = APIClient()
    client.force_authenticate(a.owner)
    response = client.get("/api/transactions/?page_size=2")
    assert len(response.data["results"]) == 2
    assert response.data["next"]
    assert len({p["transaction_id"] for p in response.data["results"]}) == 2


@pytest.mark.django_db
@pytest.mark.parametrize("key", ["a" * 129, "contains space", "é"])
def test_invalid_idempotency_key_is_rejected_before_posting(make_wallet, key):
    wallet = make_wallet("a", balance=100)
    client = APIClient()
    client.force_authenticate(wallet.owner)
    before = Transaction.objects.count()
    response = client.post(
        "/api/transfers/",
        {"source_wallet": wallet.id, "destination_wallet": wallet.id, "amount": 1},
        format="json",
        HTTP_IDEMPOTENCY_KEY=key,
    )
    assert response.status_code == 400
    assert response.data["error"] == "invalid_idempotency_key"
    assert Transaction.objects.count() == before


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("different_body", [False, True])
def test_concurrent_http_retries_have_one_posting_and_one_audit(make_wallet, different_body):
    a = make_wallet("race-a", balance=10000)
    b = make_wallet("race-b")
    barrier = threading.Barrier(2)
    user = a.owner

    def send(index):
        try:
            client = APIClient()
            client.force_authenticate(user)
            barrier.wait(timeout=10)
            response = client.post(
                "/api/transfers/",
                {"source_wallet": a.id, "destination_wallet": b.id, "amount": 100 + (index if different_body else 0)},
                format="json",
                HTTP_IDEMPOTENCY_KEY="concurrent-key",
            )
            return response.status_code, response.data
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(send, range(2)))
    assert sorted(status for status, _ in responses) == ([201, 422] if different_body else [201, 201])
    assert Transaction.objects.filter(type="transfer").count() == 1
    assert AuditLog.objects.filter(action="transfer").count() == 1
    assert IdempotencyKey.objects.count() == 1
    if not different_body:
        assert responses[0][1] == responses[1][1]


@pytest.mark.django_db
@pytest.mark.parametrize("amount", [1.0, True, "100", 9_007_199_254_740_992])
def test_api_rejects_coerced_or_unsafe_money(make_wallet, amount):
    a, b = make_wallet("strict-a", balance=1000), make_wallet("strict-b")
    client = APIClient()
    client.force_authenticate(a.owner)
    before = Transaction.objects.count()
    response = client.post(
        "/api/transfers/",
        {"source_wallet": a.id, "destination_wallet": b.id, "amount": amount},
        format="json",
        HTTP_IDEMPOTENCY_KEY="strict-money",
    )
    assert response.status_code == 400
    assert Transaction.objects.count() == before


@pytest.mark.django_db
def test_seed_demo_is_opt_in_and_replay_does_not_reset_spent_balance(monkeypatch):
    from django.core.management import call_command
    from django.core.management.base import CommandError

    from ledger.models import Account

    monkeypatch.delenv("DEMO_SEED_ENABLED", raising=False)
    with pytest.raises(CommandError):
        call_command("seed_demo")
    monkeypatch.setenv("DEMO_SEED_ENABLED", "1")
    monkeypatch.setenv("DEMO_PASSWORD", "synthetic-test-password-123")
    call_command("seed_demo")
    a = Account.objects.get(owner__username="demo_alex", currency="NGN")
    b = Account.objects.get(owner__username="demo_ada", currency="NGN")
    services.transfer(a, b, 100, "spent-after-seed")
    a.refresh_from_db()
    expected = a.balance
    before = Transaction.objects.count()
    call_command("seed_demo")
    a.refresh_from_db()
    assert a.balance == expected
    assert Transaction.objects.count() == before
