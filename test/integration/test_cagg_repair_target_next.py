"""``--target next`` against real ``_next`` caggs (slice 172).

Repair, verify and rebuild-coverage are driven through the CLI on a throwaway
database holding minutes over four 70-day windows, some of them marked. The
current objects are snapshotted around every step (success criterion 4), and
the readers are compared before 063 and after 063 plus a partial ``_next``
build (success criterion 3).
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock, patch

import psycopg
import pytest
from psycopg.rows import dict_row, tuple_row
from psycopg_pool import ConnectionPool
from typer.testing import CliRunner

from manta_trading.cli.app import app
from manta_trading.cli.commands.health import gather
from manta_trading.config import Settings
from manta_trading.constants import (
    GRANULARITY_SOURCE,
    MINUTE_CAGG_CHUNK_INTERVAL,
    MINUTE_CAGG_GRANULARITIES,
    MINUTE_COVERAGE_VIEW,
    CaggTarget,
    Granularity,
    OffTape,
    target_view_name,
)
from manta_trading.data.gaps.minute_coverage import (
    build_minute_coverage_index,
    compute_missing_minute_sessions,
)
from manta_trading.market.maintenance.cagg_parity import _epoch_grid_windows
from manta_trading.market.maintenance.cagg_repair import _rebuild_window
from manta_trading.market.schema.eligible_rollups_sql import (
    NEXT_COVERAGE_PARENT,
    NEXT_COVERAGE_VIEW,
    NEXT_ROLLUP_VIEWS,
)
from manta_trading.market.schema.migrations.minute import MINUTE_MIGRATIONS
from manta_trading.market.schema.runner import apply_migrations
from manta_trading.market.timescale_minute_db import TimescaleMinuteDataDB

_ID = "063_minute_eligible_rollups_next"
_INDEX = next(i for i, m in enumerate(MINUTE_MIGRATIONS) if m["id"] == _ID)
# 064 swaps the _next objects in, so these tests stop the chain at 063.
THROUGH_063 = MINUTE_MIGRATIONS[: _INDEX + 1]
_SYMBOL = "ZZNXT"
_FIRST_DAY = date(2025, 1, 2)
_DAYS = 240  # four 70-day windows
CURRENT_VIEWS = (
    *(GRANULARITY_SOURCE[g] for g in MINUTE_CAGG_GRANULARITIES),
    MINUTE_COVERAGE_VIEW,
)
NEXT_VIEWS = (*NEXT_ROLLUP_VIEWS, NEXT_COVERAGE_VIEW)
# Bounded refresh: NULL bounds walk 7-day coverage buckets from the epoch.
_REFRESH_SPAN = (datetime(2024, 12, 2, tzinfo=UTC), datetime(2026, 1, 5, tzinfo=UTC))
# The migration chain and four repair sweeps outrun the 30 s default.
pytestmark = pytest.mark.timeout(600)
# repair's recommended order: the coverage parent first.
BUILD_ORDER = (Granularity.H4, Granularity.H1, Granularity.M15, Granularity.M5)


def _apply(url: str, migrations: list[dict[str, Any]]) -> None:
    with ConnectionPool(url, min_size=1, max_size=2) as pool:
        apply_migrations(pool, migrations)


def _seed(url: str) -> None:
    """Six minutes a day on weekdays; every seventh day's third minute is
    all-marked, every fifth day's first minute is marked HIGH."""
    rows = []
    for offset in range(_DAYS):
        day = _FIRST_DAY + timedelta(days=offset)
        if day.weekday() >= 5:
            continue
        start = datetime(day.year, day.month, day.day, 14, 30, tzinfo=UTC)
        for minute in range(6):
            mark = 0
            if offset % 7 == 0 and minute == 2:
                mark = int(OffTape.ALL)
            elif offset % 5 == 0 and minute == 0:
                mark = int(OffTape.HIGH)
            high = 150 if mark else 101
            rows.append(
                (
                    start + timedelta(minutes=minute),
                    _SYMBOL,
                    100,
                    high,
                    99,
                    100.5,
                    10,
                    mark,
                )
            )
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


