"""Unit tests for mt data caggs commands (slice 154)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from manta_trading.cli.app import app

runner = CliRunner()


def _settings(*, timescale_url: str | None = "postgresql://ts/db"):
    s = MagicMock()
    s.timescale_db_url = timescale_url
    return s


def _patch_app(settings):
    import contextlib

    @contextlib.contextmanager
    def _cm():
        with (
            patch("manta_trading.cli.app.Settings", return_value=settings),
            patch("manta_trading.cli.app.setup_logging"),
        ):
            yield

    return _cm()


class TestCaggsRefresh:
    def test_unknown_granularity_token_exits_with_error(self):
        s = _settings()
        with _patch_app(s):
            with patch("psycopg.connect"):
                result = runner.invoke(
                    app,
                    [
                        "data",
                        "caggs",
                        "refresh",
                        "--granularity",
                        "99x",
                    ],
                )
        assert result.exit_code != 0
        assert "Unknown granularity token" in result.output

    def test_missing_url_exits_with_error(self):
        s = _settings(timescale_url=None)
        with _patch_app(s):
            result = runner.invoke(app, ["data", "caggs", "refresh"])
        assert result.exit_code != 0

    def test_refresh_calls_stored_proc_for_each_cagg(self):
        s = _settings()
        mock_conn = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        with _patch_app(s):
            with patch("psycopg.connect", return_value=mock_conn):
                result = runner.invoke(app, ["data", "caggs", "refresh"])
        assert result.exit_code == 0
        # 7 caggs × 1 CALL each
        assert mock_conn.execute.call_count == 7

    def test_granularity_filter_restricts_to_subset(self):
        s = _settings()
        mock_conn = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        with _patch_app(s):
            with patch("psycopg.connect", return_value=mock_conn):
                result = runner.invoke(
                    app,
                    ["data", "caggs", "refresh", "--granularity", "5m,15m"],
                )
        assert result.exit_code == 0
        assert mock_conn.execute.call_count == 2


class TestCaggsStatus:
    def test_missing_url_exits_with_error(self):
        s = _settings(timescale_url=None)
        with _patch_app(s):
            result = runner.invoke(app, ["data", "caggs", "status"])
        assert result.exit_code != 0

    def _make_status_mock(self, *, view_name: str = "minute_5min_ohlcv"):
        """Wire a psycopg mock cursor that dispatches based on the SQL
        text passed to execute() — caggs_status now runs several distinct
        queries (meta join, catalog read, per-source MAX, per-cagg
        watermark) and a single fetchall return-value can't satisfy them
        all.
        """
        meta_row = (view_name, 1, None, None, None, None)
        mat_id_row = (view_name, 18)
        cutoff_row = (None,)  # MAX(range_start) — None means no chunks
        # When cutoff is None, source_max query is skipped, so we only
        # need watermark to be safe.
        watermark_row = (None,)

        mock_conn = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_cur = MagicMock()
        mock_cur.__enter__ = MagicMock(return_value=mock_cur)
        mock_cur.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value = mock_cur

        # Track which query was last executed, so fetchall/fetchone
        # return the right shape.
        last_sql = {"value": ""}

        def execute(sql, params=None):
            last_sql["value"] = sql
            return None

        def fetchall():
            sql = last_sql["value"]
            if "continuous_aggregates" in sql:
                return [meta_row]
            if "_timescaledb_catalog.continuous_agg" in sql:
                return [mat_id_row]
            return []

        def fetchone():
            sql = last_sql["value"]
            if "MAX(range_start)" in sql:
                return cutoff_row
            if "cagg_watermark" in sql:
                return watermark_row
            return (None,)

        mock_cur.execute.side_effect = execute
        mock_cur.fetchall.side_effect = fetchall
        mock_cur.fetchone.side_effect = fetchone
        return mock_conn, mock_cur

    def test_status_queries_timescaledb_information(self):
        s = _settings()
        mock_conn, mock_cur = self._make_status_mock()

        with _patch_app(s):
            with patch("psycopg.connect", return_value=mock_conn):
                result = runner.invoke(app, ["data", "caggs", "status"])
        assert result.exit_code == 0
        # Both expected source-of-truth queries ran.
        executed_sqls = [
            call.args[0] for call in mock_cur.execute.call_args_list
        ]
        assert any("continuous_aggregates" in s for s in executed_sqls)
        assert any(
            "_timescaledb_catalog.continuous_agg" in s for s in executed_sqls
        )

    def test_json_output(self):
        import json

        s = _settings()
        mock_conn, _ = self._make_status_mock()

        with _patch_app(s):
            with patch("psycopg.connect", return_value=mock_conn):
                result = runner.invoke(
                    app, ["data", "caggs", "status", "--json"]
                )
        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert isinstance(payload, list)
        assert payload[0]["view"] == "minute_5min_ohlcv"
        assert payload[0]["policy_installed"] is True

    def test_bc_sentinel_watermark_treated_as_unmaterialized(self):
        """Regression test (slice 156).

        cagg_watermark() returns -210866803200000000 (microsecond rep of
        '4714-11-24 BC') for a never-materialized cagg. Decoding via
        to_timestamp() crashes psycopg's TimestamptzLoader; the fix
        branches on the raw bigint and treats it as None.
        """
        from manta_trading.cli.commands.data import _CAGG_WATERMARK_MIN_VALID_US

        # Sentinel observed in production
        assert -210866803200000000 < _CAGG_WATERMARK_MIN_VALID_US
        # MIN_INT64 also covered
        assert -9223372036854775808 < _CAGG_WATERMARK_MIN_VALID_US
        # 1970-01-01 is >= bound (i.e. valid)
        assert 0 >= _CAGG_WATERMARK_MIN_VALID_US
        # Modern data well above bound
        assert 1_700_000_000_000_000 > _CAGG_WATERMARK_MIN_VALID_US

    def test_bc_sentinel_does_not_invoke_to_timestamp(self):
        """The to_timestamp() conversion must be skipped when the raw
        watermark is the BC sentinel — otherwise psycopg crashes.
        """
        s = _settings()
        meta_row = ("minute_5min_ohlcv", 1, None, None, None, None)
        mat_id_row = ("minute_5min_ohlcv", 18)
        # Production-observed BC sentinel
        bc_sentinel = -210866803200000000

        mock_conn = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_cur = MagicMock()
        mock_cur.__enter__ = MagicMock(return_value=mock_cur)
        mock_cur.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value = mock_cur

        last_sql = {"value": ""}

        def execute(sql, params=None):
            last_sql["value"] = sql
            return None

        def fetchall():
            sql = last_sql["value"]
            if "continuous_aggregates" in sql:
                return [meta_row]
            if "_timescaledb_catalog.continuous_agg" in sql:
                return [mat_id_row]
            return []

        def fetchone():
            sql = last_sql["value"]
            if "MAX(range_start)" in sql:
                return (None,)
            if "cagg_watermark" in sql:
                return (bc_sentinel,)
            if "to_timestamp" in sql:
                # Should NOT be reached; if it is, fail loudly
                raise AssertionError(
                    "to_timestamp called on BC sentinel — sentinel guard broken"
                )
            return (None,)

        mock_cur.execute.side_effect = execute
        mock_cur.fetchall.side_effect = fetchall
        mock_cur.fetchone.side_effect = fetchone

        with _patch_app(s):
            with patch("psycopg.connect", return_value=mock_conn):
                result = runner.invoke(app, ["data", "caggs", "status"])
        assert result.exit_code == 0, result.output
        # to_timestamp should not have been executed
        executed_sqls = [
            call.args[0] for call in mock_cur.execute.call_args_list
        ]
        assert not any("to_timestamp" in s for s in executed_sqls)
