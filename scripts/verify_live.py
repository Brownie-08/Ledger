"""HTTP acceptance for Django + Next.js BFF. Supply a synthetic demo database only.

The caller starts both servers. Credentials are read from private environment
variables and are never printed or included in the evidence file.
"""

import json
import os
import uuid
from pathlib import Path

import requests


def main():
    base = os.environ.get("FRONTEND_URL", "http://127.0.0.1:3000")
    session = requests.Session()
    session.headers["Origin"] = base
    result = session.post(
        base + "/api/session", json={"username": "demo_alex", "password": os.environ["DEMO_PASSWORD"]}, timeout=30
    )
    assert result.status_code == 200, (result.status_code, result.text)
    assert result.json() == {"authenticated": True}
    cookies = result.headers["Set-Cookie"]
    assert "HttpOnly" in cookies and "SameSite=strict" in cookies
    assert "access" not in result.json() and "refresh" not in result.json()
    wallets = session.get(base + "/api/backend/wallets/", timeout=30).json()
    source = next(w for w in wallets if w["currency"] == "NGN")
    usd = next(w for w in wallets if w["currency"] == "USD")
    body = {
        "source_wallet": source["id"],
        "destination_wallet": usd["id"],
        "amount": 150000,
        "reference": "HTTP acceptance FX",
    }
    key = uuid.uuid4().hex
    response = session.post(base + "/api/backend/transfers/", json=body, headers={"Idempotency-Key": key}, timeout=30)
    assert response.status_code == 201, response.text
    replay = session.post(base + "/api/backend/transfers/", json=body, headers={"Idempotency-Key": key}, timeout=30)
    assert replay.status_code == 201 and replay.json() == response.json()
    assert replay.headers["Idempotent-Replayed"] == "true"
    detail = session.get(base + f"/api/backend/transactions/{response.json()['transaction_id']}/", timeout=30)
    assert detail.status_code == 200
    assert detail.json()["totals"] == {"NGN": 0, "USD": 0}
    conflict = session.post(
        base + "/api/backend/transfers/", json={**body, "amount": 100}, headers={"Idempotency-Key": key}, timeout=30
    )
    assert conflict.status_code == 422
    csrf = session.post(
        base + "/api/backend/transfers/",
        json=body,
        headers={"Origin": "https://untrusted.example", "Idempotency-Key": uuid.uuid4().hex},
        timeout=30,
    )
    assert csrf.status_code == 403
    forbidden = session.get(base + "/api/backend/admin/audit-logs/", timeout=30)
    assert forbidden.status_code == 404
    for cookie in session.cookies:
        if cookie.name == "ledger_access":
            cookie.value = "expired-or-invalid"
    refreshed = session.get(base + "/api/backend/wallets/", timeout=30)
    assert refreshed.status_code == 200 and "ledger_access=" in refreshed.headers.get("Set-Cookie", "")
    assert session.delete(base + "/api/session", timeout=30).status_code == 200
    assert session.get(base + "/api/backend/wallets/", timeout=30).status_code == 401
    anonymous = requests.get(base + "/", timeout=30)
    assert anonymous.status_code == 200
    assert anonymous.headers["X-Frame-Options"] == "DENY"
    evidence = {
        "login": "HTTP-only cookies, no tokens in JSON",
        "database_fx_posting": response.json()["transaction_id"],
        "currency_totals": detail.json()["totals"],
        "replay": 201,
        "changed_body": conflict.status_code,
        "cross_origin_post": csrf.status_code,
        "forbidden_proxy_route": forbidden.status_code,
        "refresh": refreshed.status_code,
        "logout_then_wallets": 401,
        "security_headers": "present",
    }
    target = Path(__file__).resolve().parent.parent / ".local" / "live-verification.json"
    target.parent.mkdir(exist_ok=True)
    target.write_text(json.dumps(evidence, indent=2))
    print(json.dumps(evidence, indent=2))


if __name__ == "__main__":
    main()
