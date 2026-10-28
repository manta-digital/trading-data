"""Migration 064 swaps the eligible ``_next`` minute caggs in (slice 173).

Cold start, a populated database holding both sets, a rerun after a failure
between the renames and the drops, a rerun on a finished swap, a reader that
sees no missing name across the swap, and the readers' counts before and after.
"""

from __future__ import annotations

import threading
from datetime import UTC, date, datetime, timedelta
from typing import Any
from unittest.mock import patch

import psycopg
import pytest
from psycopg_pool import ConnectionPool

from manta_trading.constants import (
    GRANULARITY_SOURCE,
    MINUTE_CAGG_GRANULARITIES,
    MINUTE_COVERAGE_VIEW,
    Granularity,
    OffTape,
)
from manta_trading.data.gaps.minute_accounting import compute_minute_accounting
from manta_trading.data.gaps.minute_coverage import build_minute_coverage_index
from manta_trading.data.maintenance.status_queries import (
    fetch_all_health_counts_with_freshness,
)
from manta_trading.market.schema.eligible_swap_sql import SWAP_NAMES
from manta_trading.market.schema.migrations import minute as minute_migrations
from manta_trading.market.schema.migrations.minute import MINUTE_MIGRATIONS
from manta_trading.market.schema.runner import apply_migrations

_ID = "064_minute_eligible_rollup_swap"
_SYMBOL = "ZZSWP"
_FIRST_DAY = date(2025, 3, 3)
_DAYS = 30
_REFRESH_SPAN = (datetime(2025, 2, 3, tzinfo=UTC), datetime(2025, 5, 5, tzinfo=UTC))
_REFRESH = "policy_refresh_continuous_aggregate"
_COLUMNSTORE = "policy_compression"
ROLLUPS = tuple(GRANULARITY_SOURCE[g] for g in MINUTE_CAGG_GRANULARITIES)
H4 = GRANULARITY_SOURCE[Granularity.H4]
pytestmark = pytest.mark.timeout(300)


def _apply(url: str, migrations: list[dict[str, Any]] = MINUTE_MIGRATIONS) -> None:
    with ConnectionPool(url, min_size=1, max_size=2) as pool:
        apply_migrations(pool, migrations)


def _seed(url: str) -> None:
    """Six minutes a weekday; every third day's third minute is all-marked,
    every fourth day's first minute is marked HIGH with a spike."""
    rows = []
    for offset in range(_DAYS):
        day = _FIRST_DAY + timedelta(days=offset)
        if day.weekday() >= 5:
            continue
        start = datetime(day.year, day.month, day.day, 14, 30, tzinfo=UTC)
        for minute in range(6):
            mark = 0
            if offset % 3 == 0 and minute == 2:
                mark = int(OffTape.ALL)
            elif offset % 4 == 0 and minute == 0:
                mark = int(OffTape.HIGH)
            high = 150 if mark else 101
            row = (start + timedelta(minutes=minute), _SYMBOL, 100, high, 99, 100.5)
            rows.append((*row, 10, mark))
    with psycopg.connect(url) as conn:
        conn.execute(
            "INSERT INTO instruments (canonical_id, symbol, asset_class, venue,"
            " trading_calendar_id, delisted_at_eodhd, eodhd_type, eodhd_exchange)"
            " VALUES (%s, %s, 'equity', 'US', 'NYSE', FALSE, 'Common Stock', 'US')",
            (f"EQ:{_SYMBOL}", _SYMBOL),
        )
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO minute_ohlcv (time, symbol, open, high, low, close,"
                " volume, off_tape) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                rows,
            )


def _refresh(url: str, views: list[str]) -> None:
    with psycopg.connect(url, autocommit=True) as conn:
        for view in views:
            conn.execute(
                "CALL refresh_continuous_aggregate(%s, %s, %s)", (view, *_REFRESH_SPAN)
            )


def _relations(conn: psycopg.Connection[Any], names: list[str]) -> set[str]:
    rows = conn.execute(
        "SELECT relname FROM pg_class WHERE relname = ANY(%s)", (names,)
    ).fetchall()
    return {row[0] for row in rows}


def _rows(conn: psycopg.Connection[Any], view: str) -> list[tuple[Any, ...]]:
    return conn.execute(f"SELECT * FROM {view} ORDER BY 1, 2").fetchall()  # noqa: S608


def _definition(conn: psycopg.Connection[Any], view: str) -> str:
    row = conn.execute(
        "SELECT view_definition FROM timescaledb_information.continuous_aggregates"
        " WHERE view_name = %s",
        (view,),
    ).fetchone()
    assert row is not None, view
    return str(row[0])


def _jobs(conn: psycopg.Connection[Any], view: str) -> list[str]:
    rows = conn.execute(
        "SELECT proc_name FROM timescaledb_information.jobs"
        " WHERE hypertable_name = %s ORDER BY proc_name",
        (view,),
    ).fetchall()
    return [row[0] for row in rows]


def _leftovers(conn: psycopg.Connection[Any]) -> set[str]:
    return _relations(conn, [name for n in SWAP_NAMES for name in (n.next, n.prev)])


