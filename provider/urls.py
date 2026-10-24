from django.urls import path

from . import views

urlpatterns = [
    path("charges/", views.SimulatedCheckout.as_view()),
    path("bank-transfers/", views.SimulatedBankTransfer.as_view()),
]
