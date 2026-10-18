"""TimescaleDB Minute Data Database Access Layer.

High-performance data access layer for financial minute OHLCV data.
Uses psycopg3 ConnectionPool for connection management.
"""

from __future__ import annotations

import io
import time
from datetime import datetime
from typing import TYPE_CHECKING, Any

import pandas as pd
import psycopg
from psycopg_pool import ConnectionPool

from manta_trading.constants import (
    DB_BULK_SESSION,
    PRINTS_ELIGIBLE_1M_ONLY_MESSAGE,
    DbSessionSettings,
    Granularity,
    PrintMode,
)
from manta_trading.data.adjustment import adjusted as adjusted_fn
from manta_trading.data.quality.off_tape_store import mark_inserted
from manta_trading.logging import get_logger
from manta_trading.market.schema.eligible_sql import eligible_value, has_eligible_price

if TYPE_CHECKING:
    from manta_trading.market.schema.databases import Database

_logger = get_logger(__name__)

#: Tape-eligible 1m bars (slice 171). Rendered once from ``eligible_sql``.
_ELIGIBLE_MINUTE_QUERY = f"""
    SELECT time,
           {eligible_value("open")} AS open,
           {eligible_value("high")} AS high,
           {eligible_value("low")} AS low,
           {eligible_value("close")} AS close,
           volume
    FROM minute_ohlcv
    WHERE symbol = %s
    AND time >= %s
    AND time <= %s
    AND {has_eligible_price()}
    ORDER BY time
"""


