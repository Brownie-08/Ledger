"""KYC tiers: instant signup at tier 0 with tight limits; an admin review grants tier 1.

All limits are enforced from ``post_transaction`` (under the account row locks) and from ``open_wallet``,
so the API views can't be the only line of defence.
"""

from datetime import datetime, time

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.utils import timezone

from . import virtual_accounts
from .audit import audit
from .models import Account, Entry, Hold, KYCProfile, OutboxEvent, Transaction
from .money import fmt
from .services import KycRequired, LedgerError, LimitExceeded

# Transaction types that count as the wallet owner spending/sending money.
OUTFLOW_TYPES = {"transfer", "fx_transfer", "capture", "withdrawal_hold"}
# Credits that return the user's own money; never blocked by the balance cap.
CAP_EXEMPT_CREDITS = {"reversal", "refund"}
# Capability needed (on the debited wallet's owner) per transaction type.
CAPABILITY_FOR_TYPE = {"withdrawal_hold": "withdrawals", "fx_transfer": "fx"}


class KycStateError(LedgerError):
    code = "kyc_invalid_state"
    http_status = 409


def enforced():
    return getattr(settings, "KYC_ENFORCED", True)


def tier_rules(tier):
    return settings.KYC_TIERS[tier]


def profile_for(user) -> KYCProfile:
    """Always read from the DB (``user.kyc`` may be a stale cached instance on a long-lived user object)."""
    try:
        with transaction.atomic():
            return KYCProfile.objects.get_or_create(user=user)[0]
    except IntegrityError:
        return KYCProfile.objects.get(user=user)


def tier_of(user):
    return KYCProfile.objects.filter(user=user).values_list("tier", flat=True).first() or 0


def can(user, capability):
    return bool(tier_rules(tier_of(user))[capability])


def outflow_today(account_id, currency):
    start = timezone.make_aware(datetime.combine(timezone.localdate(), time.min))
    total = Entry.objects.filter(
        account_id=account_id,
        amount__lt=0,
        created_at__gte=start,
        transaction__type__in=OUTFLOW_TYPES,
        transaction__status=Transaction.Status.POSTED,  # reversed (e.g. failed withdrawal) doesn't count
    ).aggregate(s=Sum("amount"))["s"]
    return -(total or 0)


def enforce_tier_limits(accounts, deltas, txn_type):
    """Called by post_transaction with the locked accounts. Raises KycRequired / LimitExceeded."""
    if not enforced():
        return
    owners = {a.owner_id for a in accounts.values() if a.type == Account.Type.USER_WALLET and a.owner_id}
    if not owners:
        return
    tiers = dict(KYCProfile.objects.filter(user_id__in=owners).values_list("user_id", "tier"))
    for acc_id, delta in deltas.items():
        acc = accounts[acc_id]
        if acc.type != Account.Type.USER_WALLET or not acc.owner_id:
            continue
        tier = tiers.get(acc.owner_id, 0)
        rules = tier_rules(tier)
        cur = acc.currency
        if delta < 0:
            cap = CAPABILITY_FOR_TYPE.get(txn_type)
            if cap and not rules[cap]:
                raise KycRequired(f"Tier {tier} accounts cannot use {cap}; complete KYC")
            if txn_type in OUTFLOW_TYPES:
                _check_daily(tier, acc_id, cur, -delta, outflow_today(acc_id, cur))
        elif delta > 0 and txn_type not in CAP_EXEMPT_CREDITS:
            limit = rules["max_balance"][cur]
            if acc.balance + delta > limit:
                raise LimitExceeded(
                    f"Max balance for tier {tier} is {fmt(limit, cur)}: balance {fmt(acc.balance, cur)} "
                    f"+ {fmt(delta, cur)} would exceed it",
                    limit="max_balance",
                    tier=tier,
                    account_id=acc_id,
                    currency=cur,
                    amount_minor=delta,
                    limit_minor=limit,
                    balance_minor=acc.balance,
                )


