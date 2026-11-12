"""The one read path of the futures time-series routes (slice 231, TD3, TD4, TD6).

:func:`read_series` builds the plan and reads every planned window in one
``REPEATABLE READ READ ONLY`` transaction on the pooled connection, as ``mt
data tick get`` does, so the plan's ``incomplete`` verdicts and the rows come
from the same state. Rows are collected under the row ceiling: the collector
raises :class:`RowCeilingExceeded` as soon as the total passes it, so a refused
request reads at most the ceiling plus one fetch batch. Nothing here formats a
price or a time (the models do) or maps an error (``tick_errors`` does).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence

import psycopg

from manta_trading.data.tick.get_plan import GetPlan
from manta_trading.data.tick.manifest_reads import Conn
from manta_trading.data.tick.tick_reads import (
    Bounds,
    OnBatch,
    Row,
    TickWindow,
    clip,
)

#: Builds the plan on the read's own connection, inside its snapshot.
PlanFor = Callable[[Conn], Awaitable[GetPlan]]
#: Reads one window: ``read_ticks``, or ``read_provider_bars`` bound to a granularity.
ReadWindow = Callable[[Conn, TickWindow, OnBatch], Awaitable[int]]
#: Admits the request on its plan before any window is read (308 D5).
Admit = Callable[[Conn, GetPlan], Awaitable[None]]


class RowCeilingExceeded(Exception):
    """The read passed the row ceiling; the route answers ``422``."""


def _collector(rows: list[Row], ceiling: int) -> OnBatch:
    def on_batch(batch: Sequence[Row]) -> None:
        rows.extend(batch)
        if len(rows) > ceiling:
            raise RowCeilingExceeded

    return on_batch


async def read_series(
    conn: Conn,
    plan_for: PlanFor,
    read: ReadWindow,
    ceiling: int,
    bounds: Bounds = (None, None),
    admit: Admit | None = None,
) -> tuple[GetPlan, list[Row]]:
    """The plan and its rows, in plan order, from one snapshot (TD6).

    ``admit``, when given, runs after the plan and before the first window, in
    the same snapshot; whatever it raises propagates and no window is read.

    Raises:
        RowCeilingExceeded: more than ``ceiling`` rows; the transaction rolls
            back and the named cursor closes on the way out.
    """
    rows: list[Row] = []
    on_batch = _collector(rows, ceiling)
    await conn.set_isolation_level(psycopg.IsolationLevel.REPEATABLE_READ)
    await conn.set_read_only(True)
    async with conn.transaction():
        plan = await plan_for(conn)
        if admit is not None:
            await admit(conn, plan)
        for window in clip(plan.segments, bounds):
            await read(conn, window, on_batch)
    return plan, rows
