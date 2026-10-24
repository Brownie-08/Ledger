import pytest
from django.contrib.auth import get_user_model

from ledger import services
from ledger.models import Account


@pytest.fixture(autouse=True)
def _no_fees_by_default(settings):
    """Core tests assume fee-free transfers; fee tests opt in explicitly."""
    settings.TRANSFER_FEE_BPS = 0
    settings.TRANSFER_FEE_MIN = 0
    settings.TRANSFER_FEE_MAX = 0


@pytest.fixture(autouse=True)
def _kyc_off_by_default(settings):
    """Pre-KYC suites exercise ledger mechanics with large balances; KYC tests opt in via ``kyc_on``."""
    settings.KYC_ENFORCED = False


@pytest.fixture
def kyc_on(settings):
    settings.KYC_ENFORCED = True


@pytest.fixture(autouse=True)
def _relaxed_throttles(settings):
    """Throttle tests set their own limits; everyone else shouldn't trip them."""
    from django.core.cache import cache

    settings.LEDGER_THROTTLE_RATES = {"anon": "10000/min", "user": "10000/min", "money": "10000/min"}
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def make_wallet(db):
    def _make(username, currency="NGN", balance=0):
        user, _ = get_user_model().objects.get_or_create(username=username)
        w = Account.objects.create(owner=user, currency=currency)
        if balance:
            services.deposit(w, balance, f"seed:{username}:{currency}")
            w.refresh_from_db()
        return w

    return _make


@pytest.fixture
def webhook_transport(monkeypatch):
    """Route the provider's outgoing webhook HTTP calls into the Django test client."""
    from django.test import Client

    import provider.tasks

    client = Client()
    calls = []

    def fake_post(url, body, headers):
        extra = {f"HTTP_{k.upper().replace('-', '_')}": v for k, v in headers.items() if k != "Content-Type"}
        resp = client.post("/api/webhooks/provider/", data=body, content_type="application/json", **extra)
        calls.append((body, resp.status_code, resp.json()))
        return resp.status_code

    monkeypatch.setattr(provider.tasks, "http_post", fake_post)
    return calls


@pytest.fixture
def run_with_retries(monkeypatch):
    """Drive a Celery task like a worker would: on ``self.retry`` record the failure and
    run the next attempt (with request.retries incremented) instead of sleeping."""
    from celery.exceptions import Retry

    def _run(task, args, max_attempts=10):
        def fake_retry(exc=None, **kwargs):
            raise Retry(exc=exc)

        monkeypatch.setattr(task, "retry", fake_retry)
        for attempt in range(max_attempts):
            res = task.apply(args=args, retries=attempt, throw=False)
            if res.state == "SUCCESS":
                return res, attempt + 1
        raise AssertionError("task never succeeded")

    return _run
