"""``mt data tick status`` roll lines (slice 229, FR7, FR8a, FR8b, FR8c).

The September 2024 roll is seeded (``roll_seed``). The unchanged status
behaviour (buckets, coverage, exit 4 on an unreachable database) is pinned by
``test_tick_status_cli.py``; this module pins what the roll lines add.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import fields
from typing import Any
from unittest.mock import patch

import psycopg
import pytest
from tick_support.roll_seed import ESZ4, seed_september_roll
from typer.testing import CliRunner

from manta_trading.cli.app import app
from manta_trading.config import Settings
from manta_trading.data.tick import active_contract, tick_status_build
from manta_trading.data.tick.constants import TICK_ENV_PREFIX
from manta_trading.data.tick.tick_status_build import ProductStatus

runner = CliRunner(env={"COLUMNS": "200"})
STATUS = ["data", "tick", "status", "--product", "ES"]


@contextmanager
def _settings(tick_url: str, calendar_url: str) -> Iterator[None]:
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        tick_db_url=tick_url,
        timescale_db_url=calendar_url,
    )
    with (
        patch("manta_trading.cli.app.Settings", side_effect=lambda: settings),
        patch("manta_trading.cli.app.setup_logging"),
    ):
        yield


@pytest.fixture
def seeded(
    migrated_tick_db: str, session_migrated_db: str, monkeypatch: pytest.MonkeyPatch
) -> Iterator[None]:
    for name in list(os.environ):
        if name.upper().startswith(TICK_ENV_PREFIX) or "DATABENTO" in name.upper():
            monkeypatch.delenv(name)
    seed_september_roll(migrated_tick_db)
    with _settings(migrated_tick_db, session_migrated_db):
        yield


def _status(*extra: str) -> Any:
    return runner.invoke(app, [*STATUS, *extra])


def test_status_shows_the_default_and_calendar_lines(seeded: None) -> None:
    result = _status()
    assert result.exit_code == 0, result.output
    out = result.stdout
    assert "ES  v.0 (default)  ESZ4 (183748)  exp 2024-12-20 13:30 UTC" in out
    assert "inputs: volume of session 2024-09-25" in out
    assert "ES  c.0            ESZ4 (183748)" in out
    assert "next roll: session 2024-12-23 → ESH5 (5002)" in out
    # Under the contract lines.
    assert out.index("contracts (") < out.index("v.0 (default)")


def test_status_json_keeps_every_field_and_adds_rolls(seeded: None) -> None:
    result = _status("--json")
    assert result.exit_code == 0, result.output
    body = json.loads(result.stdout)
    (product,) = body["products"]
    assert set(product) == {f.name for f in fields(ProductStatus)}
    assert body["exit_code"] == 0 and body["complete_basis"] == "units"
    volume, calendar = product["rolls"]
    assert (volume["rule"], volume["is_default"]) == ("v.0", True)
    assert (calendar["rule"], calendar["is_default"]) == ("c.0", False)
    for key in (
        "instrument_id",
        "raw_symbol",
        "expiration",
        "basis_session",
        "basis_unit_ids",
        "definition_unit_id",
        "next_roll",
        "known_through",
        "roll_no_later_than",
        "error",
    ):
        assert key in volume
    assert volume["raw_symbol"] == "ESZ4" and volume["error"] is None


def test_a_broken_invariant_in_one_rule_is_its_error_line_exit_3(
    seeded: None, migrated_tick_db: str
) -> None:
    """09-27 ranks by 09-25; with ESZ4's row gone there, v.0 breaks, c.0 not."""
    with psycopg.connect(migrated_tick_db, autocommit=True) as conn:
        conn.execute(
            "DELETE FROM tick_ingest_ledger WHERE session_date = '2024-09-25'"
            " AND instrument_id = %s",
            (ESZ4.instrument_id,),
        )
    result = _status()
    assert result.exit_code == 3, result.output
    out = result.stdout
    assert "ES  v.0 (default)  error: ESZ4 (183748) is listed in session" in out
    assert "ES  c.0            ESZ4 (183748)" in out
    assert "sessions held:" in out and "contracts (" in out


def _locking(url: str) -> Any:
    """``latest_active`` with the ledger locked for its whole run: a roll read
    that hangs after status's own reads finished."""
    real = tick_status_build.latest_active

    async def locked(*args: Any) -> Sequence[Any]:
        with psycopg.connect(url) as holder:
            holder.execute("LOCK TABLE tick_ingest_ledger IN ACCESS EXCLUSIVE MODE")
            try:
                return await real(*args)
            finally:
                holder.rollback()

    return locked


def test_a_roll_read_timeout_is_an_error_line_exit_3(
    seeded: None, migrated_tick_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(active_contract, "TICK_ROLL_STATEMENT_TIMEOUT_SECONDS", 1)
    monkeypatch.setattr(tick_status_build, "latest_active", _locking(migrated_tick_db))
    result = _status("--json")
    assert result.exit_code == 3, result.output
    body = json.loads(result.stdout)
    rolls = body["products"][0]["rolls"]
    assert all("statement timeout" in line["error"] for line in rolls)
    assert body["products"][0]["buckets"]["complete"] > 0
    assert body["exit_code"] == 3
