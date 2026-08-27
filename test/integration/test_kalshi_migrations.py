"""Integration tests: the ``kalshi`` migration track on a throwaway database.

Uses only the ``ephemeral_db`` fixture (``MT_TIMESCALE_TEST_URL``); never the
production URL. Every destructive statement here — ``DROP SCHEMA ... CASCADE``,
``DELETE FROM schema_migrations`` — targets the UUID-named database the
fixture minted for this test (CLAUDE.md, destructive-statement rule).
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import psycopg
import pytest
from psycopg import errors
from psycopg_pool import ConnectionPool

from manta_trading.data.kalshi import models as km
from manta_trading.data.kalshi.constants import (
    KALSHI_CANDLE_CHUNK_INTERVAL,
    KALSHI_CANDLE_COMPRESS_AFTER,
    CandlePeriod,
    MarketStatus,
    Surface,
)
from manta_trading.data.kalshi.db import PreflightError, open_sync_connection
from manta_trading.market.schema.migrations import TRACKS
from manta_trading.market.schema.migrations.kalshi import APP_ROLE
from manta_trading.market.schema.runner import apply_migrations, list_migration_state

KALSHI_IDS = [
    "kalshi_001_schema",
    "kalshi_002_catalog",
    "kalshi_003_collection_state",
    "kalshi_004_catalog_sync_semantics",
    "kalshi_005_candlesticks",
]
BOOTSTRAP_ID = "001_schema_migrations"
TABLES = {
    "series",
    "events",
    "markets",
    "sync_state",
    "awaiting_settlement",
    "market_candle_state",
    "candlesticks",
}
CANDLES_ID = "kalshi_005_candlesticks"


@pytest.fixture
def pool(kalshi_bare_db: str) -> Iterator[ConnectionPool[Any]]:
    with ConnectionPool(kalshi_bare_db, min_size=1, max_size=2) as pool:
        yield pool


@pytest.fixture
def applied(pool: ConnectionPool[Any]) -> list[str]:
    """Bare database → kalshi track applied. Returns the newly applied IDs."""
    return apply_migrations(pool, TRACKS["kalshi"])


def ledger(conn: psycopg.Connection) -> set[str]:
    rows = conn.execute("SELECT migration_id FROM schema_migrations").fetchall()
    return {r[0] for r in rows}


def kalshi_tables(conn: psycopg.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = 'kalshi'"
    ).fetchall()
    return {r[0] for r in rows}


def constraint_defs(conn: psycopg.Connection) -> dict[str, str]:
    rows = conn.execute(
        "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint "
        "WHERE connamespace = 'kalshi'::regnamespace"
    ).fetchall()
    return {r[0]: r[1] for r in rows}


class TestApply:
    def test_bare_apply_bootstraps_then_applies_track(
        self, applied: list[str], ephemeral_db: str
    ):
        assert applied == KALSHI_IDS
        with psycopg.connect(ephemeral_db) as conn:
            assert ledger(conn) == {BOOTSTRAP_ID, *KALSHI_IDS}

    def test_second_apply_is_noop(self, applied: list[str], pool: ConnectionPool[Any]):
        assert apply_migrations(pool, TRACKS["kalshi"]) == []
        state = list_migration_state(pool, TRACKS["kalshi"])
        assert state["pending"] == []
        assert {m["id"] for m in state["applied"]} == {BOOTSTRAP_ID, *KALSHI_IDS}


class TestSchemaObjects:
    def test_tables_exist(self, applied: list[str], ephemeral_db: str):
        with psycopg.connect(ephemeral_db) as conn:
            assert kalshi_tables(conn) == TABLES

    def test_primary_and_foreign_keys(self, applied: list[str], ephemeral_db: str):
        with psycopg.connect(ephemeral_db) as conn:
            defs = constraint_defs(conn)
        assert defs["series_pkey"] == "PRIMARY KEY (ticker)"
        assert defs["events_pkey"] == "PRIMARY KEY (event_ticker)"
        assert defs["markets_pkey"] == "PRIMARY KEY (ticker)"
        assert defs["sync_state_pkey"] == "PRIMARY KEY (surface)"
        assert defs["awaiting_settlement_pkey"] == "PRIMARY KEY (market_ticker)"
        assert defs["market_candle_state_pkey"] == "PRIMARY KEY (market_ticker, period)"
        assert "REFERENCES kalshi.series(ticker)" in defs["events_series_ticker_fkey"]
        assert (
            "REFERENCES kalshi.events(event_ticker)"
            in defs["markets_event_ticker_fkey"]
        )
        assert (
            "REFERENCES kalshi.markets(ticker)"
            in defs["awaiting_settlement_market_ticker_fkey"]
        )
        assert (
            "REFERENCES kalshi.markets(ticker)"
            in defs["market_candle_state_market_ticker_fkey"]
        )
        assert (
            defs["candlesticks_pkey"]
            == "PRIMARY KEY (market_ticker, period, end_period_ts)"
        )
        candles_fk = defs["candlesticks_market_ticker_fkey"]
        assert "REFERENCES kalshi.markets(ticker)" in candles_fk

    def test_check_constraints_derive_from_enums(
        self, applied: list[str], ephemeral_db: str
    ):
        with psycopg.connect(ephemeral_db) as conn:
            defs = constraint_defs(conn)
        for status in MarketStatus:
            assert f"'{status.value}'::text" in defs["markets_status_check"]
        for surface in Surface:
            assert f"'{surface.value}'::text" in defs["sync_state_surface_check"]
        for period in CandlePeriod:
            assert str(int(period)) in defs["market_candle_state_period_check"]
            assert str(int(period)) in defs["candlesticks_period_check"]

    def test_indexes(self, applied: list[str], ephemeral_db: str):
        with psycopg.connect(ephemeral_db) as conn:
            rows = conn.execute(
                "SELECT indexname FROM pg_indexes WHERE schemaname = 'kalshi'"
            ).fetchall()
        names = {r[0] for r in rows}
        assert {
            "markets_event_ticker_idx",
            "markets_status_idx",
            "markets_close_time_idx",
            "events_series_ticker_idx",
        } <= names

    def test_grants_to_application_role(self, applied: list[str], ephemeral_db: str):
        with psycopg.connect(ephemeral_db) as conn:
            usage = conn.execute(
                "SELECT has_schema_privilege(%s, 'kalshi', 'USAGE')", (APP_ROLE,)
            ).fetchone()
            rows = conn.execute(
                "SELECT table_name, privilege_type "
                "FROM information_schema.table_privileges "
                "WHERE grantee = %s AND table_schema = 'kalshi'",
                (APP_ROLE,),
            ).fetchall()
        assert usage == (True,)
        privileges = {(r[0], r[1]) for r in rows}
        for table in TABLES:
            for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE"):
                assert (table, privilege) in privileges
            assert (table, "TRUNCATE") not in privileges

    def test_nothing_references_public(self, applied: list[str], ephemeral_db: str):
        with psycopg.connect(ephemeral_db) as conn:
            foreign = conn.execute(
                "SELECT count(*) FROM pg_constraint WHERE connamespace = "
                "'kalshi'::regnamespace AND contype = 'f' AND "
                "confrelid::regclass::text NOT LIKE 'kalshi.%'"
            ).fetchone()
            views = conn.execute(
                "SELECT count(*) FROM information_schema.views "
                "WHERE table_schema = 'kalshi'"
            ).fetchone()
        assert foreign == (0,)
        assert views == (0,)


def _seed_series_and_event(conn: psycopg.Connection) -> None:
    conn.execute("INSERT INTO kalshi.series (ticker, raw) VALUES ('S1', '{}'::jsonb)")
    conn.execute(
        "INSERT INTO kalshi.events (event_ticker, series_ticker, raw) "
        "VALUES ('S1-E1', 'S1', '{}'::jsonb)"
    )


class TestConstraintRejection:
    def test_unknown_status_fails_check(self, applied: list[str], ephemeral_db: str):
        with psycopg.connect(ephemeral_db) as conn:
            _seed_series_and_event(conn)
            with pytest.raises(errors.CheckViolation):
                conn.execute(
                    "INSERT INTO kalshi.markets "
                    "(ticker, event_ticker, status, close_time, raw) "
                    "VALUES ('S1-E1-M1', 'S1-E1', 'bogus', %s, '{}'::jsonb)",
                    (datetime(2026, 1, 1, tzinfo=UTC),),
                )

    def test_known_status_is_accepted(self, applied: list[str], ephemeral_db: str):
        with psycopg.connect(ephemeral_db) as conn:
            _seed_series_and_event(conn)
            conn.execute(
                "INSERT INTO kalshi.markets "
                "(ticker, event_ticker, status, close_time, raw) "
                "VALUES ('S1-E1-M1', 'S1-E1', %s, %s, '{}'::jsonb)",
                (MarketStatus.ACTIVE.value, datetime(2026, 1, 1, tzinfo=UTC)),
            )
            count = conn.execute("SELECT count(*) FROM kalshi.markets").fetchone()
        assert count == (1,)

    def test_unknown_series_fails_fk(self, applied: list[str], ephemeral_db: str):
        with psycopg.connect(ephemeral_db) as conn:
            with pytest.raises(errors.ForeignKeyViolation):
                conn.execute(
                    "INSERT INTO kalshi.events (event_ticker, series_ticker, raw) "
                    "VALUES ('X-E1', 'NO-SUCH-SERIES', '{}'::jsonb)"
                )

    def test_unknown_period_fails_check(self, applied: list[str], ephemeral_db: str):
        with psycopg.connect(ephemeral_db) as conn:
            _seed_series_and_event(conn)
            conn.execute(
                "INSERT INTO kalshi.markets "
                "(ticker, event_ticker, status, close_time, raw) "
                "VALUES ('S1-E1-M1', 'S1-E1', %s, %s, '{}'::jsonb)",
                (MarketStatus.ACTIVE.value, datetime(2026, 1, 1, tzinfo=UTC)),
            )
            with pytest.raises(errors.CheckViolation):
                conn.execute(
                    "INSERT INTO kalshi.market_candle_state (market_ticker, period) "
                    "VALUES ('S1-E1-M1', 5)"
                )


class TestTeardownReapply:
    def test_drop_schema_then_reapply(
        self, applied: list[str], ephemeral_db: str, pool: ConnectionPool[Any]
    ):
        """The design's rollback posture, on the throwaway database only."""
        with psycopg.connect(ephemeral_db) as conn:
            conn.execute("DROP SCHEMA kalshi CASCADE")
            assert kalshi_tables(conn) == set()
            # The shared ledger survives the schema drop; clear this track's rows.
            conn.execute(
                "DELETE FROM schema_migrations WHERE migration_id = ANY(%s)",
                (KALSHI_IDS,),
            )
            conn.commit()
            assert ledger(conn) == {BOOTSTRAP_ID}
        assert apply_migrations(pool, TRACKS["kalshi"]) == KALSHI_IDS
        with psycopg.connect(ephemeral_db) as conn:
            assert kalshi_tables(conn) == TABLES


