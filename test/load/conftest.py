"""Shared fixtures for the load tier (slice 187 D10).

``prod_shaped_db`` was slice 167's, defined inside
``test_167_data_status_nfr.py``. Slice 187 adds a second module that needs the
same fixture, so it moves here — pytest auto-discovers ``conftest.py`` fixtures,
so neither module imports it and 167's test is unchanged apart from losing the
definition.

**What these fixtures do and do not reproduce.** ``prod_shaped_db`` reproduces
the *row-count* shape that drives coverage and ``data_status`` reads: 12,000
symbols x 10 years, one bar per symbol-year. It does **not** reproduce
production's 3,371-chunk ``daily_ohlcv`` planning cost, and no affordable
fixture does — the D1/D2 measurements against prod are the evidence for that
dimension and live in the slice design, not here. ``dense_minute_db`` is the
other axis: one symbol with enough 1-minute bars to exceed the admission
ceiling, which is what a *request*-latency bound needs.

This module must never read the production DB URL;
``test_load_tier_never_references_prod_db_url`` globs every ``*.py`` in this
directory, including this one.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from psycopg_pool import ConnectionPool

from manta_trading.constants import (
    COVERAGE_BUCKET_INTERVAL,
    DAILY_COVERAGE_VIEW,
    GRANULARITY_SOURCE,
    MINUTE_COVERAGE_VIEW,
    Granularity,
)
from manta_trading.market.schema.migrations.minute import MINUTE_MIGRATIONS
from manta_trading.market.schema.runner import apply_migrations

SYMBOL_COUNT = 12_000
"""At least the production universe (11,625 symbols as of 2026-07)."""

YEAR_COUNT = 10

FIRST_YEAR = 2010
"""Preserved from slice 167's original definition — the extraction must not
change the seeded history's span."""

BARS_PER_YEAR = max(1, int(timedelta(days=365) / COVERAGE_BUCKET_INTERVAL))
"""Bars per symbol-year, derived from the coverage bucket width (slice 169).

**This fixture was width-blind before slice 169 and would have silently under-
tested the new width.** It seeded exactly one bar per symbol-year, so every bar
landed in its own coverage bucket at any width up to a year: 12,000 x 10 =
120,000 coverage rows at a 365-day bucket *and* at a 7-day bucket. Narrowing the
width would not have moved the measured cost at all, so
``test_167_data_status_nfr`` would have kept passing while saying nothing about
the shape actually shipped.

Deriving the bar count from the constant makes coverage rows scale the way
production's do — one bar per bucket per symbol-year, i.e. ~52 rows/symbol-year
at a 7-day width. That is what puts the fixture's coverage row count in the same
order as the prod-shaped measurement taken in slice 169 Task B (16.7 M daily
coverage rows over 12,040 symbols x 64.6 years).
"""


def symbol_name(i: int) -> str:
    return f"ZZLD{i:05d}"


def _apply_schema(url: str) -> None:
    with ConnectionPool(url, min_size=1, max_size=2) as pool:
        apply_migrations(pool, MINUTE_MIGRATIONS)


def _seed_prod_shape(url: str) -> None:
    """COPY instruments plus ``BARS_PER_YEAR`` minute and daily bars per symbol-year.

    All symbols share the same timestamps, so the raw rows land in a bounded
    number of hypertable chunks while the coverage caggs still materialize the
    symbols x buckets row count that drives the view's read cost.

    Bar spacing is one per ``COVERAGE_BUCKET_INTERVAL`` (slice 169), so each bar
    occupies its own coverage bucket and the cagg row count scales with the
    width the way production's does. See ``BARS_PER_YEAR``.
    """
    spacing = COVERAGE_BUCKET_INTERVAL
    minute_ts = [
        datetime(FIRST_YEAR + y, 1, 2, 14, 31, tzinfo=UTC) + spacing * b
        for y in range(YEAR_COUNT)
        for b in range(BARS_PER_YEAR)
    ]
    daily_ts = [
        datetime(FIRST_YEAR + y, 1, 2, 0, 0, tzinfo=UTC) + spacing * b
        for y in range(YEAR_COUNT)
        for b in range(BARS_PER_YEAR)
    ]

    with psycopg.connect(url) as conn:
        with conn.cursor() as cur:
            with cur.copy(
                "COPY instruments "
                "(canonical_id, symbol, asset_class, venue, "
                " trading_calendar_id, delisted_at_eodhd, "
                " eodhd_type, eodhd_exchange) FROM STDIN"
            ) as copy:
                for i in range(SYMBOL_COUNT):
                    sym = symbol_name(i)
                    copy.write_row(
                        (
                            f"EQ:{sym}",
                            sym,
                            "equity",
                            "US",
                            "NYSE",
                            False,
                            "Common Stock",
                            "US",
                        )
                    )

            for table, stamps in (
                ("minute_ohlcv", minute_ts),
                ("daily_ohlcv", daily_ts),
            ):
                with cur.copy(
                    f"COPY {table} "
                    "(time, symbol, open, high, low, close, volume) FROM STDIN"
                ) as copy:
                    for i in range(SYMBOL_COUNT):
                        sym = symbol_name(i)
                        for ts in stamps:
                            copy.write_row((ts, sym, 10.0, 10.0, 10.0, 10.0, 100))
        conn.commit()


