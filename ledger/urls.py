from django.urls import path
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from . import views

urlpatterns = [
    path("auth/register/", views.Register.as_view()),
    path("auth/token/", TokenObtainPairView.as_view()),
    path("auth/token/refresh/", TokenRefreshView.as_view()),
    path("admin/reconciliation/", views.ReconciliationListRun.as_view()),
    path("admin/reconciliation/<int:pk>/", views.ReconciliationDetail.as_view()),
    path("admin/audit-logs/", views.AuditLogList.as_view()),
    path("kyc/", views.KYCMe.as_view()),
    path("admin/kyc/", views.KYCAdminList.as_view()),
    path("admin/kyc/<int:pk>/approve/", views.KYCApprove.as_view()),
    path("admin/kyc/<int:pk>/reject/", views.KYCReject.as_view()),
    path("wallets/", views.WalletListCreate.as_view()),
    path("wallets/<int:pk>/", views.WalletDetail.as_view()),
    path("wallets/<int:pk>/deposit/", views.Deposit.as_view()),
    path("wallets/<int:pk>/transactions/", views.WalletHistory.as_view()),
    path("transfers/", views.Transfer.as_view()),
    path("wallets/<int:pk>/withdrawals/", views.WithdrawalCreate.as_view()),
    path("withdrawals/<int:pk>/", views.WithdrawalDetail.as_view()),
    path("webhooks/provider/", views.ProviderWebhook.as_view()),
    path("wallets/<int:pk>/holds/", views.HoldCreate.as_view()),
    path("holds/<int:pk>/", views.HoldDetail.as_view()),
    path("holds/<int:pk>/capture/", views.HoldCapture.as_view()),
    path("holds/<int:pk>/release/", views.HoldRelease.as_view()),
    path("transactions/<int:pk>/refund/", views.RefundCreate.as_view()),
    path("transactions/<int:pk>/reverse/", views.ReverseTransaction.as_view()),
    path("fx-rates/", views.FxRateList.as_view()),
]
