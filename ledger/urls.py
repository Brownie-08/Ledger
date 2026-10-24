from django.urls import path

from . import views

urlpatterns = [
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
