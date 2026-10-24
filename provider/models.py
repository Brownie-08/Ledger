"""The mock provider's *own* records. In real life these live in the PSP's system;
reconciliation compares them against our ledger."""

from django.db import models


class ProviderTransaction(models.Model):
    class Kind(models.TextChoices):
        CHARGE = "charge"
        PAYOUT = "payout"

    class Status(models.TextChoices):
        PENDING = "pending"
        SUCCEEDED = "succeeded"
        FAILED = "failed"

    kind = models.CharField(max_length=10, choices=Kind.choices)
    provider_ref = models.CharField(max_length=100, unique=True)
    client_reference = models.CharField(max_length=100, unique=True)
    wallet_id = models.BigIntegerField(null=True, blank=True)
    withdrawal_id = models.BigIntegerField(null=True, blank=True)
    amount = models.BigIntegerField()
    currency = models.CharField(max_length=3)
    bank_account = models.CharField(max_length=34, blank=True, default="")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    failure_reason = models.CharField(max_length=255, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)


class ProviderEvent(models.Model):
    event_id = models.CharField(max_length=100, unique=True)
    type = models.CharField(max_length=50)
    payload = models.JSONField()
    attempts = models.PositiveIntegerField(default=0)
    delivered_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
