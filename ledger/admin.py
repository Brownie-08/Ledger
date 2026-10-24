"""Read-only Django admin for browsing the ledger.

Money moves only through the service layer (balanced, idempotent, audited). The admin is therefore view-only:
no add, change or delete. Posted entries are also protected by DB triggers.
"""

from django.contrib import admin

from . import models


class ReadOnlyAdmin(admin.ModelAdmin):
    list_per_page = 50

    def get_list_display(self, request):
        return [f.name for f in self.model._meta.concrete_fields][:9]

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


for m in [
    models.Account,
    models.Transaction,
    models.Entry,
    models.Hold,
    models.Withdrawal,
    models.Refund,
    models.FxRate,
    models.KYCProfile,
    models.VirtualAccount,
    models.SuspenseResolution,
    models.ReconciliationReport,
    models.AuditLog,
    models.OutboxEvent,
    models.WebhookEvent,
    models.IdempotencyKey,
]:
    admin.site.register(m, ReadOnlyAdmin)
