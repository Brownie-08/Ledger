from decimal import Decimal

from django.db import migrations

RATES = {("USD", "NGN"): Decimal("1500"), ("GBP", "NGN"): Decimal("1900"), ("GBP", "USD"): Decimal("1.27")}


def seed(apps, schema_editor):
    FxRate = apps.get_model("ledger", "FxRate")
    q = Decimal("0.0000000001")
    for (base, quote), rate in RATES.items():
        FxRate.objects.update_or_create(base=base, quote=quote, defaults={"rate": rate})
        FxRate.objects.update_or_create(base=quote, quote=base, defaults={"rate": (1 / rate).quantize(q)})


def unseed(apps, schema_editor):
    apps.get_model("ledger", "FxRate").objects.all().delete()


class Migration(migrations.Migration):
    dependencies = [("ledger", "0004_week4_holds_fx_refunds")]
    operations = [migrations.RunPython(seed, unseed)]
