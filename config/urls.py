from django.urls import include, path

urlpatterns = [
    path("api/", include("ledger.urls")),
    path("provider/", include("provider.urls")),
]