#: Columns that are ours, not Kalshi's; every catalog table carries them.
BOOKKEEPING_COLUMNS = {"raw", "first_seen_at", "last_synced_at"}


class TestModelColumnParity:
    """Model field set == table column set (review 261 F002).

    Keeps ``models.py`` and ``kalshi_002_catalog`` from drifting apart so a
    field→column upsert in slice 262 cannot silently skip either side.
    """

    @pytest.mark.parametrize(
        ("model", "table", "model_only"),
        [
            (km.Series, "series", set[str]()),
            (km.Event, "events", {"markets"}),  # nested, never a column
            (km.Market, "markets", set[str]()),
        ],
    )
    def test_fields_match_columns(
        self,
        applied: list[str],
        ephemeral_db: str,
        model: type[km.KalshiModel],
        table: str,
        model_only: set[str],
    ):
        with psycopg.connect(ephemeral_db) as conn:
            rows = conn.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'kalshi' AND table_name = %s",
                (table,),
            ).fetchall()
        columns = {r[0] for r in rows} - BOOKKEEPING_COLUMNS
        fields = set(model.model_fields) - model_only
        assert fields == columns


class TestSyncStateComments:
    """``kalshi_004`` (slice 262): comment-only migration, idempotent."""

    def test_in_track_and_reapplies(
        self, applied: list[str], pool: ConnectionPool[Any]
    ):
        assert "kalshi_004_catalog_sync_semantics" in [
            m["id"] for m in TRACKS["kalshi"]
        ]
        assert "kalshi_004_catalog_sync_semantics" in applied
        assert apply_migrations(pool, TRACKS["kalshi"]) == []

    def test_watermark_comment_states_window_semantics(
        self, applied: list[str], ephemeral_db: str
    ):
        with psycopg.connect(ephemeral_db) as conn:
            row = conn.execute(
                "SELECT col_description('kalshi.sync_state'::regclass, attnum) "
                "FROM pg_attribute WHERE attrelid = 'kalshi.sync_state'::regclass "
                "AND attname = 'watermark_ts'"
            ).fetchone()
        assert row is not None and "completed settled window" in row[0]


