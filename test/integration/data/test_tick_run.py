"""``mt data tick run`` on the test cluster (slice 234, Task 3.10).

Throwaway primary and tick databases, a fake provider (no provider call), and
the real recorder, ingest pass and abandoned-run closer.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import psycopg
import pytest
from tick_support.fake_provider import FakeClock, FakeTickProvider
from tick_support.runs import connect, tick_run
from tick_support.tier_units import TBBO_DAY, TRADES_DAY, seed_tier_unit
from tick_support.universe import use_untiered

from manta_trading.cli.commands._pass_run import pass_run_recorder
from manta_trading.cli.commands.tick_run_cmds import run_acquisition
from manta_trading.cli.commands.tick_store_cmds import ingest_units
from manta_trading.cli.tick_run import run_tick
from manta_trading.config import Settings
from manta_trading.data.acquisition.pass_runs import PassKind, PassRunOutcome
from manta_trading.data.tick.acquisition_pass import AwaitTiming, pass_phases
from manta_trading.data.tick.constants import TickSchema
from manta_trading.data.tick.databento.dbn_file import DbnFileReader
from manta_trading.data.tick.pass_contract import TickPassPhaseName

#: A pid no process holds: above the kernel's pid_max ceiling.
DEAD_PID = 2**22 + 1


@pytest.fixture(autouse=True)
def untiered(monkeypatch: pytest.MonkeyPatch) -> None:
    use_untiered(monkeypatch)


def _row(url: str, run_id: uuid.UUID) -> dict[str, Any]:
    with psycopg.connect(url) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT pass, outcome, exit_code, detail, phase, progress_done,"
            " progress_total FROM pass_runs WHERE run_id = %s",
            (run_id,),
        )
        names = [d.name for d in cur.description or ()]
        row = cur.fetchone()
    assert row is not None
    return dict(zip(names, row, strict=True))


async def test_a_run_exits_0_and_closes_one_tick_row_complete(
    migrated_tick_db: str, ephemeral_db: str, tmp_path: Path
) -> None:
    from manta_trading.market.timescale_minute_db import TimescaleMinuteDataDB

    db = TimescaleMinuteDataDB(conninfo=ephemeral_db)
    db.apply_schema_migrations()
    db.close()
    archive = tmp_path / "archive"
    archive.mkdir()
    trades = await seed_tier_unit(
        migrated_tick_db, archive, TickSchema.TRADES, TRADES_DAY, job_id="T"
    )
    tbbo = await seed_tier_unit(
        migrated_tick_db, archive, TickSchema.TBBO, TBBO_DAY, job_id="B"
    )
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        timescale_db_url=ephemeral_db,
        tick_db_url=migrated_tick_db,
        tick_archive_dir=archive,
    )
    clock = FakeClock(datetime(2026, 9, 30, tzinfo=UTC))
    phases = pass_phases(
        (None, None),
        False,
        DbnFileReader(),
        timing=AwaitTiming(budget_seconds=0, interval_seconds=1),
        free=lambda _: 10**12,
    )
    seen: list[str] = []

    async with connect(migrated_tick_db) as conn:
        run = tick_run(
            conn,
            FakeTickProvider(clock=clock),
            archive,
            clock,
            timescale_db_url=ephemeral_db,
            tick_spend_ceiling_usd=Decimal("1"),
            tick_spend_30d_ceiling_usd=Decimal("5"),
        )

        async def acquire(hook: Any) -> Any:
            async def spy(name: TickPassPhaseName) -> None:
                seen.append(str(name))
                await hook(name)

            return await run_acquisition(run, phases, spy)

        with pass_run_recorder(settings) as recorder:
            result = await run_tick(
                recorder, acquire, lambda beat: ingest_units(settings, (), beat)
            )

    assert result.exit_code == 0, result.detail
    with psycopg.connect(ephemeral_db) as pconn:
        rows = pconn.execute(
            "SELECT run_id FROM pass_runs WHERE pass = %s", (PassKind.TICK.value,)
        ).fetchall()
    assert len(rows) == 1
    row = _row(ephemeral_db, rows[0][0])
    assert row["outcome"] == PassRunOutcome.COMPLETE.value
    assert row["exit_code"] == 0
    assert row["detail"] == "acquire=ok ingest=ok"
    assert seen == [str(p.name) for p in phases]
    expected = trades.record_count + tbbo.record_count
    assert (row["phase"], row["progress_done"], row["progress_total"]) == (
        str(TickPassPhaseName.INGEST),
        expected,
        expected,
    )


def test_an_orphaned_tick_row_closes_as_abandoned(session_migrated_db: str) -> None:
    """TD2: the next run's ``recorder.open`` closes this host's dead-pid row."""
    url = session_migrated_db
    orphan = uuid.uuid4()
    with psycopg.connect(url) as conn:
        conn.execute(
            "INSERT INTO pass_runs (run_id, pass, hostname, pid, started_at)"
            " VALUES (%s, %s, %s, %s, %s)",
            (
                orphan,
                PassKind.TICK.value,
                os.uname().nodename,
                DEAD_PID,
                datetime(2026, 10, 14, 1, 0, tzinfo=UTC),
            ),
        )
    settings = Settings(_env_file=None, timescale_db_url=url)  # type: ignore[call-arg]
    with pass_run_recorder(settings) as recorder:
        assert recorder is not None
        fresh = recorder.open(PassKind.TICK)
    assert fresh is not None
    old = _row(url, orphan)
    assert old["outcome"] == PassRunOutcome.FAILED.value
    assert old["detail"].startswith(f"abandoned: pid {DEAD_PID}")
    assert old["detail"].endswith("gone")
    assert _row(url, fresh)["outcome"] is None
