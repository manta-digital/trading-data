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
from dataclasses import replace

import psycopg

from manta_trading.data.tick.get_plan import GetPlan
from manta_trading.data.tick.manifest_reads import Conn
from manta_trading.data.tick.tick_reads import OnBatch, Row, TickWindow

#: Builds the plan on the read's own connection, inside its snapshot.
PlanFor = Callable[[Conn], Awaitable[GetPlan]]
#: Reads one window: ``read_ticks``, or ``read_bars`` bound to a granularity.
ReadWindow = Callable[[Conn, TickWindow, OnBatch], Awaitable[int]]
#: Half-open instant bounds in epoch ns, ``[from, until)``; ``None`` = open.
Bounds = tuple[int | None, int | None]


class RowCeilingExceeded(Exception):
    """The read passed the row ceiling; the route answers ``422``."""


def clip(windows: Sequence[TickWindow], bounds: Bounds) -> tuple[TickWindow, ...]:
    """Each window cut to ``[from, until)``; windows left empty are dropped.

    Windows are closed (``open_ns <= ts <= close_ns``), so ``until`` becomes
    ``until − 1``. Consecutive bounds ``[a, b)``, ``[b, c)`` therefore page
    through a session with no overlap and no gap (TD3).
    """
    from_ns, until_ns = bounds
    clipped = []
    for window in windows:
        open_ns = window.open_ns if from_ns is None else max(window.open_ns, from_ns)
        close_ns = (
            window.close_ns if until_ns is None else min(window.close_ns, until_ns - 1)
        )
        if open_ns <= close_ns:
            clipped.append(replace(window, open_ns=open_ns, close_ns=close_ns))
    return tuple(clipped)


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
) -> tuple[GetPlan, list[Row]]:
    """The plan and its rows, in plan order, from one snapshot (TD6).

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
        for window in clip(plan.segments, bounds):
            await read(conn, window, on_batch)
    return plan, rows
