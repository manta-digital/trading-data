"""Price validation shared by the minute and daily insert paths."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

OHLC_FIELDS = ("open", "high", "low", "close")

# What parse_ohlc_prices raises for an unusable bar. InvalidOperation is here
# because Decimal raises it (an ArithmeticError, not a ValueError) on
# unparseable input like str(None).
UNUSABLE_BAR_ERRORS = (KeyError, ValueError, TypeError, InvalidOperation)


def parse_ohlc_prices(bar: dict) -> dict[str, Decimal]:
    """Return the bar's OHLC prices, or raise one of UNUSABLE_BAR_ERRORS.

    Provider payloads occasionally carry null or absent price fields (EODHD
    sent open=null for CVR and LFWD, 2026-08-14). A price that is missing,
    non-numeric, non-finite, or <= 0 makes the bar unusable. Never substitute
    a default — a fabricated 0.00 price poisons every consumer downstream.
    The finite/positive check guards the values Decimal accepts but the
    tables must never hold ("NaN", "Inf", 0).
    """
    prices: dict[str, Decimal] = {}
    for field in OHLC_FIELDS:
        value = Decimal(str(bar[field]))
        if not value.is_finite() or value <= 0:
            raise ValueError(f"unusable {field} price: {value}")
        prices[field] = value
    return prices
