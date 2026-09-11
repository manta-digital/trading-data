"""The calendar-based minute accounting against a real database.

Requires ``MT_TIMESCALE_TEST_URL``; ``migrated_db`` creates and drops its own
throwaway database. The migration chain seeds the NYSE calendar, so the test
reads session bounds from it rather than inventing them.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import psycopg

from manta_trading.data.gaps.minute_accounting import (
    compute_minute_accounting,
    render_minute_accounting,
    summary_line,
)
from manta_trading.data.quality.fetch_status import FetchStatus

CALENDAR = "NYSE"
DAYS = (date(2026, 3, 2), date(2026, 3, 3), date(2026, 3, 4))
TRADED = "ACCA"  # bars on day 1, UNKNOWN row on day 2, no trades on day 3
UNTRACKED = "ACCB"  # traded every day, no bars, no gap rows
INDEX = "ACCX"  # index instrument: never part of the universe
DELISTED = "ACCZ"  # delisted: never part of the universe


def _daily(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=UTC)


def _sessions(conn: psycopg.Connection) -> dict[date, tuple[datetime, datetime]]:
    """The calendar's own bounds for DAYS (14:30–21:00 UTC before DST)."""
    rows = conn.execute(
        "SELECT session_date, session_open_utc, session_close_utc "
        "FROM trading_sessions WHERE calendar_id = %s AND session_date = ANY(%s)",
        (CALENDAR, list(DAYS)),
    ).fetchall()
    bounds = {row[0]: (row[1], row[2]) for row in rows}
    assert set(bounds) == set(DAYS), "the migration seeds the NYSE calendar"
    return bounds


def _instrument(
    cur: psycopg.Cursor, symbol: str, eodhd_type: str, delisted: date | None
) -> None:
    cur.execute(
        "INSERT INTO instruments (canonical_id, symbol, asset_class, venue, "
        " trading_calendar_id, delisted_at_eodhd, eodhd_type, eodhd_exchange, "
        " first_listing_date, delisted_date) "
        "VALUES (%s,%s,%s,%s,%s,FALSE,%s,'US',%s,%s) ON CONFLICT DO NOTHING",
        (
            f"EQ:{symbol}",
            symbol,
            "index" if eodhd_type == "INDEX" else "equity",
            "US",
            CALENDAR,
            eodhd_type,
            DAYS[0],
            delisted,
        ),
    )


def _seed(conn: psycopg.Connection) -> datetime:
    """Seed the fixture; returns the ``now`` at which exactly DAYS have closed."""
    bounds = _sessions(conn)
    with conn.cursor() as cur:
        _instrument(cur, TRADED, "Common Stock", None)
        _instrument(cur, UNTRACKED, "ETF", None)
        _instrument(cur, INDEX, "INDEX", None)
        _instrument(cur, DELISTED, "Common Stock", DAYS[0])
        # TRADED day 1: bars inside the session.
        for minute in range(1, 60, 10):
            cur.execute(
                "INSERT INTO minute_ohlcv (time, symbol, open, high, low, close, "
                " volume) VALUES (%s,%s,10,10,10,10,100) ON CONFLICT DO NOTHING",
                (bounds[DAYS[0]][0] + timedelta(minutes=minute), TRADED),
            )
        # Daily bars: TRADED traded days 1-2 and printed zero volume on day 3;
        # UNTRACKED traded all three; the excluded ones traded too (irrelevant).
        for symbol, day, volume in (
            (TRADED, DAYS[0], 1000),
            (TRADED, DAYS[1], 1000),
            (TRADED, DAYS[2], 0),
            *((UNTRACKED, d, 500) for d in DAYS),
            *((INDEX, d, 500) for d in DAYS),
            *((DELISTED, d, 500) for d in DAYS),
        ):
            cur.execute(
                "INSERT INTO daily_ohlcv (time, symbol, open, high, low, close, "
                " volume) VALUES (%s,%s,10,10,10,10,%s) ON CONFLICT DO NOTHING",
                (_daily(day), symbol, volume),
            )
        # TRADED day 2: the gap table knows and is still asking.
        cur.execute(
            "INSERT INTO data_gaps (symbol, granularity, gap_start, gap_end, "
            " fetch_status, attempt_count) VALUES (%s,'minute',%s,%s,%s,0)",
            (TRADED, bounds[DAYS[1]][0], bounds[DAYS[1]][1], str(FetchStatus.UNKNOWN)),
        )
    conn.commit()
    return bounds[DAYS[-1]][1] + timedelta(hours=1)


def test_accounting_classifies_every_expected_session(migrated_db: str) -> None:
    with psycopg.connect(migrated_db) as conn:
        now = _seed(conn)
    with psycopg.connect(migrated_db, autocommit=True) as conn:
        conn.execute(
            "CALL refresh_continuous_aggregate('minute_4hour_ohlcv', NULL, NULL)"
        )
    with psycopg.connect(migrated_db) as conn:
        rows = compute_minute_accounting(conn, now=now)

    assert [r.year for r in rows] == [2026, None]
    total = rows[-1]
    # Two symbols in the universe × three closed sessions; the index and the
    # delisted symbol are not counted at all.
    assert total.expected == 6
    assert total.covered == 1  # TRADED day 1
    assert total.no_trade == 1  # TRADED day 3: zero daily volume
    assert total.unknown == 1  # TRADED day 2
    assert total.untracked == 3  # UNTRACKED: traded, no bars, no rows
    assert total.hole == 0 and total.exhausted == 0
    assert total.missing == 5 and total.fillable == 4
    assert "6" in summary_line(rows) and "total" in render_minute_accounting(rows)


def test_sessions_after_now_are_not_expected(migrated_db: str) -> None:
    """A session that has not closed yet is not missing: at the first day's
    open nothing has closed since listing, so nothing is expected."""
    with psycopg.connect(migrated_db) as conn:
        _seed(conn)
        first_open = _sessions(conn)[DAYS[0]][0]
        rows = compute_minute_accounting(conn, now=first_open)
    assert rows == [] or rows[-1].expected == 0


def test_an_empty_calendar_accounts_nothing(migrated_db: str) -> None:
    with psycopg.connect(migrated_db) as conn:
        conn.execute("DELETE FROM trading_sessions")
        assert compute_minute_accounting(conn, now=datetime.now(UTC)) == []
    assert "empty" in render_minute_accounting([])