def column_comment(conn: psycopg.Connection, table: str, column: str) -> str:
    row = conn.execute(
        "SELECT col_description(%s::regclass, attnum) FROM pg_attribute "
        "WHERE attrelid = %s::regclass AND attname = %s",
        (table, table, column),
    ).fetchone()
    assert row is not None and row[0] is not None
    return row[0]


class TestCandlesticksHypertable:
    """``kalshi_005`` (slice 264, Decision 4): hypertable, compression, policy."""

    def test_in_track_and_reapplies(
        self, applied: list[str], pool: ConnectionPool[Any]
    ):
        assert CANDLES_ID in applied
        assert apply_migrations(pool, TRACKS["kalshi"]) == []

    def test_is_hypertable_with_configured_chunk_interval(
        self, applied: list[str], ephemeral_db: str
    ):
        with psycopg.connect(ephemeral_db) as conn:
            hypertables = conn.execute(
                "SELECT hypertable_name FROM timescaledb_information.hypertables "
                "WHERE hypertable_schema = 'kalshi'"
            ).fetchall()
            interval = conn.execute(
                "SELECT time_interval FROM timescaledb_information.dimensions "
                "WHERE hypertable_schema = 'kalshi' "
                "AND hypertable_name = 'candlesticks' "
                "AND column_name = 'end_period_ts'"
            ).fetchone()
        assert hypertables == [("candlesticks",)]
        assert interval == (KALSHI_CANDLE_CHUNK_INTERVAL,)

    def test_compression_settings(self, applied: list[str], ephemeral_db: str):
        with psycopg.connect(ephemeral_db) as conn:
            row = conn.execute(
                "SELECT segmentby, orderby "
                "FROM timescaledb_information.hypertable_compression_settings "
                "WHERE hypertable = 'kalshi.candlesticks'::regclass"
            ).fetchone()
        assert row == ("market_ticker", "end_period_ts DESC")

    def test_compression_policy_horizon(self, applied: list[str], ephemeral_db: str):
        """Read back by hypertable name and ``proc_name`` — never by job id,
        which regenerates whenever the policy is recreated."""
        with psycopg.connect(ephemeral_db) as conn:
            rows = conn.execute(
                "SELECT (config->>'compress_after')::interval "
                "FROM timescaledb_information.jobs "
                "WHERE hypertable_schema = 'kalshi' "
                "AND hypertable_name = 'candlesticks' "
                "AND proc_name = 'policy_compression'"
            ).fetchall()
        assert rows == [(KALSHI_CANDLE_COMPRESS_AFTER,)]

    def test_coverage_from_ts_added(self, applied: list[str], ephemeral_db: str):
        with psycopg.connect(ephemeral_db) as conn:
            row = conn.execute(
                "SELECT data_type FROM information_schema.columns "
                "WHERE table_schema = 'kalshi' "
                "AND table_name = 'market_candle_state' "
                "AND column_name = 'coverage_from_ts'"
            ).fetchone()
        assert row == ("timestamp with time zone",)

    def test_rewritten_comments(self, applied: list[str], ephemeral_db: str):
        with psycopg.connect(ephemeral_db) as conn:
            watermark = column_comment(
                conn, "kalshi.market_candle_state", "watermark_ts"
            )
            coverage = column_comment(
                conn, "kalshi.market_candle_state", "coverage_from_ts"
            )
            sync = column_comment(conn, "kalshi.sync_state", "watermark_ts")
        # Decision 3: the window end, not the newest stored candle.
        assert "NOT the newest stored candle" in watermark
        assert "newest stored candle for this market" not in watermark
        # Decision 5.
        assert "first window ever requested" in coverage
        # Decision 11 — and kalshi_004's catalog and trades clauses survive.
        assert "candlesticks: market_settled_ts of the historical cutoff" in sync
        assert "catalog: settlement_ts upper bound" in sync
        assert "trades: created_time of the newest stored trade" in sync


