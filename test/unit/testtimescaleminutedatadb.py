"""Unit tests for TimescaleMinuteDataDB (psycopg3).

Integration tests require MT_TIMESCALE_DB_URL to be set.
"""

from __future__ import annotations

import os
import time
from datetime import datetime
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

from manta_trading.market.timescale_minute_db import TimescaleMinuteDataDB


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _create_sample_minute_data(rows: int = 100) -> pd.DataFrame:
    """Create sample minute OHLCV data for testing."""
    start_time = datetime(2024, 8, 22, 9, 30)
    timestamps = pd.date_range(start_time, periods=rows, freq="1min")

    rng = np.random.default_rng(42)
    base_price = 341.0
    prices = base_price + np.cumsum(rng.normal(0, 0.5, rows))

    return pd.DataFrame(
        {
            "open": np.round(prices + rng.normal(0, 0.2, rows), 4),
            "high": np.round(prices + np.abs(rng.normal(0, 0.3, rows)) + 0.1, 4),
            "low": np.round(prices - np.abs(rng.normal(0, 0.3, rows)) - 0.1, 4),
            "close": np.round(prices + rng.normal(0, 0.2, rows), 4),
            "volume": rng.integers(1000, 50000, rows),
        },
        index=timestamps,
    )


def _mock_pool():
    """Create a mock ConnectionPool with proper context manager nesting."""
    pool = MagicMock()
    conn = MagicMock()
    cur = MagicMock()

    conn.__enter__ = MagicMock(return_value=conn)
    conn.__exit__ = MagicMock(return_value=False)
    conn.cursor.return_value.__enter__ = MagicMock(return_value=cur)
    conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
    pool.connection.return_value = conn

    return pool, conn, cur


# ---------------------------------------------------------------------------
# Unit tests (no DB required)
# ---------------------------------------------------------------------------