def _check_daily(tier, acc_id, cur, amount, used):
    limit = tier_rules(tier)["daily_outflow"][cur]
    if used + amount > limit:
        raise LimitExceeded(
            f"Daily outflow limit for tier {tier} is {fmt(limit, cur)} (used {fmt(used, cur)}, "
            f"requested {fmt(amount, cur)})",
            limit="daily_outflow",
            tier=tier,
            account_id=acc_id,
            currency=cur,
            amount_minor=amount,
            limit_minor=limit,
            used_minor=used,
        )


def active_holds_today(account_id):
    start = timezone.make_aware(datetime.combine(timezone.localdate(), time.min))
    return (
        Hold.objects.filter(account_id=account_id, status=Hold.Status.ACTIVE, created_at__gte=start).aggregate(
            s=Sum("amount")
        )["s"]
        or 0
    )


def enforce_hold_limits(acc, amount):
    """Placing a hold reserves spend: posted outflow today + today's still-active holds + this hold must fit
    the daily limit. (Capture is checked again in post_transaction, counting only posted outflow.)"""
    if not enforced() or acc.type != Account.Type.USER_WALLET or not acc.owner_id:
        return
    tier = tier_of(acc.owner_id)
    _check_daily(tier, acc.id, acc.currency, amount, outflow_today(acc.id, acc.currency) + active_holds_today(acc.id))


def open_wallet(owner, currency):
    """Create a user wallet if the owner's tier allows the currency; tier 1 gets a virtual account."""
    tier = tier_of(owner)
    rules = tier_rules(tier)
    if enforced() and currency not in rules["currencies"]:
        raise KycRequired(f"Tier {tier} accounts can only open {', '.join(rules['currencies'])} wallets")
    with transaction.atomic():
        wallet = Account.objects.create(owner=owner, currency=currency)
        if rules["virtual_accounts"]:
            virtual_accounts.issue(wallet)
    return wallet


def submit(user, data, request=None):
    with transaction.atomic():
        profile = KYCProfile.objects.select_for_update().get(pk=profile_for(user).pk)
        if profile.status == KYCProfile.Status.APPROVED:
            raise KycStateError("KYC already approved")
        for field, value in data.items():
            setattr(profile, field, value)
        profile.status = KYCProfile.Status.PENDING
        profile.submitted_at = timezone.now()
        profile.rejection_reason = ""
        profile.reviewer = None
        profile.reviewed_at = None
        profile.save()
        audit("kyc.submit", request=request, obj=profile, id_type=profile.id_type, country=profile.country)
    return profile


def _review(profile_id, reviewer, request):
    profile = KYCProfile.objects.select_for_update().select_related("user").get(pk=profile_id)
    if profile.status != KYCProfile.Status.PENDING:
        raise KycStateError(f"KYC request is {profile.status}, not pending")
    profile.reviewer = reviewer
    profile.reviewed_at = timezone.now()
    return profile


def approve(profile_id, reviewer, request=None):
    with transaction.atomic():
        profile = _review(profile_id, reviewer, request)
        profile.status = KYCProfile.Status.APPROVED
        profile.tier = 1
        profile.save()
        issued = []
        if tier_rules(1)["virtual_accounts"]:
            wallets = Account.objects.filter(owner=profile.user, type=Account.Type.USER_WALLET).order_by("id")
            issued = [virtual_accounts.issue(w).account_number for w in wallets]  # backfill
        OutboxEvent.objects.create(
            event_type="kyc.approved",
            payload={"user_id": profile.user_id, "tier": 1, "virtual_accounts": issued},
        )
        audit("kyc.approve", actor=reviewer, request=request, obj=profile, user_id=profile.user_id, tier=1)
    return profile


def reject(profile_id, reviewer, reason, request=None):
    with transaction.atomic():
        profile = _review(profile_id, reviewer, request)
        profile.status = KYCProfile.Status.REJECTED
        profile.rejection_reason = reason
        profile.save()
        OutboxEvent.objects.create(event_type="kyc.rejected", payload={"user_id": profile.user_id, "reason": reason})
        audit("kyc.reject", actor=reviewer, request=request, obj=profile, user_id=profile.user_id, reason=reason)
    return profile
