"""HMAC-SHA256 webhook signatures (Stripe-style ``t=<ts>,v1=<hex>`` header)."""

import hashlib
import hmac
import time

HEADER = "X-Provider-Signature"


class InvalidSignature(Exception):
    pass


def sign(body: bytes, secret: str, timestamp: int | None = None) -> str:
    ts = int(time.time()) if timestamp is None else timestamp
    mac = hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={ts},v1={mac}"


def verify(body: bytes, header: str | None, secret: str, tolerance: int = 300, now: int | None = None) -> None:
    if not header:
        raise InvalidSignature("missing signature")
    try:
        parts = dict(p.split("=", 1) for p in header.split(","))
        ts, given = int(parts["t"]), parts["v1"]
    except (ValueError, KeyError):
        raise InvalidSignature("malformed signature")
    now = int(time.time()) if now is None else now
    if abs(now - ts) > tolerance:
        raise InvalidSignature("timestamp outside tolerance")
    expected = hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, given):
        raise InvalidSignature("signature mismatch")