class TestLedgerPreflight:
    """``open_sync_connection`` (slice 264, Decision 8) requires every id in
    the kalshi track, naming the missing ones."""

    async def test_missing_migration_named(self, kalshi_db: str):
        with psycopg.connect(kalshi_db) as conn:
            conn.execute(
                "DELETE FROM schema_migrations WHERE migration_id = %s", (CANDLES_ID,)
            )
            conn.commit()
        with pytest.raises(PreflightError) as exc:
            await open_sync_connection(kalshi_db)
        assert CANDLES_ID in str(exc.value)
        assert "mt data migrate apply --track kalshi" in str(exc.value)

    async def test_restoring_the_row_lets_the_connection_open(self, kalshi_db: str):
        with psycopg.connect(kalshi_db) as conn:
            conn.execute(
                "DELETE FROM schema_migrations WHERE migration_id = %s", (CANDLES_ID,)
            )
            conn.commit()
        with pytest.raises(PreflightError):
            await open_sync_connection(kalshi_db)
        with psycopg.connect(kalshi_db) as conn:
            conn.execute(
                "INSERT INTO schema_migrations (migration_id) VALUES (%s)",
                (CANDLES_ID,),
            )
            conn.commit()
        conn_ok = await open_sync_connection(kalshi_db)
        await conn_ok.close()

    async def test_names_every_missing_id(self, kalshi_db: str):
        with psycopg.connect(kalshi_db) as conn:
            conn.execute(
                "DELETE FROM schema_migrations WHERE migration_id = ANY(%s)",
                (KALSHI_IDS[-2:],),
            )
            conn.commit()
        with pytest.raises(PreflightError) as exc:
            await open_sync_connection(kalshi_db)
        for migration_id in KALSHI_IDS[-2:]:
            assert migration_id in str(exc.value)

    async def test_bare_database_is_the_same_error(self, ephemeral_db: str):
        """No ``schema_migrations`` table at all: every id is pending — a
        PreflightError, not an unhandled psycopg error."""
        with pytest.raises(PreflightError) as exc:
            await open_sync_connection(ephemeral_db)
        assert BOOTSTRAP_ID in str(exc.value)
        assert CANDLES_ID in str(exc.value)
