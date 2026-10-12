"""The roll path's snapshot, timeouts and connection failures (229 TD9, FR8b).

``build_schedule`` raises (``active`` maps it to exit 4); ``latest_active``
turns the same failure into an ``error`` on every line (``status``).
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from typing import Any

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict
from tick_support.roll_seed import ESH5, ESU4, ESZ4, SeededRoll, seed_september_roll
from tick_support.rows import (
    FILE_COLUMNS,
    insert_ledger_row,
    insert_request,
    insert_unit,
)
from tick_support.universe import UNTIERED

from manta_trading.data.tick import active_contract
from manta_trading.data.tick.active_contract import (
    build_schedule,
    latest_active,
    roll_conninfo,
)
from manta_trading.data.tick.constants import (
    TICK_DB_CONNECT_TIMEOUT_SECONDS,
    TICK_DB_KEEPALIVES_IDLE_SECONDS,
    TICK_ROLL_STATEMENT_TIMEOUT_SECONDS,
    TickSchema,
    UnitState,
)
from manta_trading.data.tick.roll_rules import STATUS_ROLL_RULES, RollRule
from manta_trading.data.tick.roll_types import ActiveContract

ES = UNTIERED[0]
V0 = RollRule.parse("v.0")
#: Session 09-12 ranks by 09-10, where ESU4 leads until the swap below.
LAG_DAY, ANSWERED = date(2024, 9, 10), date(2024, 9, 12)
UNREACHABLE = "postgresql://nobody@127.0.0.1:9/none"


@pytest.fixture
def seeded(migrated_tick_db: str) -> SeededRoll:
    return seed_september_roll(migrated_tick_db)


def _supersede_with_swapped_volume(url: str, unit_id: int) -> int:
    """Commit a tbbo unit for the lag day whose ledger puts ESZ4 first."""
    with psycopg.connect(url) as conn, conn.transaction():
        request = insert_request(conn, schema=TickSchema.TBBO.value)
        tbbo = insert_unit(
            conn,
            request,
            unit_date=LAG_DAY,
            state=UnitState.INGESTED.value,
            **FILE_COLUMNS,
        )
        conn.execute(
            "UPDATE tick_archive_unit SET superseded_by_unit_id = %s"
            " WHERE unit_id = %s",
            (tbbo, unit_id),
        )
        for contract, volume in ((ESU4, 1), (ESZ4, 900), (ESH5, 1)):
            insert_ledger_row(
                conn,
                tbbo,
                instrument_id=contract.instrument_id,
                session_date=LAG_DAY,
                volume=volume,
            )
    return tbbo


async def _answer(tick_url: str, calendar_url: str) -> ActiveContract:
    schedule = await build_schedule(
        tick_url, calendar_url, ES, V0, ANSWERED, date(2024, 9, 13)
    )
    (answer,) = schedule.answers
    assert isinstance(answer, ActiveContract)
    return answer


async def test_a_unit_committed_mid_read_is_not_seen(
    seeded: SeededRoll,
    migrated_tick_db: str,
    session_migrated_db: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The supersession commits after the snapshot's first read and before
    its verdict and volume reads: the answer is wholly the old state."""
    committed: list[int] = []
    real = active_contract.definitions_as_of

    async def commit_then_read(conn: Any, shape: Any) -> date:
        committed.append(
            _supersede_with_swapped_volume(migrated_tick_db, seeded.units[LAG_DAY])
        )
        return await real(conn, shape)

    monkeypatch.setattr(active_contract, "definitions_as_of", commit_then_read)
    during = await _answer(migrated_tick_db, session_migrated_db)
    assert (during.raw_symbol, during.basis_unit_ids) == (
        "ESU4",
        (seeded.units[LAG_DAY],),
    )
    monkeypatch.setattr(active_contract, "definitions_as_of", real)
    after = await _answer(migrated_tick_db, session_migrated_db)
    assert (after.raw_symbol, after.basis_unit_ids) == ("ESZ4", (committed[0],))


@contextmanager
def _locked(url: str, table: str) -> Iterator[None]:
    """Hold an ACCESS EXCLUSIVE lock on ``table`` until the block ends."""
    with psycopg.connect(url) as holder:
        holder.execute(f"LOCK TABLE {table} IN ACCESS EXCLUSIVE MODE")
        try:
            yield
        finally:
            holder.rollback()


async def test_a_held_lock_ends_in_the_statement_timeout(
    seeded: SeededRoll,
    migrated_tick_db: str,
    session_migrated_db: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(active_contract, "TICK_ROLL_STATEMENT_TIMEOUT_SECONDS", 1)
    with _locked(migrated_tick_db, "tick_ingest_ledger"):
        with pytest.raises(psycopg.errors.QueryCanceled):
            await _answer(migrated_tick_db, session_migrated_db)
        lines = await latest_active(
            migrated_tick_db, session_migrated_db, ES, STATUS_ROLL_RULES
        )
    assert len(lines) == len(STATUS_ROLL_RULES)
    assert all(line.error and "statement timeout" in line.error for line in lines)
    assert all(line.answer is None for line in lines)


async def test_an_unreachable_tick_database_raises_or_errors_every_line(
    session_migrated_db: str,
) -> None:
    with pytest.raises(psycopg.OperationalError):
        await _answer(UNREACHABLE, session_migrated_db)
    lines = await latest_active(UNREACHABLE, session_migrated_db, ES, STATUS_ROLL_RULES)
    assert all(line.error for line in lines)


def test_roll_conninfo_carries_the_bounds() -> None:
    params = conninfo_to_dict(roll_conninfo("postgresql://u@h/db"))
    assert params["connect_timeout"] == str(TICK_DB_CONNECT_TIMEOUT_SECONDS)
    assert params["keepalives"] == "1"
    assert params["keepalives_idle"] == str(TICK_DB_KEEPALIVES_IDLE_SECONDS)
    options = str(params["options"])
    assert f"statement_timeout={TICK_ROLL_STATEMENT_TIMEOUT_SECONDS}s" in options
    assert "default_transaction_read_only=on" in options


async def test_the_calendar_is_opened_with_roll_conninfo(
    seeded: SeededRoll,
    migrated_tick_db: str,
    session_migrated_db: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opened: list[str] = []
    real = active_contract.open_calendar

    def spy(url: str, calendar_id: str) -> Any:
        opened.append(url)
        return real(url, calendar_id)

    monkeypatch.setattr(active_contract, "open_calendar", spy)
    await _answer(migrated_tick_db, session_migrated_db)
    await latest_active(migrated_tick_db, session_migrated_db, ES, STATUS_ROLL_RULES)
    assert opened == [roll_conninfo(session_migrated_db)] * 2