def _refresh_coverage(url: str) -> None:
    """Materialize the coverage caggs over the full seeded history.

    NULL bounds (runbook R2a): an explicit window under two coverage buckets is
    rejected by the engine, and full history is what the fixture needs anyway.
    Parent before child — refreshing ``minute_coverage`` over an
    unmaterialized 4-hour cagg rolls up nothing (measured in slice 167 s7).
    """
    with psycopg.connect(url, autocommit=True) as conn:
        for view in (
            GRANULARITY_SOURCE[Granularity.H4],
            MINUTE_COVERAGE_VIEW,
            DAILY_COVERAGE_VIEW,
        ):
            conn.execute(f"CALL refresh_continuous_aggregate('{view}', NULL, NULL)")


@pytest.fixture
def prod_shaped_db(ephemeral_db: str) -> str:
    """Ephemeral DB at production row-count shape (slice 167)."""
    _apply_schema(ephemeral_db)
    _seed_prod_shape(ephemeral_db)
    _refresh_coverage(ephemeral_db)
    return ephemeral_db


# --- dense-minute fixture (slice 187 D10, assertion 2) -----------------------

DENSE_SYMBOL = "ZZDENSE"

DENSE_DAYS = 120

DENSE_BARS_PER_DAY = 960
"""Extended-hours 1-minute bars per day (04:00-20:00 UTC-ish window).

120 x 960 = 115,200 rows, which exceeds ``API_MAX_BARS_PER_REQUEST`` (75,000)
so a ``1m`` request can be issued *at* the admission ceiling rather than below
it. Breadth is not the point here — ``prod_shaped_db`` covers that; this
fixture is about a single response large enough for request latency to be a
real question.
"""

DENSE_START = datetime(2024, 1, 1, 4, 0, tzinfo=UTC)


@pytest.fixture
def dense_minute_db(ephemeral_db: str) -> str:
    """One symbol with ~115k dense 1-minute bars, seeded by ``COPY``.

    No cagg refresh: a ``1m`` request reads the raw hypertable, so the minute
    caggs are irrelevant to what assertion 2 measures and refreshing them would
    add minutes to the fixture for nothing.
    """
    _apply_schema(ephemeral_db)

    with psycopg.connect(ephemeral_db) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO instruments "
                "(canonical_id, symbol, asset_class, venue, "
                " trading_calendar_id, delisted_at_eodhd, "
                " eodhd_type, eodhd_exchange) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT DO NOTHING",
                (
                    f"EQ:{DENSE_SYMBOL}",
                    DENSE_SYMBOL,
                    "equity",
                    "US",
                    "NYSE",
                    False,
                    "Common Stock",
                    "US",
                ),
            )
            with cur.copy(
                "COPY minute_ohlcv "
                "(time, symbol, open, high, low, close, volume) FROM STDIN"
            ) as copy:
                for day in range(DENSE_DAYS):
                    day_start = DENSE_START + timedelta(days=day)
                    for minute in range(DENSE_BARS_PER_DAY):
                        copy.write_row(
                            (
                                day_start + timedelta(minutes=minute),
                                DENSE_SYMBOL,
                                10.0,
                                10.0,
                                10.0,
                                10.0,
                                100,
                            )
                        )
        conn.commit()
    return ephemeral_db
