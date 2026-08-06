"""Integration tests for ``_select_daily_mode`` against real schema.

Regression coverage for the 20260805 daemon hang (journal 20260806): the
20260804 incident emptied ``data_gaps``, which for the first time let mode
selection fall through to its second query — then a ``COUNT(DISTINCT symbol)``
over the raw ``daily_ohlcv`` hypertable that could not finish planning against
3,371 chunks and ran forever under ``statement_timeout=0``. The rewrite
answers "any cold symbol?" from ``acquisition_state`` instead.

Per the rendered-output rule (journal 20260725) these tests execute the REAL
query by calling ``_select_daily_mode`` with a live connection on an ephemeral
migrated database — including the exact post-incident shape: ``data_gaps``
empty AND ``acquisition_state`` empty. The old hang itself is not reproducible
here (it needs thousands of chunks); what is pinned is that the mode query
touches only bounded tables and stays correct on real schema.
"""

from __future__ import annotations

from datetime import datetime, timezone

import psycopg
import pytest

from manta_trading.constants import DB_BULK_SESSION, DailyMode
from manta_trading.data.acquisition.daemon.daily import (
    _DAILY_GRANULARITY,
    _select_daily_mode,
)
from manta_trading.data.acquisition.state import LastAttemptOutcome
from manta_trading.market.db_session import make_configure_connection

_PROVIDER = "eodhd"
_NOW = datetime(2026, 8, 6, 12, 0, tzinfo=timezone.utc)


@pytest.fixture()
def conn(migrated_db: str):
    with psycopg.connect(migrated_db) as connection:
        yield connection


def _seed_state(
    conn: psycopg.Connection, symbol: str, outcome: LastAttemptOutcome
) -> None:
    conn.execute(
        "INSERT INTO acquisition_state "
        "(symbol, granularity, provider, last_attempt_ts, last_attempt_outcome) "
        "VALUES (%s, %s, %s, %s, %s)",
        (symbol, _DAILY_GRANULARITY, _PROVIDER, _NOW, str(outcome)),
    )


def _seed_unknown_gap(conn: psycopg.Connection, symbol: str) -> None:
    conn.execute(
        "INSERT INTO data_gaps "
        "(symbol, granularity, gap_start, gap_end, fetch_status) "
        "VALUES (%s, %s, %s, %s, 'UNKNOWN')",
        (symbol, _DAILY_GRANULARITY, _NOW, _NOW),
    )


class TestSelectDailyModeSql:
    def test_post_incident_shape_empty_tables_is_backfill(self, conn):
        # The 20260805 wedge shape: data_gaps AND acquisition_state both
        # empty. Must classify (as BACKFILL) instead of hanging.
        mode = _select_daily_mode(conn, ["AAPL", "MSFT"])
        assert mode == DailyMode.BACKFILL

    def test_all_symbols_warm_is_steady_state(self, conn):
        _seed_state(conn, "AAPL", LastAttemptOutcome.SUCCESS)
        _seed_state(conn, "MSFT", LastAttemptOutcome.PARTIAL)
        mode = _select_daily_mode(conn, ["AAPL", "MSFT"])
        assert mode == DailyMode.STEADY_STATE

    def test_symbol_with_only_empty_outcome_is_backfill(self, conn):
        _seed_state(conn, "AAPL", LastAttemptOutcome.SUCCESS)
        _seed_state(conn, "MSFT", LastAttemptOutcome.EMPTY)
        mode = _select_daily_mode(conn, ["AAPL", "MSFT"])
        assert mode == DailyMode.BACKFILL

    def test_symbol_missing_from_state_is_backfill(self, conn):
        _seed_state(conn, "AAPL", LastAttemptOutcome.SUCCESS)
        mode = _select_daily_mode(conn, ["AAPL", "MSFT"])
        assert mode == DailyMode.BACKFILL

    def test_unknown_gap_short_circuits_to_backfill(self, conn):
        _seed_state(conn, "AAPL", LastAttemptOutcome.SUCCESS)
        _seed_unknown_gap(conn, "AAPL")
        mode = _select_daily_mode(conn, ["AAPL"])
        assert mode == DailyMode.BACKFILL

    def test_other_granularity_state_does_not_warm_daily(self, conn):
        # A warm minute-granularity row must not mark the symbol warm for
        # the daily cycle.
        conn.execute(
            "INSERT INTO acquisition_state "
            "(symbol, granularity, provider, last_attempt_ts, last_attempt_outcome) "
            "VALUES (%s, 'minute', %s, %s, %s)",
            ("AAPL", _PROVIDER, _NOW, str(LastAttemptOutcome.SUCCESS)),
        )
        mode = _select_daily_mode(conn, ["AAPL"])
        assert mode == DailyMode.BACKFILL


class TestDaemonSessionTimeout:
    def test_configure_hook_installs_statement_timeout(self, migrated_db):
        # The daemon pools install make_configure_connection(DB_BULK_SESSION);
        # a session without a statement_timeout is what let the old mode
        # query run for 15+ hours.
        with psycopg.connect(migrated_db) as connection:
            make_configure_connection(DB_BULK_SESSION)(connection)
            row = connection.execute("SHOW statement_timeout").fetchone()
            assert row is not None and row[0] == "5min"
            row = connection.execute("SHOW timezone").fetchone()
            assert row is not None and row[0] == "UTC"
