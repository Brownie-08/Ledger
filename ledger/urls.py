from django.urls import path

from . import views

urlpatterns = [
    path("wallets/", views.WalletListCreate.as_view()),
    path("wallets/<int:pk>/", views.WalletDetail.as_view()),
    path("wallets/<int:pk>/deposit/", views.Deposit.as_view()),
    path("wallets/<int:pk>/transactions/", views.WalletHistory.as_view()),
    path("transfers/", views.Transfer.as_view()),
]
