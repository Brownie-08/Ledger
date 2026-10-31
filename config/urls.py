from django.conf import settings
from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularRedocView, SpectacularSwaggerView

from ledger.maintenance import maintenance
from ledger.observability import healthz, metrics_view

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="docs"),
    path("api/redoc/", SpectacularRedocView.as_view(url_name="schema"), name="redoc"),
    path("api/", include("ledger.urls")),
    path("metrics", metrics_view),
    path("healthz", healthz),
    path("internal/maintenance/", maintenance),
]
if settings.MOCK_PROVIDER_ENABLED:
    urlpatterns.append(path("provider/", include("provider.urls")))
