"""Resolving inbound money parked in suspense: allocate it to a wallet, or return it to the sender.

Each parked ``inbound_unallocated`` transaction can be resolved exactly once (unique ``SuspenseResolution``
plus a deterministic idempotency key). Repeating the same decision returns the original resolution;
a conflicting second decision is a 409.
"""

from django.db import transaction

from . import services
from .audit import audit
from .models import Account, OutboxEvent, SuspenseResolution, Transaction
from .money import fmt
from .services import EntrySpec, LedgerError


class SuspenseStateError(LedgerError):
    code = "suspense_invalid_state"
    http_status = 409


def unresolved():
    return Transaction.objects.filter(
        type="inbound_unallocated", status=Transaction.Status.POSTED, suspense_resolution__isnull=True
    ).order_by("id")


def item(txn):
    amount, currency = txn.metadata.get("amount"), txn.entries.first().currency
    return {
        "transaction_id": txn.id,
        "provider_ref": txn.metadata.get("provider_ref"),
        "amount_minor": amount,
        "currency": currency,
        "amount_display": fmt(amount, currency),
        "reason": txn.metadata.get("reason"),
        "account_number": txn.metadata.get("account_number"),
        "sender_name": txn.metadata.get("sender_name", ""),
        "created_at": txn.created_at.isoformat(),
    }


def _lock_parked(txn_id):
    try:
        return Transaction.objects.select_for_update().get(
            pk=txn_id, type="inbound_unallocated", status=Transaction.Status.POSTED
        )
    except Transaction.DoesNotExist:
        raise LedgerError(f"Transaction {txn_id} is not a parked inbound item") from None


def _existing(parked, action, wallet_id=None):
    res = SuspenseResolution.objects.filter(parked=parked).first()
    if res is None:
        return None
    if res.action == action and res.wallet_id == wallet_id:
        return res  # idempotent repeat
    raise SuspenseStateError(f"Item {parked.id} was already resolved ({res.action})")


def allocate(txn_id, wallet, actor, *, override_limits=False, reason="", request=None):
    """Move a parked amount suspense -> wallet. KYC tier limits apply unless ``override_limits`` (needs a reason)."""
    if override_limits and len(reason.strip()) < 3:
        raise LedgerError("An override reason is required when overriding KYC limits")
    with transaction.atomic():
        parked = _lock_parked(txn_id)
        if res := _existing(parked, SuspenseResolution.Action.ALLOCATE, wallet.id):
            return res
        amount, currency = parked.metadata["amount"], parked.entries.first().currency
        if wallet.type != Account.Type.USER_WALLET:
            raise LedgerError("Target must be a user wallet")
        if wallet.currency != currency:
            raise services.CurrencyMismatch(
                f"Parked item is {currency}, wallet {wallet.id} is {wallet.currency}", currency=currency
            )
        suspense = services.system_account(Account.Type.SUSPENSE, currency)
        txn = services.post_transaction(
            [EntrySpec(suspense.id, -amount, currency), EntrySpec(wallet.id, amount, currency)],
            f"suspense-allocate:{parked.id}",
            type="suspense_allocation",
            reference=parked.reference,
            metadata={
                "parked_transaction": parked.id,
                "provider_ref": parked.metadata.get("provider_ref"),
                "amount": amount,
                "admin_override_limits": override_limits,
                "reason": reason,
                "actor_id": getattr(actor, "id", None),
            },
            override_limits=override_limits,
        )
        res = SuspenseResolution.objects.create(
            parked=parked,
            action=SuspenseResolution.Action.ALLOCATE,
            wallet=wallet,
            resolution_txn=txn,
            actor=actor,
            reason=reason,
            override_limits=override_limits,
        )
        OutboxEvent.objects.create(
            event_type="suspense.allocated",
            payload={
                "parked_transaction": parked.id,
                "transaction_id": txn.id,
                "wallet_id": wallet.id,
                "amount": amount,
                "currency": currency,
                "override_limits": override_limits,
            },
        )
        audit(
            "suspense.allocate",
            actor=actor,
            request=request,
            obj=res,
            transaction=txn,
            parked=parked.id,
            wallet_id=wallet.id,
            amount=amount,
            override_limits=override_limits,
            reason=reason,
        )
    return res


def return_to_sender(txn_id, actor, *, reason="", request=None):
    """Mock refund out: suspense -> system_cash (the money leaves our bank account back to the sender)."""
    with transaction.atomic():
        parked = _lock_parked(txn_id)
        if res := _existing(parked, SuspenseResolution.Action.RETURN):
            return res
        amount, currency = parked.metadata["amount"], parked.entries.first().currency
        suspense = services.system_account(Account.Type.SUSPENSE, currency)
        cash = services.system_account(Account.Type.SYSTEM_CASH, currency)
        txn = services.post_transaction(
            [EntrySpec(suspense.id, -amount, currency), EntrySpec(cash.id, amount, currency)],
            f"suspense-return:{parked.id}",
            type="suspense_return",
            reference=parked.reference,
            metadata={
                "parked_transaction": parked.id,
                "provider_ref": parked.metadata.get("provider_ref"),
                "amount": amount,
                "reason": reason,
                "mock_refund_ref": f"rf_{parked.metadata.get('provider_ref')}",
            },
        )
        res = SuspenseResolution.objects.create(
            parked=parked, action=SuspenseResolution.Action.RETURN, resolution_txn=txn, actor=actor, reason=reason
        )
        OutboxEvent.objects.create(
            event_type="suspense.returned",
            payload={"parked_transaction": parked.id, "transaction_id": txn.id, "amount": amount, "currency": currency},
        )
        audit(
            "suspense.return", actor=actor, request=request, obj=res, transaction=txn, parked=parked.id, amount=amount
        )
    return res


def resolution_body(res):
    return {
        "parked_transaction": res.parked_id,
        "action": res.action,
        "wallet_id": res.wallet_id,
        "transaction_id": res.resolution_txn_id,
        "amount_minor": res.resolution_txn.metadata.get("amount"),
        "override_limits": res.override_limits,
        "reason": res.reason,
        "actor": getattr(res.actor, "username", None),
        "created_at": res.created_at.isoformat(),
    }
