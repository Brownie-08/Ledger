import pytest
from django.contrib.auth import get_user_model

from ledger import services
from ledger.models import Account


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
