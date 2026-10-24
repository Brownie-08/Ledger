from .context import get_request_id
from .models import AuditLog


def audit(action, *, actor=None, request=None, transaction=None, obj=None, **data):
    if request is not None:
        actor = actor or (request.user if request.user.is_authenticated else None)
    AuditLog.objects.create(
        actor=actor,
        action=action,
        transaction=transaction,
        object_type=type(obj).__name__ if obj is not None else "",
        object_id=str(obj.pk) if obj is not None else "",
        data=data,
        request_id=get_request_id(),
        ip=request.META.get("REMOTE_ADDR") if request is not None else None,
    )
