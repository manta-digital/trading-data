"""Integration tests: migrations 051–052 (slice 169 coverage-cagg narrowing).

Runs against a throwaway database created by the ``ephemeral_db`` fixture, so
these tests never mutate a shared database and never touch a production job.
Requires ``MT_TIMESCALE_TEST_URL``.

What these cover, and why each is here rather than at the unit tier:

- **D.3 / criterion 3** — the whole chain applies cleanly on a cold-start
  database and ends at 052, with both caggs at the *new* width read back from
  the catalog rather than asserted against the SQL text.
- **D.3a / criterion 7** — ``data_status``'s column names, order, and types are
  byte-identical to the 167 D2 contract after the rebuild. The unit tier can
  only compare generated SQL; this compares what PostgreSQL actually built.
- **D.4** — 051 is non-transactional, so a failure between its steps (1) and
  (3) leaves the database half-migrated. Re-running must converge. Simulated by
  executing only the drop half, then re-running the migration in full.
- **D.5 / F002** — dropping either coverage cagg *without* first dropping
  ``data_status`` must raise a dependency error. This pins the reason step (1)
  exists, so a future reorder of 051 breaks a test instead of failing on prod.
- **D.5a / criterion 16** — ``assert_cagg_fresh`` reports both coverage views
  fresh on a database with real materialized history, and a pre-167 cagg still
  resolves via the unchanged generic formula.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from psycopg.rows import dict_row

from manta_trading.constants import (
    COVERAGE_BUCKET_INTERVAL,
    COVERAGE_BUCKET_LAG_BUDGET,
    DAILY_COVERAGE_REFRESH_END_OFFSET,
    DAILY_COVERAGE_REFRESH_START_OFFSET,
    DAILY_COVERAGE_VIEW,
    MINUTE_COVERAGE_REFRESH_END_OFFSET,
    MINUTE_COVERAGE_REFRESH_START_OFFSET,
    MINUTE_COVERAGE_VIEW,
)

_COVERAGE_VIEWS = (MINUTE_COVERAGE_VIEW, DAILY_COVERAGE_VIEW)

_MIGRATION_051_ID = "051_coverage_cagg_bucket_narrowing"
_MIGRATION_052_ID = "052_coverage_cagg_refresh_policies_narrowed"

_FIXTURE_SYMBOL = "ZZ169"

# A settled historical window, far from the refresh policies' trailing edge, so
# a scheduled refresh cannot race the assertions.
_FIXTURE_START = datetime(2010, 3, 1, 14, 30, tzinfo=UTC)

# The 167 D2 column contract, in order. Asserted as a literal because it IS the
# contract — deriving it from the view under test would make the test vacuous.
_DATA_STATUS_COLUMNS = [
    "symbol",
    "granularity",
    "trading_calendar_id",
    "first_bar_ts",
    "last_bar_ts",
    "bars_stored",
    "target_end_ts",
    "effective_start",
    "gap_count",
    "has_retry_exhausted",
    "last_attempt_ts",
    "last_attempt_outcome",
    "health",
]


def _apply_migrations(url: str) -> None:
    from psycopg_pool import ConnectionPool

    from manta_trading.market.schema.migrations.minute import MINUTE_MIGRATIONS
    from manta_trading.market.schema.runner import apply_migrations

    with ConnectionPool(url, min_size=1, max_size=2) as pool:
        apply_migrations(pool, MINUTE_MIGRATIONS)


def _applied_ids(conn: psycopg.Connection) -> list[str]:
    with conn.cursor() as cur:
        cur.execute("SELECT migration_id FROM schema_migrations ORDER BY migration_id")
        return [r[0] for r in cur.fetchall()]


def _cagg_names(conn: psycopg.Connection) -> set[str]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT view_name FROM timescaledb_information.continuous_aggregates"
        )
        return {r["view_name"] for r in cur.fetchall()}


def _bucket_width_seconds(conn: psycopg.Connection, view_name: str) -> float:
    """The cagg's actual bucket width, read from its installed definition.

    TimescaleDB 2.29 dropped ``bucket_width`` from
    ``timescaledb_information.continuous_aggregates``, so the width is parsed
    out of the stored ``time_bucket('<interval>'::interval, ...)`` call and
    normalised through the server rather than by hand.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT substring(view_definition, %s) "
            "FROM timescaledb_information.continuous_aggregates "
            "WHERE view_name = %s",
            (r"time_bucket\('([^']*)'", view_name),
        )
        row = cur.fetchone()
        assert row is not None and row[0], f"{view_name}: no time_bucket found"
        cur.execute("SELECT EXTRACT(EPOCH FROM %s::interval)", (row[0],))
        got = cur.fetchone()
    assert got is not None
    return float(got[0])


