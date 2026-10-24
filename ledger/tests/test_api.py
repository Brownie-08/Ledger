import pytest
from rest_framework.test import APIClient

pytestmark = pytest.mark.django_db


@pytest.fixture
def api():
    return APIClient()


def post(api, url, data, key):
    return api.post(url, data, format="json", HTTP_IDEMPOTENCY_KEY=key)


def make(api, username, currency="NGN"):
    r = post(api, "/api/wallets/", {"username": username, "currency": currency}, f"w-{username}-{currency}")
    assert r.status_code == 201, r.data
    return r.data["id"]


def test_wallet_create_and_get(api):
    wid = make(api, "ada")
    r = api.get(f"/api/wallets/{wid}/")
    assert r.status_code == 200 and r.data["balance"] == 0 and r.data["currency"] == "NGN"


def test_duplicate_wallet_conflict(api):
    make(api, "ada")
    r = post(api, "/api/wallets/", {"username": "ada", "currency": "NGN"}, "other")
    assert r.status_code == 409


def test_post_requires_idempotency_key(api):
    r = api.post("/api/wallets/", {"username": "x", "currency": "NGN"}, format="json")
    assert r.status_code == 400


def test_deposit_transfer_flow(api):
    a, b = make(api, "a"), make(api, "b")
    assert post(api, f"/api/wallets/{a}/deposit/", {"amount": 10_000}, "d1").status_code == 201
    r = post(api, "/api/transfers/", {"source_wallet": a, "destination_wallet": b, "amount": 2_500}, "t1")
    assert r.status_code == 201
    assert api.get(f"/api/wallets/{a}/").data["balance"] == 7_500
    assert api.get(f"/api/wallets/{b}/").data["balance"] == 2_500


def test_idempotent_replay(api):
    a = make(api, "a")
    r1 = post(api, f"/api/wallets/{a}/deposit/", {"amount": 500}, "d1")
    r2 = post(api, f"/api/wallets/{a}/deposit/", {"amount": 500}, "d1")
    assert r1.data == r2.data and r2["Idempotent-Replayed"] == "true"
    assert api.get(f"/api/wallets/{a}/").data["balance"] == 500


def test_same_key_different_body_422(api):
    a = make(api, "a")
    post(api, f"/api/wallets/{a}/deposit/", {"amount": 500}, "d1")
    r = post(api, f"/api/wallets/{a}/deposit/", {"amount": 900}, "d1")
    assert r.status_code == 422
    assert api.get(f"/api/wallets/{a}/").data["balance"] == 500


def test_overdraft_rejected(api):
    a, b = make(api, "a"), make(api, "b")
    r = post(api, "/api/transfers/", {"source_wallet": a, "destination_wallet": b, "amount": 1}, "t")
    assert r.status_code == 422 and r.data["error"] == "insufficient_funds"


def test_cross_currency_transfer_rejected(api):
    a, b = make(api, "a", "NGN"), make(api, "b", "USD")
    post(api, f"/api/wallets/{a}/deposit/", {"amount": 100}, "d")
    r = post(api, "/api/transfers/", {"source_wallet": a, "destination_wallet": b, "amount": 50}, "t")
    assert r.status_code == 422


def test_negative_amount_rejected(api):
    a = make(api, "a")
    assert post(api, f"/api/wallets/{a}/deposit/", {"amount": -5}, "d").status_code == 400


def test_history_cursor_pagination(api):
    a = make(api, "a")
    for i in range(5):
        post(api, f"/api/wallets/{a}/deposit/", {"amount": 100 + i}, f"d{i}")
    r = api.get(f"/api/wallets/{a}/transactions/?page_size=2")
    assert len(r.data["results"]) == 2 and r.data["results"][0]["amount"] == 104
    seen = [e["amount"] for e in r.data["results"]]
    while r.data["next"]:
        r = api.get(r.data["next"])
        seen += [e["amount"] for e in r.data["results"]]
    assert seen == [104, 103, 102, 101, 100]
