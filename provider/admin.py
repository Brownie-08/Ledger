from django.contrib import admin

from ledger.admin import ReadOnlyAdmin

from . import models

admin.site.register(models.ProviderTransaction, ReadOnlyAdmin)
admin.site.register(models.ProviderEvent, ReadOnlyAdmin)
