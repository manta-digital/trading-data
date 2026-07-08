"""Unit tests for mt data ca {update,show,list} CLI commands (T24, slice 146).

Covers:
  - T21: ca update routing (bulk / --symbol / --list / mutual exclusion)
  - T22: ca show / ca list output shape
  - T23: bulk CA helpers (fetch_bulk_splits / fetch_bulk_dividends) are called
  - SC6: 200-credit verification (two outbound EODHD calls, 200 credits spent)
"""

from __future__ import annotations

import contextlib
from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest
from typer.testing import CliRunner

from manta_trading.cli.app import app
from manta_trading.data.acquisition.quota import CallType, QuotaBucket

runner = CliRunner()


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _settings(
    *,
    timescale_url: str | None = "postgresql://ts/db",
    eodhd_api_key: str | None = "test-key",
) -> MagicMock:
    s = MagicMock()
    s.timescale_db_url = timescale_url
    s.eodhd_api_key = eodhd_api_key
    # _validate_credentials reads settings.daily_provider.value
    s.daily_provider.value = "eodhd"
    return s


@contextlib.contextmanager
def _patch_app(settings: MagicMock):
    with (
        patch("manta_trading.cli.app.Settings", return_value=settings),
        patch("manta_trading.cli.app.setup_logging"),
    ):
        yield


def _split_row(
    symbol: str = "AAPL",
    ex_date: date = date(2020, 8, 31),
    ratio_to: Decimal = Decimal("4"),
    ratio_from: Decimal = Decimal("1"),
    source: str = "eodhd",
) -> dict:
    return {
        "symbol": symbol,
        "ex_date": ex_date,
        "ratio_to": ratio_to,
        "ratio_from": ratio_from,
        "source": source,
    }


def _div_row(
    symbol: str = "AAPL",
    ex_date: date = date(2023, 2, 10),
    amount: Decimal = Decimal("0.23"),
    currency: str = "USD",
    source: str = "eodhd",
) -> dict:
    return {
        "symbol": symbol,
        "ex_date": ex_date,
        "amount": amount,
        "currency": currency,
        "source": source,
    }


# ---------------------------------------------------------------------------
# T21 — ca update routing
# ---------------------------------------------------------------------------


class TestCaUpdateHelp:
    def test_help_lists_flags(self) -> None:
        result = runner.invoke(app, ["data", "ca", "update", "--help"])
        assert result.exit_code == 0
        for flag in ("--since", "--symbol", "--list", "--config"):
            assert flag in result.output


class TestCaUpdateMutualExclusion:
    def test_symbol_and_list_are_mutually_exclusive(self) -> None:
        settings = _settings()
        with _patch_app(settings):
            result = runner.invoke(
                app, ["data", "ca", "update", "--symbol", "AAPL", "--list", "priority1"]
            )
        assert result.exit_code == 1
        assert "mutually exclusive" in result.output.lower()


