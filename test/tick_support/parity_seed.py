"""Seed the 2024-12-03 ticks and a bar day for the parity tests (slice 306).

The cut ``tbbo`` day is ingested for real. The bar day is either the derived
real one or hand-built from the ticks' own minutes (``bars_for``), so a test can
make any class: agree, differ, ``tick_only``, ``provider_only``, close-stamped.
The neighbouring days (12-02, 12-04) get empty ingested units in both families,
so the sessions that touch 12-03 are complete in both. Nothing here reads a
production URL.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import psycopg
from tick_support.cli import cli_settings
from tick_support.ingest import ingest_inputs, run_ingest_phase
from tick_support.reads import session_window
from tick_support.roll_seed import seed_tier_days
from tick_support.tick_bar_helpers import seed_bar_units, write_bar_file
from tick_support.tier_units import TBBO_DAY, seed_tier_unit
from typer.testing import CliRunner

from manta_trading.cli.app import app
from manta_trading.data.tick.constants import TickSchema
from manta_trading.data.tick.tick_reads import TickWindow

NS_MINUTE = 60 * 10**9
SESSION = TBBO_DAY
DAYS_AROUND = [date(2024, 12, 2), date(2024, 12, 4)]
#: One minute's open, high, low, close and volume, raw integers.
Minute = tuple[int, int, int, int, int]


@dataclass(frozen=True)
class ParityDb:
    url: str
    calendar: str
    symbol: str
    window: TickWindow  # the 2024-12-03 session of the busiest traded contract
    tick_minutes: dict[int, Minute]  # bucketed in Python, independent of src


#: Builds the hand-made bar day from the ticks' minutes (``None``: the real one).
BarsFor = Callable[[ParityDb], list[dict[str, int]]]


def tick_minutes(url: str, window: TickWindow) -> dict[int, Minute]:
    """The window's trades bucketed by Python at the session open + n minutes."""
    trades: dict[int, list[tuple[Any, ...]]] = defaultdict(list)
    with psycopg.connect(url) as conn:
        rows = conn.execute(
            "SELECT ts_event, sequence, sequence_ordinal, price, size FROM tick_trade"
            " WHERE instrument_id = %s AND ts_event BETWEEN %s AND %s",
            (window.instrument_id, window.open_ns, window.close_ns),
        ).fetchall()
    for ts, sequence, ordinal, price, size in rows:
        bucket = window.open_ns + (ts - window.open_ns) // NS_MINUTE * NS_MINUTE
        trades[bucket].append(((ts, sequence, ordinal), price, size))
    minutes: dict[int, Minute] = {}
    for bucket, group in trades.items():
        group.sort()
        prices = [price for _, price, _ in group]
        volume = sum(size for *_, size in group)
        minutes[bucket] = (prices[0], max(prices), min(prices), prices[-1], volume)
    return minutes


def bar(instrument_id: int, ts_event: int, minute: Minute) -> dict[str, int]:
    """A hand-built bar record for ``write_bar_file``."""
    o, h, lo, c, v = minute
    return {
        "instrument_id": instrument_id,
        "ts_event": ts_event,
        "open": o,
        "high": h,
        "low": lo,
        "close": c,
        "volume": v,
    }


async def seed_parity(
    url: str, calendar: str, tmp_path: Path, bars_for: BarsFor | None = None
) -> ParityDb:
    await seed_tier_unit(url, tmp_path, TickSchema.TBBO, TBBO_DAY, job_id="TICKS")
    inputs = ingest_inputs(url, calendar)
    assert (await run_ingest_phase(url, tmp_path, inputs)).summary["ingested"] == 1
    with psycopg.connect(url) as conn:
        row = conn.execute(
            "SELECT t.instrument_id, max(d.raw_symbol) FROM tick_trade t"
            " JOIN tick_definition d USING (instrument_id)"
            " GROUP BY t.instrument_id ORDER BY count(*) DESC LIMIT 1"
        ).fetchone()
    assert row is not None, "the tbbo fixture ingested no trades"
    window = session_window(calendar, SESSION, int(row[0]), row[1])
    db = ParityDb(url, calendar, row[1], window, tick_minutes(url, window))
    bar_file = None
    if bars_for is not None:
        bar_file = write_bar_file(tmp_path / "bars.dbn.zst", SESSION, bars_for(db))
    await seed_bar_units(url, tmp_path, job_id="BARS", bar_file=bar_file)
    assert (await run_ingest_phase(url, tmp_path, inputs)).summary["ingested"] == 1
    for schema in (TickSchema.TBBO, TickSchema.OHLCV_1M):
        seed_tier_days(url, DAYS_AROUND, schema)
    return db


_RUNNER = CliRunner(env={"COLUMNS": "200"})


async def run_parity(db: ParityDb, *extra: str) -> Any:
    """``mt data tick parity --contract <the busiest> --json`` in a worker thread
    (the command owns an event loop of its own)."""
    args = ["data", "tick", "parity", "--contract", db.symbol, "--json", *extra]
    with cli_settings(db.url, db.calendar):
        return await asyncio.to_thread(_RUNNER.invoke, app, args)
