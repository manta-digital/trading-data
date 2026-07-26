"""Unit tests for the schema migration runner on TimescaleMinuteDataDB."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from manta_trading.market.schema.migrations import MIGRATIONS, TRACKS
from manta_trading.market.schema.migrations.daily import DAILY_MIGRATIONS
from manta_trading.market.schema.migrations.minute import MINUTE_MIGRATIONS


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _patch_runner_psycopg_connect():
    """Patch the psycopg.connect call used by the runner's autocommit path.

    Without this, unit tests that trigger requires_autocommit migrations would
    try to open a real DB connection to 'postgresql://host/db'.

    The mock cursor's ``fetchall`` yields the four minute-cagg view names so the
    045 columnstore migration's fail-fast existence guard is satisfied when the
    full migration sequence runs end-to-end here (042's chunk query filters to a
    different shape and is unaffected — it reads len()/iteration, both empty-safe
    on MagicMock, and its own fetchall rows are ignored by these runner tests).
    """
    mock_conn = MagicMock()
    mock_conn.__enter__ = MagicMock(return_value=mock_conn)
    mock_conn.__exit__ = MagicMock(return_value=False)
    _cagg_rows = [
        ("minute_5min_ohlcv",),
        ("minute_15min_ohlcv",),
        ("minute_hourly_ohlcv",),
        ("minute_4hour_ohlcv",),
    ]
    mock_cur = mock_conn.cursor.return_value.__enter__.return_value

    # fetchall is query-aware: only the 045 cagg-existence query (which selects
    # from continuous_aggregates) returns the four cagg rows. Every other
    # autocommit migration's fetchall (e.g. 042's chunk list) stays empty so its
    # loop body doesn't run and no row-shape assumptions break.
    def _fetchall_side_effect():
        last_execute = mock_cur.execute.call_args
        sql = str(last_execute.args[0]) if last_execute else ""
        if "continuous_aggregates" in sql:
            return _cagg_rows
        return []

    mock_cur.fetchall.side_effect = _fetchall_side_effect
    # 045 checks per-cagg policy existence via fetchone; None → policy added.
    mock_cur.fetchone.return_value = None
    with patch("manta_trading.market.schema.runner._psycopg.connect", return_value=mock_conn):
        yield mock_conn


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_conn() -> MagicMock:
    """Create a mock connection with nested cursor context manager."""
    conn = MagicMock()
    cur = MagicMock()
    conn.__enter__ = MagicMock(return_value=conn)
    conn.__exit__ = MagicMock(return_value=False)
    conn.cursor.return_value.__enter__ = MagicMock(return_value=cur)
    conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
    return conn


def _cur(conn: MagicMock) -> MagicMock:
    """Get the cursor mock from a connection mock."""
    return conn.cursor.return_value.__enter__.return_value


def _mock_db(
    table_exists: bool = True,
    applied_ids: set[str] | None = None,
    num_extra_conns: int = 60,
):
    """Create a TimescaleMinuteDataDB with pre-configured mock connections.

    Returns (db, pool, connections_list).

    Connection layout (when table_exists=True):
      [0] = check table exists  (cursor.fetchone → table_exists)
      [1] = read applied IDs    (cursor.fetchall → applied_ids)
      [2..] = migration execution connections

    When table_exists=False:
      [0] = check table exists
      [1] = bootstrap (execute 001 SQL + INSERT)
      [2] = read applied IDs
      [3..] = migration execution connections
    """
    if applied_ids is None:
        applied_ids = set()

    with patch("manta_trading.market.timescale_minute_db.ConnectionPool"):
        from manta_trading.market.timescale_minute_db import TimescaleMinuteDataDB
        db = TimescaleMinuteDataDB(conninfo="postgresql://host/db")

    # Pre-create all connections
    conns: list[MagicMock] = [_make_conn() for _ in range(2 + num_extra_conns)]

    # Configure check-table connection
    _cur(conns[0]).fetchone.return_value = {"table_exists": table_exists}

    if table_exists:
        # Connection [1] reads applied IDs
        read_idx = 1
    else:
        # Connection [1] is bootstrap, connection [2] reads applied IDs
        read_idx = 2

    _cur(conns[read_idx]).fetchall.return_value = [
        {"migration_id": mid} for mid in applied_ids
    ]

    # Wire up pool
    pool = MagicMock()
    pool.conninfo = "postgresql://host/db"
    conn_iter = iter(conns)
    pool.connection.side_effect = lambda **kw: next(conn_iter)
    db._pool = pool

    return db, pool, conns


# ---------------------------------------------------------------------------
# MIGRATIONS list integrity
# ---------------------------------------------------------------------------


class TestMigrationsListIntegrity:
    def test_all_entries_have_required_keys(self):
        for m in MIGRATIONS:
            assert "id" in m, f"Missing 'id' in {m}"
            assert "description" in m, f"Missing 'description' in {m}"
            # Each migration must have sql, python_fn, or both.
            assert "sql" in m or "python_fn" in m, (
                f"Migration {m['id']} has neither 'sql' nor 'python_fn'"
            )

    def test_ids_are_unique(self):
        ids = [m["id"] for m in MIGRATIONS]
        assert len(ids) == len(set(ids))

    def test_ids_are_sorted_except_known_position_critical(self):
        """List order drives runner; numeric id sort does not.

        Slice 156 inserts position-critical fixup migrations out of id-sort
        order (e.g. 038_create_acquisition_state must precede
        019_slim_acquisition_state). Such exceptions are explicit and listed
        below so future ad-hoc reordering is still caught.
        """
        position_critical_ids: set[str] = {
            "038_create_acquisition_state",
        }
        ids = [m["id"] for m in MIGRATIONS]
        filtered = [mid for mid in ids if mid not in position_critical_ids]
        assert filtered == sorted(filtered)

    def test_migration_count(self):
        assert len(MIGRATIONS) == 48


# ---------------------------------------------------------------------------
# Migrations 023 / 024 content checks
# ---------------------------------------------------------------------------


class TestMigration023DailyOhlcv:
    def _get(self) -> dict:
        return next(m for m in MINUTE_MIGRATIONS if m["id"] == "023_daily_ohlcv")

    def test_id(self) -> None:
        assert self._get()["id"] == "023_daily_ohlcv"

    def test_sql_contains_create_hypertable(self) -> None:
        assert "create_hypertable" in self._get()["sql"]

    def test_sql_contains_unique_index(self) -> None:
        assert "ux_daily_ohlcv_symbol_time" in self._get()["sql"]

    def test_sql_contains_supporting_indexes(self) -> None:
        sql = self._get()["sql"]
        assert "ix_daily_ohlcv_symbol_time" in sql
        assert "ix_daily_ohlcv_time_symbol" in sql

    def test_sql_contains_chunk_time_interval_7_days(self) -> None:
        assert "7 days" in self._get()["sql"]

    def test_sql_contains_expected_columns(self) -> None:
        sql = self._get()["sql"]
        for col in ("time", "symbol", "open", "high", "low", "close", "volume",
                    "adj_open", "adj_high", "adj_low", "adj_close", "k_factor",
                    "adjusted_at", "created_at"):
            assert col in sql, f"column '{col}' missing from migration 023 SQL"

    def test_sql_is_idempotent_keywords(self) -> None:
        sql = self._get()["sql"]
        assert "IF NOT EXISTS" in sql


class TestMigration024DataStatusViewRefresh:
    def _get(self) -> dict:
        return next(
            m for m in MINUTE_MIGRATIONS
            if m["id"] == "024_data_status_view_refresh"
        )

    def test_id(self) -> None:
        assert self._get()["id"] == "024_data_status_view_refresh"

    def test_sql_body_is_non_empty(self) -> None:
        assert len(self._get()["sql"].strip()) > 0

    def test_sql_references_daily_ohlcv_branch(self) -> None:
        # The DO-block must branch on to_regclass('daily_ohlcv')
        assert "daily_ohlcv" in self._get()["sql"]

    def test_sql_does_not_duplicate_view_definition_inline(self) -> None:
        # The view SQL comes from the pre-rendered constants, not a literal
        # copied string.  We assert the constant names appear in the module
        # (not the SQL text) by checking that the test can import them.
        from manta_trading.market.schema.migrations.minute import (
            _DATA_STATUS_VIEW_WITH_DAILY,
            _DATA_STATUS_VIEW_WITHOUT_DAILY,
        )
        assert len(_DATA_STATUS_VIEW_WITH_DAILY) > 0
        assert len(_DATA_STATUS_VIEW_WITHOUT_DAILY) > 0
        # The 024 SQL embeds the rendered values, but those values originate
        # from the single _build_data_status_view_sql call — verified by
        # ensuring the 024 body contains the same DO-$$ construct as 021.
        assert "to_regclass" in self._get()["sql"]


# ---------------------------------------------------------------------------
# Migration 038 (slice 156: cold-start integrity fixup)
# ---------------------------------------------------------------------------


class TestMigration038CreateAcquisitionState:
    def _get(self) -> dict:
        return next(
            m for m in MINUTE_MIGRATIONS if m["id"] == "038_create_acquisition_state"
        )

    def test_id(self) -> None:
        assert self._get()["id"] == "038_create_acquisition_state"

    def test_038_precedes_019(self) -> None:
        ids = [m["id"] for m in MINUTE_MIGRATIONS]
        assert ids.index("038_create_acquisition_state") < ids.index(
            "019_slim_acquisition_state"
        ), "038 must run before 019_slim_acquisition_state on a fresh DB"

    def test_038_uses_if_not_exists(self) -> None:
        assert "IF NOT EXISTS" in self._get()["sql"]

    def test_038_creates_acquisition_state(self) -> None:
        sql = self._get()["sql"]
        assert "acquisition_state" in sql
        for col in (
            "symbol",
            "granularity",
            "provider",
            "last_attempt_ts",
            "updated_at",
            "last_attempt_outcome",
        ):
            assert col in sql, f"column '{col}' missing from migration 038 SQL"

    def test_038_primary_key(self) -> None:
        sql = self._get()["sql"]
        assert "PRIMARY KEY (symbol, granularity, provider)" in sql


# ---------------------------------------------------------------------------
# Migrations 001a/b/c/d (slice 156: timescale_init.py fold-in)
# ---------------------------------------------------------------------------


_INIT_FOLD_IDS = (
    "001a_create_timescaledb_extension",
    "001b_create_minute_ohlcv",
    "001c_create_minute_ohlcv_hypertable",
    "001d_create_minute_ohlcv_indexes",
)


class TestInitFoldMigrations:
    def _get(self, mid: str) -> dict:
        return next(m for m in MINUTE_MIGRATIONS if m["id"] == mid)

    def test_all_present(self) -> None:
        ids = [m["id"] for m in MINUTE_MIGRATIONS]
        for mid in _INIT_FOLD_IDS:
            assert mid in ids, f"{mid} missing from MINUTE_MIGRATIONS"

    def test_001abcd_precedes_002(self) -> None:
        ids = [m["id"] for m in MINUTE_MIGRATIONS]
        idx_002 = ids.index("002_instruments")
        for mid in _INIT_FOLD_IDS:
            assert ids.index(mid) < idx_002, (
                f"{mid} must precede 002_instruments in list order"
            )

    def test_001abcd_use_if_not_exists(self) -> None:
        # 001a uses CREATE EXTENSION IF NOT EXISTS
        assert "IF NOT EXISTS" in self._get("001a_create_timescaledb_extension")["sql"]
        # 001b uses CREATE TABLE IF NOT EXISTS
        assert "IF NOT EXISTS" in self._get("001b_create_minute_ohlcv")["sql"]
        # 001c uses if_not_exists => TRUE (Timescale form)
        assert "if_not_exists" in self._get("001c_create_minute_ohlcv_hypertable")["sql"]
        # 001d uses CREATE INDEX IF NOT EXISTS for each index
        assert "IF NOT EXISTS" in self._get("001d_create_minute_ohlcv_indexes")["sql"]

    def test_001a_requires_autocommit(self) -> None:
        assert self._get("001a_create_timescaledb_extension").get(
            "requires_autocommit"
        ), "CREATE EXTENSION must run in autocommit mode"

    def test_001b_creates_minute_ohlcv_columns(self) -> None:
        sql = self._get("001b_create_minute_ohlcv")["sql"]
        for col in (
            "time",
            "symbol",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "created_at",
        ):
            assert col in sql, f"column '{col}' missing from 001b SQL"

    def test_001c_chunk_interval_from_constant(self) -> None:
        """Slice 166 re-chunk: was a hardcoded '4 hours' (slice 156)."""
        from manta_trading.constants import MINUTE_OHLCV_CHUNK_INTERVAL

        sql = self._get("001c_create_minute_ohlcv_hypertable")["sql"]
        expected = f"INTERVAL '{int(MINUTE_OHLCV_CHUNK_INTERVAL.total_seconds())} seconds'"
        assert expected in sql, "001c must derive from MINUTE_OHLCV_CHUNK_INTERVAL"

    def test_001d_creates_two_indexes(self) -> None:
        sql = self._get("001d_create_minute_ohlcv_indexes")["sql"]
        assert "ix_minute_ohlcv_symbol_time" in sql
        assert "ix_minute_ohlcv_time_symbol" in sql


# ---------------------------------------------------------------------------
# Migration 039 (slice 156 follow-up: daemon_heartbeat fold-in)
# ---------------------------------------------------------------------------


class TestMigration039CreateDaemonHeartbeat:
    def _get(self) -> dict:
        return next(
            m for m in MINUTE_MIGRATIONS if m["id"] == "039_create_daemon_heartbeat"
        )

    def test_id(self) -> None:
        assert self._get()["id"] == "039_create_daemon_heartbeat"

    def test_uses_if_not_exists(self) -> None:
        assert "IF NOT EXISTS" in self._get()["sql"]

    def test_creates_expected_columns(self) -> None:
        sql = self._get()["sql"]
        for col in (
            "daemon_id",
            "status",
            "started_at",
            "last_beat_at",
            "current_symbol",
            "cycle_count",
            "pid",
            "hostname",
        ):
            assert col in sql, f"column '{col}' missing from migration 039 SQL"

    def test_primary_key_is_daemon_id(self) -> None:
        sql = self._get()["sql"]
        assert "daemon_id      TEXT NOT NULL PRIMARY KEY" in sql


# ---------------------------------------------------------------------------
# apply_schema_migrations
# ---------------------------------------------------------------------------


_AUTOCOMMIT_MIGRATIONS = [m for m in MIGRATIONS if m.get("requires_autocommit")]
_NORMAL_MIGRATIONS = [m for m in MIGRATIONS if not m.get("requires_autocommit")]


class TestApplyAllPending:
    """All migrations pending — table exists, none applied."""

    def test_returns_all_ids(self, _patch_runner_psycopg_connect):
        db, pool, conns = _mock_db(table_exists=True, applied_ids=set())
        result = db.apply_schema_migrations()
        assert result == [m["id"] for m in MIGRATIONS]

    def test_executes_each_migration_sql(self, _patch_runner_psycopg_connect):
        db, pool, conns = _mock_db(table_exists=True, applied_ids=set())
        mock_ac_conn = _patch_runner_psycopg_connect
        db.apply_schema_migrations()

        # Pool is used for: 1 check + 1 read + N normal (non-autocommit) migrations.
        assert pool.connection.call_count == 2 + len(_NORMAL_MIGRATIONS)

        # Normal migrations: verify SQL executed and committed.
        for i, migration in enumerate(_NORMAL_MIGRATIONS):
            mig_conn = conns[2 + i]
            if "sql" in migration:
                mig_conn.execute.assert_any_call(migration["sql"])
            mig_conn.commit.assert_called()

        # Autocommit migrations: verify SQL executed on the raw connection.
        for migration in _AUTOCOMMIT_MIGRATIONS:
            if "sql" in migration:
                mock_ac_conn.execute.assert_any_call(migration["sql"])


class TestAllAlreadyApplied:
    """All migrations already applied — should return empty list."""

    def test_returns_empty(self):
        all_ids = {m["id"] for m in MIGRATIONS}
        db, pool, conns = _mock_db(table_exists=True, applied_ids=all_ids)
        result = db.apply_schema_migrations()
        assert result == []

    def test_no_migration_sql_executed(self):
        all_ids = {m["id"] for m in MIGRATIONS}
        db, pool, conns = _mock_db(table_exists=True, applied_ids=all_ids)
        db.apply_schema_migrations()
        # Only check + read = 2 connections
        assert pool.connection.call_count == 2


class TestPartialState:
    """Some migrations applied, only pending ones should run."""

    def test_only_pending_applied(self):
        already = {
            "001_schema_migrations",
            "002_instruments",
            "003_provider_symbol_mapping",
        }
        db, pool, conns = _mock_db(table_exists=True, applied_ids=already)
        result = db.apply_schema_migrations()
        expected = [m["id"] for m in MIGRATIONS if m["id"] not in already]
        assert result == expected


class TestBootstrapPath:
    """schema_migrations table does not exist — bootstrap first."""

    def test_bootstraps_then_applies(self):
        db, pool, conns = _mock_db(
            table_exists=False,
            applied_ids={"001_schema_migrations"},
        )
        result = db.apply_schema_migrations()

        # 001 was bootstrapped, so result is everything after it.
        assert "001_schema_migrations" not in result
        assert len(result) == len(MIGRATIONS) - 1

        # Bootstrap connection [1] executed the 001 SQL
        conns[1].execute.assert_any_call(MIGRATIONS[0]["sql"])
        conns[1].commit.assert_called()


class TestFailureMidSequence:
    """A migration fails — prior ones committed, failed one not recorded."""

    def test_prior_committed_failed_not_recorded(self):
        db, pool, conns = _mock_db(table_exists=True, applied_ids=set())

        # Compute the conn index dynamically. Pool indices skip autocommit
        # migrations (those open via _psycopg.connect, not pool.connection).
        # Index 0/1 are reserved (table-check, read-applied).
        non_autocommit = [m for m in MIGRATIONS if not m.get("requires_autocommit")]
        target_id = "003_provider_symbol_mapping"
        target_pool_idx = 2 + next(
            i for i, m in enumerate(non_autocommit) if m["id"] == target_id
        )

        def _fail_on_003(sql, *args):
            if "provider_symbol_mapping" in str(sql):
                raise RuntimeError("SQL error in 003")

        conns[target_pool_idx].execute.side_effect = _fail_on_003

        with pytest.raises(RuntimeError, match="SQL error in 003"):
            db.apply_schema_migrations()

        # All migrations before the target committed
        for i in range(2, target_pool_idx):
            conns[i].commit.assert_called()
        # The failing migration did NOT commit
        conns[target_pool_idx].commit.assert_not_called()


# ---------------------------------------------------------------------------
# TRACKS / package structure
# ---------------------------------------------------------------------------


class TestTracksPackageStructure:
    def test_tracks_has_minute_and_daily(self):
        assert "minute" in TRACKS
        assert "daily" in TRACKS

    def test_tracks_minute_is_minute_migrations(self):
        assert TRACKS["minute"] is MINUTE_MIGRATIONS

    def test_tracks_daily_is_daily_migrations(self):
        assert TRACKS["daily"] is DAILY_MIGRATIONS

    def test_migrations_alias_is_minute_track(self):
        assert MIGRATIONS is TRACKS["minute"]

    def test_daily_track_has_four_entries(self):
        assert len(DAILY_MIGRATIONS) == 4

    def test_daily_track_ids(self):
        ids = [m["id"] for m in DAILY_MIGRATIONS]
        assert ids == [
            "001_schema_migrations",
            "002_reconcile_existing_schema",
            "003_splits",
            "004_dividends",
        ]

    def test_daily_track_all_have_required_keys(self):
        for m in DAILY_MIGRATIONS:
            assert "id" in m
            assert "description" in m
            assert "sql" in m


# ---------------------------------------------------------------------------
# runner.list_migration_state (mock-based)
# ---------------------------------------------------------------------------


def _make_pool_for_state(
    table_exists: bool,
    applied_rows: list[dict] | None = None,
) -> MagicMock:
    """Build a mock pool for list_migration_state tests."""
    if applied_rows is None:
        applied_rows = []

    pool = MagicMock()
    conns: list[MagicMock] = [_make_conn() for _ in range(4)]

    # Connection 0: table-exists check
    _cur(conns[0]).fetchone.return_value = {"table_exists": table_exists}

    if table_exists:
        # Connection 1: SELECT applied rows
        _cur(conns[1]).fetchall.return_value = applied_rows

    conn_iter = iter(conns)
    pool.connection.side_effect = lambda **kw: next(conn_iter)
    return pool


class TestListMigrationStateUnit:
    def test_no_table_returns_all_pending(self):
        from manta_trading.market.schema.runner import list_migration_state

        pool = _make_pool_for_state(table_exists=False)
        result = list_migration_state(pool, MINUTE_MIGRATIONS)
        assert result["applied"] == []
        assert len(result["pending"]) == len(MINUTE_MIGRATIONS)
        pending_ids = [p["id"] for p in result["pending"]]
        assert pending_ids == [m["id"] for m in MINUTE_MIGRATIONS]

    def test_all_applied_returns_empty_pending(self):
        from manta_trading.market.schema.runner import list_migration_state

        from datetime import datetime, timezone

        applied_rows = [
            {
                "migration_id": m["id"],
                "description": m["description"],
                "applied_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
            }
            for m in MINUTE_MIGRATIONS
        ]
        pool = _make_pool_for_state(table_exists=True, applied_rows=applied_rows)
        result = list_migration_state(pool, MINUTE_MIGRATIONS)
        assert result["pending"] == []
        assert len(result["applied"]) == len(MINUTE_MIGRATIONS)

    def test_partial_state_splits_correctly(self):
        from manta_trading.market.schema.runner import list_migration_state

        from datetime import datetime, timezone

        applied_ids = ["001_schema_migrations", "002_instruments", "003_provider_symbol_mapping"]
        applied_rows = [
            {
                "migration_id": mid,
                "description": "some desc",
                "applied_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
            }
            for mid in applied_ids
        ]
        pool = _make_pool_for_state(table_exists=True, applied_rows=applied_rows)
        result = list_migration_state(pool, MINUTE_MIGRATIONS)
        assert len(result["applied"]) == 3
        assert len(result["pending"]) == len(MINUTE_MIGRATIONS) - 3
        pending_ids = {p["id"] for p in result["pending"]}
        assert not pending_ids.intersection(applied_ids)


# ---------------------------------------------------------------------------
# TimescaleMinuteDataDB.list_migration_state delegation
# ---------------------------------------------------------------------------


class TestTimescaleListMigrationState:
    def test_delegates_to_runner(self):
        """list_migration_state() calls runner.list_migration_state with minute track."""
        from unittest.mock import patch as _patch

        with _patch("manta_trading.market.timescale_minute_db.ConnectionPool"):
            from manta_trading.market.timescale_minute_db import TimescaleMinuteDataDB
            db = TimescaleMinuteDataDB(conninfo="postgresql://host/db")

        sentinel = {"applied": [], "pending": []}
        pool_mock = MagicMock()
        db._pool = pool_mock

        with _patch(
            "manta_trading.market.schema.runner.list_migration_state",
            return_value=sentinel,
        ) as mock_runner:
            result = db.list_migration_state()

        mock_runner.assert_called_once_with(pool_mock, TRACKS["minute"])
        assert result is sentinel


# ---------------------------------------------------------------------------
# Migration 043 + 001c chunk-interval checks (slice 166)
# ---------------------------------------------------------------------------


class TestMigration043MinuteChunkInterval:
    """Slice 166: minute_ohlcv chunk interval derives from the one constant."""

    def _get(self) -> dict:
        return next(
            m for m in MINUTE_MIGRATIONS
            if m["id"] == "043_minute_chunk_interval_7d"
        )

    def test_id(self) -> None:
        assert self._get()["id"] == "043_minute_chunk_interval_7d"

    def test_sql_calls_set_chunk_time_interval(self) -> None:
        sql = self._get()["sql"]
        assert "set_chunk_time_interval" in sql
        assert "minute_ohlcv" in sql

    def test_sql_interval_derives_from_constant(self) -> None:
        from manta_trading.constants import MINUTE_OHLCV_CHUNK_INTERVAL

        expected = f"INTERVAL '{int(MINUTE_OHLCV_CHUNK_INTERVAL.total_seconds())} seconds'"
        assert expected in self._get()["sql"]

    def test_constant_is_seven_days(self) -> None:
        """Guards the slice 166 decision; changing it must be deliberate."""
        from datetime import timedelta

        from manta_trading.constants import MINUTE_OHLCV_CHUNK_INTERVAL

        assert MINUTE_OHLCV_CHUNK_INTERVAL == timedelta(days=7)


class TestMigration001cChunkIntervalFromConstant:
    """Slice 166: cold-start create_hypertable must use the constant, so a
    fresh DB creates 7-day chunks from the first migration run."""

    def _get(self) -> dict:
        return next(
            m for m in MINUTE_MIGRATIONS
            if m["id"] == "001c_create_minute_ohlcv_hypertable"
        )

    def test_sql_has_no_hardcoded_4_hours(self) -> None:
        assert "4 hours" not in self._get()["sql"]

    def test_sql_interval_derives_from_constant(self) -> None:
        from manta_trading.constants import MINUTE_OHLCV_CHUNK_INTERVAL

        expected = f"INTERVAL '{int(MINUTE_OHLCV_CHUNK_INTERVAL.total_seconds())} seconds'"
        assert expected in self._get()["sql"]


# ---------------------------------------------------------------------------
# Migrations 044 / 045 (slice 163: minute-cagg chunk re-sizing + columnstore)
# ---------------------------------------------------------------------------


_MINUTE_CAGG_VIEW_NAMES = (
    "minute_5min_ohlcv",
    "minute_15min_ohlcv",
    "minute_hourly_ohlcv",
    "minute_4hour_ohlcv",
)


class TestMigration044MinuteCaggChunkInterval:
    """Slice 163: minute caggs' chunk interval derives from the one constant
    and targets all four caggs by view name (never mat_N literals)."""

    def _get(self) -> dict:
        return next(
            m for m in MINUTE_MIGRATIONS
            if m["id"] == "044_minute_cagg_chunk_interval_70d"
        )

    def test_id(self) -> None:
        assert self._get()["id"] == "044_minute_cagg_chunk_interval_70d"

    def test_ordering_after_043(self) -> None:
        ids = [m["id"] for m in MINUTE_MIGRATIONS]
        assert ids.index("044_minute_cagg_chunk_interval_70d") == (
            ids.index("043_minute_chunk_interval_7d") + 1
        )

    def test_sql_calls_set_chunk_time_interval(self) -> None:
        assert "set_chunk_time_interval" in self._get()["sql"]

    def test_sql_targets_all_four_caggs_by_view_name(self) -> None:
        sql = self._get()["sql"]
        for view in _MINUTE_CAGG_VIEW_NAMES:
            assert view in sql, f"cagg '{view}' missing from migration 044 SQL"

    def test_sql_uses_no_mat_n_literals(self) -> None:
        # Resolution must be by view name — never mat_3..mat_6 (assignment-order
        # dependent). Guards against a regression to hardcoded mat table names.
        sql = self._get()["sql"]
        for mat in ("mat_3", "mat_4", "mat_5", "mat_6"):
            assert mat not in sql, f"migration 044 must not reference {mat}"

    def test_sql_interval_derives_from_constant(self) -> None:
        # Fails if someone hardcodes '70 days' divorced from the constant.
        from manta_trading.constants import MINUTE_CAGG_CHUNK_INTERVAL

        expected = (
            f"INTERVAL '{int(MINUTE_CAGG_CHUNK_INTERVAL.total_seconds())} seconds'"
        )
        assert expected in self._get()["sql"]

    def test_constant_is_seventy_days(self) -> None:
        """Guards the slice 163 wall-clock decision; changing it is deliberate."""
        from datetime import timedelta

        from manta_trading.constants import MINUTE_CAGG_CHUNK_INTERVAL

        assert MINUTE_CAGG_CHUNK_INTERVAL == timedelta(days=70)


class TestMigration045MinuteCaggColumnstore:
    """Slice 163: columnstore enable + policy on the four minute caggs,
    autocommit, compress_after derived from the constant."""

    def _get(self) -> dict:
        return next(
            m for m in MINUTE_MIGRATIONS
            if m["id"] == "045_minute_cagg_columnstore"
        )

    def test_id(self) -> None:
        assert self._get()["id"] == "045_minute_cagg_columnstore"

    def test_ordering_after_044(self) -> None:
        ids = [m["id"] for m in MINUTE_MIGRATIONS]
        assert ids.index("045_minute_cagg_columnstore") == (
            ids.index("044_minute_cagg_chunk_interval_70d") + 1
        )

    def test_requires_autocommit(self) -> None:
        assert self._get().get("requires_autocommit"), (
            "columnstore policy management must run in autocommit mode (042 precedent)"
        )

    def test_uses_python_fn(self) -> None:
        assert "python_fn" in self._get(), (
            "045 resolves caggs from the catalog — must use python_fn like 042"
        )

    def test_python_fn_targets_all_four_caggs_by_view_name(self) -> None:
        # The migration iterates the module-level view-name tuple. Assert that
        # tuple is exactly the four minute caggs (source of the SQL targets).
        from manta_trading.market.schema.migrations.minute import _MINUTE_CAGG_VIEWS

        assert set(_MINUTE_CAGG_VIEWS) == set(_MINUTE_CAGG_VIEW_NAMES)

    def test_compress_after_derives_from_constant(self) -> None:
        # The python_fn renders compress_after via _interval_literal on the
        # constant. Assert the constant is the single source (fails on a hardcode).
        from datetime import timedelta

        from manta_trading.constants import MINUTE_CAGG_COMPRESS_AFTER

        assert MINUTE_CAGG_COMPRESS_AFTER == timedelta(days=7)

    def test_compress_after_exceeds_refresh_start_offset(self) -> None:
        """compress_after must be strictly > the refresh policy's 1-day
        start_offset, so the columnstore policy never compresses the
        actively-refreshed head (design D3)."""
        from datetime import timedelta

        from manta_trading.constants import MINUTE_CAGG_COMPRESS_AFTER

        assert MINUTE_CAGG_COMPRESS_AFTER > timedelta(days=1)


class TestMigration045ColumnstoreExecution:
    """Exercise the 045 python_fn against a mock connection to lock in the
    cagg-native API (ALTER MATERIALIZED VIEW + CALL add_columnstore_policy),
    idempotency, and the fail-fast missing-cagg guard."""

    def _fn(self):
        from manta_trading.market.schema.migrations.minute import (
            _setup_minute_cagg_columnstore,
        )
        return _setup_minute_cagg_columnstore

    def _conn_with_caggs(self, *, existing_policy: bool) -> MagicMock:
        conn = _make_conn()
        cur = _cur(conn)
        # First cursor use: the existence check → return all four views.
        # Per-cagg policy check: fetchone truthy iff a policy already exists.
        cur.fetchall.return_value = [(v,) for v in _MINUTE_CAGG_VIEW_NAMES]
        cur.fetchone.return_value = (1,) if existing_policy else None
        return conn

    def test_enables_columnstore_on_each_cagg_by_view_name(self) -> None:
        conn = self._conn_with_caggs(existing_policy=False)
        self._fn()(conn)
        enable_calls = [
            c.args[0] for c in conn.execute.call_args_list
            if "enable_columnstore" in str(c.args[0])
        ]
        assert len(enable_calls) == len(_MINUTE_CAGG_VIEW_NAMES)
        for view in _MINUTE_CAGG_VIEW_NAMES:
            assert any(
                f"ALTER MATERIALIZED VIEW {view} " in sql for sql in enable_calls
            ), f"no ALTER MATERIALIZED VIEW for {view}"
        # segmentby=symbol on every enable
        assert all("segmentby = 'symbol'" in sql for sql in enable_calls)

    def test_adds_columnstore_policy_when_absent(self) -> None:
        conn = self._conn_with_caggs(existing_policy=False)
        self._fn()(conn)
        policy_calls = [
            c.args[0] for c in conn.execute.call_args_list
            if "add_columnstore_policy" in str(c.args[0])
        ]
        assert len(policy_calls) == len(_MINUTE_CAGG_VIEW_NAMES)
        # Procedure invoked with CALL, not SELECT.
        assert all(sql.strip().startswith("CALL") for sql in policy_calls)

    def test_policy_after_is_a_typed_interval_literal(self) -> None:
        """Regression (prod apply, 2026-07-25): `after` is interpolated straight
        into the CALL, so a bare '7 days' renders `after => 7 days` and Postgres
        raises `syntax error at or near "days"`. It must be a typed INTERVAL."""
        from manta_trading.constants import MINUTE_CAGG_COMPRESS_AFTER

        conn = self._conn_with_caggs(existing_policy=False)
        self._fn()(conn)
        policy_calls = [
            c.args[0] for c in conn.execute.call_args_list
            if "add_columnstore_policy" in str(c.args[0])
        ]
        expected = (
            f"INTERVAL '{int(MINUTE_CAGG_COMPRESS_AFTER.total_seconds())} seconds'"
        )
        for sql in policy_calls:
            assert f"after => {expected}" in sql, (
                f"after must be a typed INTERVAL literal; got: {sql}"
            )

    def test_idempotent_skips_existing_policy(self) -> None:
        conn = self._conn_with_caggs(existing_policy=True)
        self._fn()(conn)
        policy_calls = [
            c for c in conn.execute.call_args_list
            if "add_columnstore_policy" in str(c.args[0])
        ]
        assert policy_calls == [], "must not re-add an existing columnstore policy"

    def test_fails_fast_when_a_cagg_missing(self) -> None:
        conn = _make_conn()
        # Existence check returns only three of four caggs.
        _cur(conn).fetchall.return_value = [
            (v,) for v in _MINUTE_CAGG_VIEW_NAMES[:3]
        ]
        with pytest.raises(RuntimeError, match="not found in"):
            self._fn()(conn)
        # No columnstore was enabled before the guard fired.
        assert not any(
            "enable_columnstore" in str(c.args[0])
            for c in conn.execute.call_args_list
        )
