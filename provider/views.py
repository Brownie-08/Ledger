from drf_spectacular.utils import extend_schema, inline_serializer
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
