"""Read bars, mark them, write ``minute_ohlcv.off_tape`` (slice 171).

The one marking routine: both minute insert paths call :func:`mark_inserted`
and the history sweep calls :func:`write_masks`. Everything runs on the
caller's connection inside the caller's transaction and never commits, so a
failure rolls back the bars it was marking.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from datetime import date, datetime, time, timedelta

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
    conn: psycopg.Connection, symbol: str, first: date, last: date
) -> pd.DataFrame:
    """Whole ET days ``first..last`` of one symbol, with stored marks."""
    end = et_day_start(last + timedelta(days=1))
    rows = conn.execute(_READ_DAYS, (symbol, et_day_start(first), end)).fetchall()
    frame = pd.DataFrame(
        rows, columns=["time", "open", "high", "low", "close", "off_tape"]
    )
    frame["time"] = pd.to_datetime(frame["time"], utc=True)
    return frame


def write_masks(conn: psycopg.Connection, masks: pd.DataFrame) -> int:
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
        cur.execute(
            f"UPDATE minute_ohlcv AS m SET off_tape = t.mask FROM {_MASK_TABLE} t"
            " WHERE m.symbol = t.symbol AND m.time = t.time"
        )
        updated = cur.rowcount
        cur.execute(f"DROP TABLE pg_temp.{_MASK_TABLE}")
    return updated


def mark_days(conn: psycopg.Connection, symbol: str, et_days: Iterable[date]) -> int:
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
    conn: psycopg.Connection, symbol: str, times: Iterable[datetime]
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
