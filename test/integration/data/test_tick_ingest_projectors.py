"""The worker reaches storage only through the projector registry (slice 300, D2)."""

from __future__ import annotations

import dataclasses
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from tick_support.ingest import plan_unit, run_worker, trade_count, unit_state
from tick_support.tier_units import TBBO_DAY, SeededUnit, seed_tier_unit

from manta_trading.data.tick import projectors
from manta_trading.data.tick.constants import TickSchema
from manta_trading.data.tick.ingest_plan import Calendars
from manta_trading.data.tick.ingest_worker import UnitResult
from manta_trading.data.tick.projectors import NoProjectorError
from manta_trading.data.tick.tick_trade_projector import TICK_TRADE


@pytest.fixture
def calendars(session_migrated_db: str) -> Iterator[Calendars]:
    cache = Calendars(session_migrated_db)
    yield cache
    cache.close()


class _Spy:
    """Records the calls the worker makes, and does the real work."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []

    def load(self, cur: Any, plan: Any, *args: Any) -> Any:
        self.calls.append(("load", plan.unit.unit_id))
        return TICK_TRADE.load(cur, plan, *args)

    def stored_count(self, cur: Any, unit_id: int, *bounds: Any) -> int:
        self.calls.append(("stored_count", unit_id))
        return TICK_TRADE.stored_count(cur, unit_id, *bounds)

    def finish(self, cur: Any, unit_id: int, loaded: Any) -> None:
        self.calls.append(("finish", unit_id))
        TICK_TRADE.finish(cur, unit_id, loaded)

    def delete_unit(self, cur: Any, unit_id: int, *bounds: Any) -> None:
        self.calls.append(("delete_unit", unit_id))
        TICK_TRADE.delete_unit(cur, unit_id, *bounds)


async def _seed(url: str, archive: Path, schema: TickSchema, job: str) -> SeededUnit:
    return await seed_tier_unit(url, archive, schema, TBBO_DAY, job_id=job)


async def test_a_unit_with_an_unregistered_schema_fails_explicitly(
    migrated_tick_db: str, tmp_path: Path, calendars: Calendars
) -> None:
    url = migrated_tick_db
    trades = await _seed(url, tmp_path, TickSchema.TRADES, "T")
    plan = await plan_unit(url, calendars, trades.unit_id)
    other = dataclasses.replace(plan.unit, schema=TickSchema.DEFINITION)
    with pytest.raises(NoProjectorError, match="schema definition"):
        run_worker(dataclasses.replace(plan, unit=other), tmp_path, url)
    assert unit_state(url, trades.unit_id)[0] == "verified"
    assert trade_count(url, trades.unit_id) == 0


async def test_a_superseded_unit_is_deleted_by_its_own_schemas_projector(
    migrated_tick_db: str,
    tmp_path: Path,
    calendars: Calendars,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = migrated_tick_db
    trades_spy, tbbo_spy = _Spy(), _Spy()
    monkeypatch.setattr(
        projectors,
        "INGEST_PROJECTORS",
        {TickSchema.TRADES: trades_spy, TickSchema.TBBO: tbbo_spy},
    )
    trades = await _seed(url, tmp_path, TickSchema.TRADES, "T")
    plan = await plan_unit(url, calendars, trades.unit_id)
    assert run_worker(plan, tmp_path, url).result is UnitResult.INGESTED
    tbbo = await _seed(url, tmp_path, TickSchema.TBBO, "B")
    plan = await plan_unit(url, calendars, tbbo.unit_id)
    assert run_worker(plan, tmp_path, url).result is UnitResult.INGESTED
    assert [c for c in trades_spy.calls if c[0] == "delete_unit"] == [
        ("delete_unit", trades.unit_id)
    ]
    assert tbbo_spy.calls == [
        ("load", tbbo.unit_id),
        ("stored_count", tbbo.unit_id),
        ("finish", tbbo.unit_id),
    ]
    assert trade_count(url, trades.unit_id) == 0
