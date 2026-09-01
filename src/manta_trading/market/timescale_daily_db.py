"""TimescaleDB Daily Data Database Access Layer.

Read access for daily and coarser OHLCV data: daily_ohlcv and the weekly,
monthly, and quarterly continuous aggregates installed in slice 152.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
from psycopg_pool import ConnectionPool

from manta_trading.constants import (
    DB_BULK_SESSION,
    GRANULARITY_SOURCE,
    DbSessionSettings,
    Granularity,
)
from manta_trading.data.adjustment import adjusted as adjusted_fn
from manta_trading.logging import get_logger

_logger = get_logger(__name__)

_DAILY_GRAINS = {Granularity.D1, Granularity.W1, Granularity.MO1, Granularity.Q1}
_MINUTE_GRAINS = {
    Granularity.M1,
    Granularity.M5,
    Granularity.M15,
    Granularity.H1,
    Granularity.H4,
}


class TimescaleDailyDataDB:
    """Read daily and coarser OHLCV bars from TimescaleDB."""

    def __init__(
        self, conninfo: str, *, session: DbSessionSettings = DB_BULK_SESSION
    ) -> None:
        """Initialize TimescaleDB connection pool.

        Args:
            conninfo: PostgreSQL connection string.
            session: Per-connection ``work_mem`` and ``statement_timeout``.
                Defaults to the bulk/analytics values every CLI and daemon
                caller has always used; the serving API passes
                ``API_SERVING_SESSION`` (slice 186 D1).
        """
        self.conninfo = conninfo
        self._session = session
        self._pool: ConnectionPool | None = None
        self._init_pool()

    def _configure_connection(self, conn) -> None:  # type: ignore[no-untyped-def]
        """Configure session parameters for TimescaleDB performance.

        An instance method, not a static one: the two workload-dependent
        values come from ``self._session`` (slice 186 D1).
        """
        conn.autocommit = True
        conn.execute("SET timezone = 'UTC'")
        conn.execute(f"SET work_mem = '{self._session.work_mem}'")
        conn.execute(f"SET statement_timeout = '{self._session.statement_timeout}'")
        conn.autocommit = False

    def _init_pool(self) -> None:
        """Initialize the connection pool."""
        try:
            self._pool = ConnectionPool(
                self.conninfo,
                min_size=2,
                max_size=8,
                max_lifetime=3600.0,
                configure=self._configure_connection,
            )
            _logger.info("TimescaleDailyDataDB connection pool initialized")
        except Exception as e:
            _logger.error("Failed to initialize daily DB pool: %s", e)
            raise

    def _ensure_pool(self) -> ConnectionPool:
        """Return the pool, raising if not initialized."""
        if self._pool is None:
            raise RuntimeError("TimescaleDailyDataDB pool is not initialized")
        return self._pool

    def get_daily_data(
        self,
        symbol: str,
        start: date,
        end: date,
        granularity: Granularity,
        *,
        adjusted: bool = True,
    ) -> pd.DataFrame:
        """Return OHLCV bars for symbol in [start, end] at the given granularity.

        Args:
            symbol: Ticker symbol.
            start: Inclusive start date.
            end: Inclusive end date.
            granularity: One of the daily/coarser Granularity tokens.
            adjusted: When True, apply split/dividend adjustment (default True).

        Raises:
            ValueError: If granularity is a minute-grain token.
        """
        if granularity in _MINUTE_GRAINS:
            raise ValueError(
                f"Granularity {granularity!r} is minute-grain; "
                "use TimescaleMinuteDataDB for sub-daily data"
            )

        source = GRANULARITY_SOURCE[granularity]
        df = self._query(source, symbol, start, end)

        if adjusted and not df.empty:
            pool = self._ensure_pool()
            with pool.connection() as conn:
                df = adjusted_fn(df, symbol, conn)

        return df

    def _query(self, source: str, symbol: str, start: date, end: date) -> pd.DataFrame:
        """Execute the appropriate SELECT for the given source table/view."""
        pool = self._ensure_pool()

        if source == GRANULARITY_SOURCE[Granularity.D1]:
            sql = (
                "SELECT time AS trade_date, open, high, low, close, volume"
                " FROM daily_ohlcv"
                " WHERE symbol = %s AND time >= %s AND time <= %s"
                " ORDER BY time"
            )
        else:
            # source is one of the cagg view names — validated against
            # GRANULARITY_SOURCE whitelist, so use in the query is safe.
            sql = (
                f"SELECT time_bucket AS trade_date, open, high, low, close, volume"
                f' FROM "{source}"'
                f" WHERE symbol = %s AND time_bucket >= %s AND time_bucket <= %s"
                f" ORDER BY time_bucket"
            )

        with pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (symbol, start, end))
                rows = cur.fetchall()

        return self._rows_to_dataframe(rows)

    @staticmethod
    def _rows_to_dataframe(rows: list) -> pd.DataFrame:  # type: ignore[type-arg]
        """Convert cursor rows to a pandas DataFrame with proper types."""
        if not rows:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

        df = pd.DataFrame(
            rows, columns=["trade_date", "open", "high", "low", "close", "volume"]
        )
        df["trade_date"] = pd.to_datetime(df["trade_date"], utc=True)
        df = df.set_index("trade_date")
        df[["open", "high", "low", "close"]] = df[
            ["open", "high", "low", "close"]
        ].astype("float64")
        df["volume"] = df["volume"].astype("int64")
        return df

    def close(self) -> None:
        """Close the connection pool."""
        if self._pool is not None:
            self._pool.close()
            self._pool = None
            _logger.info("TimescaleDailyDataDB connection pool closed")