class TestTimescaleMinuteDataDBUnit:
    """Tests that don't require a real database."""

    def test_constructor(self):
        with patch("manta_trading.market.timescale_minute_db.ConnectionPool"):
            db = TimescaleMinuteDataDB(conninfo="postgresql://host/db")
            assert db.conninfo == "postgresql://host/db"
            assert db._pool is not None

    def test_constructor_failure(self):
        with patch(
            "manta_trading.market.timescale_minute_db.ConnectionPool",
            side_effect=Exception("Connection failed"),
        ):
            with pytest.raises(Exception, match="Connection failed"):
                TimescaleMinuteDataDB(conninfo="postgresql://host/db")

    def test_pool_parameters(self):
        with patch("manta_trading.market.timescale_minute_db.ConnectionPool") as mock_cls:
            TimescaleMinuteDataDB(conninfo="postgresql://host/db")
            mock_cls.assert_called_once()
            call_kwargs = mock_cls.call_args
            assert call_kwargs.kwargs["min_size"] == 4
            assert call_kwargs.kwargs["max_size"] == 10
            assert call_kwargs.kwargs["max_lifetime"] == 3600.0
            assert call_kwargs.kwargs["configure"] is not None

    def test_ensure_pool_raises_when_none(self):
        with patch("manta_trading.market.timescale_minute_db.ConnectionPool"):
            db = TimescaleMinuteDataDB(conninfo="postgresql://host/db")
            db._pool = None
            with pytest.raises(RuntimeError, match="not initialized"):
                db._ensure_pool()

    def test_close(self):
        with patch("manta_trading.market.timescale_minute_db.ConnectionPool") as mock_cls:
            mock_pool = MagicMock()
            mock_cls.return_value = mock_pool

            db = TimescaleMinuteDataDB(conninfo="postgresql://host/db")
            db.close()

            mock_pool.close.assert_called_once()
            assert db._pool is None

    def test_close_when_none(self):
        with patch("manta_trading.market.timescale_minute_db.ConnectionPool"):
            db = TimescaleMinuteDataDB(conninfo="postgresql://host/db")
            db._pool = None
            db.close()  # should not raise

    # -- write tests --------------------------------------------------------

    def test_write_empty_data(self):
        with patch("manta_trading.market.timescale_minute_db.ConnectionPool"):
            db = TimescaleMinuteDataDB(conninfo="postgresql://host/db")
            assert db.write_minute_data_bulk("TSLA", pd.DataFrame()) is False

    def test_write_none_data(self):
        with patch("manta_trading.market.timescale_minute_db.ConnectionPool"):
            db = TimescaleMinuteDataDB(conninfo="postgresql://host/db")
            assert db.write_minute_data_bulk("TSLA", None) is False

    def test_write_success(self):
        with patch("manta_trading.market.timescale_minute_db.ConnectionPool"):
            db = TimescaleMinuteDataDB(conninfo="postgresql://host/db")

        pool, conn, cur = _mock_pool()
        db._pool = pool

        # Mock the COPY context manager
        mock_copy = MagicMock()
        mock_copy.__enter__ = MagicMock(return_value=mock_copy)
        mock_copy.__exit__ = MagicMock(return_value=False)
        cur.copy.return_value = mock_copy

        test_data = _create_sample_minute_data(10)
        result = db.write_minute_data_bulk("TSLA", test_data)

        assert result is True
        cur.copy.assert_called_once()
        mock_copy.write.assert_called_once()

    def test_write_error_handling(self):
        with patch("manta_trading.market.timescale_minute_db.ConnectionPool"):
            db = TimescaleMinuteDataDB(conninfo="postgresql://host/db")

        pool, conn, cur = _mock_pool()
        db._pool = pool

        # Make copy raise an error
        cur.copy.side_effect = Exception("DB error")

        test_data = _create_sample_minute_data(10)
        result = db.write_minute_data_bulk("TSLA", test_data)
        assert result is False

    # -- read tests ---------------------------------------------------------

    def test_get_minute_data_success(self):
        with patch("manta_trading.market.timescale_minute_db.ConnectionPool"):
            db = TimescaleMinuteDataDB(conninfo="postgresql://host/db")

        pool, conn, cur = _mock_pool()
        db._pool = pool

        # Return sample rows (time, open, high, low, close, volume)
        cur.fetchall.return_value = [
            (datetime(2024, 8, 22, 9, 30), 341.0, 342.0, 340.0, 341.5, 10000),
            (datetime(2024, 8, 22, 9, 31), 341.5, 343.0, 341.0, 342.0, 12000),
        ]

        result = db.get_minute_data(
            "TSLA",
            datetime(2024, 8, 22, 9, 30),
            datetime(2024, 8, 22, 16, 0),
            adjusted=False,
        )

        assert isinstance(result, pd.DataFrame)
        assert len(result) == 2
        assert list(result.columns) == ["open", "high", "low", "close", "volume"]
        assert result.index.name == "time"

    def test_get_minute_data_empty(self):
        with patch("manta_trading.market.timescale_minute_db.ConnectionPool"):
            db = TimescaleMinuteDataDB(conninfo="postgresql://host/db")

        pool, conn, cur = _mock_pool()
        db._pool = pool
        cur.fetchall.return_value = []

        result = db.get_minute_data(
            "TSLA",
            datetime(2024, 8, 22, 9, 30),
            datetime(2024, 8, 22, 16, 0),
        )

        assert isinstance(result, pd.DataFrame)
        assert result.empty

    def test_get_minute_data_with_aggregation(self):
        with patch("manta_trading.market.timescale_minute_db.ConnectionPool"):
            db = TimescaleMinuteDataDB(conninfo="postgresql://host/db")

        pool, conn, cur = _mock_pool()
        db._pool = pool
        cur.fetchall.return_value = []

        for agg in ["5m", "15m", "1h", "4h"]:
            result = db.get_minute_data(
                "TSLA",
                datetime(2024, 8, 22, 9, 30),
                datetime(2024, 8, 22, 16, 0),
                aggregation=agg,
            )
            assert isinstance(result, pd.DataFrame)

    def test_get_minute_data_invalid_aggregation(self):
        with patch("manta_trading.market.timescale_minute_db.ConnectionPool"):
            db = TimescaleMinuteDataDB(conninfo="postgresql://host/db")

        pool, conn, cur = _mock_pool()
        db._pool = pool

        # Invalid aggregation should return empty DataFrame (error caught)
        result = db.get_minute_data(
            "TSLA",
            datetime(2024, 8, 22, 9, 30),
            datetime(2024, 8, 22, 16, 0),
            aggregation="invalid",
        )
        assert isinstance(result, pd.DataFrame)
        assert result.empty

    def test_get_aggregated_data_raises_for_invalid(self):
        with patch("manta_trading.market.timescale_minute_db.ConnectionPool"):
            db = TimescaleMinuteDataDB(conninfo="postgresql://host/db")

        with pytest.raises(ValueError, match="Unsupported aggregation"):
            db._get_aggregated_data(
                "TSLA",
                datetime(2024, 8, 22, 9, 30),
                datetime(2024, 8, 22, 16, 0),
                "invalid",
            )

    def test_aggregation_view_mapping(self):
        """Verify correct view names after slice 152 (raw projection, no _v2)."""
        expected = {
            "5m":  "minute_5min_ohlcv",
            "15m": "minute_15min_ohlcv",
            "1h":  "minute_hourly_ohlcv",
            "4h":  "minute_4hour_ohlcv",
        }
        assert TimescaleMinuteDataDB.AGGREGATION_VIEWS == expected

    # -- fleet summary ------------------------------------------------------

    def test_get_fleet_summary_success(self):
        with patch("manta_trading.market.timescale_minute_db.ConnectionPool"):
            db = TimescaleMinuteDataDB(conninfo="postgresql://host/db")

        pool, conn, cur = _mock_pool()
        db._pool = pool
        cur.fetchall.return_value = [
            {"symbol": "AAPL", "earliest": datetime(2020, 1, 2), "latest": datetime(2024, 8, 22), "row_count": 500000},
            {"symbol": "TSLA", "earliest": datetime(2019, 6, 1), "latest": datetime(2024, 8, 22), "row_count": 600000},
        ]

        result = db.get_fleet_summary()

        assert result["total_symbols"] == 2
        assert len(result["symbols"]) == 2
        assert result["symbols"][0]["symbol"] == "AAPL"
        assert result["symbols"][1]["row_count"] == 600000

    def test_get_fleet_summary_error(self):
        with patch("manta_trading.market.timescale_minute_db.ConnectionPool"):
            db = TimescaleMinuteDataDB(conninfo="postgresql://host/db")
            db._pool = MagicMock()
            db._pool.connection.side_effect = Exception("DB error")

            result = db.get_fleet_summary()
            assert "error" in result

    # -- detect_gaps --------------------------------------------------------

    def test_detect_gaps_with_gaps(self):
        from datetime import date as dt_date
        with patch("manta_trading.market.timescale_minute_db.ConnectionPool"):
            db = TimescaleMinuteDataDB(conninfo="postgresql://host/db")

        pool, conn, cur = _mock_pool()
        db._pool = pool
        cur.fetchall.return_value = [
            {"gap_start": dt_date(2023, 12, 20), "gap_end": dt_date(2024, 1, 3), "gap_days": 14},
        ]

        result = db.detect_gaps("AAPL")

        assert len(result) == 1
        assert result[0]["gap_days"] == 14
        assert result[0]["gap_start"] == dt_date(2023, 12, 20)

    def test_detect_gaps_no_gaps(self):
        with patch("manta_trading.market.timescale_minute_db.ConnectionPool"):
            db = TimescaleMinuteDataDB(conninfo="postgresql://host/db")

        pool, conn, cur = _mock_pool()
        db._pool = pool
        cur.fetchall.return_value = []

        result = db.detect_gaps("AAPL")
        assert result == []

    def test_detect_gaps_error(self):
        with patch("manta_trading.market.timescale_minute_db.ConnectionPool"):
            db = TimescaleMinuteDataDB(conninfo="postgresql://host/db")
            db._pool = MagicMock()
            db._pool.connection.side_effect = Exception("DB error")

            result = db.detect_gaps("AAPL")
            assert result == []

    # -- get_daily_bar_counts -----------------------------------------------

    def test_get_daily_bar_counts_success(self):
        from datetime import date as dt_date
        with patch("manta_trading.market.timescale_minute_db.ConnectionPool"):
            db = TimescaleMinuteDataDB(conninfo="postgresql://host/db")

        pool, conn, cur = _mock_pool()
        db._pool = pool
        cur.fetchall.return_value = [
            {"trade_date": dt_date(2024, 8, 20), "bar_count": 390, "first_bar": datetime(2024, 8, 20, 9, 30), "last_bar": datetime(2024, 8, 20, 16, 0)},
            {"trade_date": dt_date(2024, 8, 21), "bar_count": 210, "first_bar": datetime(2024, 8, 21, 9, 30), "last_bar": datetime(2024, 8, 21, 13, 0)},
        ]

        result = db.get_daily_bar_counts("AAPL")

        assert len(result) == 2
        assert result[0]["bar_count"] == 390
        assert result[1]["bar_count"] == 210

    def test_get_daily_bar_counts_empty(self):
        with patch("manta_trading.market.timescale_minute_db.ConnectionPool"):
            db = TimescaleMinuteDataDB(conninfo="postgresql://host/db")

        pool, conn, cur = _mock_pool()
        db._pool = pool
        cur.fetchall.return_value = []

        result = db.get_daily_bar_counts("AAPL")
        assert result == []

    # -- coverage / metrics -------------------------------------------------

    def test_get_coverage_analysis_error(self):
        with patch("manta_trading.market.timescale_minute_db.ConnectionPool"):
            db = TimescaleMinuteDataDB(conninfo="postgresql://host/db")
            db._pool = MagicMock()
            db._pool.connection.side_effect = Exception("DB error")

            result = db.get_coverage_analysis("TSLA")
            assert result["symbol"] == "TSLA"
            assert "error" in result

    def test_get_system_metrics_error(self):
        with patch("manta_trading.market.timescale_minute_db.ConnectionPool"):
            db = TimescaleMinuteDataDB(conninfo="postgresql://host/db")
            db._pool = MagicMock()
            db._pool.connection.side_effect = Exception("DB error")

            result = db.get_system_metrics()
            assert "error" in result

    # -- DataFrame construction ---------------------------------------------

    def test_rows_to_dataframe(self):
        rows = [
            (datetime(2024, 8, 22, 9, 30), 341.0, 342.0, 340.0, 341.5, 10000),
            (datetime(2024, 8, 22, 9, 31), 341.5, 343.0, 341.0, 342.0, 12000),
        ]
        df = TimescaleMinuteDataDB._rows_to_dataframe(rows)

        assert len(df) == 2
        assert df.index.name == "time"
        assert df["open"].dtype == "float64"
        assert df["volume"].dtype == "int64"
        assert df.index.tz is not None  # should be UTC

    def test_rows_to_dataframe_empty(self):
        df = TimescaleMinuteDataDB._rows_to_dataframe([])
        assert df.empty
        assert list(df.columns) == ["open", "high", "low", "close", "volume"]


# ---------------------------------------------------------------------------
# Integration tests (require MT_TIMESCALE_DB_URL)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    not os.environ.get("MT_TIMESCALE_DB_URL"),
    reason="MT_TIMESCALE_DB_URL not set",
)
class TestTimescaleMinuteDataDBIntegration:
    """Integration tests that require a real TimescaleDB."""

    @pytest.fixture(autouse=True)
    def _setup(self):
        url = os.environ["MT_TIMESCALE_DB_URL"]
        self.db = TimescaleMinuteDataDB(conninfo=url)
        yield
        self.db.close()

    def test_get_coverage_analysis(self):
        result = self.db.get_coverage_analysis("TSLA")
        assert "symbol" in result

    def test_get_system_metrics(self):
        result = self.db.get_system_metrics()
        assert "hypertable" in result or "error" in result
