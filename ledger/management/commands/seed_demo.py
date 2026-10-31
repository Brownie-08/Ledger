"""Create opt-in, synthetic demo data without resetting an existing database."""

import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from ledger import holds, services
from ledger.models import Account, KYCProfile


class Command(BaseCommand):
    help = (
        "Seed synthetic demo users and wallets once. Requires DEMO_SEED_ENABLED=1 and DEMO_PASSWORD. Never resets data."
    )

    @transaction.atomic
    def handle(self, *args, **options):
        if os.environ.get("DEMO_SEED_ENABLED") != "1":
            raise CommandError("Set DEMO_SEED_ENABLED=1 only for a synthetic demo database")
        password = os.environ.get("DEMO_PASSWORD", "")
        if len(password) < 12:
            raise CommandError("DEMO_PASSWORD must be at least 12 characters")
        users = []
        for username in ("demo_alex", "demo_ada"):
            user, created = get_user_model().objects.get_or_create(username=username)
            if created:
                user.set_password(password)
                user.save(update_fields=["password"])
                KYCProfile.objects.create(user=user, tier=1, status="approved")
            elif not KYCProfile.objects.filter(user=user, tier=1).exists():
                raise CommandError(f"Existing {username} is not a seeded tier-1 user; refusing to alter it")
            users.append(user)
        for i, user in enumerate(users):
            for currency, amount in (("NGN", 250000000), ("USD", 425000), ("GBP", 182000)):
                wallet, created = Account.objects.get_or_create(
                    owner=user, currency=currency, type=Account.Type.USER_WALLET
                )
                if created:
                    services.deposit(
                        wallet,
                        amount if i == 0 else amount // 10,
                        f"demo:opening:{wallet.id}",
                        "Synthetic demo opening balance",
                    )
        alex = Account.objects.get(owner=users[0], currency="NGN")
        ada = Account.objects.get(owner=users[1], currency="NGN")
        holds.place_hold(alex, ada, 450000, "demo:sample-hold", reference="Synthetic authorisation")
        self.stdout.write(
            self.style.SUCCESS(
                f"Demo users ready. Alex NGN wallet #{alex.id}; Ada NGN wallet #{ada.id}. No balances reset."
            )
        )
