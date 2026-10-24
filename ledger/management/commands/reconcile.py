import json

from django.core.management.base import BaseCommand

from ledger.reconciliation import run_reconciliation


class Command(BaseCommand):
    help = "Run reconciliation (ledger vs provider + integrity) and print the report."

    def handle(self, *args, **opts):
        r = run_reconciliation()
        self.stdout.write(
            json.dumps({"id": r.id, "status": r.status, "summary": r.summary, "mismatches": r.mismatches}, indent=2)
        )