def _seed_history(url: str) -> None:
    """Seed raw bars spanning several buckets, then materialize the chain.

    Deliberately spans more than one ``COVERAGE_BUCKET_INTERVAL`` so the caggs
    hold multiple rows per symbol — a single-bucket fixture would pass the
    rollup assertions even if bucketing were broken.
    """
    span = COVERAGE_BUCKET_INTERVAL * 3
    minute_rows = [
        (_FIXTURE_START + timedelta(minutes=i * 97), _FIXTURE_SYMBOL, 10.0, 1)
        for i in range(int(span.total_seconds() // (97 * 60)))
    ]
    daily_rows = [
        (_FIXTURE_START + timedelta(days=i), _FIXTURE_SYMBOL, 10.0, 1)
        for i in range(span.days)
    ]

    with psycopg.connect(url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO instruments (canonical_id, symbol, asset_class, "
                " venue, trading_calendar_id, delisted_at_eodhd, "
                " eodhd_type, eodhd_exchange) "
                "VALUES (%s, %s, 'equity', 'US', 'NYSE', FALSE, "
                " 'Common Stock', 'US') "
                "ON CONFLICT DO NOTHING",
                (f"EQ:{_FIXTURE_SYMBOL}", _FIXTURE_SYMBOL),
            )
            cur.executemany(
                "INSERT INTO minute_ohlcv (time, symbol, open, high, low, "
                " close, volume) VALUES (%s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT DO NOTHING",
                [(t, s, p, p, p, p, v) for t, s, p, v in minute_rows],
            )
            cur.executemany(
                "INSERT INTO daily_ohlcv (time, symbol, open, high, low, "
                " close, volume) VALUES (%s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT DO NOTHING",
                [(t, s, p, p, p, p, v) for t, s, p, v in daily_rows],
            )
        conn.commit()

    # Parent before child: refreshing minute_coverage over an unmaterialized
    # 4-hour cagg rolls up nothing (measured, slice 167 s7).
    with psycopg.connect(url, autocommit=True) as conn:
        for view in ("minute_4hour_ohlcv", MINUTE_COVERAGE_VIEW, DAILY_COVERAGE_VIEW):
            conn.execute(f"CALL refresh_continuous_aggregate('{view}', NULL, NULL)")


@pytest.fixture
def migrated(ephemeral_db: str) -> str:
    _apply_migrations(ephemeral_db)
    return ephemeral_db


class TestChainAppliesOnColdStart:
    """D.3 / criterion 3."""

    def test_chain_ends_at_052(self, migrated: str) -> None:
        with psycopg.connect(migrated) as conn:
            applied = _applied_ids(conn)
        assert _MIGRATION_051_ID in applied
        assert _MIGRATION_052_ID in applied
        assert applied[-1] == _MIGRATION_052_ID

    def test_both_coverage_caggs_exist(self, migrated: str) -> None:
        with psycopg.connect(migrated) as conn:
            names = _cagg_names(conn)
        for view in _COVERAGE_VIEWS:
            assert view in names

    @pytest.mark.parametrize("view_name", _COVERAGE_VIEWS)
    def test_bucket_width_is_the_constant(self, migrated: str, view_name: str) -> None:
        """Read back from the catalog, compared against the constant — never a
        literal, so this keeps holding when the width changes again (D5)."""
        with psycopg.connect(migrated) as conn:
            width_s = _bucket_width_seconds(conn, view_name)
        assert width_s == COVERAGE_BUCKET_INTERVAL.total_seconds()

    def test_data_status_exists_and_is_queryable_when_empty(
        self, migrated: str
    ) -> None:
        """Criterion 13, cold-start case. Verified directly rather than
        inferred from column compatibility (F002)."""
        with psycopg.connect(migrated) as conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM data_status")
            row = cur.fetchone()
        assert row is not None
        assert row[0] == 0

    @pytest.mark.parametrize(
        ("view_name", "start_offset", "end_offset"),
        [
            (
                MINUTE_COVERAGE_VIEW,
                MINUTE_COVERAGE_REFRESH_START_OFFSET,
                MINUTE_COVERAGE_REFRESH_END_OFFSET,
            ),
            (
                DAILY_COVERAGE_VIEW,
                DAILY_COVERAGE_REFRESH_START_OFFSET,
                DAILY_COVERAGE_REFRESH_END_OFFSET,
            ),
        ],
    )
    def test_052_installs_policy_at_the_new_offsets(
        self,
        migrated: str,
        view_name: str,
        start_offset: timedelta,
        end_offset: timedelta,
    ) -> None:
        with (
            psycopg.connect(migrated) as conn,
            conn.cursor(row_factory=dict_row) as cur,
        ):
            cur.execute(
                "SELECT config FROM timescaledb_information.jobs "
                "WHERE proc_name = 'policy_refresh_continuous_aggregate' "
                "AND hypertable_name = %s",
                (view_name,),
            )
            rows = cur.fetchall()
            assert len(rows) == 1, f"{view_name}: expected exactly one policy"
            config = rows[0]["config"]
            for key, expected in (
                ("start_offset", start_offset),
                ("end_offset", end_offset),
            ):
                # dict_row is in force on this cursor, so the scalar comes back
                # keyed by its column name rather than by position.
                cur.execute(
                    "SELECT EXTRACT(EPOCH FROM %s::interval) AS seconds",
                    (config[key],),
                )
                got = cur.fetchone()
                assert got is not None
                assert float(got["seconds"]) == expected.total_seconds()

    def test_doc_comment_carries_the_bucket_width_term(self, migrated: str) -> None:
        """Criterion 14, read back via obj_description — the artifact
        140-arch points operators at, so the one that must not lie."""
        from manta_trading.market.schema.migrations.minute import _interval_literal

        with psycopg.connect(migrated) as conn, conn.cursor() as cur:
            cur.execute("SELECT obj_description('data_status'::regclass, 'pg_class')")
            row = cur.fetchone()
        assert row is not None and row[0]
        comment = str(row[0])
        assert _interval_literal(COVERAGE_BUCKET_INTERVAL) in comment
        assert "at most the two-hop refresh interval" not in comment


class TestDataStatusColumnContract:
    """D.3a / criterion 7 — the 167 D2 contract survives the rebuild."""

    def test_columns_match_the_contract_exactly(self, migrated: str) -> None:
        with psycopg.connect(migrated) as conn, conn.cursor() as cur:
            cur.execute("SELECT * FROM data_status LIMIT 0")
            assert cur.description is not None
            names = [d.name for d in cur.description]
        assert names == _DATA_STATUS_COLUMNS

    def test_column_types_are_unchanged(self, migrated: str) -> None:
        """Types, not just names: ``SUM(bars)`` over bigint yields numeric, so
        a lost ``::BIGINT`` cast would silently retype ``bars_stored`` for every
        downstream reader — and ``CREATE OR REPLACE VIEW`` would refuse the
        change outright ("cannot change data type of view column").

        The full map is asserted rather than a hand-picked subset: a partial
        check is exactly how a retyped column slips through. Note ``symbol`` and
        ``trading_calendar_id`` are ``character varying`` (inherited from
        ``instruments``), not ``text`` — the contract is what the view actually
        builds, not what one might assume from the raw hypertables.
        """
        expected = {
            "symbol": "character varying",
            "granularity": "text",
            "trading_calendar_id": "character varying",
            "first_bar_ts": "timestamp with time zone",
            "last_bar_ts": "timestamp with time zone",
            "bars_stored": "bigint",
            "target_end_ts": "timestamp with time zone",
            "effective_start": "date",
            "gap_count": "bigint",
            "has_retry_exhausted": "boolean",
            "last_attempt_ts": "timestamp with time zone",
            "last_attempt_outcome": "text",
            "health": "text",
        }
        with (
            psycopg.connect(migrated) as conn,
            conn.cursor(row_factory=dict_row) as cur,
        ):
            cur.execute(
                "SELECT column_name, data_type FROM information_schema.columns "
                "WHERE table_name = 'data_status' ORDER BY ordinal_position"
            )
            types = {r["column_name"]: r["data_type"] for r in cur.fetchall()}
        assert types == expected


class TestMigration051IsIdempotent:
    """D.4 — 051 is non-transactional, so re-running must converge."""

    def test_reapplying_from_a_half_dropped_state_converges(
        self, migrated: str
    ) -> None:
        """Simulates the design's Window A: a failure between steps (1) and (3).

        Drops ``data_status`` and both caggs by hand — the state 051 would be
        left in if it died after its drops — then re-runs the migration in full
        and asserts everything is back.
        """
        from manta_trading.market.schema.migrations.minute import MINUTE_MIGRATIONS

        migration = next(m for m in MINUTE_MIGRATIONS if m["id"] == _MIGRATION_051_ID)

        with psycopg.connect(migrated, autocommit=True) as conn:
            conn.execute("DROP VIEW IF EXISTS data_status")
            for view in _COVERAGE_VIEWS:
                conn.execute(f"DROP MATERIALIZED VIEW IF EXISTS {view}")

            assert not (_cagg_names(conn) & set(_COVERAGE_VIEWS))

            # Re-run 051 exactly as the runner would.
            migration["python_fn"](conn)

            names = _cagg_names(conn)
            for view in _COVERAGE_VIEWS:
                assert view in names
            with conn.cursor() as cur:
                cur.execute("SELECT count(*) FROM data_status")
                row = cur.fetchone()
            assert row is not None

    def test_reapplying_over_a_complete_state_is_a_no_op(self, migrated: str) -> None:
        """The other half of idempotency: re-running when nothing is missing
        must not raise, and must not duplicate anything."""
        from manta_trading.market.schema.migrations.minute import MINUTE_MIGRATIONS

        migration = next(m for m in MINUTE_MIGRATIONS if m["id"] == _MIGRATION_051_ID)
        with psycopg.connect(migrated, autocommit=True) as conn:
            migration["python_fn"](conn)
            names = _cagg_names(conn)
        for view in _COVERAGE_VIEWS:
            assert view in names


class TestDropOrderingIsEnforced:
    """D.5 / F002 — pins the reason step (1) exists."""

    @pytest.mark.parametrize("view_name", _COVERAGE_VIEWS)
    def test_dropping_a_cagg_before_data_status_raises(
        self, migrated: str, view_name: str
    ) -> None:
        """``data_status``'s bars_summary CTE selects from both coverage caggs,
        so PostgreSQL records a hard relation dependency.

        If this ever stops raising, step (1) has become unnecessary — but far
        more likely the view definition drifted away from reading the caggs, so
        this failing is a signal to check ``_build_data_status_view_sql``, not
        to delete the pre-drop.
        """
        with psycopg.connect(migrated, autocommit=True) as conn:
            with pytest.raises(psycopg.errors.DependentObjectsStillExist):
                conn.execute(f"DROP MATERIALIZED VIEW {view_name}")

    @pytest.mark.parametrize("view_name", _COVERAGE_VIEWS)
    def test_dropping_after_data_status_succeeds(
        self, migrated: str, view_name: str
    ) -> None:
        """The ordering 051 uses: with the view gone first, the drop is clean
        and needs no CASCADE."""
        with psycopg.connect(migrated, autocommit=True) as conn:
            conn.execute("DROP VIEW data_status")
            conn.execute(f"DROP MATERIALIZED VIEW {view_name}")
            assert view_name not in _cagg_names(conn)


class TestCoverageFreshnessOnRealHistory:
    """D.5a / criterion 16 — the generic bucket-lag check clears end-to-end."""

    @pytest.mark.parametrize("view_name", _COVERAGE_VIEWS)
    def test_assert_cagg_fresh_resolves_the_per_view_budget(
        self, migrated: str, view_name: str
    ) -> None:
        """C.4's map and C.5's wiring must reach a real ``assert_cagg_fresh``
        call, not merely compute correctly in isolation."""
        from manta_trading.constants import COVERAGE_SOURCE_TABLE
        from manta_trading.market.maintenance.cagg_freshness import (
            assert_cagg_fresh,
            reset_freshness_cache,
        )

        _seed_history(migrated)
        reset_freshness_cache()

        with psycopg.connect(migrated) as conn:
            verdict = assert_cagg_fresh(
                conn, view_name, source_table=COVERAGE_SOURCE_TABLE[view_name]
            )

        assert verdict.threshold == COVERAGE_BUCKET_LAG_BUDGET[view_name]

    def test_pre_167_cagg_keeps_the_generic_budget(self, migrated: str) -> None:
        """Regression companion at the integration tier: a cagg with no entry
        in the map must resolve via the unchanged formula, so the override
        provably does not leak."""
        from manta_trading.constants import MAX_COVERAGE_SOURCE_STALENESS
        from manta_trading.market.maintenance.cagg_freshness import (
            assert_cagg_fresh,
            reset_freshness_cache,
        )

        _seed_history(migrated)
        reset_freshness_cache()

        view_name = "daily_monthly_ohlcv"
        assert view_name not in COVERAGE_BUCKET_LAG_BUDGET

        with psycopg.connect(migrated) as conn:
            verdict = assert_cagg_fresh(conn, view_name)

        assert verdict.threshold is not None
        # The generic formula caps the start_offset term at the ceiling, so the
        # resolved budget can never reach one coverage bucket width.
        assert verdict.threshold <= MAX_COVERAGE_SOURCE_STALENESS + timedelta(days=30)
        assert verdict.threshold != COVERAGE_BUCKET_LAG_BUDGET[DAILY_COVERAGE_VIEW]