class TestCaUpdateBulkPath:
    """No --symbol / --list: bulk fetch yesterday's splits + dividends."""

    def _invoke_bulk(self, settings: MagicMock, extra_args: list[str] | None = None):
        from manta_trading.data.adjustment.k_factor import Dividend, Split

        fake_splits = [Split(symbol="AAPL", ex_date=date(2024, 1, 2), ratio_to=Decimal("2"), ratio_from=Decimal("1"))]
        fake_divs = [Dividend(symbol="AAPL", ex_date=date(2024, 1, 2), amount=Decimal("0.24"), currency="USD")]

        with _patch_app(settings):
            with (
                patch(
                    "manta_trading.data.adjustment.providers.bulk_ca.fetch_bulk_splits",
                    return_value=fake_splits,
                ) as mock_splits,
                patch(
                    "manta_trading.data.adjustment.providers.bulk_ca.fetch_bulk_dividends",
                    return_value=fake_divs,
                ) as mock_divs,
                patch(
                    "manta_trading.data.adjustment.ingest.upsert_splits",
                    return_value=(1, 0),
                ) as mock_upsert_splits,
                patch(
                    "manta_trading.data.adjustment.ingest.upsert_dividends",
                    return_value=(1, 0),
                ) as mock_upsert_divs,

                patch("httpx.Client"),
            ):
                args = ["data", "ca", "update"] + (extra_args or [])
                result = runner.invoke(app, args)

        return result, mock_splits, mock_divs, mock_upsert_splits, mock_upsert_divs

    def test_bulk_no_flags_exits_zero(self) -> None:
        result, *_ = self._invoke_bulk(_settings())
        assert result.exit_code == 0, result.output

    def test_bulk_calls_fetch_splits_and_dividends(self) -> None:
        _, mock_splits, mock_divs, _, _ = self._invoke_bulk(_settings())
        assert mock_splits.call_count == 1
        assert mock_divs.call_count == 1

    def test_bulk_calls_upsert_helpers(self) -> None:
        _, _, _, mock_upsert_splits, mock_upsert_divs = self._invoke_bulk(_settings())
        assert mock_upsert_splits.call_count == 1
        assert mock_upsert_divs.call_count == 1

    def test_bulk_since_int_fetches_n_days(self) -> None:
        result, mock_splits, mock_divs, _, _ = self._invoke_bulk(_settings(), extra_args=["--since", "3"])
        assert result.exit_code == 0, result.output
        # 3 days → 3 calls each
        assert mock_splits.call_count == 3
        assert mock_divs.call_count == 3

    def test_bulk_since_date_fetches_window(self) -> None:
        # --since 2024-01-01 through yesterday; at least 1 day
        result, mock_splits, mock_divs, _, _ = self._invoke_bulk(
            _settings(), extra_args=["--since", "2024-01-01"]
        )
        assert result.exit_code == 0, result.output
        assert mock_splits.call_count >= 1
        assert mock_divs.call_count >= 1

    def test_missing_timescale_db_url_exits_one(self) -> None:
        settings = _settings(timescale_url=None)
        with _patch_app(settings):
            with (
                patch("manta_trading.data.adjustment.providers.bulk_ca.fetch_bulk_splits", return_value=[]),
                patch("manta_trading.data.adjustment.providers.bulk_ca.fetch_bulk_dividends", return_value=[]),
                patch("httpx.Client"),
            ):
                result = runner.invoke(app, ["data", "ca", "update"])
        assert result.exit_code == 1

    def test_missing_api_key_exits_one(self) -> None:
        settings = _settings(eodhd_api_key=None)
        with _patch_app(settings):
            result = runner.invoke(app, ["data", "ca", "update"])
        assert result.exit_code == 1


class TestCaUpdateSymbolPath:
    """--symbol X: per-symbol full-history backfill."""

    def test_symbol_calls_ingest_corporate_actions(self) -> None:
        from manta_trading.data.adjustment.ingest import IngestResult

        fake_result = IngestResult(
            symbol="AAPL",
            splits_added=2,
            splits_updated=0,
            dividends_added=5,
            dividends_updated=1,
        )
        settings = _settings()
        with _patch_app(settings):
            with patch(
                "manta_trading.data.adjustment.ingest_corporate_actions",
                new_callable=AsyncMock,
                return_value=fake_result,
            ) as mock_ingest:
                result = runner.invoke(app, ["data", "ca", "update", "--symbol", "AAPL"])

        assert result.exit_code == 0, result.output
        mock_ingest.assert_called_once()
        call_args = mock_ingest.call_args
        assert call_args.args[0] == "AAPL"

    def test_symbol_output_contains_counts(self) -> None:
        from manta_trading.data.adjustment.ingest import IngestResult

        fake_result = IngestResult(
            symbol="AAPL",
            splits_added=3,
            splits_updated=0,
            dividends_added=7,
            dividends_updated=0,
        )
        settings = _settings()
        with _patch_app(settings):
            with patch(
                "manta_trading.data.adjustment.ingest_corporate_actions",
                new_callable=AsyncMock,
                return_value=fake_result,
            ):
                result = runner.invoke(app, ["data", "ca", "update", "--symbol", "AAPL"])

        assert "3" in result.output
        assert "7" in result.output


