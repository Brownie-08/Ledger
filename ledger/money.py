"""Human-readable money for messages. Amounts are integer minor units everywhere else."""

SYMBOLS = {"NGN": "₦", "USD": "$", "GBP": "£"}
EXPONENT = {"NGN": 2, "USD": 2, "GBP": 2}


def fmt(minor, currency):
    """``fmt(2000000, "NGN") -> '₦20,000.00 NGN'``."""
    exp = EXPONENT.get(currency, 2)
    sign = "-" if minor < 0 else ""
    major = abs(minor) / 10**exp
    return f"{sign}{SYMBOLS.get(currency, '')}{major:,.{exp}f} {currency}"
