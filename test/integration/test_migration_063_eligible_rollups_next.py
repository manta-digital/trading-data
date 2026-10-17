"""Migration 063 builds the eligible ``_next`` minute rollups (slice 172).

Catalog tests (cold start, populated, partial rerun), then value tests: hand
written minutes with explicit marks for the edge buckets, and 171's real
off-tape fixture days, which go in through ``write_minute_data_bulk`` and so
carry the marks the insert path computes (``mark_days``).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pandas as pd
import psycopg
import pytest
from psycopg_pool import ConnectionPool

from manta_trading.constants import (
    GRANULARITY_SOURCE,
    MINUTE_CAGG_CHUNK_INTERVAL,
    MINUTE_CAGG_GRANULARITIES,
    MINUTE_COVERAGE_VIEW,
    OFF_TAPE_DAY_TZ,
    CaggTarget,
    Granularity,
    OffTape,
    PrintMode,
    target_view_name,
)
from manta_trading.market.schema.eligible_rollups_sql import (
    NEXT_COVERAGE_VIEW,
    NEXT_ROLLUP_VIEWS,
)
from manta_trading.market.schema.migrations.minute import MINUTE_MIGRATIONS
from manta_trading.market.schema.runner import apply_migrations
from manta_trading.market.timescale_minute_db import TimescaleMinuteDataDB

_ID = "063_minute_eligible_rollups_next"
_INDEX = next(i for i, m in enumerate(MINUTE_MIGRATIONS) if m["id"] == _ID)
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "off_tape"
PRICES = ["open", "high", "low", "close"]
CURRENT_VIEWS = (
    *(GRANULARITY_SOURCE[g] for g in MINUTE_CAGG_GRANULARITIES),
    MINUTE_COVERAGE_VIEW,
)
NEXT_VIEWS = (*NEXT_ROLLUP_VIEWS, NEXT_COVERAGE_VIEW)
NEXT_5M = target_view_name(GRANULARITY_SOURCE[Granularity.M5], CaggTarget.NEXT)
NEXT_1H = target_view_name(GRANULARITY_SOURCE[Granularity.H1], CaggTarget.NEXT)
CURRENT_5M = GRANULARITY_SOURCE[Granularity.M5]
WINDOW = (datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 12, 31, tzinfo=UTC))
_REFRESH = "policy_refresh_continuous_aggregate"
_COLUMNSTORE = "policy_compression"


def _apply(url: str, migrations: list[dict]) -> None:
    with ConnectionPool(url, min_size=1, max_size=2) as pool:
        apply_migrations(pool, migrations)


def _refresh(url: str, views: tuple[str, ...]) -> None:
    with psycopg.connect(url, autocommit=True) as conn:
        for view in views:
            conn.execute(f"CALL refresh_continuous_aggregate('{view}', NULL, NULL)")


def _jobs(conn: psycopg.Connection, view: str) -> list[str]:
    rows = conn.execute(
        "SELECT proc_name FROM timescaledb_information.jobs"
        " WHERE hypertable_name = %s ORDER BY proc_name",
        (view,),
    ).fetchall()
    return [row[0] for row in rows]


def _chunk_interval(conn: psycopg.Connection, view: str) -> timedelta:
    row = conn.execute(
        "SELECT d.time_interval FROM timescaledb_information.continuous_aggregates c"
        " JOIN timescaledb_information.dimensions d"
        "   ON d.hypertable_name = c.materialization_hypertable_name"
        " WHERE c.view_name = %s",
        (view,),
    ).fetchone()
    assert row is not None, view
    return row[0]


def _count(conn: psycopg.Connection, view: str) -> int:
    row = conn.execute(f"SELECT count(*) FROM {view}").fetchone()  # noqa: S608
    assert row is not None
    return int(row[0])


def _definition(conn: psycopg.Connection, view: str) -> str:
    row = conn.execute(
        "SELECT view_definition FROM timescaledb_information.continuous_aggregates"
        " WHERE view_name = %s",
        (view,),
    ).fetchone()
    assert row is not None, view
    return str(row[0])


def _load(name: str) -> pd.DataFrame:
    bars = pd.read_csv(FIXTURES / f"{name}.csv")
    bars["time"] = pd.to_datetime(bars["time"], utc=True)
    return bars


def _write_fixtures(url: str, names: list[str]) -> None:
    minute_db = TimescaleMinuteDataDB(url)
    try:
        for name in names:
            bars = _load(name)
            frame = bars.set_index("time")[[*PRICES, "volume"]]
            assert minute_db.write_minute_data_bulk(bars["symbol"].iloc[0], frame)
    finally:
        minute_db.close()


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------


def test_cold_start_creates_five_empty_next_objects(migrated_db: str) -> None:
    with psycopg.connect(migrated_db) as conn:
        for view in NEXT_VIEWS:
            assert _count(conn, view) == 0, view
        for view in NEXT_ROLLUP_VIEWS:
            assert _jobs(conn, view) == [_COLUMNSTORE, _REFRESH], view
            assert _chunk_interval(conn, view) == MINUTE_CAGG_CHUNK_INTERVAL, view
        # minute_coverage_next mirrors minute_coverage: a refresh policy only,
        # and the chunk interval TimescaleDB gives a cagg over the 4h rollup.
        assert _jobs(conn, NEXT_COVERAGE_VIEW) == _jobs(conn, MINUTE_COVERAGE_VIEW)
        assert _jobs(conn, NEXT_COVERAGE_VIEW) == [_REFRESH]
        assert _chunk_interval(conn, NEXT_COVERAGE_VIEW) == _chunk_interval(
            conn, MINUTE_COVERAGE_VIEW
        )


def test_policies_match_the_current_twins(migrated_db: str) -> None:
    sql = (
        "SELECT schedule_interval,"
        " config - 'mat_hypertable_id' - 'hypertable_id' AS config"
        " FROM timescaledb_information.jobs"
        " WHERE hypertable_name = %s AND proc_name = %s"
    )
    with psycopg.connect(migrated_db) as conn:
        for current, nxt in zip(CURRENT_VIEWS, NEXT_VIEWS, strict=True):
            for proc in _jobs(conn, current):
                assert conn.execute(sql, (current, proc)).fetchall() == (
                    conn.execute(sql, (nxt, proc)).fetchall()
                ), (nxt, proc)


@pytest.fixture()
def populated_pre_063(ephemeral_db: str) -> str:
    """Chain through 062, fixture bars written, current rollups refreshed."""
    _apply(ephemeral_db, MINUTE_MIGRATIONS[:_INDEX])
    _write_fixtures(ephemeral_db, ["SPY_2026-09-08", "MSFT_2026-06-01"])
    _refresh(ephemeral_db, CURRENT_VIEWS)
    return ephemeral_db


def test_populated_database_keeps_current_objects(populated_pre_063: str) -> None:
    url = populated_pre_063
    with psycopg.connect(url) as conn:
        before = {v: (_count(conn, v), _definition(conn, v)) for v in CURRENT_VIEWS}
        assert all(count > 0 for count, _ in before.values())
    _apply(url, MINUTE_MIGRATIONS)
    with psycopg.connect(url) as conn:
        after = {v: (_count(conn, v), _definition(conn, v)) for v in CURRENT_VIEWS}
        assert after == before
        assert all(_count(conn, v) == 0 for v in NEXT_VIEWS)


def test_rerun_after_partial_apply_converges(migrated_db: str) -> None:
    with psycopg.connect(migrated_db, autocommit=True) as conn:
        conn.execute(f"DROP MATERIALIZED VIEW {NEXT_COVERAGE_VIEW}")
        conn.execute(f"DROP MATERIALIZED VIEW {NEXT_5M}")
        conn.execute("DELETE FROM schema_migrations WHERE migration_id = %s", (_ID,))
    _apply(migrated_db, MINUTE_MIGRATIONS)
    with psycopg.connect(migrated_db) as conn:
        for view in NEXT_ROLLUP_VIEWS:
            assert _jobs(conn, view) == [_COLUMNSTORE, _REFRESH], view
            assert _chunk_interval(conn, view) == MINUTE_CAGG_CHUNK_INTERVAL, view
        assert _jobs(conn, NEXT_COVERAGE_VIEW) == [_REFRESH]


# ---------------------------------------------------------------------------
# Edge buckets, hand-written minutes with explicit marks
# ---------------------------------------------------------------------------

_T0 = datetime(2026, 9, 8, 14, 0, tzinfo=UTC)  # a 5m bucket start


def _minute(offset: int, high: str, low: str, mark: int | None) -> tuple:
    return (
        _T0 + timedelta(minutes=offset),
        "EDGE",
        "100",
        high,
        low,
        "100.5",
        10,
        mark,
    )


@pytest.fixture()
def edge_db(migrated_db: str) -> str:
    rows = [
        # Bucket 1: one all-marked minute among four clean ones.
        *(_minute(i, "101", "99", 0) for i in range(4)),
        _minute(4, "500", "1", int(OffTape.ALL)),
        # Bucket 2: only all-marked minutes.
        *(_minute(5 + i, "500", "1", int(OffTape.ALL)) for i in range(5)),
        # Bucket 3: one bar marked HIGH only; the rest clean.
        _minute(10, "900", "99", int(OffTape.HIGH)),
        *(_minute(11 + i, "101", "99", 0) for i in range(4)),
    ]
    with psycopg.connect(migrated_db) as conn:
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO minute_ohlcv (time, symbol, open, high, low, close,"
                " volume, off_tape) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                rows,
            )
    _refresh(migrated_db, (NEXT_5M,))
    return migrated_db


def _bucket(url: str, offset: int) -> tuple:
    with psycopg.connect(url) as conn:
        row = conn.execute(
            f"SELECT open, high, low, close, volume, minute_count FROM {NEXT_5M}"  # noqa: S608
            " WHERE symbol = 'EDGE' AND time_bucket = %s",
            (_T0 + timedelta(minutes=offset),),
        ).fetchone()
    assert row is not None
    return tuple(row)


def test_all_marked_minute_is_left_out_but_counted(edge_db: str) -> None:
    open_, high, low, close, volume, count = _bucket(edge_db, 0)
    assert (high, low, volume, count) == (Decimal("101"), Decimal("99"), 40, 5)
    assert (open_, close) == (Decimal("100"), Decimal("100.5"))


def test_bucket_of_only_marked_minutes_keeps_a_null_row(edge_db: str) -> None:
    assert _bucket(edge_db, 5) == (None, None, None, None, None, 5)


def test_high_only_mark_clamps_the_high(edge_db: str) -> None:
    # The marked bar's eligible range is [99, 100.5], so its high clamps to
    # 100.5 and the bucket high is the clean bars' 101.
    _, high, low, _, volume, count = _bucket(edge_db, 10)
    assert (high, low, volume, count) == (Decimal("101"), Decimal("99"), 50, 5)


# ---------------------------------------------------------------------------
# Real off-tape fixture days (slice 171's criteria)
# ---------------------------------------------------------------------------

KNOWN = [  # (fixture, ET minutes, value): 171's success criterion 1
    ("SPY_2026-09-08", ["07:35", "07:36", "07:37", "07:38"], 770.19),
    ("SPY_2026-09-08", ["16:01", "16:03", "16:04"], 770.3516),
    ("SPY_2026-09-08", ["16:52", "17:26"], 724.228),
    ("SPY_2026-09-11", ["17:33"], 787.5169),
    ("AVGO_2026-06-03", ["16:15"], 295.4665),
    ("MSFT_2026-08-05", ["16:13"], 384.28),
]
FIXTURE_DAYS = sorted({name for name, *_ in KNOWN} | {"MSFT_2026-06-01"})


@pytest.fixture(scope="module")
def fixture_db(module_migrated_db: str) -> str:
    """Every fixture day written and the _next 5m/1h refreshed, once per file;
    the tests below only read."""
    _write_fixtures(module_migrated_db, FIXTURE_DAYS)
    _refresh(module_migrated_db, (NEXT_5M, NEXT_1H))
    return module_migrated_db


def _rollup(url: str, view: str, symbol: str) -> pd.DataFrame:
    with psycopg.connect(url) as conn:
        rows = conn.execute(
            f"SELECT time_bucket, open, high, low, close, volume, minute_count"  # noqa: S608
            f" FROM {view} WHERE symbol = %s ORDER BY time_bucket",
            (symbol,),
        ).fetchall()
    frame = pd.DataFrame(
        rows, columns=["time", *PRICES, "volume", "minute_count"]
    ).set_index("time")
    frame.index = pd.to_datetime(frame.index, utc=True)
    return frame


_BUCKET = {NEXT_5M: "5min", NEXT_1H: "1h"}


@pytest.mark.parametrize("view", [NEXT_5M, NEXT_1H])
@pytest.mark.parametrize(("name", "minutes", "value"), KNOWN)
def test_known_print_absent_from_next(
    fixture_db: str, view: str, name: str, minutes: list[str], value: float
) -> None:
    """The value is gone from every bucket holding one of its minutes. (It
    can trade for real elsewhere in the day: SPY's 770.19 does at 07:01 ET.)"""
    symbol, day = name.split("_")
    stamps = [pd.Timestamp(f"{day} {m}", tz=OFF_TAPE_DAY_TZ) for m in minutes]
    buckets = {s.tz_convert(UTC).floor(_BUCKET[view]) for s in stamps}
    raw = _load(name).set_index("time").loc[[s.tz_convert(UTC) for s in stamps]]
    assert (raw[PRICES].astype(float) - value).abs().lt(5e-5).to_numpy().any()
    bars = _rollup(fixture_db, view, symbol)
    bars = bars[bars.index.isin(buckets)]
    assert len(bars) == len(buckets)
    hits = (bars[PRICES].astype(float) - value).abs().lt(5e-5).to_numpy().any()
    assert not hits, f"{value} ({minutes} ET) present in {view}"


def _eligible_1m(url: str, symbol: str) -> pd.DataFrame:
    minute_db = TimescaleMinuteDataDB(url)
    try:
        return minute_db.get_minute_data(
            symbol, *WINDOW, adjusted=False, prints=PrintMode.ELIGIBLE
        )
    finally:
        minute_db.close()


def _rolled_up_5m(frame: pd.DataFrame) -> pd.DataFrame:
    grouped = frame.astype(float).resample("5min")
    out = pd.DataFrame(
        {
            "open": grouped["open"].first(),
            "high": grouped["high"].max(),
            "low": grouped["low"].min(),
            "close": grouped["close"].last(),
            "volume": grouped["volume"].sum(),
        }
    )
    return out[grouped["open"].count() > 0]


@pytest.mark.parametrize("name", FIXTURE_DAYS)
def test_eligible_1m_rolled_up_equals_next_5m(fixture_db: str, name: str) -> None:
    symbol = name.split("_")[0]
    expected = _rolled_up_5m(_eligible_1m(fixture_db, symbol))
    served = _rollup(fixture_db, NEXT_5M, symbol).dropna(subset=["open"])
    served = served[[*PRICES, "volume"]].astype(float)
    pd.testing.assert_frame_equal(
        served, expected, check_names=False, check_freq=False, check_index_type=False
    )


def test_msft_after_hours_high_is_lower_than_raw(fixture_db: str) -> None:
    """The walkthrough's case: MSFT 2026-06-01 from 16:40 ET on."""
    start = pd.Timestamp("2026-06-01 16:40", tz=OFF_TAPE_DAY_TZ)
    end = pd.Timestamp("2026-06-02 00:00", tz=OFF_TAPE_DAY_TZ)
    with psycopg.connect(fixture_db) as conn:
        row = conn.execute(
            "SELECT max(high) FROM minute_ohlcv"
            " WHERE symbol = 'MSFT' AND time >= %s AND time < %s",
            (start.to_pydatetime(), end.to_pydatetime()),
        ).fetchone()
    assert row is not None
    raw_high = float(row[0])
    served = _rollup(fixture_db, NEXT_5M, "MSFT")
    served_high = float(served.loc[start:end, "high"].max())
    eligible = _eligible_1m(fixture_db, "MSFT").loc[start:end]
    assert served_high < raw_high
    assert served_high == pytest.approx(float(eligible["high"].max()))
