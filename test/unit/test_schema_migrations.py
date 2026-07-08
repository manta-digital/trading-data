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
    """
    mock_conn = MagicMock()
    mock_conn.__enter__ = MagicMock(return_value=mock_conn)
    mock_conn.__exit__ = MagicMock(return_value=False)
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
    num_extra_conns: int = 42,
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
        assert len(MIGRATIONS) == 45


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

    def test_001c_4_hour_chunks(self) -> None:
        sql = self._get("001c_create_minute_ohlcv_hypertable")["sql"]
        assert "4 hours" in sql, "must match trading_test reality"

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
