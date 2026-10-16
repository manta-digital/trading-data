"""Time the off-tape sweep on one production-sized chunk (slice 171, task 6.5).

Copies one ``minute_ohlcv`` chunk's rows from production (read-only, by its
time range) into a throwaway database this script creates on the test
cluster, applies the minute migrations, compresses the chunk, then times
``sweep_chunk`` on it and projects the full-history sweep from rows/s.

The throwaway database is named ``mt_test_sweep_<hex>`` and dropped on exit.

Manual, developer-run. Reads ``MT_TIMESCALE_DB_URL`` (production, read-only)
and ``MT_TIMESCALE_TEST_URL`` (test-cluster admin). Usage::

    uv run python scripts/measure_off_tape_sweep_chunk.py \\
        --start 2026-10-01T00:00Z --end 2026-10-08T00:00Z
"""

from __future__ import annotations

import argparse
import os
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from urllib.parse import urlparse, urlunparse

import psycopg
from psycopg_pool import ConnectionPool

from manta_trading.market.maintenance.off_tape_sweep import list_chunks, sweep_chunk
from manta_trading.market.schema.migrations.minute import MINUTE_MIGRATIONS
from manta_trading.market.schema.runner import apply_migrations

_COLUMNS = "time, symbol, open, high, low, close, volume"


@contextmanager
def _throwaway(admin_url: str) -> Iterator[str]:
    name = f"mt_test_sweep_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(admin_url, autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE "{name}"')
    try:
        yield urlunparse(urlparse(admin_url)._replace(path=f"/{name}"))
    finally:
        with psycopg.connect(admin_url, autocommit=True) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def _copy(prod_url: str, target_url: str, start: datetime, end: datetime) -> int:
    query = (
        f"COPY (SELECT {_COLUMNS} FROM minute_ohlcv"
        f" WHERE time >= '{start.isoformat()}' AND time < '{end.isoformat()}')"
        " TO STDOUT (FORMAT BINARY)"
    )
    with psycopg.connect(prod_url) as source, psycopg.connect(target_url) as target:
        source.read_only = True
        with (
            source.cursor().copy(query) as out,
            target.cursor().copy(
                f"COPY minute_ohlcv ({_COLUMNS}) FROM STDIN (FORMAT BINARY)"
            ) as into,
        ):
            for block in out:
                into.write(block)
        target.commit()
        row = target.execute("SELECT count(*) FROM minute_ohlcv").fetchone()
    assert row is not None
    return int(row[0])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--start", type=datetime.fromisoformat, required=True)
    parser.add_argument("--end", type=datetime.fromisoformat, required=True)
    args = parser.parse_args()
    prod = os.environ["MT_TIMESCALE_DB_URL"]
    with psycopg.connect(prod) as conn:
        total = conn.execute("SELECT approximate_row_count('minute_ohlcv')").fetchone()
    assert total is not None
    with _throwaway(os.environ["MT_TIMESCALE_TEST_URL"]) as url:
        with ConnectionPool(url, min_size=1, max_size=2) as pool:
            apply_migrations(pool, MINUTE_MIGRATIONS)
        began = time.perf_counter()
        rows = _copy(prod, url, args.start, args.end)
        print(f"copied {rows:,} rows in {time.perf_counter() - began:.0f} s")
        with psycopg.connect(url, autocommit=True) as conn:
            for chunk in list_chunks(conn):
                conn.execute("SELECT compress_chunk(%s::regclass)", (chunk.name,))
            conn.execute("SET statement_timeout = 0")
            chunks = list_chunks(conn)
            biggest = max(
                chunks,
                key=lambda c: (c.end <= args.end and c.start >= args.start, c.end),
            )
            result = sweep_chunk(conn, biggest)
    rate = result.rows / result.seconds
    print(f"chunk {biggest.name} [{biggest.start} → {biggest.end}]")
    print(f"swept {result.rows:,} rows in {result.seconds:.1f} s ({rate:,.0f} rows/s)")
    print(f"marked {result.marked:,} ({result.marked / result.rows:.3%})")
    hours = int(total[0]) / rate / 3600
    print(f"projection: {int(total[0]):,} rows → {hours:.1f} h single process")


if __name__ == "__main__":
    main()
