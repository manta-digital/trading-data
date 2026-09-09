"""Shared fixtures for the load tier (slice 187 D10).

**Which machine these thresholds describe.** Every latency and throughput bound
in this tier was established on manta9000 — 32 cores, 125 GiB RAM — when the
test databases lived on that host alongside production. Slice 917 moved the test
databases to a dedicated machine (20 cores, 62 GiB, reached over a LAN), and the
thresholds were deliberately **not** re-derived: re-baselining was declined
because the numbers still serve their purpose as a regression signal.

The consequence is worth stating plainly: **a failure here on different hardware
is not automatically a regression.** Check what machine the tier ran against
before treating a breach as a defect. The runbook for the test cluster is
project-documents/user/runbooks/test-database-cluster.md.

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

from datetime import UTC, date, datetime, timedelta

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


# --- minute-session-mass fixture (slice 921 Task 5.8) ------------------------

MASS_SYMBOL_COUNT = 11_000
"""Symbols carrying bars in the judged session, matching the ~11k active
minute universe measured on production."""

MASS_BUCKETS_PER_SYMBOL = 2
"""4-hour cagg buckets a symbol occupies within one regular session.

A 13:30-20:00 UTC session intersects exactly two 4-hour buckets — the one
starting at 12:00 and the one starting at 16:00 — because the buckets are
aligned to the day, not to the session. 11,000 symbols x 2 buckets is ~22,000
cagg rows; the ~41k measured on production spans a wider active universe, and
what the NFR is about is the aggregate scan over a full session's rows, which
this reproduces at the right order of magnitude.
"""

MASS_SESSION_DATE = date(2026, 9, 8)
MASS_SESSION_OPEN = datetime(2026, 9, 8, 13, 30, tzinfo=UTC)
MASS_SESSION_CLOSE = datetime(2026, 9, 8, 20, 0, tzinfo=UTC)

MASS_BARS_PER_SYMBOL_PER_BUCKET = 90
"""Raw minute bars seeded per symbol per bucket, all inside the session.

11,000 x 2 x 90 = ~1.98M bars, the healthy session mass measured on
2026-08-27, and 5,077 per session-minute against the 2,500 floor. The count
matters because the check sums ``minute_count``: a fixture with the right ROW
count but a trivial bar count would exercise the scan without exercising the
aggregate the check actually reads, and would sit under the floor the check
judges against.
"""

MASS_BUCKET_STARTS_UTC = (12, 16)
"""Hour of each 4-hour bucket the session intersects.

Bars are seeded INSIDE ``[session_open, session_close)`` rather than at
offsets from the open: the cagg's buckets are aligned to the day, so
open + 4h * n walks straight out of the session window and the bars land in
buckets the mass query never reads.
"""


def _seed_session_mass_shape(url: str) -> None:
    """Seed one dense NYSE session: calendar, sessions, instruments, bars.

    ``prod_shaped_db`` cannot measure this NFR — it seeds one bar per symbol
    per 7-day bucket from 2010 and no ``trading_sessions`` at all, so the mass
    query's ``[session_open, session_close)`` window is empty against it, the
    read returns in milliseconds and the bound is never exercised. Hence this
    sibling fixture rather than an extension of that one: the two want
    opposite shapes (breadth of history vs. density inside one session).
    """
    with psycopg.connect(url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO trading_calendars "
                "(calendar_id, exchange_name, timezone, market_open, "
                " market_close, has_extended_hours) "
                "VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
                (
                    "NYSE",
                    "New York Stock Exchange",
                    "America/New_York",
                    "09:30",
                    "16:00",
                    True,
                ),
            )
            # A short run of sessions so the judged-session selection has real
            # candidates to choose between, with the dense one newest.
            for offset in range(5):
                session_date = MASS_SESSION_DATE - timedelta(days=offset)
                cur.execute(
                    "INSERT INTO trading_sessions "
                    "(calendar_id, session_date, session_open_utc, "
                    " session_close_utc) VALUES (%s, %s, %s, %s) "
                    "ON CONFLICT DO NOTHING",
                    (
                        "NYSE",
                        session_date,
                        MASS_SESSION_OPEN - timedelta(days=offset),
                        MASS_SESSION_CLOSE - timedelta(days=offset),
                    ),
                )

            with cur.copy(
                "COPY instruments "
                "(canonical_id, symbol, asset_class, venue, "
                " trading_calendar_id, delisted_at_eodhd, "
                " eodhd_type, eodhd_exchange) FROM STDIN"
            ) as copy:
                for i in range(MASS_SYMBOL_COUNT):
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

            # Bars inside the session window, spread across the two 4-hour
            # buckets it intersects. Each group starts at the later of the
            # bucket start and the session open, so every bar lands within
            # [session_open, session_close) — the window the mass query reads.
            with cur.copy(
                "COPY minute_ohlcv "
                "(time, symbol, open, high, low, close, volume) FROM STDIN"
            ) as copy:
                bases = [
                    max(
                        MASS_SESSION_OPEN.replace(hour=hour, minute=0),
                        MASS_SESSION_OPEN,
                    )
                    for hour in MASS_BUCKET_STARTS_UTC
                ]
                for i in range(MASS_SYMBOL_COUNT):
                    sym = symbol_name(i)
                    for base in bases:
                        for minute in range(MASS_BARS_PER_SYMBOL_PER_BUCKET):
                            copy.write_row(
                                (
                                    base + timedelta(minutes=minute),
                                    sym,
                                    10.0,
                                    10.0,
                                    10.0,
                                    10.0,
                                    100,
                                )
                            )
        conn.commit()


@pytest.fixture
def session_mass_db(ephemeral_db: str) -> str:
    """Ephemeral DB holding one production-shaped trading session.

    Refreshes only the 4-hour cagg — the mass check reads that view and
    nothing else, so materializing the coverage caggs would add minutes for
    nothing.
    """
    _apply_schema(ephemeral_db)
    _seed_session_mass_shape(ephemeral_db)
    with psycopg.connect(ephemeral_db, autocommit=True) as conn:
        view = GRANULARITY_SOURCE[Granularity.H4]
        conn.execute(f"CALL refresh_continuous_aggregate('{view}', NULL, NULL)")
    return ephemeral_db
