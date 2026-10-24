import json

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from ledger import suspense
from ledger.models import Account
from ledger.services import LedgerError


class Command(BaseCommand):
    help = (
        "Inbound money parked in suspense. "
        "`suspense list` | `suspense allocate <txn> --wallet <id> [--override-limits --reason ..]` | "
        "`suspense return <txn> [--reason ..]`. Use --actor <staff username> for the audit log."
    )

    def add_arguments(self, parser):
        parser.add_argument("action", choices=["list", "allocate", "return"])
        parser.add_argument("transaction_id", nargs="?", type=int)
        parser.add_argument("--wallet", type=int)
        parser.add_argument("--override-limits", action="store_true")
        parser.add_argument("--reason", default="")
        parser.add_argument("--actor", help="staff username recorded as the actor")

    def handle(self, *args, **o):
        if o["action"] == "list":
            items = [suspense.item(t) for t in suspense.unresolved()]
            self.stdout.write(json.dumps(items, indent=2))
            return
        if not o["transaction_id"]:
            raise CommandError("transaction_id is required")
        actor = None
        if o["actor"]:
            actor = get_user_model().objects.filter(username=o["actor"], is_staff=True).first()
            if actor is None:
                raise CommandError(f"No staff user {o['actor']!r}")
        try:
            if o["action"] == "allocate":
                if not o["wallet"]:
                    raise CommandError("--wallet is required")
                wallet = Account.objects.filter(pk=o["wallet"], type=Account.Type.USER_WALLET).first()
                if wallet is None:
                    raise CommandError(f"No user wallet {o['wallet']}")
                res = suspense.allocate(
                    o["transaction_id"], wallet, actor, override_limits=o["override_limits"], reason=o["reason"]
                )
            else:
                res = suspense.return_to_sender(o["transaction_id"], actor, reason=o["reason"])
        except LedgerError as exc:
            raise CommandError(f"{exc.code}: {exc}") from exc
        self.stdout.write(json.dumps(suspense.resolution_body(res), indent=2))
