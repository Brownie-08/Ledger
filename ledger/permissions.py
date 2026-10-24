from rest_framework.permissions import BasePermission


def owns(user, account):
    return user.is_staff or (account.owner_id is not None and account.owner_id == user.id)


class IsStaff(BasePermission):
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_staff)
