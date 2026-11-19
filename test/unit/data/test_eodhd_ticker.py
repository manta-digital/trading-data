"""The one EODHD ticker rule (issue #46)."""

from __future__ import annotations

import pytest

from manta_trading.data.eodhd_ticker import eodhd_ticker


@pytest.mark.parametrize(
    ("symbol", "ticker"),
    [
        ("AAPL", "AAPL.US"),  # a bare symbol is a US listing
        ("VIX.INDX", "VIX.INDX"),  # an index carries its own exchange
        ("BMW.XETRA", "BMW.XETRA"),
        ("^TNX", "^TNX.US"),  # the three US-exchange index rows keep working
    ],
)
def test_ticker(symbol: str, ticker: str) -> None:
    assert eodhd_ticker(symbol) == ticker