def _assert_swapped(conn: psycopg.Connection[Any]) -> None:
    assert _relations(conn, [n.current for n in SWAP_NAMES]) == {
        n.current for n in SWAP_NAMES
    }
    assert _leftovers(conn) == set()
    for view in ROLLUPS:
        assert "off_tape" in _definition(conn, view), view
        assert _jobs(conn, view) == [_COLUMNSTORE, _REFRESH], view
    assert _jobs(conn, MINUTE_COVERAGE_VIEW) == [_REFRESH]
    assert H4 in _definition(conn, MINUTE_COVERAGE_VIEW)
    conn.execute("SELECT count(*) FROM data_status").fetchone()


def _readers(url: str) -> dict[str, Any]:
    """What accounting, ``data_status``, ``/api/v1/status`` and the coverage
    index read, at a fixed ``now``."""
    with psycopg.connect(url) as conn:
        status = conn.execute("SELECT * FROM data_status ORDER BY 1, 2").fetchall()
        counts, _ = fetch_all_health_counts_with_freshness(conn)
        accounting = compute_minute_accounting(
            conn, now=datetime(2025, 6, 1, tzinfo=UTC)
        )
        index = build_minute_coverage_index(conn)
    return {
        "data_status": status,
        "health_counts": counts,
        "accounting": accounting,
        "coverage_index": index,
    }


@pytest.fixture()
def both_sets_db(pre_swap_db: str) -> str:
    """Through 063, seeded, current and ``_next`` sets refreshed."""
    _seed(pre_swap_db)
    _refresh(pre_swap_db, [*ROLLUPS, *(n.next for n in SWAP_NAMES[:-1])])
    _refresh(pre_swap_db, [MINUTE_COVERAGE_VIEW, SWAP_NAMES[-1].next])
    return pre_swap_db


def test_cold_start_ends_with_eligible_current_names(migrated_db: str) -> None:
    with psycopg.connect(migrated_db) as conn:
        _assert_swapped(conn)


def test_populated_current_names_serve_the_former_next_rows(
    both_sets_db: str,
) -> None:
    url = both_sets_db
    with psycopg.connect(url) as conn:
        expected = {n.current: _rows(conn, n.next) for n in SWAP_NAMES}
        raw = {n.current: _rows(conn, n.current) for n in SWAP_NAMES}
    assert all(expected.values())
    # The seed's marks make the eligible rollups differ from the raw ones.
    assert expected[GRANULARITY_SOURCE[Granularity.M5]] != raw[
        GRANULARITY_SOURCE[Granularity.M5]
    ]
    _apply(url)
    with psycopg.connect(url) as conn:
        _assert_swapped(conn)
        assert {view: _rows(conn, view) for view in expected} == expected


def test_readers_count_the_same_before_and_after(both_sets_db: str) -> None:
    before = _readers(both_sets_db)
    assert before["data_status"] and before["coverage_index"]
    _apply(both_sets_db)
    assert _readers(both_sets_db) == before


def test_rerun_after_a_failure_before_the_drops_converges(both_sets_db: str) -> None:
    url = both_sets_db
    with (
        patch.object(
            minute_migrations,
            "swap_drop_statements",
            side_effect=RuntimeError("killed before the drops"),
        ),
        pytest.raises(RuntimeError, match="killed before the drops"),
    ):
        _apply(url)
    with psycopg.connect(url) as conn:
        assert _leftovers(conn) == {n.prev for n in SWAP_NAMES}
        applied = conn.execute(
            "SELECT 1 FROM schema_migrations WHERE migration_id = %s", (_ID,)
        ).fetchone()
        assert applied is None
    _apply(url)
    with psycopg.connect(url) as conn:
        _assert_swapped(conn)


def test_rerun_on_a_finished_swap_changes_nothing(migrated_db: str) -> None:
    with psycopg.connect(migrated_db) as conn:
        before = {n.current: _definition(conn, n.current) for n in SWAP_NAMES}
    with psycopg.connect(migrated_db, autocommit=True) as conn:
        minute_migrations._swap_eligible_rollups(conn)
    with psycopg.connect(migrated_db) as conn:
        _assert_swapped(conn)
        assert {n.current: _definition(conn, n.current) for n in SWAP_NAMES} == before


def test_a_reader_sees_no_missing_name_across_the_swap(both_sets_db: str) -> None:
    url = both_sets_db
    stop = threading.Event()
    errors: list[BaseException] = []
    reads = 0

    def read() -> None:
        nonlocal reads
        with psycopg.connect(url, autocommit=True) as conn:
            while not stop.is_set():
                try:
                    conn.execute("SELECT count(*) FROM data_status").fetchone()
                    conn.execute(f"SELECT count(*) FROM {H4}").fetchone()  # noqa: S608
                except psycopg.Error as exc:
                    # Recorded for the assertion below; the reader keeps going.
                    errors.append(exc)
                reads += 1

    reader = threading.Thread(target=read)
    reader.start()
    try:
        _apply(url)
    finally:
        stop.set()
        reader.join(timeout=30)
    assert reads > 0
    assert errors == []
