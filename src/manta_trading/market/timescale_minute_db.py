"""TimescaleDB Minute Data Database Access Layer.

High-performance data access layer for financial minute OHLCV data.
Uses psycopg3 ConnectionPool for connection management.
"""

from __future__ import annotations

import io
import time
from datetime import datetime

import pandas as pd
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from manta_trading.constants import Granularity
from manta_trading.data.adjustment import adjusted as adjusted_fn
from manta_trading.logging import get_logger

_logger = get_logger(__name__)


class TimescaleMinuteDataDB:
    """High-performance TimescaleDB data access layer for minute OHLCV data."""

    # Map canonical Granularity tokens to materialized views (slice 152: raw projection, no _v2)
    AGGREGATION_VIEWS = {
        "5m":  "minute_5min_ohlcv",
        "15m": "minute_15min_ohlcv",
        "1h":  "minute_hourly_ohlcv",
        "4h":  "minute_4hour_ohlcv",
    }

    def __init__(self, conninfo: str):
        """Initialize TimescaleDB connection with optimized settings.

        Args:
            conninfo: PostgreSQL connection string
                (e.g. ``postgresql://user:pass@host:5432/dbname``)
        """
        self.conninfo = conninfo
        self._pool: ConnectionPool | None = None
        self._init_pool()

    @staticmethod
    def _configure_connection(conn) -> None:
        """Configure session parameters for TimescaleDB performance.

        Uses autocommit so SET commands don't leave the connection
        in INTRANS state (required by psycopg3 ConnectionPool).
        """
        conn.autocommit = True
        conn.execute("SET timezone = 'UTC'")
        conn.execute("SET work_mem = '512MB'")
        conn.execute("SET statement_timeout = '300s'")
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
                open, high, low, close, volume. Optionally may also
                include the six adjustment columns produced upstream
                by the orchestrator: adj_open, adj_high, adj_low,
                adj_close, k_factor, adjusted_at. When all six are
                present they are written alongside the raw columns
                in the same transaction; missing or partial adj
                columns are an error (raise).

        Returns:
            True if successful, False otherwise.
        """
        if data is None or data.empty:
            _logger.warning("No data to write for %s", symbol)
            return False

        adj_columns = (
            "adj_open", "adj_high", "adj_low", "adj_close",
            "k_factor", "adjusted_at",
        )
        present = [c for c in adj_columns if c in data.columns]
        if present and len(present) != len(adj_columns):
            missing = sorted(set(adj_columns) - set(present))
            _logger.error(
                "Partial adjustment columns supplied for %s; missing %s. "
                "All six (%s) or none must be supplied.",
                symbol, missing, list(adj_columns),
            )
            return False
        with_adj = len(present) == len(adj_columns)

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
            base_cols = ["time", "symbol", "open", "high", "low", "close", "volume"]
            if with_adj:
                copy_cols = base_cols + list(adj_columns)
            else:
                copy_cols = base_cols
            copy_data = copy_data[copy_cols]

            # Ensure timestamps are UTC (covers both the row-time index
            # and adjusted_at if present).
            if copy_data["time"].dt.tz is None:
                copy_data["time"] = copy_data["time"].dt.tz_localize("UTC")
            else:
                copy_data["time"] = copy_data["time"].dt.tz_convert("UTC")
            if with_adj:
                if copy_data["adjusted_at"].dt.tz is None:
                    copy_data["adjusted_at"] = copy_data[
                        "adjusted_at"
                    ].dt.tz_localize("UTC")
                else:
                    copy_data["adjusted_at"] = copy_data[
                        "adjusted_at"
                    ].dt.tz_convert("UTC")

            astype_map: dict[str, str] = {
                "open": "float64",
                "high": "float64",
                "low": "float64",
                "close": "float64",
                "volume": "int64",
                "symbol": "str",
            }
            if with_adj:
                # adj OHLC and k_factor cast to float64 for CSV transit;
                # PostgreSQL casts to NUMERIC on INSERT.
                astype_map.update({
                    "adj_open": "float64",
                    "adj_high": "float64",
                    "adj_low": "float64",
                    "adj_close": "float64",
                    "k_factor": "float64",
                })
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
            # skip duplicates instead of raising. When adjustment
            # columns are supplied they ride along in the same staging
            # table and INSERT, keeping raw + adj atomic.
            if with_adj:
                staging_create_sql = """
                    CREATE TEMP TABLE IF NOT EXISTS staging_minute_ohlcv (
                        time        TIMESTAMPTZ NOT NULL,
                        symbol      TEXT        NOT NULL,
                        open        NUMERIC(12, 4) NOT NULL,
                        high        NUMERIC(12, 4) NOT NULL,
                        low         NUMERIC(12, 4) NOT NULL,
                        close       NUMERIC(12, 4) NOT NULL,
                        volume      BIGINT      NOT NULL,
                        adj_open    NUMERIC(20, 8),
                        adj_high    NUMERIC(20, 8),
                        adj_low     NUMERIC(20, 8),
                        adj_close   NUMERIC(20, 8),
                        k_factor    NUMERIC(20, 12),
                        adjusted_at TIMESTAMPTZ
                    ) ON COMMIT DROP
                """
                staging_copy_sql = (
                    "COPY staging_minute_ohlcv "
                    "(time, symbol, open, high, low, close, volume, "
                    "adj_open, adj_high, adj_low, adj_close, k_factor, "
                    "adjusted_at) "
                    "FROM STDIN WITH (FORMAT CSV, NULL '')"
                )
                insert_sql = """
                    INSERT INTO minute_ohlcv
                        (time, symbol, open, high, low, close, volume,
                         adj_open, adj_high, adj_low, adj_close,
                         k_factor, adjusted_at)
                    SELECT time, symbol, open, high, low, close, volume,
                           adj_open, adj_high, adj_low, adj_close,
                           k_factor, adjusted_at
                    FROM staging_minute_ohlcv
                    ON CONFLICT (symbol, time) DO NOTHING
                """
            else:
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
                conn.commit()

            write_time = time.perf_counter() - start_time
            rows_per_sec = len(data) / write_time if write_time > 0 else 0
            _logger.info(
                "TimescaleDB bulk write: %.3fs (%d rows, %.0f rows/s, "
                "adj=%s)",
                write_time, len(data), rows_per_sec, with_adj,
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
    ) -> pd.DataFrame:
        """High-performance time-series query with optional aggregation.

        Args:
            symbol: Stock symbol.
            start_time: Start datetime for query range.
            end_time: End datetime for query range.
            aggregation: Optional aggregation level
                (``'5m'``, ``'15m'``, ``'1h'``, ``'4h'``, or a ``Granularity`` member).
            adjusted: When True, apply split/dividend adjustment (default True).

        Returns:
            DataFrame with DatetimeIndex (UTC) and OHLCV columns.
        """
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

        except Exception as e:
            _logger.error("TimescaleDB query failed for %s: %s", symbol, e)
            return pd.DataFrame()

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

    def get_coverage_analysis(self, symbol: str) -> dict:
        """Analyze data coverage using TimescaleDB functions."""
        try:
            pool = self._ensure_pool()
            with pool.connection() as conn:
                with conn.cursor(row_factory=dict_row) as cur:
                    cur.execute(
                        """
                        SELECT
                            MIN(time) as earliest_data,
                            MAX(time) as latest_data,
                            COUNT(*) as total_rows,
                            1 as symbol_count
                        FROM minute_ohlcv
                        WHERE symbol = %s
                        """,
                        (symbol,),
                    )
                    coverage = cur.fetchone()

                    compression_info = []
                    try:
                        cur.execute("""
                            SELECT
                                chunk_schema,
                                chunk_name,
                                is_compressed,
                                COALESCE(compressed_heap_size, 0) as compressed_heap_size,
                                COALESCE(uncompressed_heap_size, 0) as uncompressed_heap_size,
                                compression_status
                            FROM chunk_compression_stats('minute_ohlcv')
                            ORDER BY chunk_name DESC
                            LIMIT 10
                        """)
                        compression_info = cur.fetchall()
                    except Exception as e:
                        _logger.warning("Compression stats not available: %s", e)

                    return {
                        "symbol": symbol,
                        "earliest_data": coverage["earliest_data"],
                        "latest_data": coverage["latest_data"],
                        "total_rows": coverage["total_rows"],
                        "symbol_count": coverage["symbol_count"],
                        "compression_info": [
                            {
                                "chunk": chunk["chunk_name"],
                                "compressed": chunk["is_compressed"],
                                "compression_ratio": (
                                    (chunk["uncompressed_heap_size"] - chunk["compressed_heap_size"])
                                    / chunk["uncompressed_heap_size"]
                                )
                                if chunk["uncompressed_heap_size"] > 0
                                else 0,
                                "status": chunk["compression_status"],
                            }
                            for chunk in compression_info
                        ],
                    }

        except Exception as e:
            _logger.error("Coverage analysis failed for %s: %s", symbol, e)
            return {"symbol": symbol, "error": str(e)}

    def get_fleet_summary(self) -> dict:
        """Get per-symbol summary of minute data across all symbols.

        Returns:
            Dict with ``symbols`` list and ``total_symbols`` count,
            or ``{"error": ...}`` on failure.
        """
        try:
            pool = self._ensure_pool()
            with pool.connection() as conn:
                with conn.cursor(row_factory=dict_row) as cur:
                    cur.execute("""
                        SELECT
                            symbol,
                            MIN(time) AS earliest,
                            MAX(time) AS latest,
                            COUNT(*) AS row_count
                        FROM minute_ohlcv
                        GROUP BY symbol
                        ORDER BY symbol
                    """)
                    rows = cur.fetchall()

            return {
                "symbols": rows,
                "total_symbols": len(rows),
            }

        except Exception as e:
            _logger.error("Fleet summary failed: %s", e)
            return {"error": str(e)}

    def detect_gaps(self, symbol: str) -> list[dict]:
        """Detect missing-day gaps for a symbol using calendar-day threshold.

        Uses a >3 calendar day gap between consecutive trading days as
        the heuristic (pre-calendar approximation).

        Returns:
            List of ``{"gap_start": date, "gap_end": date, "gap_days": int}``
            dicts, or empty list if none found or on error.
        """
        try:
            pool = self._ensure_pool()
            with pool.connection() as conn:
                with conn.cursor(row_factory=dict_row) as cur:
                    cur.execute(
                        """
                        WITH daily_presence AS (
                            SELECT DISTINCT date_trunc('day', time)::date AS trade_date
                            FROM minute_ohlcv
                            WHERE symbol = %(symbol)s
                        ),
                        gaps AS (
                            SELECT
                                trade_date AS gap_end,
                                LAG(trade_date) OVER (ORDER BY trade_date) AS gap_start,
                                trade_date - LAG(trade_date) OVER (ORDER BY trade_date) AS gap_days
                            FROM daily_presence
                        )
                        SELECT gap_start, gap_end, gap_days
                        FROM gaps
                        WHERE gap_days > 3
                        ORDER BY gap_start
                        """,
                        {"symbol": symbol},
                    )
                    return cur.fetchall()

        except Exception as e:
            _logger.error("Gap detection failed for %s: %s", symbol, e)
            return []

    def get_daily_bar_counts(self, symbol: str) -> list[dict]:
        """Get per-day bar counts for a symbol (Level 2 gap detection).

        Returns:
            List of ``{"trade_date": date, "bar_count": int,
            "first_bar": datetime, "last_bar": datetime}`` dicts,
            or empty list on error.
        """
        try:
            pool = self._ensure_pool()
            with pool.connection() as conn:
                with conn.cursor(row_factory=dict_row) as cur:
                    cur.execute(
                        """
                        SELECT
                            date_trunc('day', time)::date AS trade_date,
                            COUNT(*) AS bar_count,
                            MIN(time) AS first_bar,
                            MAX(time) AS last_bar
                        FROM minute_ohlcv
                        WHERE symbol = %(symbol)s
                        GROUP BY date_trunc('day', time)::date
                        ORDER BY trade_date
                        """,
                        {"symbol": symbol},
                    )
                    return cur.fetchall()

        except Exception as e:
            _logger.error("Daily bar counts failed for %s: %s", symbol, e)
            return []

    def get_system_metrics(self) -> dict:
        """Get comprehensive TimescaleDB system metrics."""
        try:
            pool = self._ensure_pool()
            with pool.connection() as conn:
                with conn.cursor(row_factory=dict_row) as cur:
                    # Hypertable stats
                    hypertable_stats = None
                    try:
                        cur.execute("""
                            SELECT
                                hypertable_name,
                                num_chunks,
                                pg_size_pretty(
                                    hypertable_size(
                                        format('%I.%I', hypertable_schema, hypertable_name)
                                    )
                                ) as size
                            FROM _timescaledb_catalog.hypertable
                            WHERE hypertable_name = 'minute_ohlcv'
                        """)
                        hypertable_stats = cur.fetchone()
                    except Exception as e:
                        _logger.warning("Hypertable stats not available: %s", e)

                    # Compression efficiency
                    compression_stats = None
                    try:
                        cur.execute("""
                            SELECT
                                COUNT(*) as total_chunks,
                                COUNT(*) FILTER (WHERE is_compressed) as compressed_chunks,
                                AVG(
                                    CASE WHEN uncompressed_heap_size > 0
                                    THEN (uncompressed_heap_size - compressed_heap_size)::float
                                         / uncompressed_heap_size
                                    ELSE 0 END
                                ) as avg_compression_ratio
                            FROM chunk_compression_stats('minute_ohlcv')
                        """)
                        compression_stats = cur.fetchone()
                    except Exception as e:
                        _logger.warning("Compression stats not available: %s", e)

                    # Continuous aggregation health
                    cagg_health = []
                    try:
                        cur.execute("""
                            SELECT
                                view_name,
                                job_status,
                                last_run_started_at,
                                last_successful_finish
                            FROM timescaledb_information.continuous_aggregates
                            WHERE view_name LIKE '%minute%ohlcv'
                        """)
                        cagg_health = cur.fetchall()
                    except Exception as e:
                        _logger.warning("Continuous aggregation health not available: %s", e)

                    return {
                        "hypertable": {
                            "name": hypertable_stats["hypertable_name"] if hypertable_stats else None,
                            "chunks": hypertable_stats["num_chunks"] if hypertable_stats else 0,
                            "size": hypertable_stats["size"] if hypertable_stats else "Unknown",
                        },
                        "compression": {
                            "total_chunks": compression_stats["total_chunks"] if compression_stats else 0,
                            "compressed_chunks": compression_stats["compressed_chunks"] if compression_stats else 0,
                            "avg_compression_ratio": compression_stats["avg_compression_ratio"] if compression_stats else 0,
                        },
                        "continuous_aggregations": [
                            {
                                "view": cagg["view_name"],
                                "status": cagg["job_status"],
                                "last_run": cagg["last_run_started_at"],
                                "last_success": cagg["last_successful_finish"],
                            }
                            for cagg in cagg_health
                        ],
                    }

        except Exception as e:
            _logger.error("System metrics failed: %s", e)
            return {"error": str(e)}

    # ------------------------------------------------------------------
    # Schema migrations
    # ------------------------------------------------------------------

    def apply_schema_migrations(self) -> list[str]:
        """Apply pending schema migrations and return IDs of newly applied ones.

        Each migration runs in its own transaction.  A failure mid-sequence
        leaves prior migrations committed.
        """
        from manta_trading.market.schema.migrations import TRACKS
        from manta_trading.market.schema.runner import apply_migrations

        return apply_migrations(self._ensure_pool(), TRACKS["minute"])

    def list_migration_state(self) -> dict[str, list[dict[str, str]]]:
        """Return applied/pending state for the minute migration track."""
        from manta_trading.market.schema.migrations import TRACKS
        from manta_trading.market.schema.runner import list_migration_state

        return list_migration_state(self._ensure_pool(), TRACKS["minute"])

    def close(self) -> None:
        """Close the connection pool."""
        if self._pool is not None:
            self._pool.close()
            self._pool = None
            _logger.info("TimescaleDB connection pool closed")
