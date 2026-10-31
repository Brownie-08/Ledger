from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from ledger.models import Currency
from ledger.serializers import MinorAmountField

from . import api


class ChargeSerializer(serializers.Serializer):
    wallet_id = serializers.IntegerField()
    amount = MinorAmountField()
    currency = serializers.ChoiceField(choices=Currency.choices)
    client_reference = serializers.CharField(required=False)


class SimulatedCheckout(APIView):
    """Pretend a customer paid by card at the provider's hosted checkout.

    This stands in for an external service, so it doesn't use our JWT auth (anon throttle still applies).
    """

    authentication_classes = []
    permission_classes = []

    @extend_schema(
        request=ChargeSerializer,
        responses=inline_serializer(
            "ProviderCharge",
            {
                "provider_ref": serializers.CharField(),
                "status": serializers.CharField(),
                "amount": serializers.IntegerField(),
                "currency": serializers.CharField(),
            },
        ),
    )
    def post(self, request):
        s = ChargeSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        txn = api.create_charge(**s.validated_data)
        return Response(
            {"provider_ref": txn.provider_ref, "status": txn.status, "amount": txn.amount, "currency": txn.currency},
            status=201,
        )


class BankTransferSerializer(serializers.Serializer):
    account_number = serializers.CharField(max_length=17)
    amount = MinorAmountField()
    currency = serializers.ChoiceField(choices=Currency.choices)
    sender_name = serializers.CharField(required=False, default="", allow_blank=True)
    client_reference = serializers.CharField(required=False)


class SimulatedBankTransfer(APIView):
    """Pretend someone sent a bank transfer to a virtual account number (mock provider, no JWT)."""

    authentication_classes = []
    permission_classes = []

    @extend_schema(
        request=BankTransferSerializer,
        responses=inline_serializer(
            "ProviderBankTransfer",
            {
                "provider_ref": serializers.CharField(),
                "status": serializers.CharField(),
                "account_number": serializers.CharField(),
                "amount": serializers.IntegerField(),
                "currency": serializers.CharField(),
            },
        ),
    )
    def post(self, request):
        s = BankTransferSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        txn = api.simulate_bank_transfer(**s.validated_data)
        return Response(
            {
                "provider_ref": txn.provider_ref,
                "status": txn.status,
                "account_number": txn.bank_account,
                "amount": txn.amount,
                "currency": txn.currency,
            },
            status=201,
        )
