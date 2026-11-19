"""EODHD tickers: ``{code}.{exchange}``, the one rule every EODHD fetch uses.

A stored symbol that already carries an exchange suffix (``VIX.INDX``,
``BMW.XETRA``) is its own ticker. A bare symbol is a US listing (issue #46:
index instruments are stored as ``{code}.INDX`` so they never take the US
default).
"""

from __future__ import annotations

from typing import Final

#: EODHD's exchange code for US listings, the default for a bare symbol.
EODHD_US_EXCHANGE: Final = "US"
#: EODHD's exchange code for indices.
EODHD_INDEX_EXCHANGE: Final = "INDX"


def with_exchange(code: str, exchange: str) -> str:
    """``code`` on ``exchange`` as EODHD spells it."""
    return f"{code}.{exchange}"


def eodhd_ticker(symbol: str) -> str:
    """The EODHD ticker for a stored symbol: itself when it has an exchange
    suffix, else the US listing."""
    if "." in symbol:
        return symbol
    return with_exchange(symbol, EODHD_US_EXCHANGE)
