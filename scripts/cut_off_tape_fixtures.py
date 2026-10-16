"""Cut whole-ET-day minute bars from production as off-tape fixtures (slice 171).

Each fixture is one symbol's ET calendar day of ``minute_ohlcv`` (every bar
whose ``time`` converts to that date in America/New_York, which for a winter
day includes the after-hours tail past 00:00 UTC). Columns: ``time`` (UTC,
ISO 8601), ``symbol``, ``open``, ``high``, ``low``, ``close``, ``volume``,
values exactly as stored. Rows are ordered by time, so a rerun writes
byte-identical files while the stored bars are unchanged.

The quiet regular-hours day is SPY 2026-09-22: running the detector at the
starting calibration (K=10, M=10, R=0.003) over every SPY day from 2026-08-31
to 2026-10-09 (on 2026-10-16), it was the only day with zero marks at all,
and so zero between 09:30 and 16:00 ET.

The winter day is SPY 2026-01-07: its after-hours bars run to 01:00 UTC
on 2026-01-08.

Manual, developer-run, read-only. Takes the production URL as ``--db-url``.
Output lands in ``test/fixtures/off_tape/``. Usage::

    uv run python scripts/cut_off_tape_fixtures.py --db-url "$MT_TIMESCALE_DB_URL"
"""

from __future__ import annotations

import argparse
import csv
from datetime import date
from pathlib import Path
from typing import Any

import psycopg

OUT = Path(__file__).resolve().parents[1] / "test" / "fixtures" / "off_tape"

#: (symbol, ET date) of every fixture; see the README beside the files.
FIXTURES: tuple[tuple[str, str], ...] = (
    # Known off-tape values (design success criterion 1).
    ("SPY", "2026-09-08"),
    ("SPY", "2026-09-11"),
    ("AVGO", "2026-06-03"),
    ("MSFT", "2026-08-05"),
    # Earnings jumps (design success criterion 2); AVGO 06-03 is above.
    ("MSFT", "2026-07-29"),
    ("AAPL", "2026-07-30"),
    ("SMCI", "2026-07-21"),
    ("PLTR", "2026-08-03"),
    ("TSLA", "2026-07-22"),
    ("MU", "2026-06-24"),
    ("LULU", "2026-09-03"),
    ("AMZN", "2026-07-30"),
    ("CRM", "2026-08-26"),
    ("AMD", "2026-08-04"),
    ("NFLX", "2026-07-16"),
    ("UPST", "2026-08-04"),
    ("META", "2026-07-29"),
    # Dense off-tape prints after 16:40 ET.
    ("MSFT", "2026-06-01"),
    # Winter: after-hours tail crosses 00:00 UTC.
    ("SPY", "2026-01-07"),
    # Quiet: zero marks.
    ("SPY", "2026-09-22"),
    # Tick-bound slow dip at 15:34 ET that only the dollar floor spares.
    ("ARRY", "2026-10-08"),
)

COLUMNS = ("time", "symbol", "open", "high", "low", "close", "volume")

_QUERY = """
SELECT time, symbol, open, high, low, close, volume
FROM minute_ohlcv
WHERE symbol = %(symbol)s
  AND time >= (%(day)s::date + time '00:00') AT TIME ZONE 'America/New_York'
  AND time <  (%(day)s::date + 1 + time '00:00') AT TIME ZONE 'America/New_York'
ORDER BY time
"""


def _cut(conn: psycopg.Connection[Any], symbol: str, day: str) -> Path:
    rows = conn.execute(_QUERY, {"symbol": symbol, "day": date.fromisoformat(day)})
    path = OUT / f"{symbol}_{day}.csv"
    count = 0
    with path.open("w", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(COLUMNS)
        for when, *rest in rows:
            writer.writerow([when.isoformat(), *rest])
            count += 1
    if count == 0:
        raise RuntimeError(f"{symbol} {day}: no bars")
    print(f"{path.name}: {count} bars")
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--db-url", required=True, help="production URL, read-only")
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    with psycopg.connect(args.db_url) as conn:
        conn.read_only = True
        conn.execute("SET TIME ZONE 'UTC'")
        for symbol, day in FIXTURES:
            _cut(conn, symbol, day)


if __name__ == "__main__":
    main()
