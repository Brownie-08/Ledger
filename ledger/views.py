from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404
from rest_framework import generics, status
from rest_framework.pagination import CursorPagination
from rest_framework.response import Response
from rest_framework.views import APIView

from . import holds, services, signing, webhooks, withdrawals
from .idempotency import idempotent
from .models import Account, Entry, FxRate, Hold, Transaction, Withdrawal
from .serializers import (
    CaptureSerializer,
    DepositSerializer,
    EntrySerializer,
    FxRateSerializer,
    HoldCreateSerializer,
    HoldSerializer,
    RefundSerializer,
    ReverseSerializer,
    TransferSerializer,
    WalletCreateSerializer,
    WalletSerializer,
    WithdrawalCreateSerializer,
    WithdrawalSerializer,
)


def ledger_error(exc):
    code = status.HTTP_422_UNPROCESSABLE_ENTITY
    return Response({"error": exc.code, "detail": str(exc)}, status=code)


def txn_body(txn):
    return {
        "transaction_id": txn.id,
        "type": txn.type,
        "status": txn.status,
        "metadata": txn.metadata,
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


class ProviderWebhook(APIView):
    authentication_classes = []
    permission_classes = []

    def post(self, request):
        from django.conf import settings

        raw = request.body
        try:
            signing.verify(
                raw,
                request.headers.get(signing.HEADER),
                settings.PROVIDER_WEBHOOK_SECRET,
                settings.PROVIDER_WEBHOOK_TOLERANCE_SECONDS,
            )
        except signing.InvalidSignature as exc:
            return Response({"error": "invalid_signature", "detail": str(exc)}, status=400)
        try:
            result = webhooks.process_event(request.data)
        except webhooks.UnknownEvent:
            return Response({"error": "unknown_event"}, status=400)
        return Response({"status": result})


class WithdrawalCreate(APIView):
    @idempotent("wallet.withdraw")
    def post(self, request, pk):
        wallet = get_object_or_404(Account, pk=pk, type=Account.Type.USER_WALLET)
        s = WithdrawalCreateSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        try:
            wd = withdrawals.create_withdrawal(
                wallet, s.validated_data["amount"], s.validated_data["bank_account"], request.idempotency_key
            )
        except services.LedgerError as exc:
            return ledger_error(exc)
        wd.refresh_from_db()
        return Response(WithdrawalSerializer(wd).data, status=201)


class WithdrawalDetail(generics.RetrieveAPIView):
    queryset = Withdrawal.objects.all()
    serializer_class = WithdrawalSerializer


class HoldCreate(APIView):
    @idempotent("hold.create")
    def post(self, request, pk):
        wallet = get_object_or_404(Account, pk=pk, type=Account.Type.USER_WALLET)
        s = HoldCreateSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        d = s.validated_data
        merchant = get_object_or_404(Account, pk=d["merchant_wallet"], type=Account.Type.USER_WALLET)
        try:
            hold = holds.place_hold(
                wallet,
                merchant,
                d["amount"],
                request.idempotency_key,
                timedelta(seconds=d["expires_in_seconds"]),
                d["reference"],
            )
        except services.LedgerError as exc:
            return ledger_error(exc)
        return Response(HoldSerializer(hold).data, status=201)


class HoldDetail(generics.RetrieveAPIView):
    queryset = Hold.objects.all()
    serializer_class = HoldSerializer


class HoldCapture(APIView):
    @idempotent("hold.capture")
    def post(self, request, pk):
        hold = get_object_or_404(Hold, pk=pk)
        s = CaptureSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        try:
            hold = holds.capture_hold(hold.id, s.validated_data.get("amount"))
        except services.LedgerError as exc:
            return ledger_error(exc)
        return Response(HoldSerializer(hold).data)


class HoldRelease(APIView):
    @idempotent("hold.release")
    def post(self, request, pk):
        hold = get_object_or_404(Hold, pk=pk)
        try:
            hold = holds.release_hold(hold.id)
        except services.LedgerError as exc:
            return ledger_error(exc)
        return Response(HoldSerializer(hold).data)


class RefundCreate(APIView):
    @idempotent("transaction.refund")
    def post(self, request, pk):
        get_object_or_404(Transaction, pk=pk)
        s = RefundSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        try:
            txn = services.refund_transaction(
                pk, s.validated_data["amount"], request.idempotency_key, s.validated_data["reason"]
            )
        except services.LedgerError as exc:
            return ledger_error(exc)
        return Response(txn_body(txn), status=201)


class ReverseTransaction(APIView):
    @idempotent("transaction.reverse")
    def post(self, request, pk):
        get_object_or_404(Transaction, pk=pk)
        s = ReverseSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        try:
            txn = services.reverse_transaction(pk, request.idempotency_key, reason=s.validated_data["reason"])
        except services.LedgerError as exc:
            return ledger_error(exc)
        return Response(txn_body(txn), status=201)


class FxRateList(generics.ListAPIView):
    queryset = FxRate.objects.order_by("base", "quote")
    serializer_class = FxRateSerializer
    pagination_class = None
