from django.contrib.auth import get_user_model
from rest_framework import serializers

from .models import Account, Currency, Entry


class WalletSerializer(serializers.ModelSerializer):
    class Meta:
        model = Account
        fields = ["id", "owner", "currency", "type", "balance", "version", "created_at"]
        read_only_fields = ["type", "balance", "version", "created_at"]


class WalletCreateSerializer(serializers.Serializer):
    owner = serializers.PrimaryKeyRelatedField(queryset=get_user_model().objects.all(), required=False)
    username = serializers.CharField(required=False)
    currency = serializers.ChoiceField(choices=Currency.choices)

    def validate(self, attrs):
        if not attrs.get("owner") and not attrs.get("username"):
            raise serializers.ValidationError("Provide owner (user id) or username")
        return attrs


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
