from django.contrib.auth import get_user_model
from rest_framework import serializers

from .models import Account, Currency, Entry, FxRate, Hold, Withdrawal


class WalletSerializer(serializers.ModelSerializer):
    available_balance = serializers.IntegerField(read_only=True)

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
            "created_at",
        ]
        read_only_fields = fields


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
    amount = serializers.IntegerField(min_value=1)
    reference = serializers.CharField(required=False, default="", allow_blank=True)


class TransferSerializer(serializers.Serializer):
    source_wallet = serializers.IntegerField()
    destination_wallet = serializers.IntegerField()
    amount = serializers.IntegerField(min_value=1)
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
    amount = serializers.IntegerField(min_value=1)
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
    amount = serializers.IntegerField(min_value=1)
    expires_in_seconds = serializers.IntegerField(min_value=1, max_value=30 * 86400, default=7 * 86400)
    reference = serializers.CharField(required=False, default="", allow_blank=True)


class CaptureSerializer(serializers.Serializer):
    amount = serializers.IntegerField(min_value=1, required=False)


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
    amount = serializers.IntegerField(min_value=1)
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
