"""Unit tests for mt data caggs commands (slice 154)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

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


# ---------------------------------------------------------------------------
# caggs verify (slice 163)
# ---------------------------------------------------------------------------


def _parity_report(*, granularity, in_parity: bool):
    """Build a minimal CaggParityReport double for CLI tests.

    Uses the real dataclasses so property logic (in_parity, coverage, totals)
    is exercised end-to-end from real window counts."""
    from datetime import datetime, timezone

    from manta_trading.constants import MINUTE_CAGG_CHUNK_INTERVAL
    from manta_trading.market.maintenance.cagg_parity import (
        CaggChunkSummary,
        CaggParityReport,
        WindowCounts,
        rollup_by_year,
    )

    def _utc(y):
        return datetime(y, 1, 1, tzinfo=timezone.utc)

    cagg = 1000 if in_parity else 208  # 100% vs ~21% coverage
    windows = [WindowCounts(_utc(2019), _utc(2019).replace(month=3), 1000, cagg)]
    view = {
        "5m": "minute_5min_ohlcv",
        "15m": "minute_15min_ohlcv",
        "1h": "minute_hourly_ohlcv",
        "4h": "minute_4hour_ohlcv",
    }[granularity.value]
    return CaggParityReport(
        granularity=granularity,
        view_name=view,
        windows=windows,
        years=rollup_by_year(windows),
        chunk_summary=CaggChunkSummary(view, 117, MINUTE_CAGG_CHUNK_INTERVAL),
    )


class TestCaggsVerify:
    def test_missing_url_exits_with_error(self):
        s = _settings(timescale_url=None)
        with _patch_app(s):
            result = runner.invoke(app, ["data", "caggs", "verify"])
        assert result.exit_code != 0

    def test_unknown_granularity_token_errors(self):
        s = _settings()
        with _patch_app(s):
            result = runner.invoke(
                app, ["data", "caggs", "verify", "--granularity", "99x"]
            )
        assert result.exit_code != 0
        assert "Unknown granularity token" in result.output

    def test_daily_granularity_rejected(self):
        # 1d is a valid Granularity but not a minute cagg — must be refused.
        s = _settings()
        with _patch_app(s):
            result = runner.invoke(
                app, ["data", "caggs", "verify", "--granularity", "1d"]
            )
        assert result.exit_code != 0
        assert "not a minute cagg" in result.output

    def test_full_parity_exits_zero(self):
        from manta_trading.constants import Granularity

        s = _settings()
        reports = [_parity_report(granularity=g, in_parity=True)
                   for g in (Granularity.M5, Granularity.M15,
                             Granularity.H1, Granularity.H4)]
        with _patch_app(s):
            with patch(
                "manta_trading.market.maintenance.cagg_parity.compute_parity",
                return_value=reports,
            ):
                result = runner.invoke(app, ["data", "caggs", "verify"])
        assert result.exit_code == 0, result.output

    def test_parity_failure_exits_nonzero(self):
        from manta_trading.cli.commands.data import _EXIT_PARITY_FAILURE
        from manta_trading.constants import Granularity

        s = _settings()
        reports = [_parity_report(granularity=Granularity.H4, in_parity=False)]
        with _patch_app(s):
            with patch(
                "manta_trading.market.maintenance.cagg_parity.compute_parity",
                return_value=reports,
            ):
                result = runner.invoke(
                    app, ["data", "caggs", "verify", "--granularity", "4h"]
                )
        assert result.exit_code == _EXIT_PARITY_FAILURE
        assert "PARITY FAILURE" in result.output

    def test_detail_flag_reports_per_window(self):
        from manta_trading.constants import Granularity

        s = _settings()
        reports = [_parity_report(granularity=Granularity.H4, in_parity=False)]
        with _patch_app(s):
            with patch(
                "manta_trading.market.maintenance.cagg_parity.compute_parity",
                return_value=reports,
            ):
                result = runner.invoke(
                    app,
                    ["data", "caggs", "verify", "--granularity", "4h", "--detail"],
                )
        # Per-window detail prints a window start date (2019-01-01), not a bare year.
        assert "2019-01-01" in result.output

    def test_json_output_shape_and_exit(self):
        import json

        from manta_trading.cli.commands.data import _EXIT_PARITY_FAILURE
        from manta_trading.constants import Granularity

        s = _settings()
        reports = [_parity_report(granularity=Granularity.H4, in_parity=False)]
        with _patch_app(s):
            with patch(
                "manta_trading.market.maintenance.cagg_parity.compute_parity",
                return_value=reports,
            ):
                result = runner.invoke(
                    app,
                    ["data", "caggs", "verify", "--granularity", "4h", "--json"],
                )
        assert result.exit_code == _EXIT_PARITY_FAILURE
        payload = json.loads(result.output)
        assert payload[0]["view"] == "minute_4hour_ohlcv"
        assert payload[0]["in_parity"] is False
        assert payload[0]["chunk_count"] == 117
        assert isinstance(payload[0]["rows"], list)

    def test_granularity_order_is_canonical(self):
        """Requesting out-of-order tokens still verifies smallest-first."""
        from manta_trading.constants import Granularity

        s = _settings()
        captured = {}

        def _fake_compute(url, grans):
            captured["grans"] = grans
            return [_parity_report(granularity=g, in_parity=True) for g in grans]

        with _patch_app(s):
            with patch(
                "manta_trading.market.maintenance.cagg_parity.compute_parity",
                side_effect=_fake_compute,
            ):
                result = runner.invoke(
                    app, ["data", "caggs", "verify", "--granularity", "4h,5m"]
                )
        assert result.exit_code == 0, result.output
        # Canonical order is 5m, 15m, 1h, 4h → requested {4h,5m} → (5m, 4h).
        assert captured["grans"] == (Granularity.M5, Granularity.H4)


# ---------------------------------------------------------------------------
# caggs repair (slice 163)
# ---------------------------------------------------------------------------


class TestCaggsRepair:
    def test_missing_url_exits_preflight_code(self):
        from manta_trading.cli.commands.data import _EXIT_REPAIR_PREFLIGHT

        s = _settings(timescale_url=None)
        with _patch_app(s):
            result = runner.invoke(app, ["data", "caggs", "repair"])
        assert result.exit_code == _EXIT_REPAIR_PREFLIGHT

    def test_unknown_granularity_errors(self):
        s = _settings()
        with _patch_app(s):
            result = runner.invoke(
                app, ["data", "caggs", "repair", "--granularity", "99x"]
            )
        assert result.exit_code != 0
        assert "Unknown granularity token" in result.output

    def test_dry_run_flag_propagates(self):
        from manta_trading.market.maintenance.cagg_repair import RepairResult

        s = _settings()
        captured = {}

        def _fake_repair(url, grans, *, dry_run, assume_headroom_gb, progress):
            captured["dry_run"] = dry_run
            captured["headroom"] = assume_headroom_gb
            return RepairResult(dry_run=dry_run)

        with _patch_app(s):
            with patch(
                "manta_trading.market.maintenance.cagg_repair.run_repair",
                side_effect=_fake_repair,
            ):
                result = runner.invoke(
                    app, ["data", "caggs", "repair", "--dry-run"]
                )
        assert result.exit_code == 0, result.output
        assert captured["dry_run"] is True
        assert captured["headroom"] is None

    def test_headroom_flag_propagates(self):
        from manta_trading.market.maintenance.cagg_repair import RepairResult

        s = _settings()
        captured = {}

        def _fake_repair(url, grans, *, dry_run, assume_headroom_gb, progress):
            captured["headroom"] = assume_headroom_gb
            return RepairResult(dry_run=dry_run)

        with _patch_app(s):
            with patch(
                "manta_trading.market.maintenance.cagg_repair.run_repair",
                side_effect=_fake_repair,
            ):
                result = runner.invoke(
                    app,
                    ["data", "caggs", "repair", "--granularity", "4h",
                     "--assume-headroom-gb", "50"],
                )
        assert result.exit_code == 0, result.output
        assert captured["headroom"] == 50.0

    def test_preflight_refusal_surfaces_exit_code(self):
        from manta_trading.cli.commands.data import _EXIT_REPAIR_PREFLIGHT
        from manta_trading.market.maintenance.rechunk import PreflightError

        s = _settings()
        with _patch_app(s):
            with patch(
                "manta_trading.market.maintenance.cagg_repair.run_repair",
                side_effect=PreflightError("job 1003 still scheduled"),
            ):
                result = runner.invoke(
                    app, ["data", "caggs", "repair", "--granularity", "4h"]
                )
        assert result.exit_code == _EXIT_REPAIR_PREFLIGHT
        assert "Pre-flight refused" in result.output
        assert "1003" in result.output

    def test_repair_failure_surfaces_exit_code(self):
        from manta_trading.cli.commands.data import _EXIT_REPAIR_FAILED
        from manta_trading.market.maintenance.cagg_repair import RepairError

        s = _settings()
        with _patch_app(s):
            with patch(
                "manta_trading.market.maintenance.cagg_repair.run_repair",
                side_effect=RepairError("window 2020-01-01 rebuild failed"),
            ):
                result = runner.invoke(
                    app, ["data", "caggs", "repair", "--granularity", "4h"]
                )
        assert result.exit_code == _EXIT_REPAIR_FAILED
        assert "Repair failed" in result.output

    def test_interrupt_surfaces_resume_message(self):
        from manta_trading.cli.commands.data import _EXIT_REPAIR_INTERRUPTED

        s = _settings()
        with _patch_app(s):
            with patch(
                "manta_trading.market.maintenance.cagg_repair.run_repair",
                side_effect=KeyboardInterrupt(),
            ):
                result = runner.invoke(
                    app, ["data", "caggs", "repair", "--granularity", "4h"]
                )
        assert result.exit_code == _EXIT_REPAIR_INTERRUPTED
        assert "resume" in result.output.lower()

    def test_granularity_subset_propagates_canonical_order(self):
        from manta_trading.constants import Granularity
        from manta_trading.market.maintenance.cagg_repair import RepairResult

        s = _settings()
        captured = {}

        def _fake_repair(url, grans, *, dry_run, assume_headroom_gb, progress):
            captured["grans"] = grans
            return RepairResult(dry_run=dry_run)

        with _patch_app(s):
            with patch(
                "manta_trading.market.maintenance.cagg_repair.run_repair",
                side_effect=_fake_repair,
            ):
                result = runner.invoke(
                    app,
                    ["data", "caggs", "repair", "--granularity", "1h,15m",
                     "--dry-run"],
                )
        assert result.exit_code == 0, result.output
        # Canonical order: 5m,15m,1h,4h → requested {1h,15m} → (15m, 1h).
        assert captured["grans"] == (Granularity.M15, Granularity.H1)

    def test_real_run_refuses_default_all_with_run_order(self):
        # Review F001: no static pause configuration satisfies pre-flight for
        # an all-cagg real sweep (the 4h cagg is both target and coverage
        # source), so a real run must name exactly one granularity. The
        # refusal happens BEFORE run_repair and carries the recommended order.
        from manta_trading.cli.commands.data import _EXIT_REPAIR_PREFLIGHT

        s = _settings()
        with _patch_app(s):
            with patch(
                "manta_trading.market.maintenance.cagg_repair.run_repair",
            ) as fake_repair:
                result = runner.invoke(app, ["data", "caggs", "repair"])
        assert result.exit_code == _EXIT_REPAIR_PREFLIGHT
        fake_repair.assert_not_called()
        # Rich wraps long lines; normalize whitespace before matching.
        flat = " ".join(result.output.split())
        assert "exactly ONE granularity" in flat
        assert "4h -> 1h -> 15m -> 5m" in flat
        assert "cagg-maintenance-pausing" in flat

    def test_real_run_refuses_multi_granularity(self):
        from manta_trading.cli.commands.data import _EXIT_REPAIR_PREFLIGHT

        s = _settings()
        with _patch_app(s):
            with patch(
                "manta_trading.market.maintenance.cagg_repair.run_repair",
            ) as fake_repair:
                result = runner.invoke(
                    app, ["data", "caggs", "repair", "--granularity", "1h,15m"]
                )
        assert result.exit_code == _EXIT_REPAIR_PREFLIGHT
        fake_repair.assert_not_called()

    def test_completed_run_prints_resume_reminder(self):
        # Review F008: pre-flight required the target's refresh + columnstore
        # policies paused; completion must remind the operator to resume them
        # (an unresumed columnstore policy leaves late-sweep chunks
        # uncompressed indefinitely) and point at the catch-up refresh.
        from manta_trading.market.maintenance.cagg_repair import RepairResult

        s = _settings()
        with _patch_app(s):
            with patch(
                "manta_trading.market.maintenance.cagg_repair.run_repair",
                return_value=RepairResult(dry_run=False),
            ):
                result = runner.invoke(
                    app, ["data", "caggs", "repair", "--granularity", "4h"]
                )
        assert result.exit_code == 0, result.output
        out = result.output
        assert "resume" in out.lower()
        assert "columnstore" in out.lower()
        assert "cagg-maintenance-pausing" in out

    def test_dry_run_output_omits_resume_reminder(self):
        # Dry run pauses nothing, so the resume reminder would be noise.
        from manta_trading.market.maintenance.cagg_repair import RepairResult

        s = _settings()
        with _patch_app(s):
            with patch(
                "manta_trading.market.maintenance.cagg_repair.run_repair",
                return_value=RepairResult(dry_run=True),
            ):
                result = runner.invoke(app, ["data", "caggs", "repair", "--dry-run"])
        assert result.exit_code == 0, result.output
        assert "NEXT:" not in result.output