def _refresh(url: str, views: tuple[str, ...]) -> None:
    with psycopg.connect(url, autocommit=True) as conn:
        for view in views:
            conn.execute(
                "CALL refresh_continuous_aggregate(%s, %s, %s)", (view, *_REFRESH_SPAN)
            )


def _set_jobs(url: str, view: str, *, scheduled: bool) -> None:
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(
            "SELECT alter_job(job_id, scheduled => %s)"
            " FROM timescaledb_information.jobs WHERE hypertable_name = %s",
            (scheduled, view),
        )


@contextmanager
def _paused(url: str, view: str) -> Iterator[None]:
    _set_jobs(url, view, scheduled=False)
    try:
        yield
    finally:
        _set_jobs(url, view, scheduled=True)


def _cli(url: str, *args: str) -> Any:
    settings = MagicMock()
    settings.timescale_db_url = url
    settings.timescale_maintenance_url = url
    with (
        patch("manta_trading.cli.app.Settings", return_value=settings),
        patch("manta_trading.cli.app.setup_logging"),
    ):
        return CliRunner().invoke(app, ["data", "caggs", *args])


def _snapshot(url: str, views: tuple[str, ...]) -> dict[str, tuple[Any, ...]]:
    """Row count, policy scheduled flags and definition of each view."""
    out: dict[str, tuple[Any, ...]] = {}
    with psycopg.connect(url) as conn:
        for view in views:
            count = conn.execute(f"SELECT count(*) FROM {view}").fetchone()  # noqa: S608
            flags = conn.execute(
                "SELECT proc_name, scheduled FROM timescaledb_information.jobs"
                " WHERE hypertable_name = %s ORDER BY proc_name",
                (view,),
            ).fetchall()
            definition = conn.execute(
                "SELECT view_definition FROM"
                " timescaledb_information.continuous_aggregates WHERE view_name = %s",
                (view,),
            ).fetchone()
            out[view] = (count, tuple(flags), definition)
    return out


@pytest.fixture(scope="module")
def seeded_db(module_pre_swap_db: str) -> str:
    """Migrated, seeded, current objects refreshed; ``_next`` empty."""
    _seed(module_pre_swap_db)
    _refresh(module_pre_swap_db, CURRENT_VIEWS)
    return module_pre_swap_db


def _rebuilt_starts(output: str, view: str) -> list[str]:
    return re.findall(rf"{view} window \d+/\d+ (\d{{4}}-\d{{2}}-\d{{2}})\.\.", output)


def test_build_next_with_target_and_order(seeded_db: str) -> None:
    """The whole build, in the cutover's order, through the CLI. One test
    because each step needs the previous one."""
    url = seeded_db
    current_before = _snapshot(url, CURRENT_VIEWS)

    # verify --target next fails before the build.
    assert _cli(url, "verify", "--target", "next").exit_code == 2

    for granularity in BUILD_ORDER:
        view = target_view_name(GRANULARITY_SOURCE[granularity], CaggTarget.NEXT)
        with _paused(url, view):
            result = _cli(
                url,
                "repair",
                "--target",
                "next",
                "--order",
                "newest-first",
                "--granularity",
                granularity.value,
                "--assume-headroom-gb",
                "100",
            )
        assert result.exit_code == 0, result.output
        starts = _rebuilt_starts(result.output, view)
        assert len(starts) >= 3, result.output
        assert starts == sorted(starts, reverse=True), "newest window first"
        assert _snapshot(url, CURRENT_VIEWS) == current_before

    verified = _cli(url, "verify", "--target", "next", "--json")
    assert verified.exit_code == 0, verified.output
    assert all(view in verified.output for view in NEXT_ROLLUP_VIEWS)

    # Coverage refuses while its parent's refresh is paused, naming it.
    with _paused(url, NEXT_COVERAGE_VIEW), _paused(url, NEXT_COVERAGE_PARENT):
        refused = _cli(
            url, "rebuild-coverage", "--family", "minute", "--target", "next"
        )
    assert refused.exit_code == 2
    assert NEXT_COVERAGE_PARENT in refused.output

    with _paused(url, NEXT_COVERAGE_VIEW):
        rebuilt = _cli(
            url, "rebuild-coverage", "--family", "minute", "--target", "next"
        )
    assert rebuilt.exit_code == 0, rebuilt.output
    assert NEXT_COVERAGE_VIEW in rebuilt.output
    checked = _cli(
        url, "rebuild-coverage", "--family", "minute", "--target", "next", "--verify"
    )
    assert checked.exit_code == 0, checked.output
    assert _snapshot(url, CURRENT_VIEWS) == current_before

    # Same bar count as the current coverage; marks change no count.
    with psycopg.connect(url) as conn:
        bars = [
            conn.execute(f"SELECT sum(bars) FROM {view}").fetchone()  # noqa: S608
            for view in (MINUTE_COVERAGE_VIEW, NEXT_COVERAGE_VIEW)
        ]
    assert bars[0] == bars[1]

    status = _cli(url, "status", "--json")
    assert status.exit_code == 0, status.output
    assert all(view in status.output for view in NEXT_VIEWS)


