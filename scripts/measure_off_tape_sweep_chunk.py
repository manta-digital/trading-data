"""Time the off-tape sweep on one production-sized chunk (slice 171, task 6.5).

Copies one ``minute_ohlcv`` chunk's rows from production (read-only, by its
time range) into a throwaway database this script creates on the test
cluster, applies the minute migrations, compresses the chunk, then times
``sweep_chunk`` on it and projects the full-history sweep from rows/s.

The throwaway database is named ``mt_test_sweep_<hex>`` and dropped on exit.

Manual, developer-run. Takes the production URL (read-only) and the
test-cluster admin URL as arguments. Usage::

    uv run python scripts/measure_off_tape_sweep_chunk.py \\
        --prod-url "$MT_TIMESCALE_DB_URL" --admin-url "$MT_TIMESCALE_TEST_URL" \\
        --start 2026-10-01T00:00Z --end 2026-10-08T00:00Z
"""

from __future__ import annotations

import argparse
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from urllib.parse import urlparse, urlunparse

import psycopg
from psycopg import sql
from psycopg_pool import ConnectionPool

from manta_trading.market.maintenance.off_tape_sweep import list_chunks, sweep_chunk
from manta_trading.market.schema.migrations.minute import MINUTE_MIGRATIONS
from manta_trading.market.schema.runner import apply_migrations

_COLUMNS = "time, symbol, open, high, low, close, volume"


@contextmanager
def _throwaway(admin_url: str) -> Iterator[str]:
    name = f"mt_test_sweep_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(admin_url, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        yield urlunparse(urlparse(admin_url)._replace(path=f"/{name}"))
    finally:
        with psycopg.connect(admin_url, autocommit=True) as admin:
            admin.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
                    sql.Identifier(name)
                )
            )


def _copy(prod_url: str, target_url: str, start: datetime, end: datetime) -> int:
    # COPY (query) takes no bind parameters: compose the bounds as literals.
    query = sql.SQL(
        "COPY (SELECT {columns} FROM minute_ohlcv WHERE time >= {start}"
        " AND time < {end}) TO STDOUT (FORMAT BINARY)"
    ).format(columns=sql.SQL(_COLUMNS), start=sql.Literal(start), end=sql.Literal(end))
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
    parser.add_argument("--prod-url", required=True, help="production, read-only")
    parser.add_argument("--admin-url", required=True, help="test-cluster admin")
    parser.add_argument("--start", type=datetime.fromisoformat, required=True)
    parser.add_argument("--end", type=datetime.fromisoformat, required=True)
    args = parser.parse_args()
    prod = args.prod_url
    with psycopg.connect(prod) as conn:
        total = conn.execute("SELECT approximate_row_count('minute_ohlcv')").fetchone()
    assert total is not None
    with _throwaway(args.admin_url) as url:
        with ConnectionPool(url, min_size=1, max_size=2) as pool:
            apply_migrations(pool, MINUTE_MIGRATIONS)
        began = time.perf_counter()
        rows = _copy(prod, url, args.start, args.end)
        print(f"copied {rows:,} rows in {time.perf_counter() - began:.0f} s")
        with psycopg.connect(url, autocommit=True) as conn:
            for each in list_chunks(conn):
                conn.execute("SELECT compress_chunk(%s::regclass)", (each.name,))
            conn.execute("SET statement_timeout = 0")
            chunks = list_chunks(conn)
            # The newest chunk lying wholly inside the requested range.
            chunk = max(
                (c for c in chunks if c.start >= args.start and c.end <= args.end),
                key=lambda c: c.end,
            )
            result = sweep_chunk(conn, chunk)
    rate = result.rows / result.seconds
    print(f"chunk {chunk.name} [{chunk.start} → {chunk.end}]")
    print(f"swept {result.rows:,} rows in {result.seconds:.1f} s ({rate:,.0f} rows/s)")
    print(f"marked {result.marked:,} ({result.marked / result.rows:.3%})")
    hours = int(total[0]) / rate / 3600
    print(f"projection: {int(total[0]):,} rows → {hours:.1f} h single process")


if __name__ == "__main__":
    main()
