"""Participant-scoped, balanced ledger views without counterparty identity disclosure."""

from collections import defaultdict

from django.db.models import Q
from drf_spectacular.utils import extend_schema_field
from rest_framework import generics, serializers

from .models import Transaction
from .views import HistoryPagination


class ExplorerEntrySerializer(serializers.Serializer):
    account_id = serializers.IntegerField()
    amount = serializers.IntegerField()
    currency = serializers.CharField()
    label = serializers.CharField()


class ExplorerSerializer(serializers.ModelSerializer):
    transaction_id = serializers.IntegerField(source="id")
    entries = serializers.SerializerMethodField()
    totals = serializers.SerializerMethodField()

    class Meta:
        model = Transaction
        fields = ["transaction_id", "type", "status", "reference", "created_at", "entries", "totals"]

    @extend_schema_field(ExplorerEntrySerializer(many=True))
    def get_entries(self, obj):
        user = self.context["request"].user
        return [
            {
                "account_id": e.account_id,
                "amount": e.amount,
                "currency": e.currency,
                "label": (
                    f"Your {e.currency} wallet"
                    if e.account.owner_id == user.id
                    else "Counterparty wallet"
                    if e.account.owner_id
                    else e.account.get_type_display()
                ),
            }
            for e in obj.entries.all()
        ]

    @extend_schema_field(serializers.DictField(child=serializers.IntegerField()))
    def get_totals(self, obj):
        totals = defaultdict(int)
        for entry in obj.entries.all():
            totals[entry.currency] += entry.amount
        return dict(totals)


class ParticipantMixin:
    serializer_class = ExplorerSerializer

    def get_queryset(self):
        qs = Transaction.objects.prefetch_related("entries__account").order_by("-id")
        if not self.request.user.is_staff:
            qs = qs.filter(entries__account__owner=self.request.user).distinct()
        if search := self.request.query_params.get("search", "")[:120]:
            qs = qs.filter(Q(reference__icontains=search) | Q(type__icontains=search))
        return qs


class TransactionList(ParticipantMixin, generics.ListAPIView):
    pagination_class = HistoryPagination


class TransactionDetail(ParticipantMixin, generics.RetrieveAPIView):
    pass
