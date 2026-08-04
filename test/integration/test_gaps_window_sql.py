"""The gaps window predicate, executed against a real database.

Requires MT_TIMESCALE_DB_URL. Seeds and removes its own rows.

These tests exist because the unit tests could not have caught the defect they
guard. `test_gaps.py` mocks the cursor and asserts on the *SQL text*, so
`gap_start < NULL` was never evaluated by Postgres — the one-sided-window bug
(every `?start=` or `?end=` request silently returning zero gaps) passed a green
suite for as long as it existed. This is the journal's 20260725 rule applied to
a WHERE clause: assert the rendered artifact against its real consumer, not one
side of the transformation.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, date, datetime

import psycopg
import pytest

from manta_trading.api_server.routes.gaps import (
    _GAPS_SQL,
    _window_end_utc,
    _window_start_utc,
)

_TIMESCALE_URL = os.environ.get("MT_TIMESCALE_DB_URL", "")

pytestmark = pytest.mark.skipif(
    not _TIMESCALE_URL,
    reason="MT_TIMESCALE_DB_URL not set",
)

_TEST_SYMBOL = "ZZZGAPWIN_186"

# One gap per month, Feb–Apr 2024, each spanning a single day.
_GAPS = [
    (datetime(2024, 2, 10, tzinfo=UTC), datetime(2024, 2, 11, tzinfo=UTC)),
    (datetime(2024, 3, 10, tzinfo=UTC), datetime(2024, 3, 11, tzinfo=UTC)),
    (datetime(2024, 4, 10, tzinfo=UTC), datetime(2024, 4, 11, tzinfo=UTC)),
]


@pytest.fixture
def seeded_conn() -> Iterator[psycopg.Connection]:
    with psycopg.connect(_TIMESCALE_URL) as conn:
        conn.execute("SET statement_timeout = '20s'")
        conn.execute("DELETE FROM data_gaps WHERE symbol = %s", (_TEST_SYMBOL,))
        for gap_start, gap_end in _GAPS:
            conn.execute(
                "INSERT INTO data_gaps (symbol, granularity, gap_start, gap_end,"
                " fetch_status, attempt_count)"
                " VALUES (%s, 'minute', %s, %s, 'UNKNOWN', 0)",
                (_TEST_SYMBOL, gap_start, gap_end),
            )
        conn.commit()
        try:
            yield conn
        finally:
            conn.execute("DELETE FROM data_gaps WHERE symbol = %s", (_TEST_SYMBOL,))
            conn.commit()


def _query(
    conn: psycopg.Connection,
    *,
    granularity: str | None = None,
    start: date | None = None,
    end: date | None = None,
) -> int:
    """Run the route's real SQL with the route's real parameter shape."""
    rows = conn.execute(
        _GAPS_SQL,
        (
            _TEST_SYMBOL,
            granularity,
            granularity,
            _window_end_utc(end) if end is not None else None,
            _window_start_utc(start) if start is not None else None,
        ),
    ).fetchall()
    return len(rows)


def test_no_filters_returns_every_gap(seeded_conn: psycopg.Connection) -> None:
    assert _query(seeded_conn) == 3


def test_start_only_is_an_open_ended_window(seeded_conn: psycopg.Connection) -> None:
    """The regression this file exists for.

    A one-sided window used to run the two-sided query with the other bound as
    NULL, and returned zero rows for every symbol and every date — a confident
    "no gaps" from the endpoint whose entire job is reporting gaps.
    """
    assert _query(seeded_conn, start=date(2024, 3, 1)) == 2
    assert _query(seeded_conn, start=date(1990, 1, 1)) == 3
    assert _query(seeded_conn, start=date(2030, 1, 1)) == 0


def test_end_only_is_an_open_ended_window(seeded_conn: psycopg.Connection) -> None:
    assert _query(seeded_conn, end=date(2024, 3, 31)) == 2
    assert _query(seeded_conn, end=date(2030, 1, 1)) == 3
    assert _query(seeded_conn, end=date(1990, 1, 1)) == 0


def test_both_bounds_select_the_enclosed_gap(seeded_conn: psycopg.Connection) -> None:
    assert _query(seeded_conn, start=date(2024, 3, 1), end=date(2024, 3, 31)) == 1


def test_end_date_is_inclusive(seeded_conn: psycopg.Connection) -> None:
    """A gap starting on the last requested day must be returned.

    Previously ``end`` became midnight *of* the end date, so ``gap_start < end``
    excluded anything beginning that day. Confirmed on prod against SPY's
    2004-01-01T00:00Z gap before the fix.
    """
    assert _query(seeded_conn, start=date(2024, 3, 1), end=date(2024, 3, 10)) == 1
    assert _query(seeded_conn, start=date(2024, 3, 1), end=date(2024, 3, 9)) == 0


def test_start_date_is_inclusive(seeded_conn: psycopg.Connection) -> None:
    """The gap spanning 03-10 → 03-11 overlaps a window starting on 03-10."""
    assert _query(seeded_conn, start=date(2024, 3, 10), end=date(2024, 3, 31)) == 1
    assert _query(seeded_conn, start=date(2024, 3, 11), end=date(2024, 3, 31)) == 0


def test_granularity_filter_composes_with_a_one_sided_window(
    seeded_conn: psycopg.Connection,
) -> None:
    """The combination that was doubly broken: filter plus a single bound."""
    assert _query(seeded_conn, granularity="minute", start=date(2024, 3, 1)) == 2
    assert _query(seeded_conn, granularity="daily", start=date(2024, 3, 1)) == 0
    assert _query(seeded_conn, granularity="minute") == 3
