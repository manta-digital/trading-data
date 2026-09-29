"""Reset classification on a migrated tick database (223; LLD 224 TD8)."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import psycopg
import pytest
from tick_support.fake_historical import FakeHistorical
from tick_support.rows import FILE_COLUMNS, insert_request, insert_unit

from manta_trading.config import Settings
from manta_trading.data.quality.fetch_status import FetchStatus
from manta_trading.data.tick.constants import TICK_ENV_PREFIX, UnitState
from manta_trading.data.tick.databento.adapter import DatabentoTickProvider
from manta_trading.data.tick.reset import ALL, ResetAction, reset_units
from manta_trading.data.tick.run_context import open_tick_run

NOW = datetime(2026, 9, 28, 12, tzinfo=UTC)
MISSING_ID = 999_999


@pytest.fixture(autouse=True)
def no_tick_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in list(os.environ):
        if name.upper().startswith(TICK_ENV_PREFIX):
            monkeypatch.delenv(name)


@pytest.fixture
def units(tick_conn: psycopg.Connection[Any]) -> dict[str, int]:
    """One unit per branch of the classification."""

    def unit(
        status: FetchStatus, state: UnitState = UnitState.DELIVERED, **kw: Any
    ) -> int:
        if status is not FetchStatus.UNKNOWN:
            kw["failure_reason"] = "set up by the test"
        return insert_unit(
            tick_conn,
            insert_request(tick_conn),
            state=state.value,
            fetch_status=status.value,
            **kw,
        )

    return {
        "exhausted": unit(FetchStatus.RETRY_EXHAUSTED, attempt_count=5),
        "hole": unit(FetchStatus.PROVIDER_HOLE),
        "reopened_hole": unit(FetchStatus.PROVIDER_HOLE, reopened_at=NOW),
        "reopened_exhausted": unit(FetchStatus.RETRY_EXHAUSTED, reopened_at=NOW),
        "retryable": unit(FetchStatus.FAILED_RETRYABLE, attempt_count=2),
        "verified": unit(FetchStatus.UNKNOWN, UnitState.VERIFIED, **FILE_COLUMNS),
    }


def _run(url: str, archive: Path) -> Any:
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        databento_api_key="db-test-key",
        tick_db_url=url,
        tick_archive_dir=archive,
    )
    return open_tick_run(
        settings,
        clock=lambda: NOW,
        provider_factory=lambda _: DatabentoTickProvider(
            FakeHistorical(),  # type: ignore[arg-type]
            httpx.Client(),
        ),
        env_file=None,
    )


async def test_each_branch(
    migrated_tick_db: str, units: dict[str, int], tmp_path: Path
) -> None:
    ids = [*units.values(), MISSING_ID]
    async with _run(migrated_tick_db, tmp_path) as run:
        changes = {c.unit_id: c for c in await reset_units(run, ids)}
    by_name = {name: changes[uid] for name, uid in units.items()}
    assert {name: c.action for name, c in by_name.items()} == {
        "exhausted": ResetAction.RESET,
        "hole": ResetAction.REOPENED,
        "reopened_hole": ResetAction.UNCHANGED,
        "reopened_exhausted": ResetAction.UNCHANGED,
        "retryable": ResetAction.UNCHANGED,
        "verified": ResetAction.UNCHANGED,
    }
    reset = by_name["exhausted"].after
    assert reset is not None
    assert (reset.fetch_status, reset.attempt_count, reset.failure_reason) == (
        FetchStatus.UNKNOWN,
        0,
        None,
    )
    reopened = by_name["hole"].after
    assert reopened is not None and reopened.reopened_at == NOW
    assert changes[MISSING_ID].action is ResetAction.NOT_FOUND
    assert changes[MISSING_ID].before is None


async def test_all_lists_only_what_it_changes(
    migrated_tick_db: str, units: dict[str, int], tmp_path: Path
) -> None:
    async with _run(migrated_tick_db, tmp_path) as run:
        changes = await reset_units(run, ALL)
    assert {(c.unit_id, c.action) for c in changes} == {
        (units["exhausted"], ResetAction.RESET),
        (units["hole"], ResetAction.REOPENED),
    }
