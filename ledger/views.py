from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404
from rest_framework import generics, status
from rest_framework.pagination import CursorPagination
from rest_framework.response import Response
from rest_framework.views import APIView

from . import services
from .idempotency import idempotent
from .models import Account, Entry
from .serializers import (
    DepositSerializer,
    EntrySerializer,
    TransferSerializer,
    WalletCreateSerializer,
    WalletSerializer,
)


def ledger_error(exc):
    code = status.HTTP_422_UNPROCESSABLE_ENTITY
    return Response({"error": exc.code, "detail": str(exc)}, status=code)


def txn_body(txn):
    return {
        "transaction_id": txn.id,
        "type": txn.type,
        "status": txn.status,
        "entries": [
            {"account_id": e.account_id, "amount": e.amount, "currency": e.currency} for e in txn.entries.all()
        ],
    }


class WalletListCreate(APIView):
    @idempotent("wallet.create")
    def post(self, request):
        s = WalletCreateSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        owner = s.validated_data.get("owner")
        if owner is None:
            owner, _ = get_user_model().objects.get_or_create(username=s.validated_data["username"])
        try:
            with transaction.atomic():
                wallet = Account.objects.create(owner=owner, currency=s.validated_data["currency"])
        except IntegrityError:
            return Response({"error": "wallet_exists"}, status=409)
        return Response(WalletSerializer(wallet).data, status=201)


class WalletDetail(generics.RetrieveAPIView):
    queryset = Account.objects.filter(type=Account.Type.USER_WALLET)
    serializer_class = WalletSerializer


class Deposit(APIView):
    @idempotent("wallet.deposit")
    def post(self, request, pk):
        wallet = get_object_or_404(Account, pk=pk, type=Account.Type.USER_WALLET)
        s = DepositSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        try:
            txn = services.deposit(
                wallet, s.validated_data["amount"], request.idempotency_key, s.validated_data["reference"]
            )
        except services.LedgerError as exc:
            return ledger_error(exc)
        return Response(txn_body(txn), status=201)


class Transfer(APIView):
    @idempotent("transfer")
    def post(self, request):
        s = TransferSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        d = s.validated_data
        src = get_object_or_404(Account, pk=d["source_wallet"], type=Account.Type.USER_WALLET)
        dst = get_object_or_404(Account, pk=d["destination_wallet"], type=Account.Type.USER_WALLET)
        try:
            txn = services.transfer(src, dst, d["amount"], request.idempotency_key, d["reference"])
        except services.LedgerError as exc:
            return ledger_error(exc)
        return Response(txn_body(txn), status=201)


class HistoryPagination(CursorPagination):
    ordering = "-id"
    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 100


class WalletHistory(generics.ListAPIView):
    serializer_class = EntrySerializer
    pagination_class = HistoryPagination

    def get_queryset(self):
        get_object_or_404(Account, pk=self.kwargs["pk"], type=Account.Type.USER_WALLET)
        return Entry.objects.filter(account_id=self.kwargs["pk"]).select_related("transaction")
