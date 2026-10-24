"""Throttles that read rates from settings at request time (so they're configurable per env/test)."""

from django.conf import settings
from rest_framework.throttling import AnonRateThrottle, UserRateThrottle


class _Dynamic:
    def get_rate(self):
        return settings.LEDGER_THROTTLE_RATES.get(self.scope)


class AnonThrottle(_Dynamic, AnonRateThrottle):
    scope = "anon"


class UserThrottle(_Dynamic, UserRateThrottle):
    scope = "user"


class MoneyThrottle(_Dynamic, UserRateThrottle):
    """Tighter limit on money-moving endpoints."""

    scope = "money"
