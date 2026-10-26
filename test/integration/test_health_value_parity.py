"""The ``value parity`` health check on a real database (slice 173).

The chain runs through 064, so ``minute_5min_ohlcv`` holds eligible values.
Hand-written minutes with marks on one ET day; the session is built directly,
since which session is judged is ``select_judged_session``'s business.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import psycopg
import pytest

from manta_trading.cli.commands.health_value_parity import (
    check_value_parity,
    fetch_value_parity,
)
from manta_trading.cli.commands.minute_session_mass import TradingSessionBounds
from manta_trading.constants import GRANULARITY_SOURCE, Granularity, OffTape

_5M = GRANULARITY_SOURCE[Granularity.M5]
_OPEN = datetime(2026, 9, 8, 13, 30, tzinfo=UTC)
SESSION = TradingSessionBounds(_OPEN, _OPEN + timedelta(hours=6, minutes=30))


def _minutes(symbol: str, marks: dict[int, int]) -> list[tuple[object, ...]]:
    """Twenty minutes from the open; ``marks`` maps a minute to its mask."""
    rows = []
    for offset in range(20):
        mark = marks.get(offset, 0)
        high = "150" if mark else "101"
        stamp = _OPEN + timedelta(minutes=offset)
        rows.append((stamp, symbol, "100", high, "99", "100.5", 10, mark))
    return rows


def _write(url: str, rows: list[tuple[object, ...]]) -> None:
    with psycopg.connect(url, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO minute_ohlcv (time, symbol, open, high, low, close,"
                " volume, off_tape) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                rows,
            )
        conn.execute(f"CALL refresh_continuous_aggregate('{_5M}', NULL, NULL)")


def _sample(url: str):  # type: ignore[no-untyped-def]
    with psycopg.connect(url) as conn:
        return fetch_value_parity(conn, SESSION)


@pytest.fixture()
def marked_db(migrated_db: str) -> str:
    """BBB has three marked minutes (one all-marked bucket), AAA two."""
    all_marked = {5 + i: int(OffTape.ALL) for i in range(5)}
    _write(
        migrated_db,
        [
            *_minutes("AAA", {0: int(OffTape.HIGH), 12: int(OffTape.HIGH)}),
            *_minutes(
                "BBB", {1: int(OffTape.HIGH), **dict(list(all_marked.items())[:2])}
            ),
            *_minutes("CCC", {}),
        ],
    )
    return migrated_db


def test_passes_when_the_rollup_matches(marked_db: str) -> None:
    sample = _sample(marked_db)
    check = check_value_parity(sample)
    assert check.ok, check.detail
    assert sample.symbol == "BBB"
    assert sample.marked == 3
    assert len(sample.pairs) == 4


def test_an_all_marked_bucket_is_compared(migrated_db: str) -> None:
    all_marked = {5 + i: int(OffTape.ALL) for i in range(5)}
    _write(migrated_db, _minutes("DDD", all_marked))
    sample = _sample(migrated_db)
    nulls = [p for p in sample.pairs if p.stored is not None and p.stored[0] is None]
    assert len(nulls) == 1
    assert nulls[0].stored == nulls[0].recomputed
    assert check_value_parity(sample).ok


def test_fails_naming_the_bucket_when_a_rollup_row_differs(marked_db: str) -> None:
    bucket = _OPEN + timedelta(minutes=10)
    with psycopg.connect(marked_db, autocommit=True) as conn:
        row = conn.execute(
            "SELECT materialization_hypertable_schema, materialization_hypertable_name"
            " FROM timescaledb_information.continuous_aggregates WHERE view_name = %s",
            (_5M,),
        ).fetchone()
        assert row is not None
        conn.execute(
            f'UPDATE "{row[0]}"."{row[1]}" SET high = high + 1'  # noqa: S608
            " WHERE symbol = 'BBB' AND time_bucket = %s",
            (bucket,),
        )
    check = check_value_parity(_sample(marked_db))
    assert not check.ok
    assert f"bucket {bucket.isoformat()} differs in high" in check.detail


def test_fails_when_no_minute_of_the_day_is_marked(migrated_db: str) -> None:
    _write(migrated_db, _minutes("CCC", {}))
    sample = _sample(migrated_db)
    assert sample.symbol is None
    check = check_value_parity(sample)
    assert not check.ok
    assert "no marked minute" in check.detail


def test_ties_go_to_the_first_symbol(migrated_db: str) -> None:
    _write(
        migrated_db,
        [
            *_minutes("ZZZ", {3: int(OffTape.LOW)}),
            *_minutes("MMM", {4: int(OffTape.LOW)}),
        ],
    )
    assert _sample(migrated_db).symbol == "MMM"
