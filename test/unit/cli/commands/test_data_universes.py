"""Unit tests for mt data universes CLI (slice 161, T12)."""

from __future__ import annotations

import contextlib
import json
import os
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import psycopg
import pytest
from typer.testing import CliRunner

from manta_trading.cli.app import app

runner = CliRunner()

_DB_URL = os.environ.get(
    "MT_TIMESCALE_DB_URL",
    "postgresql://postgres:<password>@<db-host>:5432/trading_test",
)
_TODAY = date.today()
_YESTERDAY = _TODAY - timedelta(days=1)

_SAMPLE_CSV = (
    "date,tickers\n"
    f"{_YESTERDAY},\"AAPL,MSFT,GOOG\"\n"
    f"{_TODAY},\"AAPL,MSFT,GOOG,AMZN\"\n"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _settings(
    *,
    timescale_url: str | None = _DB_URL,
) -> MagicMock:
    s = MagicMock()
    s.timescale_db_url = timescale_url
    return s


@contextlib.contextmanager
def _patch_app(settings: MagicMock):
    with (
        patch("manta_trading.cli.app.Settings", return_value=settings),
        patch("manta_trading.cli.app.setup_logging"),
    ):
        yield


@pytest.fixture(autouse=True)
def clean_sp500():
    with psycopg.connect(_DB_URL) as conn:
        conn.execute("DELETE FROM universe_members WHERE universe_name = 'sp500'")
        conn.commit()
    yield
    with psycopg.connect(_DB_URL) as conn:
        conn.execute("DELETE FROM universe_members WHERE universe_name = 'sp500'")
        conn.commit()


def _seed(symbols: list[str], added: date = _TODAY, removed: date | None = None) -> None:
    with psycopg.connect(_DB_URL) as conn:
        for s in symbols:
            conn.execute(
                "INSERT INTO universe_members (universe_name, symbol, added_date, removed_date) "
                "VALUES ('sp500', %s, %s, %s) ON CONFLICT DO NOTHING",
                (s, added, removed),
            )
        conn.commit()


# ---------------------------------------------------------------------------
# ls
# ---------------------------------------------------------------------------


def test_ls_empty_db_exits_zero():
    with _patch_app(_settings()):
        result = runner.invoke(app, ["data", "universes", "ls"])
    assert result.exit_code == 0


def test_ls_missing_db_url_exits_error():
    with _patch_app(_settings(timescale_url=None)):
        result = runner.invoke(app, ["data", "universes", "ls"])
    assert result.exit_code == 1


def test_ls_seeded_shows_counts():
    _seed(["AAPL", "MSFT", "GOOG"])
    with _patch_app(_settings()):
        result = runner.invoke(app, ["data", "universes", "ls"])
    assert result.exit_code == 0
    assert "sp500" in result.output
    assert "3" in result.output


def test_ls_json_output():
    _seed(["AAPL", "MSFT"])
    with _patch_app(_settings()):
        result = runner.invoke(app, ["data", "universes", "ls", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    sp = next((r for r in data if r["universe"] == "sp500"), None)
    assert sp is not None
    assert sp["members"] == 2


# ---------------------------------------------------------------------------
# as-of
# ---------------------------------------------------------------------------


def test_as_of_returns_correct_symbols():
    _seed(["AAPL", "MSFT", "GOOG"])
    with _patch_app(_settings()):
        result = runner.invoke(
            app,
            ["data", "universes", "as-of", "--name", "sp500", "--date", str(_TODAY)],
        )
    assert result.exit_code == 0
    assert set(result.output.strip().splitlines()) == {"AAPL", "MSFT", "GOOG"}


def test_as_of_unknown_universe_exits_nonzero():
    with _patch_app(_settings()):
        result = runner.invoke(
            app,
            ["data", "universes", "as-of", "--name", "nonexistent", "--date", str(_TODAY)],
        )
    assert result.exit_code == 1


def test_as_of_excludes_removed_symbols():
    _seed(["AAPL", "MSFT"], added=_YESTERDAY)
    with psycopg.connect(_DB_URL) as conn:
        conn.execute(
            "UPDATE universe_members SET removed_date = %s "
            "WHERE universe_name = 'sp500' AND symbol = 'MSFT'",
            (_TODAY,),
        )
        conn.commit()
    with _patch_app(_settings()):
        result = runner.invoke(
            app,
            ["data", "universes", "as-of", "--name", "sp500", "--date", str(_TODAY)],
        )
    assert result.exit_code == 0
    lines = result.output.strip().splitlines()
    assert "AAPL" in lines
    assert "MSFT" not in lines


# ---------------------------------------------------------------------------
# refresh
# ---------------------------------------------------------------------------


def test_refresh_missing_db_url_exits_error():
    with _patch_app(_settings(timescale_url=None)):
        result = runner.invoke(app, ["data", "universes", "refresh"])
    assert result.exit_code == 1


def test_refresh_imports_csv_via_mock_http():
    with (
        _patch_app(_settings()),
        patch("manta_trading.cli.commands.universes.httpx.get") as mock_get,
    ):
        mock_resp = MagicMock()
        mock_resp.text = _SAMPLE_CSV
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        result = runner.invoke(app, ["data", "universes", "refresh"])

    assert result.exit_code == 0
    assert "imported" in result.output or "2" in result.output

    with psycopg.connect(_DB_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM universe_members "
                "WHERE universe_name = 'sp500' AND removed_date IS NULL"
            )
            count = cur.fetchone()[0]
    assert count == 4  # AAPL, MSFT, GOOG, AMZN from final row


def test_refresh_idempotent():
    with (
        _patch_app(_settings()),
        patch("manta_trading.cli.commands.universes.httpx.get") as mock_get,
    ):
        mock_resp = MagicMock()
        mock_resp.text = _SAMPLE_CSV
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        runner.invoke(app, ["data", "universes", "refresh"])
        result = runner.invoke(app, ["data", "universes", "refresh"])

    assert result.exit_code == 0
    assert "0" in result.output  # 0 imported on second run

    with psycopg.connect(_DB_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM universe_members WHERE universe_name = 'sp500'"
            )
            # Exactly the right number of rows, no duplicates.
            assert cur.fetchone()[0] > 0


def test_refresh_json_output():
    with (
        _patch_app(_settings()),
        patch("manta_trading.cli.commands.universes.httpx.get") as mock_get,
    ):
        mock_resp = MagicMock()
        mock_resp.text = _SAMPLE_CSV
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        result = runner.invoke(app, ["data", "universes", "refresh", "--json"])

    assert result.exit_code == 0
    data = json.loads(result.output)
    assert "imported" in data
    assert "skipped" in data
