"""Integration tests: migrations 046–047 (slice 167 coverage caggs).

Runs against a throwaway database created by the ``ephemeral_db`` fixture, so
these tests never mutate a shared database and never touch a production job
(slice 168 precedent). Requires ``MT_TIMESCALE_TEST_URL``.

Tests verify:
- 046: both coverage caggs exist; ``minute_coverage`` is HIERARCHICAL (its
  source is the ``minute_4hour_ohlcv`` cagg, not the raw hypertable) while
  ``daily_coverage`` reads raw ``daily_ohlcv``.
- 047: a refresh policy exists for each, with offsets equal to the constants —
  in particular a ``start_offset`` wide enough to re-materialize changed parent
  buckets (slice 167 D4).
- Idempotency: re-applying creates no duplicate policy and raises nothing.
- Rollup arithmetic: ``SUM(bars)`` from ``minute_coverage`` equals the raw bar
  count for a seeded range, which is what catches a wrong
  ``SUM(minute_count)``-vs-``COUNT(*)`` choice in the hierarchical view.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from psycopg.rows import dict_row

from manta_trading.constants import (
    COVERAGE_BUCKET_INTERVAL,
    DAILY_COVERAGE_REFRESH_END_OFFSET,
    DAILY_COVERAGE_REFRESH_SCHEDULE_INTERVAL,
    DAILY_COVERAGE_REFRESH_START_OFFSET,
    DAILY_COVERAGE_VIEW,
    MINUTE_CAGG_REFRESH_SCHEDULE_INTERVAL,
    MINUTE_COVERAGE_REFRESH_END_OFFSET,
    MINUTE_COVERAGE_REFRESH_SCHEDULE_INTERVAL,
    MINUTE_COVERAGE_REFRESH_START_OFFSET,
    MINUTE_COVERAGE_VIEW,
)
from manta_trading.market.schema.migrations.minute import _interval_literal

_COVERAGE_VIEWS = (MINUTE_COVERAGE_VIEW, DAILY_COVERAGE_VIEW)

_FIXTURE_SYMBOL = "ZZCOV"

# A settled historical window, far from the refresh policies' trailing edge, so
# a scheduled refresh cannot race the assertions.
_FIXTURE_START = datetime(2010, 3, 1, 14, 30, tzinfo=UTC)
_FIXTURE_BAR_COUNT = 240


def _apply_migrations(url: str) -> None:
    from psycopg_pool import ConnectionPool

    from manta_trading.market.schema.migrations.minute import MINUTE_MIGRATIONS
    from manta_trading.market.schema.runner import apply_migrations

    with ConnectionPool(url, min_size=1, max_size=2) as pool:
        apply_migrations(pool, MINUTE_MIGRATIONS)


def _cagg_names(conn: psycopg.Connection) -> set[str]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT view_name FROM timescaledb_information.continuous_aggregates"
        )
        return {r["view_name"] for r in cur.fetchall()}


def _view_definition(conn: psycopg.Connection, view_name: str) -> str:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT view_definition FROM timescaledb_information"
            ".continuous_aggregates WHERE view_name = %s",
            (view_name,),
        )
        row = cur.fetchone()
    assert row is not None, f"{view_name} is not a continuous aggregate"
    return str(row[0])


def _refresh_policies(conn: psycopg.Connection) -> dict[str, dict[str, object]]:
    """Refresh-policy rows keyed by cagg view name.

    ``jobs.hypertable_name`` carries the *view* name for cagg refresh policies,
    which is what migration 047's idempotency guard keys on.
    """
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT hypertable_name, schedule_interval, scheduled, config "
            "FROM timescaledb_information.jobs "
            "WHERE proc_name = 'policy_refresh_continuous_aggregate'"
        )
        return {r["hypertable_name"]: dict(r) for r in cur.fetchall()}


def _policy_count(conn: psycopg.Connection, view_name: str) -> int:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM timescaledb_information.jobs "
            "WHERE proc_name = 'policy_refresh_continuous_aggregate' "
            "AND hypertable_name = %s",
            (view_name,),
        )
        row = cur.fetchone()
    assert row is not None
    return int(row[0])


def _config_interval_seconds(
    conn: psycopg.Connection, config: object, key: str
) -> float:
    """Parse an offset out of a job's config JSON into seconds.

    TimescaleDB stores these as interval strings; round-trip them through the
    server rather than parsing by hand.
    """
    assert isinstance(config, dict)
    with conn.cursor() as cur:
        cur.execute("SELECT EXTRACT(EPOCH FROM %s::interval)", (config[key],))
        row = cur.fetchone()
    assert row is not None
    return float(row[0])


def _seed_minute_fixture(url: str) -> None:
    """Insert a known run of minute bars for the fixture symbol."""
    rows = [
        (
            _FIXTURE_START + timedelta(minutes=i),
            _FIXTURE_SYMBOL,
            10.0,
            10.0,
            10.0,
            10.0,
            100,
        )
        for i in range(_FIXTURE_BAR_COUNT)
    ]
    with psycopg.connect(url) as conn:
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO minute_ohlcv "
                "(time, symbol, open, high, low, close, volume) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT DO NOTHING",
                rows,
            )
        conn.commit()


def _refresh_hierarchy(url: str) -> None:
    """Refresh parent then child, each in its own autocommit statement.

    ``refresh_continuous_aggregate`` cannot run inside a transaction block, and
    a multi-statement execute would open one implicitly.
    """
    window_start = _FIXTURE_START - COVERAGE_BUCKET_INTERVAL
    window_end = _FIXTURE_START + COVERAGE_BUCKET_INTERVAL
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(
            "CALL refresh_continuous_aggregate('minute_4hour_ohlcv', %s, %s)",
            (window_start, window_end),
        )
        conn.execute(
            f"CALL refresh_continuous_aggregate('{MINUTE_COVERAGE_VIEW}', %s, %s)",  # noqa: S608
            (window_start, window_end),
        )


class TestMigrations046To047:
    def test_046_creates_both_coverage_caggs(self, ephemeral_db: str) -> None:
        _apply_migrations(ephemeral_db)
        with psycopg.connect(ephemeral_db) as conn:
            caggs = _cagg_names(conn)
        for view in _COVERAGE_VIEWS:
            assert view in caggs, f"{view} not registered as a continuous aggregate"

    def test_046_minute_coverage_is_hierarchical(self, ephemeral_db: str) -> None:
        """``minute_coverage``'s source must be the 4h cagg, not raw minute data.

        This is the whole durability argument for D1 Option 1: grouping stays
        sub-millisecond regardless of the raw table's chunk count. If a future
        edit repoints it at ``minute_ohlcv`` the view still works and still
        returns correct numbers -- only the performance premise silently dies.
        """
        _apply_migrations(ephemeral_db)
        with psycopg.connect(ephemeral_db) as conn:
            definition = _view_definition(conn, MINUTE_COVERAGE_VIEW)
            parent_mat = _view_definition(conn, "minute_4hour_ohlcv")

        # A hierarchical cagg's definition resolves to its parent's
        # materialization hypertable rather than to the raw table name.
        assert "minute_ohlcv" not in definition, (
            "minute_coverage must not read the raw minute hypertable; "
            f"definition was: {definition}"
        )
        assert parent_mat  # parent exists and is itself a cagg

    def test_046_daily_coverage_reads_raw_daily(self, ephemeral_db: str) -> None:
        """The daily branch is asymmetric by design -- raw source, exact stamps."""
        _apply_migrations(ephemeral_db)
        with psycopg.connect(ephemeral_db) as conn:
            definition = _view_definition(conn, DAILY_COVERAGE_VIEW)
        assert "daily_ohlcv" in definition

    def test_047_installs_policies_with_constant_offsets(
        self, ephemeral_db: str
    ) -> None:
        _apply_migrations(ephemeral_db)

        expected = {
            MINUTE_COVERAGE_VIEW: (
                MINUTE_COVERAGE_REFRESH_START_OFFSET,
                MINUTE_COVERAGE_REFRESH_END_OFFSET,
                MINUTE_COVERAGE_REFRESH_SCHEDULE_INTERVAL,
            ),
            DAILY_COVERAGE_VIEW: (
                DAILY_COVERAGE_REFRESH_START_OFFSET,
                DAILY_COVERAGE_REFRESH_END_OFFSET,
                DAILY_COVERAGE_REFRESH_SCHEDULE_INTERVAL,
            ),
        }

        with psycopg.connect(ephemeral_db) as conn:
            policies = _refresh_policies(conn)
            for view, (start, end, schedule) in expected.items():
                assert view in policies, f"no refresh policy registered for {view}"
                policy = policies[view]
                assert policy["scheduled"] is True
                schedule_interval = policy["schedule_interval"]
                assert isinstance(schedule_interval, timedelta)
                assert schedule_interval.total_seconds() == pytest.approx(
                    schedule.total_seconds()
                )
                assert _config_interval_seconds(
                    conn, policy["config"], "start_offset"
                ) == pytest.approx(start.total_seconds())
                assert _config_interval_seconds(
                    conn, policy["config"], "end_offset"
                ) == pytest.approx(end.total_seconds())

    def test_046_047_idempotent_on_reapply(self, ephemeral_db: str) -> None:
        _apply_migrations(ephemeral_db)
        # Second pass must neither raise nor duplicate a policy.
        _apply_migrations(ephemeral_db)
        with psycopg.connect(ephemeral_db) as conn:
            caggs = _cagg_names(conn)
            for view in _COVERAGE_VIEWS:
                assert view in caggs
                assert _policy_count(conn, view) == 1

    def test_048_view_doc_comment_states_both_bounds(
        self, ephemeral_db: str
    ) -> None:
        """Criterion 4: the view must carry a retrievable doc comment.

        Section 7 covers ``bars_summary`` *output*; nothing else verifies the
        comment's *content*, so assert it here -- and assert against the
        constants rather than hard-coded interval literals, so a change to the
        refresh policy that forgets the comment fails.
        """
        _apply_migrations(ephemeral_db)
        with psycopg.connect(ephemeral_db) as conn, conn.cursor() as cur:
            cur.execute("SELECT obj_description('data_status'::regclass)")
            row = cur.fetchone()

        assert row is not None
        comment = row[0]
        assert comment, "data_status has no doc comment"

        # Both documented bounds are named.
        assert "BUCKET TRUNCATION" in comment
        assert "CAGG LAG" in comment

        # The chosen intervals, rendered from the 2.2 constants.
        for interval in (
            MINUTE_COVERAGE_REFRESH_START_OFFSET,
            MINUTE_COVERAGE_REFRESH_END_OFFSET,
            MINUTE_CAGG_REFRESH_SCHEDULE_INTERVAL,
            DAILY_COVERAGE_REFRESH_START_OFFSET,
        ):
            assert _interval_literal(interval) in comment, (
                f"doc comment omits {_interval_literal(interval)}"
            )

        # Both coverage views, and the guard contract.
        assert MINUTE_COVERAGE_VIEW in comment
        assert DAILY_COVERAGE_VIEW in comment
        assert "status_coverage" in comment

    def test_048_bars_summary_reads_coverage_caggs_on_a_live_db(
        self, ephemeral_db: str
    ) -> None:
        """The installed view definition must not scan the raw hypertables."""
        _apply_migrations(ephemeral_db)
        with psycopg.connect(ephemeral_db) as conn, conn.cursor() as cur:
            cur.execute("SELECT pg_get_viewdef('data_status'::regclass, true)")
            row = cur.fetchone()

        assert row is not None
        definition = str(row[0])
        assert MINUTE_COVERAGE_VIEW in definition
        assert DAILY_COVERAGE_VIEW in definition

    def test_046_hierarchical_rollup_matches_raw_count(
        self, ephemeral_db: str
    ) -> None:
        """``SUM(bars)`` must equal the raw bar count for the seeded range.

        The hierarchical view sums the parent's ``minute_count``; a naive
        ``COUNT(*)`` there would instead count 4-hour parent rows and pass every
        structural assertion above while reporting coverage off by orders of
        magnitude.
        """
        _apply_migrations(ephemeral_db)
        _seed_minute_fixture(ephemeral_db)
        _refresh_hierarchy(ephemeral_db)

        with psycopg.connect(ephemeral_db) as conn, conn.cursor() as cur:
            cur.execute(
                f"SELECT COALESCE(SUM(bars), 0) FROM {MINUTE_COVERAGE_VIEW} "  # noqa: S608
                "WHERE symbol = %s",
                (_FIXTURE_SYMBOL,),
            )
            row = cur.fetchone()
        assert row is not None
        assert int(row[0]) == _FIXTURE_BAR_COUNT
