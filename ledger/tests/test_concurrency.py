import random
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from django.contrib.auth import get_user_model
from django.db import connection

from ledger import services
from ledger.models import Account, Transaction


@pytest.mark.django_db(transaction=True)
def test_100_parallel_transfers_conserve_money():
    """100 transfers fired from 40 concurrent threads between 5 wallets.

    Both directions are exercised to provoke deadlocks; 40 threads keeps us under
    Postgres' default max_connections=100 (e.g. in CI).
    """
    User = get_user_model()
    wallets = []
    for i in range(5):
        w = Account.objects.create(owner=User.objects.create(username=f"u{i}"), currency="NGN")
        services.deposit(w, 1_000, f"seed{i}")
        wallets.append(w.id)
    start_total = sum(Account.objects.filter(id__in=wallets).values_list("balance", flat=True))

    barrier = threading.Barrier(40)
    errors, results = [], {"ok": 0, "insufficient": 0}
    lock = threading.Lock()

    def worker(n):
        rng = random.Random(n)
        src, dst = rng.sample(wallets, 2)
        try:
            if n < 40:
                barrier.wait()  # release the first wave simultaneously
            services.transfer(Account.objects.get(id=src), Account.objects.get(id=dst), rng.randint(1, 400), f"c{n}")
            with lock:
                results["ok"] += 1
        except services.InsufficientFunds:
            with lock:
                results["insufficient"] += 1
        except Exception as exc:  # deadlocks etc. would land here
            with lock:
                errors.append(repr(exc))
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=40) as pool:
        list(pool.map(worker, range(100)))

    assert errors == []
    assert results["ok"] + results["insufficient"] == 100
    balances = dict(Account.objects.filter(id__in=wallets).values_list("id", "balance"))
    assert sum(balances.values()) == start_total
    assert all(b >= 0 for b in balances.values())
    # cached balances must match the ledger
    for acc in Account.objects.all():
        assert acc.balance == sum(acc.entries.values_list("amount", flat=True))
    assert sum(Account.objects.values_list("balance", flat=True)) == 0
    assert Transaction.objects.filter(type="transfer").count() == results["ok"]