# ---------------------------------------------------------------------------
# Readers before 063 and after 063 plus a partial _next build
# ---------------------------------------------------------------------------


def _readers(url: str) -> dict[str, Any]:
    window = (datetime(2025, 1, 1, tzinfo=UTC), datetime(2025, 12, 31, tzinfo=UTC))
    minute_db = TimescaleMinuteDataDB(url)
    try:
        bars = {
            g: minute_db.get_minute_data(_SYMBOL, *window, g).to_dict()
            for g in (Granularity.M5, Granularity.H4)
        }
    finally:
        minute_db.close()
    with psycopg.connect(url) as conn:
        status = conn.execute(
            "SELECT * FROM data_status WHERE symbol = %s ORDER BY 1, 2", (_SYMBOL,)
        ).fetchall()
        index = build_minute_coverage_index(conn)
        assert index is not None
        missing = compute_missing_minute_sessions(conn, _SYMBOL, index, *window)
        at = datetime(2025, 9, 1, tzinfo=UTC)
        health = [
            (c.name, c.ok, c.detail)
            for c in gather(conn, Settings(_env_file=None), now=lambda: at)
        ]
    return {
        "bars": bars,
        "data_status": status,
        "coverage_index": index,
        "missing": missing,
        "health": health,
    }


@pytest.fixture()
def pre_063_db(ephemeral_db: str) -> str:
    from kalshi_support.schema import apply_kalshi_track

    _apply(ephemeral_db, MINUTE_MIGRATIONS[:_INDEX])
    apply_kalshi_track(ephemeral_db)  # mt data health reads the kalshi schema
    _seed(ephemeral_db)
    _refresh(ephemeral_db, CURRENT_VIEWS)
    return ephemeral_db


def test_readers_unchanged_by_063_and_a_partial_build(pre_063_db: str) -> None:
    url = pre_063_db
    before = _readers(url)
    assert before["bars"][Granularity.M5]["high"], "the fixture must serve bars"

    _apply(url, THROUGH_063)
    view = target_view_name(GRANULARITY_SOURCE[Granularity.H4], CaggTarget.NEXT)
    with psycopg.connect(url, autocommit=True, row_factory=tuple_row) as conn:
        span = conn.execute(
            "SELECT min(time), max(time) FROM minute_ohlcv WHERE symbol = %s",
            (_SYMBOL,),
        ).fetchone()
        assert span is not None
        lo, hi = span
        newest = _epoch_grid_windows(lo, hi, MINUTE_CAGG_CHUNK_INTERVAL)[-1]
    with (
        _paused(url, view),
        psycopg.connect(url, autocommit=True, row_factory=dict_row) as conn,
    ):
        _rebuild_window(conn, view, *newest)
    with psycopg.connect(url) as conn:
        built = conn.execute(f"SELECT count(*) FROM {view}").fetchone()  # noqa: S608
    assert built is not None and built[0] > 0, "the partial build must write rows"

    after = _readers(url)
    assert after == before
    assert not any(v in name for v in NEXT_VIEWS for name, *_ in after["health"])
