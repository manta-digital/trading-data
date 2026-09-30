"""Availability capture: the dataset edge and each day's condition (224).

LLD 224 Database / Storage Schema, *Availability capture*, and TD8. One free
``dataset_range`` and one free ``dataset_condition`` call per dataset per run.
The edge is upserted every run; a day's condition row is written only when it
is new or differs from the stored one, and a *changed* day
(``condition`` or ``last_modified_date``) reopens that day's holed units in the
same transaction. All writes of one capture commit together.

The span is ``[min, max]`` over the universe's tier ranges, every tier day the
manifest owns and every holed unit's day, narrowed by ``--start/--end`` and
clipped to the dataset's available range. A span that ends up empty makes no
condition call.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

from manta_trading.data.tick.constants import CME_DATASET, DatasetCondition
from manta_trading.data.tick.manifest_pass import reopen_holes_on_day
from manta_trading.data.tick.manifest_reads import Conn
from manta_trading.data.tick.planner import DayKey
from manta_trading.data.tick.provider import DatasetRange, DayCondition
from manta_trading.data.tick.run_context import TickRun
from manta_trading.data.tick.universe import TickUniverseEntry


@dataclass(frozen=True)
class DaySpan:
    """``[start, end)`` in UTC days."""

    start: date
    end: date


@dataclass(frozen=True)
class DatasetAvailability:
    """What one dataset's capture found and wrote."""

    dataset: str
    edge: DatasetRange
    span: DaySpan | None
    conditions: Counter[DatasetCondition] = field(default_factory=Counter)
    written: int = 0
    reopened: int = 0


def _bounds(
    dataset: str,
    universe: Sequence[TickUniverseEntry],
    owned: Sequence[DayKey],
    holed: Sequence[tuple[str, date]],
    edge_end: date,
) -> list[tuple[date, date]]:
    """Every ``[start, end)`` the dataset's wants can touch, before narrowing."""
    bounds = [
        (entry.start, entry.end if entry.end is not None else edge_end)
        for entry in universe
        if entry.tier is not None and entry.start is not None and CME_DATASET == dataset
    ]
    days = [key.day for key in owned if key.dataset == dataset]
    days += [day for held_dataset, day in holed if held_dataset == dataset]
    if days:
        bounds.append((min(days), max(days) + timedelta(days=1)))
    return bounds


def availability_span(
    dataset: str,
    universe: Sequence[TickUniverseEntry],
    owned: Sequence[DayKey],
    holed: Sequence[tuple[str, date]],
    window: tuple[date | None, date | None],
    edge: DatasetRange,
) -> DaySpan | None:
    """The days to ask the provider about, or ``None`` when there are none."""
    bounds = _bounds(dataset, universe, owned, holed, edge.end.date())
    if not bounds:
        return None
    start = max(min(b[0] for b in bounds), edge.start.date())
    end = min(max(b[1] for b in bounds), edge.end.date())
    window_start, window_end = window
    if window_start is not None:
        start = max(start, window_start)
    if window_end is not None:
        end = min(end, window_end)
    return DaySpan(start, end) if start < end else None


async def _stored_conditions(
    conn: Conn, dataset: str, span: DaySpan
) -> dict[date, tuple[str, date | None]]:
    cursor = await conn.execute(
        "SELECT condition_date, condition, last_modified_date FROM tick_day_condition"
        " WHERE dataset = %s AND condition_date >= %s AND condition_date < %s",
        (dataset, span.start, span.end),
    )
    return {row[0]: (row[1], row[2]) for row in await cursor.fetchall()}


async def _upsert_edge(
    conn: Conn, dataset: str, edge: DatasetRange, now: datetime
) -> None:
    await conn.execute(
        "INSERT INTO tick_dataset_edge (dataset, available_start, available_end,"
        " observed_at) VALUES (%s, %s, %s, %s)"
        " ON CONFLICT (dataset) DO UPDATE SET available_start ="
        " EXCLUDED.available_start, available_end = EXCLUDED.available_end,"
        " observed_at = EXCLUDED.observed_at",
        (dataset, edge.start, edge.end, now),
    )


async def _write_condition(
    conn: Conn, dataset: str, day: DayCondition, now: datetime
) -> None:
    await conn.execute(
        "INSERT INTO tick_day_condition (dataset, condition_date, condition,"
        " last_modified_date, observed_at) VALUES (%s, %s, %s, %s, %s)"
        " ON CONFLICT (dataset, condition_date) DO UPDATE SET condition ="
        " EXCLUDED.condition, last_modified_date = EXCLUDED.last_modified_date,"
        " observed_at = EXCLUDED.observed_at",
        (dataset, day.day, day.condition.value, day.last_modified, now),
    )


async def capture_dataset(
    run: TickRun,
    dataset: str,
    universe: Sequence[TickUniverseEntry],
    owned: Sequence[DayKey],
    holed: Sequence[tuple[str, date]],
    window: tuple[date | None, date | None],
) -> DatasetAvailability:
    """Capture one dataset's edge and day conditions; one transaction."""
    edge = await asyncio.to_thread(run.provider.dataset_range, dataset)
    span = availability_span(dataset, universe, owned, holed, window, edge)
    days: tuple[DayCondition, ...] = ()
    if span is not None:
        days = await asyncio.to_thread(
            run.provider.dataset_condition, dataset, span.start, span.end
        )
    now = run.clock()
    written = reopened = 0
    async with run.conn.transaction():
        await _upsert_edge(run.conn, dataset, edge, now)
        stored = await _stored_conditions(run.conn, dataset, span) if span else {}
        for day in days:
            fresh = (day.condition.value, day.last_modified)
            if stored.get(day.day) == fresh:
                continue
            await _write_condition(run.conn, dataset, day, now)
            written += 1
            if day.day in stored:  # a change, not a first observation
                reopened += await reopen_holes_on_day(run.conn, dataset, day.day, now)
    return DatasetAvailability(
        dataset,
        edge,
        span,
        Counter(day.condition for day in days),
        written,
        reopened,
    )


async def planning_state(
    conn: Conn,
) -> tuple[dict[str, date], dict[tuple[str, date], DatasetCondition]]:
    """What the planner reads: each dataset's edge as its first day past the
    available range (UTC), and every recorded day condition."""
    cursor = await conn.execute("SELECT dataset, available_end FROM tick_dataset_edge")
    edges = {
        dataset: end.astimezone(UTC).date() for dataset, end in await cursor.fetchall()
    }
    cursor = await conn.execute(
        "SELECT dataset, condition_date, condition FROM tick_day_condition"
    )
    conditions = {
        (dataset, day): DatasetCondition(condition)
        for dataset, day, condition in await cursor.fetchall()
    }
    return edges, conditions
