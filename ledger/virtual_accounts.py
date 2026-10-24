"""MOCK virtual bank account numbers, one per wallet.

Numbers are derived from HMAC(secret, wallet id, attempt), so they are reproducible but not guessable from
the wallet id, then collision-checked (and protected by a unique constraint, retried on a race). They use
made-up bank/routing codes and are clearly labelled as mock: they are not real bank accounts.
"""

import hashlib
import hmac

from django.conf import settings
from django.db import IntegrityError, transaction

from .models import Account, VirtualAccount

MOCK_NUBAN_BANK_CODE = "999"  # not a real CBN bank code
MOCK_SORT_CODE = "990001"  # 99-00-01, not an allocated UK sort code
MOCK_ROUTING_BASE = "99900001"  # 8 digits + ABA check digit
BANK_NAMES = {
    "NGN": "Ledger MFB (mock)",
    "GBP": "Ledger Bank UK (mock)",
    "USD": "Ledger Bank US (mock)",
}
MAX_ATTEMPTS = 20


def _digits(wallet_id, attempt, n):
    mac = hmac.new(settings.VIRTUAL_ACCOUNT_SECRET.encode(), f"{wallet_id}:{attempt}".encode(), hashlib.sha256)
    return str(int(mac.hexdigest(), 16))[-n:].zfill(n)


def nuban_check_digit(bank_code, serial):
    """CBN NUBAN algorithm: weights 3,7,3 over the 3-digit bank code + 9-digit serial."""
    weights = [3, 7, 3] * 4
    total = sum(int(d) * w for d, w in zip(bank_code + serial, weights, strict=True))
    return str((10 - total % 10) % 10)


def is_valid_nuban(number, bank_code=MOCK_NUBAN_BANK_CODE):
    return len(number) == 10 and number.isdigit() and nuban_check_digit(bank_code, number[:9]) == number[9]


def aba_check_digit(first8):
    weights = [3, 7, 1, 3, 7, 1, 3, 7]
    return str((10 - sum(int(d) * w for d, w in zip(first8, weights, strict=True)) % 10) % 10)


def is_valid_aba(routing):
    weights = [3, 7, 1] * 3
    if len(routing) != 9 or not routing.isdigit():
        return False
    return sum(int(d) * w for d, w in zip(routing, weights, strict=True)) % 10 == 0


MOCK_ROUTING_NUMBER = MOCK_ROUTING_BASE + aba_check_digit(MOCK_ROUTING_BASE)


def candidate(wallet, attempt):
    if wallet.currency == "NGN":
        serial = _digits(wallet.id, attempt, 9)
        return {"scheme": "nuban", "account_number": serial + nuban_check_digit(MOCK_NUBAN_BANK_CODE, serial)}
    if wallet.currency == "GBP":
        return {"scheme": "sort_code", "account_number": _digits(wallet.id, attempt, 8), "sort_code": MOCK_SORT_CODE}
    if wallet.currency == "USD":
        return {
            "scheme": "aba",
            "account_number": _digits(wallet.id, attempt, 12),
            "routing_number": MOCK_ROUTING_NUMBER,
        }
    raise ValueError(f"No virtual account scheme for {wallet.currency}")


def issue(wallet: Account) -> VirtualAccount:
    """Idempotent: returns the wallet's existing virtual account if it has one."""
    existing = VirtualAccount.objects.filter(wallet=wallet).first()
    if existing:
        return existing
    owner = wallet.owner
    name = owner.kyc.full_name if owner and hasattr(owner, "kyc") and owner.kyc.full_name else str(owner or "")
    for attempt in range(MAX_ATTEMPTS):
        c = candidate(wallet, attempt)
        if VirtualAccount.objects.filter(account_number=c["account_number"]).exists():
            continue
        try:
            with transaction.atomic():
                return VirtualAccount.objects.create(
                    wallet=wallet,
                    currency=wallet.currency,
                    bank_name=BANK_NAMES[wallet.currency],
                    account_name=name,
                    **c,
                )
        except IntegrityError:
            existing = VirtualAccount.objects.filter(wallet=wallet).first()
            if existing:
                return existing  # lost a race issuing for the same wallet
            continue  # number collided concurrently: next attempt
    raise RuntimeError(f"Could not allocate a unique virtual account for wallet {wallet.id}")
