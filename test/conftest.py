"""Shared test fixtures for database availability."""

from __future__ import annotations

import os

import pytest


@pytest.fixture
def market_db_url() -> str:
    """PostgreSQL connection URL for the market (daily OHLCV) database.

    Reads from MT_MARKET_DB_URL environment variable.
    Skips the test if not set.
    """
    url = os.environ.get("MT_MARKET_DB_URL")
    if not url:
        pytest.skip("MT_MARKET_DB_URL not set")
    return url


@pytest.fixture
def timescale_db_url() -> str:
    """PostgreSQL connection URL for the TimescaleDB (minute data) database.

    Reads from MT_TIMESCALE_DB_URL environment variable.
    Skips the test if not set.
    """
    url = os.environ.get("MT_TIMESCALE_DB_URL")
    if not url:
        pytest.skip("MT_TIMESCALE_DB_URL not set")
    return url
