"""Measure a product tick-bar request at the trade limit (slice 308, criterion 12).

The Phase 7 step, run against production after the release (issue #75):

    uv run python scripts/measure_tick_bars.py --base-url http://localhost:8100 \\
        --product ES --rule c.0 --start 2024-09-03 --end 2025-01-01 \\
        --ticks-per-bar 1000 --limit 2000000

Run from the dev checkout root: the database URLs come from ``Settings`` (the
checkout's ``.env``), as ``reconcile_tick_spend.py`` reads them. ``--limit`` is
production's ``MT_API_MAX_TICK_BAR_TRADES`` and is never read from the
environment: the dev ``.env`` is not production's.

It builds the plan the route builds (``product_plan`` over the tick tier) and
reads each window's record count and volume from the ledger. ``mt data tick
coverage --json`` cannot serve: it sums every instrument of the product and has
no tick-tier volume. From ``[--start, --end)`` it picks the run of consecutive
sessions whose counts sum to 95-100% of ``--limit``, requests those bars while
probing ``GET /api/v1/futures/products``, checks the totals per session, and
writes ``user/notes/308-tick-bar-measurement.md``. Exit 0 only when every check
passed.
"""

from __future__ import annotations

import argparse
import asyncio
import math
import os
import statistics
import sys
import time
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import psycopg
from psycopg.conninfo import make_conninfo

from manta_trading.config import Settings
from manta_trading.data.tick.constants import TICK_JIT_OPTION
from manta_trading.data.tick.get_plan import product_plan
from manta_trading.data.tick.roll_rules import RollRule
from manta_trading.data.tick.schema_families import SchemaFamily
from manta_trading.data.tick.status_reads import (
    ledger_totals,
    ledger_volumes,
    shape_of,
)
from manta_trading.data.tick.universe import TICK_UNIVERSE, TickUniverseEntry
from manta_trading.market.schema.databases import (
    Credential,
    Database,
    resolve_database_url,
)

#: The picked run's record sum lies in [BAND_LOW × limit, limit] (criterion 12).
BAND_LOW = 0.95
#: The request completes under this (306 D10).
BOUND_S = 10.0
#: A concurrent catalog request answers within this of its idle median (D9).
#: Measured on the test cluster, 2026-11-12: idle ``/products`` is 43-131 ms
#: (the probe itself varies by ~80 ms) and cutting adds 50-90 ms of GIL sharing
#: with the cutter thread (busy max 0.160-0.177 s over idle medians 0.066-0.095
#: s; max-of-7 against +0.1 s failed one run in four). Starvation would track
#: the multi-second cut, so 0.25 s separates it from that contention. The load
#: test (``test/load/test_308_tick_bars_nfr.py``) judges with ``starvation``.
STARVATION_MARGIN_S = 0.25
PROBES = 7
#: Fewer probes beside the request than this cannot show the loop was free.
MIN_BUSY_PROBES = 3
#: Let the request get past planning and admission before probing.
PROBE_DELAY_S = 0.2
REQUEST_TIMEOUT_S = 120.0
PRODUCTS_PATH = "/api/v1/futures/products"
#: Under the checkout this script lives in, whatever the working directory.
NOTE = (
    Path(__file__).resolve().parents[1]
    / "project-documents/user/notes/308-tick-bar-measurement.md"
)


@dataclass(frozen=True)
class LedgerRow:
    """One planned window: its session, instrument, records and volume."""

    session_date: date
    instrument_id: int
    records: int
    volume: int


def pick_range(rows: Sequence[LedgerRow], limit: int) -> list[LedgerRow]:
    """The first run of consecutive rows whose records sum to 95-100% of
    ``limit``. Raises ``ValueError`` when no run lands in the band."""
    low = BAND_LOW * limit
    for first in range(len(rows)):
        total = 0
        for last in range(first, len(rows)):
            total += rows[last].records
            if total > limit:
                break
            if total >= low:
                return list(rows[first : last + 1])
    raise ValueError(
        f"no run of consecutive sessions sums to {low:,.0f}-{limit:,} records"
        f" over {len(rows)} sessions"
    )


