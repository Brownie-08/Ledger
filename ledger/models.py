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


class KYCProfile(models.Model):
    """Risk-based onboarding: signup is instant at tier 0; an admin review lifts the user to tier 1."""

    class Status(models.TextChoices):
        NONE = "none"
        PENDING = "pending"
        APPROVED = "approved"
        REJECTED = "rejected"

    class IdType(models.TextChoices):
        BVN = "BVN"
        NIN = "NIN"
        PASSPORT = "PASSPORT"

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="kyc")
    tier = models.PositiveSmallIntegerField(default=0)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.NONE)
    full_name = models.CharField(max_length=200, blank=True, default="")
    date_of_birth = models.DateField(null=True, blank=True)
    country = models.CharField(max_length=2, blank=True, default="")
    id_type = models.CharField(max_length=10, choices=IdType.choices, blank=True, default="")
    id_number = models.CharField(max_length=20, blank=True, default="")  # mock data only
    submitted_at = models.DateTimeField(null=True, blank=True)
    reviewer = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="kyc_reviews"
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.CharField(max_length=255, blank=True, default="")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [models.Index(fields=["status", "submitted_at"])]


class VirtualAccount(models.Model):
    """MOCK bank details that route inbound bank transfers to one wallet. Not real accounts."""

    class Scheme(models.TextChoices):
        NUBAN = "nuban"  # NGN: 10-digit NUBAN
        SORT_CODE = "sort_code"  # GBP: 6-digit sort code + 8-digit account
        ABA = "aba"  # USD: 9-digit routing number + account number

    wallet = models.OneToOneField(Account, on_delete=models.PROTECT, related_name="virtual_account")
    scheme = models.CharField(max_length=10, choices=Scheme.choices)
    currency = models.CharField(max_length=3, choices=Currency.choices)
    bank_name = models.CharField(max_length=100)
    account_number = models.CharField(max_length=17, unique=True)
    sort_code = models.CharField(max_length=6, blank=True, default="")
    routing_number = models.CharField(max_length=9, blank=True, default="")
    account_name = models.CharField(max_length=200, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)


class SuspenseResolution(models.Model):
    """Ops decision for one parked inbound (``inbound_unallocated``) transaction. One per parked item."""

    class Action(models.TextChoices):
        ALLOCATE = "allocate"
        RETURN = "return"

    parked = models.OneToOneField(Transaction, on_delete=models.PROTECT, related_name="suspense_resolution")
    action = models.CharField(max_length=10, choices=Action.choices)
    wallet = models.ForeignKey(Account, null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    resolution_txn = models.OneToOneField(Transaction, on_delete=models.PROTECT, related_name="+")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT)
    reason = models.CharField(max_length=255, blank=True)
    override_limits = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)


class DispatchJob(models.Model):
    """Durable intent for an at-least-once Celery submission, committed with its business row."""

    kind = models.CharField(max_length=20, choices=[("payout", "Payout"), ("webhook", "Webhook")])
    object_id = models.PositiveBigIntegerField()
    attempts = models.PositiveIntegerField(default=0)
    published_at = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=100, blank=True, default="")
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["kind", "object_id"], name="one_dispatch_per_object")]
