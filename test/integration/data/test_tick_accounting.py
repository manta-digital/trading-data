"""The futures tick line in ``mt data accounting`` (slice 230, TD7, TD8; FR12, FR14).

The September 2024 roll is seeded (``roll_seed``) under the real universe
entry (tbbo wanted from 2024-11-02, so the scope is the held September
sessions plus the wanted range). The minute scan is stubbed to no rows: the
minute half has its own tests, and only its presence and exit matter here.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any
from unittest.mock import patch

import psycopg
import pytest
from tick_support.cli import clear_tick_env, cli_settings, closed_port
from tick_support.roll_seed import (
    SeededRoll,
    seed_september_roll,
    september_volumes,
)
from typer.testing import CliRunner

from manta_trading.cli.app import app
from manta_trading.data.acquisition.pass_runs import PassKind
from manta_trading.data.gaps.minute_accounting import summary_line
from manta_trading.data.tick.tick_accounting import accounting_lines
from manta_trading.data.tick.tick_status_build import build_status
from manta_trading.data.tick.universe import TICK_UNIVERSE

runner = CliRunner(env={"COLUMNS": "200"})
MINUTE_SUMMARY = summary_line([])


@pytest.fixture
def no_minute_scan() -> Iterator[None]:
    with patch(
        "manta_trading.cli.commands.accounting.compute_minute_accounting",
        return_value=[],
    ):
        yield


@pytest.fixture
def seeded(migrated_tick_db: str, monkeypatch: pytest.MonkeyPatch) -> SeededRoll:
    clear_tick_env(monkeypatch)
    return seed_september_roll(migrated_tick_db)


def _accounting(*args: str) -> Any:
    return runner.invoke(app, ["data", "accounting", *args])


async def test_the_line_equals_status_buckets(
    seeded: SeededRoll, migrated_tick_db: str, session_migrated_db: str
) -> None:
    async with await psycopg.AsyncConnection.connect(migrated_tick_db) as conn:
        lines = await accounting_lines(conn, session_migrated_db, TICK_UNIVERSE)
        status = await build_status(
            conn,
            session_migrated_db,
            TICK_UNIVERSE,
            datetime.now(UTC),
            tick_url=migrated_tick_db,
        )

    # One line per universe product; only ES is seeded (232 added GC).
    assert [x.product for x in lines] == [e.product for e in TICK_UNIVERSE]
    (line,) = [x for x in lines if x.product == "ES"]
    (product,) = [p for p in status.products if p.product == "ES"]
    assert line.buckets == product.buckets  # FR12: the same function
    assert line.sessions_in_scope == sum(product.buckets.values())
    assert line.sessions_complete == product.buckets["complete"] > 0
    # Records: the ledger sum over current units, spreads included.
    assert line.records == sum(september_volumes().values())


def test_text_and_json_with_a_reachable_tick_database(
    seeded: SeededRoll,
    migrated_tick_db: str,
    session_migrated_db: str,
    no_minute_scan: None,
) -> None:
    with cli_settings(migrated_tick_db, session_migrated_db):
        text = _accounting()
        body = json.loads(_accounting("--json").stdout)

    assert text.exit_code == 0, text.output
    lines = text.stdout.splitlines()
    assert MINUTE_SUMMARY in lines
    # One line per universe product after the header (232 added GC).
    header = lines.index("futures tick universe:")
    es_line, *others = lines[header + 1 :]
    assert len(others) == len(TICK_UNIVERSE) - 1
    assert es_line.startswith("  ES  ") and "sessions complete (" in es_line
    assert "records" in es_line
    tick = body["tick"]
    assert (tick["state"], tick["detail"]) == ("ok", None)
    (product,) = [p for p in tick["products"] if p["product"] == "ES"]
    assert list(product) == [
        "product",
        "sessions_in_scope",
        "sessions_complete",
        "buckets",
        "records",
    ]
    assert body["summary"] == MINUTE_SUMMARY  # existing keys unchanged


def _degraded(tick_url: str | None, calendar_url: str) -> Any:
    with cli_settings(tick_url, calendar_url):
        return _accounting(), json.loads(_accounting("--json").stdout)


def _recorded_detail(url: str) -> str:
    with psycopg.connect(url) as conn:
        row = conn.execute(
            "SELECT detail FROM pass_runs WHERE pass = %s"
            " ORDER BY started_at DESC LIMIT 1",
            (PassKind.ACCOUNTING.value,),
        ).fetchone()
    assert row is not None
    detail: str = row[0]
    return detail


@pytest.mark.parametrize("case", ["unset", "unreachable", "migrations_pending"])
def test_each_degraded_state_is_a_line_and_keeps_the_exit(
    case: str,
    ephemeral_tick_db: str,
    session_migrated_db: str,
    no_minute_scan: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clear_tick_env(monkeypatch)
    url = {
        "unset": None,
        "unreachable": f"postgresql://nobody@127.0.0.1:{closed_port()}/none",
        "migrations_pending": ephemeral_tick_db,  # bare: every migration pending
    }[case]
    expected = {
        "unset": "futures tick universe:  not configured (MT_TICK_DB_URL unset)",
        "unreachable": "futures tick universe:  unreachable: ",
        "migrations_pending": (
            "futures tick universe:  migrations pending"
            " (mt data migrate apply --track tick)"
        ),
    }[case]

    text, body = _degraded(url, session_migrated_db)

    assert text.exit_code == 0, text.output  # as without the tick line
    assert MINUTE_SUMMARY in text.stdout  # the minute section still prints
    assert text.stdout.splitlines()[-1].startswith(expected)
    state = "not_configured" if case == "unset" else case
    assert (body["tick"]["state"], body["tick"]["products"]) == (state, [])
    # The recorded pass detail is the minute summary alone (TD7).
    assert _recorded_detail(session_migrated_db) == MINUTE_SUMMARY
