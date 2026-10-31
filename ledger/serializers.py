import datetime

from django.contrib.auth import get_user_model
from rest_framework import serializers

from .models import Account, Currency, Entry, FxRate, Hold, KYCProfile, VirtualAccount, Withdrawal


class MinorAmountField(serializers.IntegerField):
    """JSON money must be an integer, never a coerced float, boolean or decimal string."""

    def __init__(self, **kwargs):
        super().__init__(min_value=1, max_value=9_007_199_254_740_991, **kwargs)

    def to_internal_value(self, data):
        if not isinstance(data, int) or isinstance(data, bool):
            raise serializers.ValidationError("Amounts must be JSON integers in minor units")
        return super().to_internal_value(data)


class VirtualAccountSerializer(serializers.ModelSerializer):
    mock = serializers.SerializerMethodField()

    class Meta:
        model = VirtualAccount
        fields = [
            "scheme",
            "currency",
            "bank_name",
            "account_number",
            "sort_code",
            "routing_number",
            "account_name",
            "mock",
        ]
        read_only_fields = fields

    def get_mock(self, obj) -> bool:
        return True


class WalletSerializer(serializers.ModelSerializer):
    available_balance = serializers.IntegerField(read_only=True)
    virtual_account = serializers.SerializerMethodField()

    def get_virtual_account(self, obj) -> VirtualAccountSerializer(allow_null=True):
        va = VirtualAccount.objects.filter(wallet=obj).first()
        return VirtualAccountSerializer(va).data if va else None

    class Meta:
        model = Account
        fields = [
            "id",
            "owner",
            "currency",
            "type",
            "balance",
            "held_amount",
            "available_balance",
            "version",
            "virtual_account",
            "created_at",
        ]
        read_only_fields = fields


class KYCSubmitSerializer(serializers.Serializer):
    full_name = serializers.CharField(max_length=200, min_length=3)
    date_of_birth = serializers.DateField()
    country = serializers.RegexField(r"^[A-Z]{2}$", help_text="ISO 3166-1 alpha-2, e.g. NG, GB, US")
    id_type = serializers.ChoiceField(choices=KYCProfile.IdType.choices)
    id_number = serializers.CharField(
        max_length=20, help_text="MOCK data only. BVN/NIN: 11 digits; passport: 6-9 chars"
    )

    def validate_date_of_birth(self, v):
        today = datetime.date.today()
        age = today.year - v.year - ((today.month, today.day) < (v.month, v.day))
        if age < 18:
            raise serializers.ValidationError("must be 18 or older")
        if age > 120:
            raise serializers.ValidationError("invalid date of birth")
        return v

    def validate(self, d):
        if d["id_type"] in ("BVN", "NIN"):
            if d["country"] != "NG":
                raise serializers.ValidationError({"id_type": "BVN/NIN are only valid for country NG"})
            if not (len(d["id_number"]) == 11 and d["id_number"].isdigit()):
                raise serializers.ValidationError({"id_number": f"{d['id_type']} must be 11 digits"})
        elif not (6 <= len(d["id_number"]) <= 9 and d["id_number"].isalnum()):
            raise serializers.ValidationError({"id_number": "passport number must be 6-9 letters/digits"})
        return d


class KYCProfileSerializer(serializers.ModelSerializer):
    username = serializers.CharField(source="user.username", read_only=True)
    id_number = serializers.SerializerMethodField()
    reviewer = serializers.CharField(source="reviewer.username", default=None, read_only=True)
    limits = serializers.SerializerMethodField()

    class Meta:
        model = KYCProfile
        fields = [
            "id",
            "user",
            "username",
            "tier",
            "status",
            "full_name",
            "date_of_birth",
            "country",
            "id_type",
            "id_number",
            "submitted_at",
            "reviewer",
            "reviewed_at",
            "rejection_reason",
            "limits",
        ]
        read_only_fields = fields

    def get_id_number(self, obj) -> str:
        return ("*" * max(len(obj.id_number) - 4, 0) + obj.id_number[-4:]) if obj.id_number else ""

    def get_limits(self, obj) -> dict:
        from django.conf import settings

        return settings.KYC_TIERS[obj.tier]


class KYCRejectSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=255, min_length=3)


class WalletCreateSerializer(serializers.Serializer):
    owner = serializers.PrimaryKeyRelatedField(queryset=get_user_model().objects.all(), required=False)
    username = serializers.CharField(required=False)  # staff only: create for another user
    currency = serializers.ChoiceField(choices=Currency.choices)


class RegisterSerializer(serializers.Serializer):
    username = serializers.CharField(max_length=150)
    password = serializers.CharField(min_length=8, write_only=True)

    def validate_username(self, v):
        if get_user_model().objects.filter(username=v).exists():
            raise serializers.ValidationError("username taken")
        return v


class DepositSerializer(serializers.Serializer):
    amount = MinorAmountField()
    reference = serializers.CharField(required=False, default="", allow_blank=True)


class TransferSerializer(serializers.Serializer):
    source_wallet = serializers.IntegerField()
    destination_wallet = serializers.IntegerField()
    amount = MinorAmountField()
    reference = serializers.CharField(required=False, default="", allow_blank=True)


class EntrySerializer(serializers.ModelSerializer):
    transaction_type = serializers.CharField(source="transaction.type")
    transaction_status = serializers.CharField(source="transaction.status")
    reference = serializers.CharField(source="transaction.reference")

    class Meta:
        model = Entry
        fields = [
            "id",
            "transaction",
            "transaction_type",
            "transaction_status",
            "reference",
            "amount",
            "currency",
            "created_at",
        ]


class WithdrawalCreateSerializer(serializers.Serializer):
    amount = MinorAmountField()
    bank_account = serializers.RegexField(r"^[0-9]{6,34}$")


class WithdrawalSerializer(serializers.ModelSerializer):
    class Meta:
        model = Withdrawal
        fields = [
            "id",
            "wallet",
            "amount",
            "currency",
            "bank_account",
            "status",
            "provider_ref",
            "failure_reason",
            "hold_transaction",
            "final_transaction",
            "created_at",
            "updated_at",
        ]


class HoldCreateSerializer(serializers.Serializer):
    merchant_wallet = serializers.IntegerField()
    amount = MinorAmountField()
    expires_in_seconds = serializers.IntegerField(min_value=1, max_value=30 * 86400, default=7 * 86400)
    reference = serializers.CharField(required=False, default="", allow_blank=True)


class CaptureSerializer(serializers.Serializer):
    amount = MinorAmountField(required=False)


class HoldSerializer(serializers.ModelSerializer):
    class Meta:
        model = Hold
        fields = [
            "id",
            "account",
            "merchant",
            "amount",
            "captured_amount",
            "currency",
            "status",
            "reference",
            "expires_at",
            "capture_transaction",
            "created_at",
        ]


class RefundSerializer(serializers.Serializer):
    amount = MinorAmountField()
    reason = serializers.CharField(required=False, default="", allow_blank=True)


class ReverseSerializer(serializers.Serializer):
    reason = serializers.CharField(required=False, default="", allow_blank=True)


class FxRateSerializer(serializers.ModelSerializer):
    class Meta:
        model = FxRate
        fields = ["base", "quote", "rate", "updated_at"]


class AuditLogSerializer(serializers.ModelSerializer):
    class Meta:
        from .models import AuditLog

        model = AuditLog
        fields = [
            "id",
            "actor",
            "action",
            "transaction",
            "object_type",
            "object_id",
            "data",
            "request_id",
            "ip",
            "created_at",
        ]


class ReconciliationReportSerializer(serializers.ModelSerializer):
    class Meta:
        from .models import ReconciliationReport

        model = ReconciliationReport
        fields = ["id", "status", "started_at", "finished_at", "summary", "mismatches"]
