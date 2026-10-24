import logging
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.http import Http404
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import OpenApiParameter, extend_schema, inline_serializer
from rest_framework import generics, serializers, status
from rest_framework.pagination import CursorPagination
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from . import holds, services, signing, webhooks, withdrawals
from .audit import audit
from .idempotency import idempotent
from .metrics import WEBHOOKS
from .models import Account, AuditLog, Entry, FxRate, Hold, ReconciliationReport, Transaction, Withdrawal
from .permissions import IsStaff, owns
from .reconciliation import run_reconciliation
from .serializers import (
    AuditLogSerializer,
    CaptureSerializer,
    DepositSerializer,
    EntrySerializer,
    FxRateSerializer,
    HoldCreateSerializer,
    HoldSerializer,
    ReconciliationReportSerializer,
    RefundSerializer,
    RegisterSerializer,
    ReverseSerializer,
    TransferSerializer,
    WalletCreateSerializer,
    WalletSerializer,
    WithdrawalCreateSerializer,
    WithdrawalSerializer,
)

log = logging.getLogger(__name__)


IDEMPOTENCY_HEADER = OpenApiParameter(
    "Idempotency-Key",
    str,
    OpenApiParameter.HEADER,
    required=True,
    description="Unique per logical operation. Replays return the original response with Idempotent-Replayed: true.",
)
TXN_SCHEMA = inline_serializer(
    "PostedTransaction",
    {
        "transaction_id": serializers.IntegerField(),
        "type": serializers.CharField(),
        "status": serializers.CharField(),
        "metadata": serializers.DictField(),
        "entries": inline_serializer(
            "PostedEntry",
            {
                "account_id": serializers.IntegerField(),
                "amount": serializers.IntegerField(),
                "currency": serializers.CharField(),
            },
            many=True,
        ),
    },
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


def wallet_for(request, pk, *, write=True):
    """Fetch a user wallet the caller may act on. Other users' wallets look like 404 (no enumeration)."""
    wallet = get_object_or_404(Account, pk=pk, type=Account.Type.USER_WALLET)
    if not owns(request.user, wallet):
        raise Http404
    return wallet


class MoneyMixin:
    """Money-moving endpoints get an extra, tighter throttle."""

    def get_throttles(self):
        from .throttling import MoneyThrottle

        return [*super().get_throttles(), MoneyThrottle()]


class Register(APIView):
    permission_classes = [AllowAny]

    @extend_schema(
        request=RegisterSerializer,
        responses=inline_serializer(
            "RegisteredUser", {"id": serializers.IntegerField(), "username": serializers.CharField()}
        ),
    )
    def post(self, request):
        s = RegisterSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        user = get_user_model().objects.create_user(**s.validated_data)
        return Response({"id": user.id, "username": user.username}, status=201)


class WalletListCreate(APIView):
    @extend_schema(responses=WalletSerializer(many=True))
    def get(self, request):
        qs = Account.objects.filter(type=Account.Type.USER_WALLET).order_by("id")
        if not request.user.is_staff:
            qs = qs.filter(owner=request.user)
        return Response(WalletSerializer(qs, many=True).data)

    @extend_schema(request=WalletCreateSerializer, responses=WalletSerializer, parameters=[IDEMPOTENCY_HEADER])
    @idempotent("wallet.create")
    def post(self, request):
        s = WalletCreateSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        owner = request.user
        if request.user.is_staff and s.validated_data.get("owner"):
            owner = s.validated_data["owner"]
        elif request.user.is_staff and s.validated_data.get("username"):
            owner, _ = get_user_model().objects.get_or_create(username=s.validated_data["username"])
        try:
            with transaction.atomic():
                wallet = Account.objects.create(owner=owner, currency=s.validated_data["currency"])
        except IntegrityError:
            return Response({"error": "wallet_exists"}, status=409)
        return Response(WalletSerializer(wallet).data, status=201)


class WalletDetail(APIView):
    @extend_schema(responses=WalletSerializer)
    def get(self, request, pk):
        return Response(WalletSerializer(wallet_for(request, pk)).data)


class Deposit(MoneyMixin, APIView):
    """Manual/back-office credit. Customer deposits arrive via the signed provider webhook."""

    permission_classes = [IsStaff]

    @extend_schema(request=DepositSerializer, responses=TXN_SCHEMA, parameters=[IDEMPOTENCY_HEADER])
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
        audit("deposit.manual", request=request, transaction=txn, obj=wallet, amount=s.validated_data["amount"])
        return Response(txn_body(txn), status=201)


class Transfer(MoneyMixin, APIView):
    @extend_schema(request=TransferSerializer, responses=TXN_SCHEMA, parameters=[IDEMPOTENCY_HEADER])
    @idempotent("transfer")
    def post(self, request):
        s = TransferSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        d = s.validated_data
        src = wallet_for(request, d["source_wallet"])
        dst = get_object_or_404(Account, pk=d["destination_wallet"], type=Account.Type.USER_WALLET)
        try:
            txn = services.transfer(src, dst, d["amount"], request.idempotency_key, d["reference"])
        except services.LedgerError as exc:
            return ledger_error(exc)
        audit("transfer", request=request, transaction=txn, obj=src, amount=d["amount"], destination=dst.id)
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
        wallet_for(self.request, self.kwargs["pk"])
        return Entry.objects.filter(account_id=self.kwargs["pk"]).select_related("transaction")


class ProviderWebhook(APIView):
    """Authenticated by HMAC signature, not JWT."""

    authentication_classes = []
    permission_classes = []
    throttle_classes = []

    @extend_schema(
        request=inline_serializer(
            "ProviderEvent",
            {"id": serializers.CharField(), "type": serializers.CharField(), "data": serializers.DictField()},
        ),
        responses=inline_serializer("WebhookResult", {"status": serializers.CharField()}),
    )
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
            WEBHOOKS.labels("invalid_signature").inc()
            log.warning("webhook rejected: bad signature", extra={"reason": str(exc)})
            return Response({"error": "invalid_signature", "detail": str(exc)}, status=400)
        try:
            result = webhooks.process_event(request.data)
        except webhooks.UnknownEvent:
            return Response({"error": "unknown_event"}, status=400)
        WEBHOOKS.labels(result).inc()
        return Response({"status": result})


class WithdrawalCreate(MoneyMixin, APIView):
    @extend_schema(request=WithdrawalCreateSerializer, responses=WithdrawalSerializer, parameters=[IDEMPOTENCY_HEADER])
    @idempotent("wallet.withdraw")
    def post(self, request, pk):
        wallet = wallet_for(request, pk)
        s = WithdrawalCreateSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        try:
            wd = withdrawals.create_withdrawal(
                wallet, s.validated_data["amount"], s.validated_data["bank_account"], request.idempotency_key
            )
        except services.LedgerError as exc:
            return ledger_error(exc)
        audit("withdrawal.create", request=request, transaction=wd.hold_transaction, obj=wd, amount=wd.amount)
        wd.refresh_from_db()
        return Response(WithdrawalSerializer(wd).data, status=201)


class WithdrawalDetail(APIView):
    @extend_schema(responses=WithdrawalSerializer)
    def get(self, request, pk):
        wd = get_object_or_404(Withdrawal.objects.select_related("wallet"), pk=pk)
        if not owns(request.user, wd.wallet):
            raise Http404
        return Response(WithdrawalSerializer(wd).data)


def hold_for(request, pk, *, merchant_only):
    hold = get_object_or_404(Hold.objects.select_related("account", "merchant"), pk=pk)
    allowed = owns(request.user, hold.merchant) or (not merchant_only and owns(request.user, hold.account))
    if not allowed:
        raise Http404
    return hold


class HoldCreate(MoneyMixin, APIView):
    @extend_schema(request=HoldCreateSerializer, responses=HoldSerializer, parameters=[IDEMPOTENCY_HEADER])
    @idempotent("hold.create")
    def post(self, request, pk):
        wallet = wallet_for(request, pk)
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
        audit("hold.place", request=request, obj=hold, amount=hold.amount, merchant=merchant.id)
        return Response(HoldSerializer(hold).data, status=201)


class HoldDetail(APIView):
    @extend_schema(responses=HoldSerializer)
    def get(self, request, pk):
        return Response(HoldSerializer(hold_for(request, pk, merchant_only=False)).data)


class HoldCapture(MoneyMixin, APIView):
    @extend_schema(request=CaptureSerializer, responses=HoldSerializer, parameters=[IDEMPOTENCY_HEADER])
    @idempotent("hold.capture")
    def post(self, request, pk):
        hold = hold_for(request, pk, merchant_only=True)
        s = CaptureSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        try:
            hold = holds.capture_hold(hold.id, s.validated_data.get("amount"))
        except services.LedgerError as exc:
            return ledger_error(exc)
        audit(
            "hold.capture", request=request, transaction=hold.capture_transaction, obj=hold, amount=hold.captured_amount
        )
        return Response(HoldSerializer(hold).data)


class HoldRelease(MoneyMixin, APIView):
    @extend_schema(request=None, responses=HoldSerializer, parameters=[IDEMPOTENCY_HEADER])
    @idempotent("hold.release")
    def post(self, request, pk):
        hold = hold_for(request, pk, merchant_only=True)
        try:
            hold = holds.release_hold(hold.id)
        except services.LedgerError as exc:
            return ledger_error(exc)
        audit("hold.release", request=request, obj=hold, amount=hold.amount)
        return Response(HoldSerializer(hold).data)


class RefundCreate(MoneyMixin, APIView):
    """Refund is issued by whoever received the money (payee/merchant), or staff."""

    @extend_schema(request=RefundSerializer, responses=TXN_SCHEMA, parameters=[IDEMPOTENCY_HEADER])
    @idempotent("transaction.refund")
    def post(self, request, pk):
        original = get_object_or_404(Transaction, pk=pk)
        if not request.user.is_staff:
            payees = Account.objects.filter(
                type=Account.Type.USER_WALLET, entries__transaction=original, entries__amount__gt=0
            )
            if not payees.filter(owner=request.user).exists():
                raise Http404
        s = RefundSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        try:
            txn = services.refund_transaction(
                pk, s.validated_data["amount"], request.idempotency_key, s.validated_data["reason"]
            )
        except services.LedgerError as exc:
            return ledger_error(exc)
        audit("refund", request=request, transaction=txn, obj=original, amount=s.validated_data["amount"])
        return Response(txn_body(txn), status=201)


class ReverseTransaction(MoneyMixin, APIView):
    permission_classes = [IsStaff]

    @extend_schema(request=ReverseSerializer, responses=TXN_SCHEMA, parameters=[IDEMPOTENCY_HEADER])
    @idempotent("transaction.reverse")
    def post(self, request, pk):
        get_object_or_404(Transaction, pk=pk)
        s = ReverseSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        try:
            txn = services.reverse_transaction(pk, request.idempotency_key, reason=s.validated_data["reason"])
        except services.LedgerError as exc:
            return ledger_error(exc)
        audit("reverse", request=request, transaction=txn, reverses=pk, reason=s.validated_data["reason"])
        return Response(txn_body(txn), status=201)


class FxRateList(generics.ListAPIView):
    queryset = FxRate.objects.order_by("base", "quote")
    serializer_class = FxRateSerializer
    pagination_class = None


class ReconciliationListRun(generics.ListAPIView):
    permission_classes = [IsStaff]
    queryset = ReconciliationReport.objects.all()
    pagination_class = HistoryPagination
    serializer_class = ReconciliationReportSerializer

    @extend_schema(request=None, responses=ReconciliationReportSerializer)
    def post(self, request):
        report = run_reconciliation()
        log.info("reconciliation run via API", extra={"report_id": report.id, "status": report.status})
        audit("reconciliation.run", request=request, obj=report, status=report.status)
        return Response(ReconciliationReportSerializer(report).data, status=201)


class ReconciliationDetail(generics.RetrieveAPIView):
    permission_classes = [IsStaff]
    queryset = ReconciliationReport.objects.all()
    serializer_class = ReconciliationReportSerializer


class AuditLogList(generics.ListAPIView):
    permission_classes = [IsStaff]
    serializer_class = AuditLogSerializer
    pagination_class = HistoryPagination

    def get_queryset(self):
        qs = AuditLog.objects.all()
        if action := self.request.query_params.get("action"):
            qs = qs.filter(action=action)
        return qs