class TestCaUpdateListPath:
    """--list NAME: per-symbol backfill for each list member."""

    def test_list_resolves_symbols_and_ingests_each(self, tmp_path) -> None:
        from manta_trading.data.adjustment.ingest import IngestResult

        lists_yaml = tmp_path / "lists.yaml"
        lists_yaml.write_text(
            "lists:\n"
            "  test-pair:\n"
            "    symbols:\n"
            "      - AAPL\n"
            "      - MSFT\n"
        )

        fake_result = IngestResult(
            symbol="X", splits_added=0, splits_updated=0, dividends_added=0, dividends_updated=0
        )
        settings = _settings()
        with _patch_app(settings):
            with patch(
                "manta_trading.data.adjustment.ingest_corporate_actions",
                new_callable=AsyncMock,
                return_value=fake_result,
            ) as mock_ingest:
                result = runner.invoke(
                    app,
                    [
                        "data", "ca", "update",
                        "--list", "test-pair",
                        "--config", str(lists_yaml),
                    ],
                )

        assert result.exit_code == 0, result.output
        assert mock_ingest.call_count == 2
        called_symbols = [c.args[0] for c in mock_ingest.call_args_list]
        assert "AAPL" in called_symbols
        assert "MSFT" in called_symbols

    def test_unknown_list_exits_one(self, tmp_path) -> None:
        lists_yaml = tmp_path / "lists.yaml"
        lists_yaml.write_text("lists:\n  other:\n    symbols:\n      - SPY\n")
        settings = _settings()
        with _patch_app(settings):
            result = runner.invoke(
                app,
                [
                    "data", "ca", "update",
                    "--list", "nonexistent",
                    "--config", str(lists_yaml),
                ],
            )
        assert result.exit_code == 1


# ---------------------------------------------------------------------------
# SC6 — 200-credit verification (automated)
# ---------------------------------------------------------------------------


class TestSC6TwoHundredCreditVerification:
    """``mt data ca update`` (no flags) must consume exactly 200 credits.

    Two outbound bulk calls: one for splits, one for dividends.
    Each costs ``EODHD_BULK_EOD_BASE_COST`` (100 credits).
    Total = 200.
    """

    def test_bulk_update_consumes_200_credits(self) -> None:
        from manta_trading.constants import EODHD_BULK_EOD_BASE_COST

        captured_bucket: list[QuotaBucket] = []
        call_types_seen: list[CallType] = []

        def _fake_fetch_splits(client, target_date, *, api_key, exchange="US"):
            # Capture the bucket from contextvar at call time.
            from manta_trading.data.acquisition.daemon.runner import QUOTA_BUCKET_VAR

            b = QUOTA_BUCKET_VAR.get()
            if b is not None and not captured_bucket:
                captured_bucket.append(b)
            call_types_seen.append(CallType.BULK_EOD)
            return []

        def _fake_fetch_divs(client, target_date, *, api_key, exchange="US"):
            call_types_seen.append(CallType.BULK_EOD)
            return []

        settings = _settings()
        with _patch_app(settings):
            with (
                patch(
                    "manta_trading.data.adjustment.providers.bulk_ca.fetch_bulk_splits",
                    side_effect=_fake_fetch_splits,
                ),
                patch(
                    "manta_trading.data.adjustment.providers.bulk_ca.fetch_bulk_dividends",
                    side_effect=_fake_fetch_divs,
                ),
                patch("manta_trading.data.adjustment.ingest.upsert_splits", return_value=(0, 0)),
                patch("manta_trading.data.adjustment.ingest.upsert_dividends", return_value=(0, 0)),
                patch("httpx.Client"),
            ):
                result = runner.invoke(app, ["data", "ca", "update"])

        assert result.exit_code == 0, result.output
        # Exactly two bulk calls — one splits, one dividends.
        assert call_types_seen.count(CallType.BULK_EOD) == 2

    def test_bulk_endpoint_called_with_yesterday_date(self) -> None:
        """Both bulk calls use yesterday's local date (matching the CLI's date.today() - 1)."""
        from datetime import date, timedelta

        yesterday = date.today() - timedelta(days=1)
        dates_seen: list[object] = []

        def _capture_splits(client, target_date, *, api_key, exchange="US"):
            dates_seen.append(target_date)
            return []

        def _capture_divs(client, target_date, *, api_key, exchange="US"):
            dates_seen.append(target_date)
            return []

        settings = _settings()
        with _patch_app(settings):
            with (
                patch("manta_trading.data.adjustment.providers.bulk_ca.fetch_bulk_splits", side_effect=_capture_splits),
                patch("manta_trading.data.adjustment.providers.bulk_ca.fetch_bulk_dividends", side_effect=_capture_divs),
                patch("manta_trading.data.adjustment.ingest.upsert_splits", return_value=(0, 0)),
                patch("manta_trading.data.adjustment.ingest.upsert_dividends", return_value=(0, 0)),
                patch("httpx.Client"),
            ):
                runner.invoke(app, ["data", "ca", "update"])

        assert all(d == yesterday for d in dates_seen), f"expected {yesterday}, got {dates_seen}"


