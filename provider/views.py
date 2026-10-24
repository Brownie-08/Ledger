from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from . import api


class ChargeSerializer(serializers.Serializer):
    wallet_id = serializers.IntegerField()
    amount = serializers.IntegerField(min_value=1)
    currency = serializers.ChoiceField(choices=["NGN", "USD", "GBP"])
    client_reference = serializers.CharField(required=False)


class SimulatedCheckout(APIView):
    """Pretend a customer paid by card at the provider's hosted checkout."""

    def post(self, request):
        s = ChargeSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        txn = api.create_charge(**s.validated_data)
        return Response(
            {"provider_ref": txn.provider_ref, "status": txn.status, "amount": txn.amount, "currency": txn.currency},
            status=201,
        )
