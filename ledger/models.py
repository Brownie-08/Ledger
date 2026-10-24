from django.conf import settings
from django.db import models


class Currency(models.TextChoices):
    NGN = "NGN"
    USD = "USD"
    GBP = "GBP"


class AppendOnlyError(Exception):
    pass


class Account(models.Model):
    class Type(models.TextChoices):
        USER_WALLET = "user_wallet"
        SYSTEM_CASH = "system_cash"
        FEES = "fees"
        SUSPENSE = "suspense"

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="accounts"
    )
    currency = models.CharField(max_length=3, choices=Currency.choices)
    type = models.CharField(max_length=20, choices=Type.choices, default=Type.USER_WALLET)
    balance = models.BigIntegerField(default=0)  # cached, minor units
    version = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["owner", "currency"],
                condition=models.Q(type="user_wallet"),
                name="one_wallet_per_owner_currency",
            ),
            models.UniqueConstraint(
                fields=["type", "currency"],
                condition=models.Q(owner__isnull=True),
                name="one_system_account_per_type_currency",
            ),
        ]


class Transaction(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending"
        POSTED = "posted"
        REVERSED = "reversed"

    type = models.CharField(max_length=30)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    idempotency_key = models.CharField(max_length=255, unique=True)
    reference = models.CharField(max_length=255, blank=True, default="")
    metadata = models.JSONField(default=dict, blank=True)
    reverses = models.OneToOneField("self", null=True, blank=True, on_delete=models.PROTECT, related_name="reversed_by")
    created_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        if self.pk and not kwargs.pop("_allow_status_change", False):
            old = Transaction.objects.filter(pk=self.pk).values_list("status", flat=True).first()
            if old in (self.Status.POSTED, self.Status.REVERSED):
                raise AppendOnlyError("Posted transactions are immutable")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise AppendOnlyError("Transactions cannot be deleted")


class Entry(models.Model):
    transaction = models.ForeignKey(Transaction, on_delete=models.PROTECT, related_name="entries")
    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="entries")
    amount = models.BigIntegerField()  # signed minor units; + credit to account, - debit
    currency = models.CharField(max_length=3, choices=Currency.choices)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["account", "-id"])]

    def save(self, *args, **kwargs):
        if self.pk:
            raise AppendOnlyError("Entries are append-only")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise AppendOnlyError("Entries are append-only")


class IdempotencyKey(models.Model):
    key = models.CharField(max_length=255)
    scope = models.CharField(max_length=100)
    request_hash = models.CharField(max_length=64)
    response_body = models.JSONField()
    status_code = models.PositiveSmallIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["key", "scope"], name="uniq_idem_key_scope")]


class OutboxEvent(models.Model):
    event_type = models.CharField(max_length=100)
    payload = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)
    published_at = models.DateTimeField(null=True, blank=True)


class WebhookEvent(models.Model):
    """Every provider webhook we accepted; unique provider_event_id gives dedupe."""

    provider_event_id = models.CharField(max_length=100, unique=True)
    event_type = models.CharField(max_length=100)
    payload = models.JSONField()
    received_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True, blank=True)


class Withdrawal(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending"
        POSTED = "posted"
        FAILED = "failed"

    wallet = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="withdrawals")
    amount = models.BigIntegerField()
    currency = models.CharField(max_length=3, choices=Currency.choices)
    bank_account = models.CharField(max_length=34)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    hold_transaction = models.OneToOneField(Transaction, on_delete=models.PROTECT, related_name="+")
    final_transaction = models.OneToOneField(
        Transaction, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    provider_ref = models.CharField(max_length=100, blank=True, default="")
    failure_reason = models.CharField(max_length=255, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