def check_totals(
    bars: Sequence[Mapping[str, Any]], rows: Sequence[LedgerRow], ticks_per_bar: int
) -> list[str]:
    """Per window, ``Σ trade_count`` and ``Σ volume`` against the ledger, and
    the bar count against ``Σ ceil(nᵢ / N)``. Returns one line per mismatch,
    naming the session; empty when every total matches."""
    trades: dict[tuple[date, int], int] = defaultdict(int)
    volume: dict[tuple[date, int], int] = defaultdict(int)
    for bar in bars:
        key = (date.fromisoformat(bar["session_date"]), int(bar["instrument_id"]))
        trades[key] += int(bar["trade_count"])
        volume[key] += int(bar["volume"])
    failures = []
    for row in rows:
        key = (row.session_date, row.instrument_id)
        if trades.get(key, 0) != row.records:
            failures.append(
                f"{row.session_date} {row.instrument_id}: Σ trade_count"
                f" {trades.get(key, 0):,} != ledger records {row.records:,}"
            )
        if volume.get(key, 0) != row.volume:
            failures.append(
                f"{row.session_date} {row.instrument_id}: Σ volume"
                f" {volume.get(key, 0):,} != ledger volume {row.volume:,}"
            )
    expected = sum(math.ceil(row.records / ticks_per_bar) for row in rows)
    if len(bars) != expected:
        failures.append(f"bar count {len(bars):,} != Σ ceil(nᵢ / N) {expected:,}")
    stray = set(trades) - {(r.session_date, r.instrument_id) for r in rows}
    failures.extend(f"{d} {i}: bars outside the picked range" for d, i in stray)
    return failures


def _entry(product: str) -> TickUniverseEntry:
    for entry in TICK_UNIVERSE:
        if entry.product == product:
            return entry
    raise SystemExit(f"unknown product {product!r}")


async def ledger_rows(
    settings: Settings,
    entry: TickUniverseEntry,
    rule: RollRule,
    span: tuple[date, date],
) -> list[LedgerRow]:
    """The route's plan over ``[start, end)``, each window with its ledger
    count and volume. A window with no ledger row holds no trades and scans
    nothing, as the route's admission counts it (``trades_to_scan``)."""
    tick_url = resolve_database_url(settings, Database.TICK, Credential.APPLICATION)
    calendar_url = settings.timescale_db_url
    if not calendar_url:
        raise SystemExit("MT_TIMESCALE_DB_URL is unset")
    conninfo = make_conninfo(
        tick_url, options=f"-c default_transaction_read_only=on {TICK_JIT_OPTION}"
    )
    async with await psycopg.AsyncConnection.connect(conninfo, autocommit=True) as conn:
        plan = await product_plan(
            conn, (tick_url, calendar_url), entry, rule, *span, SchemaFamily.TICK_TIER
        )
        if not plan.segments:
            raise SystemExit(f"the plan holds no sessions in {span[0]}..{span[1]}")
        first, last = plan.segments[0].session_date, plan.segments[-1].session_date
        shape = shape_of(entry)
        family = SchemaFamily.TICK_TIER
        totals = await ledger_totals(conn, shape, family, first, last)
        volumes = await ledger_volumes(conn, shape, family, first, last)
    return [
        LedgerRow(
            w.session_date,
            w.instrument_id,
            totals.get((w.instrument_id, w.session_date), 0),
            volumes.get((w.instrument_id, w.session_date), 0),
        )
        for w in plan.segments
    ]


@dataclass(frozen=True)
class Timing:
    """The request's wall time and the catalog probes idle and during it."""

    elapsed: float
    idle: list[float]
    busy: list[float]


async def _probe(client: httpx.AsyncClient) -> float:
    started = time.perf_counter()
    response = await client.get(PRODUCTS_PATH)
    response.raise_for_status()
    return time.perf_counter() - started


async def timed(
    client: httpx.AsyncClient, path: str, params: dict[str, str]
) -> tuple[float, httpx.Response]:
    """One request and its wall time."""
    started = time.perf_counter()
    response = await client.get(path, params=params)
    return time.perf_counter() - started, response


async def measure(
    client: httpx.AsyncClient, path: str, params: dict[str, str]
) -> tuple[Timing, httpx.Response]:
    """Idle probes, then the request with probes beside it (D9)."""
    idle = [await _probe(client) for _ in range(PROBES)]
    request = asyncio.create_task(timed(client, path, params))
    await asyncio.sleep(PROBE_DELAY_S)
    busy: list[float] = []
    while not request.done() and len(busy) < PROBES:
        busy.append(await _probe(client))
    elapsed, response = await request
    return Timing(elapsed, idle, busy), response