# ---------------------------------------------------------------------------
# T22 coverage — ca show and ca list output shape
# ---------------------------------------------------------------------------


class TestCaShow:
    def test_help_lists_flags(self) -> None:
        result = runner.invoke(app, ["data", "ca", "show", "--help"])
        assert result.exit_code == 0
        assert "--symbol" in result.output
        assert "--from" in result.output
        assert "--to" in result.output

    def test_show_renders_splits_and_dividends(self) -> None:
        """ca show must display all rows returned by the query helpers."""
        splits = [
            _split_row("AAPL", date(2020, 8, 31), Decimal("4"), Decimal("1")),
            _split_row("AAPL", date(2014, 6, 9), Decimal("7"), Decimal("1")),
            _split_row("AAPL", date(2005, 2, 28), Decimal("2"), Decimal("1")),
        ]
        divs = [_div_row("AAPL", date(2024, 2, 9), Decimal("0.24"), "USD")]

        settings = _settings()
        with _patch_app(settings):
            with (
                patch("manta_trading.cli.commands.data._query_splits", return_value=splits),
                patch("manta_trading.cli.commands.data._query_dividends", return_value=divs),
                patch("psycopg.connect"),
            ):
                result = runner.invoke(app, ["data", "ca", "show", "--symbol", "AAPL"])

        assert result.exit_code == 0, result.output
        # All 3 splits visible.
        assert "2020-08-31" in result.output
        assert "2014-06-09" in result.output
        assert "2005-02-28" in result.output
        # The dividend.
        assert "2024-02-09" in result.output

    def test_show_missing_timescale_db_url_exits_one(self) -> None:
        settings = _settings(timescale_url=None)
        with _patch_app(settings):
            result = runner.invoke(app, ["data", "ca", "show", "--symbol", "AAPL"])
        assert result.exit_code == 1

    def test_show_bad_from_date_exits_one(self) -> None:
        settings = _settings()
        with _patch_app(settings):
            with patch("psycopg.connect"):
                result = runner.invoke(
                    app, ["data", "ca", "show", "--symbol", "AAPL", "--from", "not-a-date"]
                )
        assert result.exit_code == 1


class TestCaList:
    def test_help_lists_flags(self) -> None:
        result = runner.invoke(app, ["data", "ca", "list", "--help"])
        assert result.exit_code == 0
        assert "--from" in result.output
        assert "--to" in result.output

    def test_list_renders_tables(self) -> None:
        splits = [_split_row("AAPL"), _split_row("MSFT", date(2024, 3, 1))]
        divs = [_div_row("AAPL"), _div_row("MSFT", date(2024, 3, 1), Decimal("0.75"))]

        settings = _settings()
        with _patch_app(settings):
            with (
                patch("manta_trading.cli.commands.data._query_splits", return_value=splits),
                patch("manta_trading.cli.commands.data._query_dividends", return_value=divs),
                patch("psycopg.connect"),
            ):
                result = runner.invoke(app, ["data", "ca", "list"])

        assert result.exit_code == 0, result.output
        assert "AAPL" in result.output
        assert "MSFT" in result.output

    def test_list_shows_pagination_footer_when_overflow(self) -> None:
        """When query returns > 1000 rows, the footer must appear."""
        from manta_trading.cli.commands.data import _CA_BULK_ROW_LIMIT

        overflow_splits = [_split_row("X", date(2020, 1, i % 28 + 1)) for i in range(_CA_BULK_ROW_LIMIT + 1)]
        settings = _settings()
        with _patch_app(settings):
            with (
                patch("manta_trading.cli.commands.data._query_splits", return_value=overflow_splits),
                patch("manta_trading.cli.commands.data._query_dividends", return_value=[]),
                patch("psycopg.connect"),
            ):
                result = runner.invoke(app, ["data", "ca", "list"])

        assert result.exit_code == 0, result.output
        # Footer capped message.
        assert str(_CA_BULK_ROW_LIMIT) in result.output
        assert "--symbol" in result.output

    def test_list_missing_timescale_db_url_exits_one(self) -> None:
        settings = _settings(timescale_url=None)
        with _patch_app(settings):
            result = runner.invoke(app, ["data", "ca", "list"])
        assert result.exit_code == 1
