"""Shared fixtures for universe unit tests."""

from __future__ import annotations

import pytest


def _make_row(
    code: str,
    type_: str,
    country: str = "USA",
    exchange: str = "US",
    currency: str = "USD",
    name: str = "",
) -> dict:
    return {
        "Code": code,
        "Name": name or code,
        "Country": country,
        "Exchange": exchange,
        "Currency": currency,
        "Type": type_,
    }


@pytest.fixture()
def eodhd_us_response() -> list[dict]:
    """≥100 active US rows covering all three kept types plus one filtered type."""
    rows: list[dict] = []
    # Common Stock — 60 rows
    for i in range(60):
        rows.append(_make_row(f"CS{i:03d}", "Common Stock"))
    # ETF — 20 rows
    for i in range(20):
        rows.append(_make_row(f"ETF{i:02d}", "ETF"))
    # INDEX — 5 rows (active US list won't normally have these, but cover the type)
    for i in range(5):
        rows.append(_make_row(f"IDX{i}", "INDEX"))
    # Mutual Fund — 5 rows (should be filtered out)
    for i in range(5):
        rows.append(_make_row(f"MF{i}", "Mutual Fund"))
    return rows


@pytest.fixture()
def eodhd_delisted_response() -> list[dict]:
    """Small list of delisted US symbols."""
    return [
        _make_row("DL001", "Common Stock"),
        _make_row("DL002", "ETF"),
    ]


@pytest.fixture()
def eodhd_indx_response() -> list[dict]:
    """INDX list with mix of USA and non-USA countries."""
    return [
        _make_row("SPX", "INDEX", country="USA", exchange="INDX"),
        _make_row("NDX", "INDEX", country="USA", exchange="INDX"),
        _make_row("FTSE", "INDEX", country="GBR", exchange="INDX"),
        _make_row("DAX", "INDEX", country="DEU", exchange="INDX"),
    ]


@pytest.fixture()
def finnhub_profile_aapl() -> dict:
    return {
        "country": "US",
        "currency": "USD",
        "exchange": "NASDAQ NMS - GLOBAL MARKET",
        "ipo": "1980-12-12",
        "name": "Apple Inc",
        "ticker": "AAPL",
    }


@pytest.fixture()
def finnhub_profile_unknown() -> dict:
    return {
        "country": "",
        "currency": "",
        "exchange": "",
        "ipo": "",
        "name": "",
        "ticker": "",
    }