def starvation(timing: Timing) -> list[str]:
    """The starvation check (D9): enough probes ran beside the request, and
    none answered later than ``STARVATION_MARGIN_S`` over the idle median."""
    if len(timing.busy) < MIN_BUSY_PROBES:
        return [
            f"only {len(timing.busy)} probe(s) ran beside the request,"
            f" fewer than {MIN_BUSY_PROBES}"
        ]
    idle_median = statistics.median(timing.idle)
    if max(timing.busy) > idle_median + STARVATION_MARGIN_S:
        return [
            f"catalog probe {max(timing.busy):.3f} s during the request, over"
            f" idle median {idle_median:.3f} s + {STARVATION_MARGIN_S} s"
        ]
    return []


def judge(timing: Timing, totals: list[str]) -> list[str]:
    """Every failed check: the totals, the 10 s bound and starvation."""
    failures = list(totals)
    if timing.elapsed >= BOUND_S:
        failures.append(f"request took {timing.elapsed:.3f} s, bound {BOUND_S} s")
    return failures + starvation(timing)


def report(
    args: argparse.Namespace,
    picked: Sequence[LedgerRow],
    timing: Timing,
    failures: Sequence[str],
) -> str:
    """The note's markdown."""
    today = datetime.now(UTC).strftime("%Y%m%d")
    ledger_sum = sum(row.records for row in picked)
    checks = "\n".join(f"- FAIL: {f}" for f in failures) or "- every check passed"
    return f"""---
docType: notes
project: trading-data
dateCreated: {today}
dateUpdated: {today}
status: complete
---

# 308 tick-bar measurement (criterion 12)

- Request: `{args.base_url}/api/v1/futures/products/{args.product}/tick-bars`
  `ticks_per_bar={args.ticks_per_bar} rule={args.rule}`
  `start={picked[0].session_date} end={picked[-1].session_date + timedelta(days=1)}`
- Sessions: {len(picked)}; ledger record sum {ledger_sum:,}
  ({ledger_sum / args.limit:.1%} of the {args.limit:,} trade limit)
- Request time: {timing.elapsed:.3f} s (bound {BOUND_S} s)
- `/futures/products` idle: {[round(s, 3) for s in timing.idle]}
  (median {statistics.median(timing.idle):.3f} s)
- During the request: {[round(s, 3) for s in timing.busy]}
  (margin {STARVATION_MARGIN_S} s)

## Checks

{checks}
"""


def _positive(text: str) -> int:
    """An argparse type: an integer of at least 1, else a usage error."""
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not an integer: {text!r}") from None
    if value < 1:
        raise argparse.ArgumentTypeError(f"must be at least 1: {value}")
    return value


def _args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--product", required=True)
    parser.add_argument("--rule", required=True)
    parser.add_argument("--start", required=True, type=date.fromisoformat)
    parser.add_argument("--end", required=True, type=date.fromisoformat)
    parser.add_argument("--ticks-per-bar", required=True, type=_positive)
    parser.add_argument("--limit", required=True, type=_positive)
    return parser.parse_args(argv)


async def _run(args: argparse.Namespace) -> tuple[str, list[str]]:
    """The note and the failed checks."""
    entry, rule = _entry(args.product), RollRule.parse(args.rule)
    rows = await ledger_rows(Settings(), entry, rule, (args.start, args.end))
    picked = pick_range(rows, args.limit)
    params = {
        "ticks_per_bar": str(args.ticks_per_bar),
        "rule": args.rule,
        "start": picked[0].session_date.isoformat(),
        "end": (picked[-1].session_date + timedelta(days=1)).isoformat(),
    }
    path = f"/api/v1/futures/products/{args.product}/tick-bars"
    async with httpx.AsyncClient(
        base_url=args.base_url, timeout=REQUEST_TIMEOUT_S
    ) as client:
        timing, response = await measure(client, path, params)
    if response.status_code != 200:
        raise SystemExit(
            f"{path} answered {response.status_code}: {response.text[:300]}"
        )
    bars = response.json()["bars"]
    failures = judge(timing, check_totals(bars, picked, args.ticks_per_bar))
    return report(args, picked, timing, failures), failures


def main(argv: Sequence[str]) -> int:
    args = _args(argv)
    # Before the production request is spent, not after it (#75).
    if not os.access(NOTE.parent, os.W_OK):
        raise SystemExit(f"cannot write the note: {NOTE.parent} is not writable")
    note, failures = asyncio.run(_run(args))
    NOTE.write_text(note)
    print(f"wrote {NOTE}; {len(failures)} failed check(s)")
    for failure in failures:
        print(f"FAIL: {failure}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
