"""``mt data tick run``: the CLI shell (slice 234). Both passes are replaced;
``test/unit/cli/test_tick_run.py`` covers the flow and
``test/integration/data/test_tick_run.py`` the real passes."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any
from unittest.mock import patch
from uuid import UUID

import pytest
from typer.testing import CliRunner

from manta_trading.cli.app import app
from manta_trading.cli.commands import tick_run_cmds, tick_store_cmds
from manta_trading.cli.commands.tick_exit import EXIT_BY_OUTCOME, EXIT_PREFLIGHT
from manta_trading.config import Settings
from manta_trading.data.tick.pass_contract import PassResult, TickOutcome
from manta_trading.data.tick.store_context import TickPreflightError

runner = CliRunner(env={"COLUMNS": "200"})
RUN = ["data", "tick", "run"]


@pytest.fixture(autouse=True)
def settings() -> Iterator[None]:
    with (
        patch(
            "manta_trading.cli.app.Settings",
            side_effect=lambda: Settings(_env_file=None),
        ),
        patch("manta_trading.cli.app.setup_logging"),
    ):
        yield


def _result(outcome: TickOutcome) -> PassResult:
    started = datetime(2026, 10, 14, tzinfo=UTC)
    return PassResult(UUID(int=5), started, (), outcome, 10)


def _passes(acquired: Any, ingested: Any) -> Any:
    async def acquire(_settings: Settings, _hook: Any) -> PassResult:
        if isinstance(acquired, Exception):
            raise acquired
        return _result(acquired)

    async def ingest(_settings: Settings, _ids: Any, _beat: Any) -> PassResult:
        return _result(ingested)

    return (
        patch.object(tick_run_cmds, "acquire_full_universe", acquire),
        patch.object(tick_store_cmds, "ingest_units", ingest),
    )


def test_it_takes_no_options() -> None:
    result = runner.invoke(app, [*RUN, "--start", "2026-01-01"])
    assert result.exit_code != 0
    assert "No such option" in result.output


@pytest.mark.parametrize(
    ("acquired", "ingested", "combined"),
    [
        (TickOutcome.OK, TickOutcome.OK, TickOutcome.OK),
        (TickOutcome.REFUSED, TickOutcome.PARTIAL, TickOutcome.REFUSED),
        (TickOutcome.IN_FLIGHT, TickOutcome.STORAGE_ABORT, TickOutcome.STORAGE_ABORT),
    ],
)
def test_the_exit_code_is_the_folded_outcome(
    acquired: TickOutcome, ingested: TickOutcome, combined: TickOutcome
) -> None:
    acquire, ingest = _passes(acquired, ingested)
    with acquire, ingest:
        result = runner.invoke(app, RUN)
    assert result.exit_code == EXIT_BY_OUTCOME[combined], result.output
    assert f"tick run: acquire={acquired} ingest={ingested}" in result.output


def test_a_refused_acquisition_prints_why_and_exits_1() -> None:
    acquire, ingest = _passes(
        TickPreflightError("another tick acquisition run holds the lock"),
        TickOutcome.OK,
    )
    with acquire, ingest:
        result = runner.invoke(app, RUN)
    assert result.exit_code == EXIT_PREFLIGHT, result.output
    assert "holds the lock" in result.output
    assert "acquire=preflight ingest=ok" in result.output
