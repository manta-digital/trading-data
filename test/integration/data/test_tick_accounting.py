"""The futures tick line in ``mt data accounting`` (slice 230, TD7, TD8; FR12, FR14).

The September 2024 roll is seeded (``roll_seed``) under the real universe
entry (tbbo wanted from 2024-11-02, so the scope is the held September
sessions plus the wanted range). The minute scan is stubbed to no rows: the
minute half has its own tests, and only its presence and exit matter here.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import replace
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
from manta_trading.data.tick import family_report
from manta_trading.data.tick import universe as universe_module
from manta_trading.data.tick.family_report import ReadProgress, futures_completeness
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


async def test_there_is_one_line_per_product_and_family_equal_to_status(
    seeded: SeededRoll, migrated_tick_db: str, session_migrated_db: str
) -> None:
    async with await psycopg.AsyncConnection.connect(migrated_tick_db) as conn:
        reports = await futures_completeness(
            conn,
            session_migrated_db,
            TICK_UNIVERSE,
            ReadProgress(),
            cross_tier=True,
        )
        status = await build_status(
            conn,
            session_migrated_db,
            TICK_UNIVERSE,
            datetime.now(UTC),
            tick_url=migrated_tick_db,
        )
    lines = accounting_lines(reports)

    # One line per product and wanted family (232 added GC, 304 the others).
    expected = [(p.product, f.family) for p in status.products for f in p.families]
    assert [(x.product, x.family) for x in lines] == expected
    for product in status.products:
        for family in product.families:
            (line,) = [
                x
                for x in lines
                if (x.product, x.family) == (product.product, family.family)
            ]
            assert line.buckets == family.buckets  # FR12: the same function
            assert line.sessions_in_scope == sum(family.buckets.values())
            assert line.sessions_complete == family.buckets["complete"]
            assert line.caught_up == family.caught_up
            assert line.source_edge == family.source_edge
    (es,) = [x for x in lines if (x.product, x.family) == ("ES", "tick_tier")]
    (es_status,) = [p for p in status.products if p.product == "ES"]
    assert es.buckets == es_status.buckets
    assert es.sessions_complete == es_status.buckets["complete"] > 0
    # Records: the ledger sum over current units, spreads included.
    assert es.records == sum(september_volumes().values())


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
    header = lines.index("futures universe:")
    assert lines[header + 1] == "  ES"
    # The tick tier first, then the other wanted families, grouped by product.
    es_tier = lines[header + 2]
    assert es_tier.startswith("    tbbo  ") and "sessions complete (" in es_tier
    assert "records" in es_tier and "caught up" in es_tier
    assert any(x.startswith("    statistics  ") for x in lines[header:])
    assert "  GC" in lines[header:]
    # Criterion 7: the check that does not run is listed with its reason.
    assert (
        "  cross-tier ohlcv_1s_minute_sum: not active: needs the ohlcv-1s family"
        in lines
    )
    tick = body["tick"]
    assert (tick["state"], tick["detail"]) == ("ok", None)
    (product,) = [p for p in tick["products"] if p["product"] == "ES"]
    # 231's consumers read the tick tier's fields at the top level.
    assert list(product) == [
        "product",
        "sessions_in_scope",
        "sessions_complete",
        "buckets",
        "records",
        "families",
    ]
    assert [f["family"] for f in product["families"]] == [
        "tick_tier",
        "statistics",
        "bar_1m",
    ]
    assert product["families"][0]["schema"] == "tbbo"
    assert product["families"][0]["records"] == product["records"]
    assert body["summary"] == MINUTE_SUMMARY  # existing keys unchanged


def test_a_family_without_a_statistics_want_is_labelled_load_complete_only(
    seeded: SeededRoll,
    migrated_tick_db: str,
    session_migrated_db: str,
    no_minute_scan: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    es_only = replace(TICK_UNIVERSE[0], history=())
    monkeypatch.setattr(universe_module, "TICK_UNIVERSE", (es_only,))
    with cli_settings(migrated_tick_db, session_migrated_db):
        text = _accounting()
        body = json.loads(_accounting("--json").stdout)

    (line,) = [x for x in text.stdout.splitlines() if x.startswith("    tbbo  ")]
    assert line.endswith("load-complete only")
    (family,) = body["tick"]["products"][0]["families"]
    assert family["load_complete_only"] is True


def test_a_statement_failing_partway_names_the_product_family_and_step(
    seeded: SeededRoll,
    migrated_tick_db: str,
    session_migrated_db: str,
    no_minute_scan: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def timed_out(*args: Any, **kwargs: Any) -> Any:
        raise psycopg.errors.QueryCanceled(
            "canceling statement due to statement timeout"
        )

    monkeypatch.setattr(family_report, "ledger_totals", timed_out)
    with cli_settings(migrated_tick_db, session_migrated_db):
        text = _accounting()
        body = json.loads(_accounting("--json").stdout)

    assert text.exit_code == 0, text.output  # as without the tick line
    assert MINUTE_SUMMARY in text.stdout
    futures = text.stdout.splitlines()[-1]
    assert futures.startswith("futures universe:  unreachable: ES tick_tier: records:")
    assert "canceling statement due to statement timeout" in futures
    assert (body["tick"]["state"], body["tick"]["products"]) == ("unreachable", [])
    assert body["tick"]["detail"].startswith("ES tick_tier: records: ")


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
        "unset": "futures universe:  not configured (MT_TICK_DB_URL unset)",
        "unreachable": "futures universe:  unreachable: ",
        "migrations_pending": (
            "futures universe:  migrations pending (mt data migrate apply --track tick)"
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
