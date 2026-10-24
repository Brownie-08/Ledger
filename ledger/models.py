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
        FX = "fx"

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="accounts"
    )
    currency = models.CharField(max_length=3, choices=Currency.choices)
    type = models.CharField(max_length=20, choices=Type.choices, default=Type.USER_WALLET)
    balance = models.BigIntegerField(default=0)  # cached, minor units
    held_amount = models.BigIntegerField(default=0)  # sum of active holds (authorisations)
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

    @property
    def available_balance(self):
        return self.balance - self.held_amount


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


class Hold(models.Model):
    """Card-style authorisation: reserves funds (reduces available balance) without moving them."""

    class Status(models.TextChoices):
        ACTIVE = "active"
        CAPTURED = "captured"
        RELEASED = "released"
        EXPIRED = "expired"

    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="holds")
    merchant = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="incoming_holds")
    amount = models.BigIntegerField()
    captured_amount = models.BigIntegerField(default=0)
    currency = models.CharField(max_length=3, choices=Currency.choices)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE)
    idempotency_key = models.CharField(max_length=255, unique=True)
    reference = models.CharField(max_length=255, blank=True, default="")
    expires_at = models.DateTimeField()
    capture_transaction = models.OneToOneField(
        Transaction, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [models.Index(fields=["status", "expires_at"])]


class FxRate(models.Model):
    """Fixed FX table: 1 unit of ``base`` = ``rate`` units of ``quote``."""

    base = models.CharField(max_length=3, choices=Currency.choices)
    quote = models.CharField(max_length=3, choices=Currency.choices)
    rate = models.DecimalField(max_digits=20, decimal_places=10)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["base", "quote"], name="uniq_fx_pair")]


class Refund(models.Model):
    original = models.ForeignKey(Transaction, on_delete=models.PROTECT, related_name="refunds")
    transaction = models.OneToOneField(Transaction, on_delete=models.PROTECT, related_name="refund_record")
    amount = models.BigIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)


class AuditLog(models.Model):
    """Append-only trail of every money-moving action (who, what, when, from which request)."""

    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT)
    action = models.CharField(max_length=100)
    transaction = models.ForeignKey(Transaction, null=True, blank=True, on_delete=models.PROTECT)
    object_type = models.CharField(max_length=50, blank=True, default="")
    object_id = models.CharField(max_length=50, blank=True, default="")
    data = models.JSONField(default=dict, blank=True)
    request_id = models.CharField(max_length=64, blank=True, default="")
    ip = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["action", "-created_at"])]

    def save(self, *args, **kwargs):
        if self.pk:
            raise AppendOnlyError("Audit log is append-only")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise AppendOnlyError("Audit log is append-only")


class ReconciliationReport(models.Model):
    class Status(models.TextChoices):
        CLEAN = "clean"
        MISMATCHES = "mismatches"

    status = models.CharField(max_length=12, choices=Status.choices)
    started_at = models.DateTimeField()
    finished_at = models.DateTimeField()
    summary = models.JSONField(default=dict)
    mismatches = models.JSONField(default=list)
