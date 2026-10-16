"""Read bars, mark them, write ``minute_ohlcv.off_tape`` (slice 171).

The one marking routine: both minute insert paths call :func:`mark_inserted`
and the history sweep calls :func:`write_masks`. Everything runs on the
caller's connection inside the caller's transaction and never commits, so a
failure rolls back the bars it was marking.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from datetime import date, datetime, time, timedelta
from typing import Any

import pandas as pd
import psycopg

from manta_trading.constants import OFF_TAPE_DAY_TZ
from manta_trading.data.quality.off_tape import et_days_of, mark_symbol_days
from manta_trading.logging import get_logger

_logger = get_logger(__name__)

_READ_DAYS = """
SELECT time, open::float8, high::float8, low::float8, close::float8, off_tape
FROM minute_ohlcv
WHERE symbol = %s AND time >= %s AND time < %s
ORDER BY time
"""
_RANGE_COLUMNS = ["symbol", "time", "open", "high", "low", "close"]
_READ_RANGE = """
SELECT symbol, time, open::float8, high::float8, low::float8, close::float8
FROM minute_ohlcv
WHERE time >= %s AND time < %s
ORDER BY symbol, time
"""
_MASK_TABLE = "_off_tape_masks"


def et_day_start(day: date) -> datetime:
    """The UTC instant an ET calendar day begins."""
    return datetime.combine(day, time(0), tzinfo=OFF_TAPE_DAY_TZ)


def _runs(days: Iterable[date]) -> Iterator[tuple[date, date]]:
    """Contiguous runs of dates as ``(first, last)``, oldest first."""
    ordered = sorted(set(days))
    if not ordered:
        return
    first = previous = ordered[0]
    for day in ordered[1:]:
        if day - previous > timedelta(days=1):
            yield first, previous
            first = day
        previous = day
    yield first, previous


def read_days(
    conn: psycopg.Connection[Any], symbol: str, first: date, last: date
) -> pd.DataFrame:
    """Whole ET days ``first..last`` of one symbol, with stored marks."""
    end = et_day_start(last + timedelta(days=1))
    rows = conn.execute(_READ_DAYS, (symbol, et_day_start(first), end)).fetchall()
    frame = pd.DataFrame(
        rows, columns=["time", "open", "high", "low", "close", "off_tape"]
    )
    frame["time"] = pd.to_datetime(frame["time"], utc=True)
    return frame


def iter_symbol_frames(
    conn: psycopg.Connection[Any],
    start: datetime,
    end: datetime,
    *,
    batch: int = 200_000,
) -> Iterator[pd.DataFrame]:
    """One frame per symbol of every bar in ``[start, end)``, streamed.

    Server-side cursor ordered by ``(symbol, time)``, so memory holds about
    one batch plus one symbol. Needs an open transaction (named cursor).
    """
    pending: list[tuple[object, ...]] = []
    with conn.cursor(name="off_tape_frames") as cursor:
        cursor.execute(_READ_RANGE, (start, end))
        while rows := cursor.fetchmany(batch):
            pending.extend(rows)
            last = pending[-1][0]
            done = [row for row in pending if row[0] != last]
            pending = [row for row in pending if row[0] == last]
            yield from _frames(done)
    yield from _frames(pending)


def _frames(rows: list[tuple[object, ...]]) -> Iterator[pd.DataFrame]:
    if not rows:
        return
    frame = pd.DataFrame(rows, columns=_RANGE_COLUMNS)
    frame["time"] = pd.to_datetime(frame["time"], utc=True)
    for _, group in frame.groupby("symbol", sort=False):
        yield group.reset_index(drop=True)


def write_masks(conn: psycopg.Connection[Any], masks: pd.DataFrame) -> int:
    """Set ``off_tape`` from ``masks`` (``symbol, time, mask``); rows updated.

    COPY into a temp table, then one ``UPDATE ... FROM``. The temp table is
    dropped afterwards so the helper can run more than once per transaction.
    """
    if masks.empty:
        return 0
    with conn.cursor() as cur:
        cur.execute(
            f"CREATE TEMP TABLE {_MASK_TABLE} (symbol text, time timestamptz,"
            " mask smallint) ON COMMIT DROP"
        )
        with cur.copy(f"COPY {_MASK_TABLE} (symbol, time, mask) FROM STDIN") as copy:
            for row in masks[["symbol", "time", "mask"]].itertuples(index=False):
                copy.write_row((row.symbol, row.time.to_pydatetime(), int(row.mask)))
        # Constant time bounds let the planner prune to the chunks touched;
        # without them a large join can scan the whole hypertable.
        cur.execute(
            f"UPDATE minute_ohlcv AS m SET off_tape = t.mask FROM {_MASK_TABLE} t"
            " WHERE m.symbol = t.symbol AND m.time = t.time"
            " AND m.time >= %s AND m.time <= %s",
            (masks["time"].min().to_pydatetime(), masks["time"].max().to_pydatetime()),
        )
        updated = cur.rowcount
        cur.execute(f"DROP TABLE pg_temp.{_MASK_TABLE}")
    return updated


def mark_days(
    conn: psycopg.Connection[Any], symbol: str, et_days: Iterable[date]
) -> int:
    """Re-mark whole ET days of one symbol; rows whose mask changed.

    ``NULL`` always differs, so every unevaluated row is written.
    """
    changed: list[pd.DataFrame] = []
    for first, last in _runs(et_days):
        bars = read_days(conn, symbol, first, last)
        if bars.empty:
            continue
        bars["mask"] = mark_symbol_days(bars)
        stale = bars["off_tape"].isna() | (bars["off_tape"] != bars["mask"])
        changed.append(bars.loc[stale, ["time", "mask"]].assign(symbol=symbol))
    if not changed:
        return 0
    return write_masks(conn, pd.concat(changed, ignore_index=True))


def mark_inserted(
    conn: psycopg.Connection[Any], symbol: str, times: Iterable[datetime]
) -> int:
    """Mark every ET day an insert frame touched (the insert paths' entry).

    Logs and re-raises: a marking failure fails the write, never commits
    unevaluated bars.
    """
    try:
        return mark_days(conn, symbol, et_days_of(times))
    except Exception:
        _logger.exception("off-tape marking failed for %s; write rolled back", symbol)
        raise