class TimescaleMinuteDataDB:
    """High-performance TimescaleDB data access layer for minute OHLCV data."""

    # Map canonical Granularity tokens to materialized views (slice 152: raw projection, no _v2)
    AGGREGATION_VIEWS = {
        "5m":  "minute_5min_ohlcv",
        "15m": "minute_15min_ohlcv",
        "1h":  "minute_hourly_ohlcv",
        "4h":  "minute_4hour_ohlcv",
    }

    def __init__(
        self, conninfo: str, *, session: DbSessionSettings = DB_BULK_SESSION
    ):
        """Initialize TimescaleDB connection with optimized settings.

        Args:
            conninfo: PostgreSQL connection string
                (e.g. ``postgresql://user:pass@host:5432/dbname``)
            session: Per-connection ``work_mem`` and ``statement_timeout``.
                Defaults to the bulk/analytics values every CLI and daemon
                caller has always used; the serving API passes
                ``API_SERVING_SESSION`` (slice 186 D1).
        """
        self.conninfo = conninfo
        self._session = session
        self._pool: ConnectionPool | None = None
        self._init_pool()

    def _configure_connection(self, conn) -> None:
        """Configure session parameters for TimescaleDB performance.

        An instance method, not a static one: the two workload-dependent
        values come from ``self._session``. Uses autocommit so SET commands
        don't leave the connection in INTRANS state (required by psycopg3
        ConnectionPool).
        """
        conn.autocommit = True
        conn.execute("SET timezone = 'UTC'")
        conn.execute(f"SET work_mem = '{self._session.work_mem}'")
        conn.execute(f"SET statement_timeout = '{self._session.statement_timeout}'")
        conn.execute("SET max_parallel_workers_per_gather = 8")
        conn.execute("SET enable_partitionwise_aggregate = on")
        conn.autocommit = False

    def _init_pool(self) -> None:
        """Initialize the connection pool."""
        try:
            self._pool = ConnectionPool(
                self.conninfo,
                min_size=4,
                max_size=10,
                max_lifetime=3600.0,
                configure=self._configure_connection,
            )
            _logger.info("TimescaleDB connection pool initialized")
        except Exception as e:
            _logger.error("Failed to initialize TimescaleDB pool: %s", e)
            raise

    def _ensure_pool(self) -> ConnectionPool:
        """Return the pool, raising if not initialized."""
        if self._pool is None:
            raise RuntimeError("TimescaleMinuteDataDB pool is not initialized")
        return self._pool

    def write_minute_data_bulk(self, symbol: str, data: pd.DataFrame) -> bool:
        """Ultra-high performance bulk write using COPY.

        Args:
            symbol: Stock symbol.
            data: DataFrame with DatetimeIndex and columns:
                open, high, low, close, volume.

        Returns:
            True if successful, False otherwise.
        """
        if data is None or data.empty:
            _logger.warning("No data to write for %s", symbol)
            return False

        try:
            start_time = time.perf_counter()
            pool = self._ensure_pool()

            # Prepare data for COPY
            copy_data = data.copy()
            copy_data.reset_index(inplace=True)

            time_col = copy_data.columns[0]
            if time_col != "time":
                copy_data.rename(columns={time_col: "time"}, inplace=True)

            copy_data["symbol"] = symbol
            copy_cols = ["time", "symbol", "open", "high", "low", "close", "volume"]
            copy_data = copy_data[copy_cols]

            # Ensure timestamps are UTC.
            if copy_data["time"].dt.tz is None:
                copy_data["time"] = copy_data["time"].dt.tz_localize("UTC")
            else:
                copy_data["time"] = copy_data["time"].dt.tz_convert("UTC")

            astype_map: dict[str, str] = {
                "open": "float64",
                "high": "float64",
                "low": "float64",
                "close": "float64",
                "volume": "int64",
                "symbol": "str",
            }
            copy_data = copy_data.astype(astype_map)

            # Generate CSV buffer
            csv_buffer = io.StringIO()
            copy_data.to_csv(
                csv_buffer,
                index=False,
                header=False,
                date_format="%Y-%m-%d %H:%M:%S%z",
                na_rep="",
            )
            csv_data = csv_buffer.getvalue().encode("utf-8")

            # Two-step: COPY into a per-connection TEMP staging table (no
            # constraint checks, full COPY throughput), then INSERT
            # ... ON CONFLICT (symbol, time) DO NOTHING into the live
            # hypertable. This honours migration 011's UNIQUE
            # (symbol, time) index — overlapping fetches now silently
            # skip duplicates instead of raising.
            staging_create_sql = """
                CREATE TEMP TABLE IF NOT EXISTS staging_minute_ohlcv (
                    time   TIMESTAMPTZ NOT NULL,
                    symbol TEXT        NOT NULL,
                    open   NUMERIC(12, 4) NOT NULL,
                    high   NUMERIC(12, 4) NOT NULL,
                    low    NUMERIC(12, 4) NOT NULL,
                    close  NUMERIC(12, 4) NOT NULL,
                    volume BIGINT      NOT NULL
                ) ON COMMIT DROP
            """
            staging_copy_sql = (
                "COPY staging_minute_ohlcv "
                "(time, symbol, open, high, low, close, volume) "
                "FROM STDIN WITH (FORMAT CSV, NULL '')"
            )
            insert_sql = """
                INSERT INTO minute_ohlcv
                    (time, symbol, open, high, low, close, volume)
                SELECT time, symbol, open, high, low, close, volume
                FROM staging_minute_ohlcv
                ON CONFLICT (symbol, time) DO NOTHING
            """

            with pool.connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(staging_create_sql)
                    with cur.copy(staging_copy_sql) as copy:
                        copy.write(csv_data)
                    cur.execute(insert_sql)
                # Slice 171: marked in the same transaction; a marking
                # failure raises, rolls back and is a failed write.
                mark_inserted(conn, symbol, copy_data["time"])
                conn.commit()

            write_time = time.perf_counter() - start_time
            rows_per_sec = len(data) / write_time if write_time > 0 else 0
            _logger.info(
                "TimescaleDB bulk write: %.3fs (%d rows, %.0f rows/s)",
                write_time, len(data), rows_per_sec,
            )
            return True

        except Exception as e:
            _logger.error("TimescaleDB bulk write failed for %s: %s", symbol, e)
            return False

    def get_minute_data(
        self,
        symbol: str,
        start_time: datetime,
        end_time: datetime,
        aggregation: str | Granularity | None = None,
        *,
        adjusted: bool = True,
        prints: PrintMode = PrintMode.ALL,
    ) -> pd.DataFrame:
        """High-performance time-series query with optional aggregation.

        Args:
            symbol: Stock symbol.
            start_time: Start datetime for query range.
            end_time: End datetime for query range.
            aggregation: Optional aggregation level
                (``'5m'``, ``'15m'``, ``'1h'``, ``'4h'``, or a ``Granularity`` member).
            adjusted: When True, apply split/dividend adjustment (default True).
            prints: ``ALL`` serves raw bars; ``ELIGIBLE`` serves tape-eligible
                1m bars (slice 171): marked prices clamped into the bar's
                eligible range, all-marked minutes absent. Adjustment applies
                after the eligible values.

        Returns:
            DataFrame with DatetimeIndex (UTC) and OHLCV columns.

        Raises:
            ValueError: ``prints=ELIGIBLE`` with an aggregation (until slice 172).
            psycopg.Error: any database failure, including a cancelled query.
                An empty frame always means an empty window.
        """
        if prints is PrintMode.ELIGIBLE and aggregation:
            raise ValueError(PRINTS_ELIGIBLE_1M_ONLY_MESSAGE)
        try:
            if aggregation:
                df = self._get_aggregated_data(symbol, start_time, end_time, str(aggregation))
            else:
                query = """
                    SELECT time, open, high, low, close, volume
                    FROM minute_ohlcv
                    WHERE symbol = %s
                    AND time >= %s
                    AND time <= %s
                    ORDER BY time
                """
                if prints is PrintMode.ELIGIBLE:
                    query = _ELIGIBLE_MINUTE_QUERY

                start_query_time = time.perf_counter()
                pool = self._ensure_pool()

                with pool.connection() as conn:
                    with conn.cursor() as cur:
                        cur.execute(query, (symbol, start_time, end_time))
                        rows = cur.fetchall()

                df = self._rows_to_dataframe(rows)

                query_time = time.perf_counter() - start_query_time
                _logger.info("TimescaleDB query: %.3fs (%d rows)", query_time, len(df))

            if adjusted and not df.empty:
                pool = self._ensure_pool()
                with pool.connection() as conn:
                    df = adjusted_fn(df, symbol, conn)

            return df

        except psycopg.errors.QueryCanceled:
            # A cancelled query is not "no data" — it is "we do not know".
            # Returning an empty frame here made a statement_timeout
            # indistinguishable from a closed market: the serving API turned it
            # into 200 {"count": 0} for a known symbol (measured 2026-08-03,
            # slice 186 walkthrough step 7b). Re-raised so the API's handler can
            # map it to 504 and the caller learns to narrow the range.
            _logger.warning(
                "TimescaleDB query cancelled for %s (statement_timeout)", symbol
            )
            raise
        except Exception:
            # Re-raised: an empty frame here made a lost connection look
            # like "no data for this symbol" (issue #37).
            _logger.exception("TimescaleDB query failed for %s", symbol)
            raise

    def _get_aggregated_data(
        self,
        symbol: str,
        start_time: datetime,
        end_time: datetime,
        aggregation: str,
    ) -> pd.DataFrame:
        """Get data from continuous aggregation views."""
        view_name = self.AGGREGATION_VIEWS.get(aggregation)
        if not view_name:
            raise ValueError(f"Unsupported aggregation: {aggregation}")

        # View name is validated against hardcoded whitelist — safe to use in query
        query = f"""
            SELECT time_bucket as time, open, high, low, close, volume
            FROM "{view_name}"
            WHERE symbol = %s
            AND time_bucket >= %s
            AND time_bucket <= %s
            ORDER BY time_bucket
        """

        start_query_time = time.perf_counter()
        pool = self._ensure_pool()

        with pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(query, (symbol, start_time, end_time))
                rows = cur.fetchall()

        df = self._rows_to_dataframe(rows)

        query_time = time.perf_counter() - start_query_time
        _logger.info(
            "TimescaleDB aggregated query (%s): %.3fs (%d bars)",
            aggregation, query_time, len(df),
        )
        return df

    @staticmethod
    def _rows_to_dataframe(rows: list) -> pd.DataFrame:
        """Convert cursor rows to a pandas DataFrame with proper types.

        Columns: time (index), open, high, low, close, volume.
        """
        if not rows:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

        df = pd.DataFrame(
            rows, columns=["time", "open", "high", "low", "close", "volume"]
        )
        df["time"] = pd.to_datetime(df["time"], utc=True)
        df = df.set_index("time")
        df[["open", "high", "low", "close"]] = df[["open", "high", "low", "close"]].astype("float64")
        df["volume"] = df["volume"].astype("int64")
        return df

    # ------------------------------------------------------------------
    # Schema migrations
    # ------------------------------------------------------------------

    def apply_schema_migrations(
        self, migrations: list[dict[str, Any]] | None = None
    ) -> list[str]:
        """Apply pending migrations and return IDs of newly applied ones.

        ``migrations`` is a track from ``TRACKS``; ``None`` selects
        ``TRACKS[DEFAULT_TRACK]`` (the minute track). Each migration runs in
        its own transaction; a failure mid-sequence leaves prior migrations
        committed.
        """
        from manta_trading.market.schema.migrations import DEFAULT_TRACK, TRACKS
        from manta_trading.market.schema.runner import apply_migrations

        track = TRACKS[DEFAULT_TRACK] if migrations is None else migrations
        return apply_migrations(self._ensure_pool(), track)

    def check_ledger_belongs(
        self, database: Database, track: str, env_var: str
    ) -> None:
        """Refuse when this database's ledger holds another database's ids (923 D5).

        Not named ``assert_*``: ``unittest.mock`` rejects such attributes, and
        CLI tests stub this wrapper with a ``MagicMock``.
        """
        from manta_trading.market.schema.databases import assert_ledger_belongs

        assert_ledger_belongs(self._ensure_pool(), database, track, env_var)

    def list_migration_state(
        self, migrations: list[dict[str, Any]] | None = None
    ) -> dict[str, list[dict[str, str]]]:
        """Return applied/pending state for a track (default: minute)."""
        from manta_trading.market.schema.migrations import DEFAULT_TRACK, TRACKS
        from manta_trading.market.schema.runner import list_migration_state

        track = TRACKS[DEFAULT_TRACK] if migrations is None else migrations
        return list_migration_state(self._ensure_pool(), track)

    def close(self) -> None:
        """Close the connection pool."""
        if self._pool is not None:
            self._pool.close()
            self._pool = None
            _logger.info("TimescaleDB connection pool closed")
