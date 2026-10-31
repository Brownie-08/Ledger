from datetime import timedelta

import pytest
from django.utils import timezone

from ledger import withdrawals
from ledger.dispatch import dispatch_due
from ledger.models import DispatchJob

pytestmark = pytest.mark.django_db


def test_broker_outage_retains_intent_and_recovery_dispatches_once(
    make_wallet, monkeypatch, django_capture_on_commit_callbacks
):
    from ledger.tasks import submit_payout

    wallet = make_wallet("dispatch", balance=10000)

    def unavailable(*args):
        raise ConnectionError("broker down")

    monkeypatch.setattr(submit_payout, "delay", unavailable)
    with django_capture_on_commit_callbacks(execute=True):
        withdrawal = withdrawals.create_withdrawal(wallet, 1000, "1234567890", "dispatch-outage")
    wallet.refresh_from_db()
    assert wallet.balance == 9000
    job = DispatchJob.objects.get(kind="payout", object_id=withdrawal.id)
    assert job.published_at is None and job.last_error == "ConnectionError"
    sent = []
    monkeypatch.setattr(submit_payout, "delay", lambda pk: sent.append(pk))
    assert dispatch_due() == 1
    assert dispatch_due() == 0
    assert sent == [withdrawal.id]
    job.refresh_from_db()
    assert job.attempts == 2 and job.last_error == ""


def test_stale_unfinished_dispatch_can_be_redelivered(make_wallet, monkeypatch, django_capture_on_commit_callbacks):
    from ledger.tasks import submit_payout

    sent = []
    monkeypatch.setattr(submit_payout, "delay", lambda pk: sent.append(pk))
    wallet = make_wallet("stale", balance=10000)
    with django_capture_on_commit_callbacks(execute=True):
        withdrawal = withdrawals.create_withdrawal(wallet, 1000, "1234567890", "stale")
    DispatchJob.objects.update(published_at=timezone.now() - timedelta(minutes=6))
    assert dispatch_due() == 1
    assert sent == [withdrawal.id, withdrawal.id]
    withdrawal.status = "failed"
    withdrawal.save(update_fields=["status"])
    DispatchJob.objects.update(published_at=timezone.now() - timedelta(minutes=6))
    assert dispatch_due() == 0
