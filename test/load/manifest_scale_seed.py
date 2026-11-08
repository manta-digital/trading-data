"""A throwaway tick database at the shape slice 307 will give production (305 D9).

ES and GC, 2010-06-08 to 2026-11-01: ``definition``, ``statistics`` and
``ohlcv-1m`` units for every UTC day a real-calendar session touches, plus the
held-window ``tbbo`` units, about 30,000 units in month-run requests, all
``ingested``. ``manifest_scale_rows`` then fills their ledgers, definitions,
day conditions and ``tick_statistics`` (about 19.4 M rows), and compresses.

The measured surfaces read the manifest, the ledgers and (through 303's
``volume_check``) resolved cleared volume; no raw ``ohlcv-1m`` or tick rows are
seeded. The fixture creates ONE database, named with the suite's ``mt_test_``
prefix, on the test cluster's admin URL (never the production variable), and
its finalizer drops only that database. A failed or aborted run therefore does
not leave about 19.4 M rows behind.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from typing import Any, Final
from urllib.parse import urlparse, urlunparse

import psycopg
import pytest
from psycopg_pool import ConnectionPool

from load.manifest_scale_rows import (
    FixtureShape,
    compress_and_analyze,
    read_fixture_shape,
    seed_conditions_and_edge,
    seed_definitions,
    seed_ledgers_and_statistics,
)
from manta_trading.data.quality.fetch_status import FetchStatus
from manta_trading.data.tick.constants import (
    CME_DATASET,
    DeliveryMode,
    TickSchema,
    UnitState,
    calendar_for_product,
)
from manta_trading.data.tick.session_days import days_touched
from manta_trading.data.tick.status_reads import calendar_sessions
from manta_trading.data.tick.universe import (
    TICK_UNIVERSE,
    HistoryWant,
    TickUniverseEntry,
)
from manta_trading.market.schema.migrations import TRACKS
from manta_trading.market.schema.runner import apply_migrations

FIRST_DAY: Final = date(2010, 6, 8)
WANT_END: Final = date(2026, 11, 1)
#: Units run a little past the want end so the last sessions' statistics
#: windows (through the next session's close) are loaded too.
UNIT_END: Final = date(2026, 11, 4)
DATASET_END: Final = datetime(2026, 11, 6, tzinfo=UTC)
#: Instrument-id ranges per product: 10 M apart, spreads 1 M above their outrights.
PRODUCT_BASE: Final = {"ES": 10_000_000, "GC": 20_000_000}
#: Name prefix of the database; ``mt_test_`` marks it as the suite's to drop.
DB_PREFIX: Final = "mt_test_m"
_NAME_LENGTH: Final = 20
_JOB_PREFIX: Final = "seed"


@dataclass(frozen=True)
class ManifestScale:
    """The seeded database and what was put in it."""

    tick_url: str
    calendar_url: str
    universe: tuple[TickUniverseEntry, ...]
    units: int
    requests: int
    statistics_rows: int


def measured_universe() -> tuple[TickUniverseEntry, ...]:
    """The production universe at the shape 307 will make it: every product
    wants definitions, ``ohlcv-1m`` bars and ``statistics`` over the full
    range; the tick tier keeps its held window."""
    history = tuple(
        HistoryWant(schema, FIRST_DAY, WANT_END)
        for schema in (
            TickSchema.DEFINITION,
            TickSchema.OHLCV_1M,
            TickSchema.STATISTICS,
        )
    )
    return tuple(replace(entry, history=history) for entry in TICK_UNIVERSE)


def _database_url(admin_url: str, name: str) -> str:
    return urlunparse(urlparse(str(admin_url))._replace(path=f"/{name}"))


def _session_days(calendar_url: str, product: str) -> list[tuple[date, date]]:
    """(UTC day, the session it belongs to) for every day a real session of
    ``product`` touches; a day two sessions touch belongs to the later one."""
    start = datetime.combine(FIRST_DAY, datetime.min.time(), UTC)
    end = datetime.combine(UNIT_END, datetime.min.time(), UTC)
    owner: dict[date, date] = {}
    for session in calendar_sessions(
        calendar_url, calendar_for_product(product), start, end
    ):
        for day in days_touched(session):
            owner[day] = session.session_date
    return sorted((d, s) for d, s in owner.items() if d >= FIRST_DAY)


def _create_scratch_tables(conn: psycopg.Connection[Any]) -> None:
    conn.execute(
        "CREATE TEMP TABLE seed_day (product text, day date, session_date date)"
    )
    conn.execute(
        "CREATE TEMP TABLE seed_unit (unit_id bigint, product text, schema text,"
        " day date, session_date date)"
    )


def _load_days(conn: psycopg.Connection[Any], calendar_url: str) -> None:
    with conn.cursor().copy("COPY seed_day FROM STDIN") as copy:
        for product in PRODUCT_BASE:
            for day, session in _session_days(calendar_url, product):
                copy.write_row((product, day, session))


def _seed_units(
    conn: psycopg.Connection[Any],
    entry: TickUniverseEntry,
    schema: TickSchema,
    first: date,
    end: date,
) -> int:
    """One request per month with one ingested unit per seeded day in it."""
    cursor = conn.execute(
        "WITH months AS ("
        " SELECT date_trunc('month', day)::date AS month, min(day) AS lo,"
        "  max(day) AS hi FROM seed_day"
        "  WHERE product = %(product)s AND day >= %(first)s AND day < %(end)s"
        "  GROUP BY 1),"
        " request AS ("
        " INSERT INTO tick_request (dataset, schema, symbols, stype_in,"
        "  range_start, range_end, delivery_mode, is_adopted, is_plan_included,"
        "  provider_job_id,"
        "  estimated_cost_usd, requested_at)"
        " SELECT %(dataset)s, %(schema)s, %(symbols)s, %(stype)s, lo, hi + 1,"
        "  %(mode)s, false, false,"
        "  %(prefix)s::text || '-' || %(product)s::text || '-' || %(schema)s::text"
        "  || '-' || to_char(month, 'YYYYMM'), 0, now() FROM months"
        " RETURNING request_id, range_start, range_end)"
        " INSERT INTO tick_archive_unit (request_id, unit_date, state,"
        "  state_changed_at, fetch_status, attempt_count, file_path,"
        "  file_size_bytes, file_sha256)"
        " SELECT r.request_id, d.day, %(state)s, now(), %(fetch)s, 0, 'seed', 1,"
        "  repeat('0', 64)"
        " FROM request r JOIN seed_day d ON d.product = %(product)s"
        "  AND d.day >= r.range_start AND d.day < r.range_end",
        {
            "product": entry.product,
            "first": first,
            "end": end,
            "dataset": CME_DATASET,
            "schema": schema.value,
            "symbols": list(entry.symbols),
            "stype": entry.stype_in.value,
            "mode": DeliveryMode.BATCH_JOB.value,
            "prefix": _JOB_PREFIX,
            "state": UnitState.INGESTED.value,
            "fetch": FetchStatus.UNKNOWN.value,
        },
    )
    return cursor.rowcount


def _register_units(conn: psycopg.Connection[Any]) -> None:
    """``seed_unit``: every seeded unit with its product, schema and day."""
    conn.execute(
        "INSERT INTO seed_unit SELECT u.unit_id,"
        " split_part(r.provider_job_id, '-', 2), r.schema, u.unit_date,"
        " d.session_date FROM tick_archive_unit u"
        " JOIN tick_request r USING (request_id)"
        " JOIN seed_day d ON d.product = split_part(r.provider_job_id, '-', 2)"
        "  AND d.day = u.unit_date WHERE r.provider_job_id LIKE %s",
        (_JOB_PREFIX + "-%",),
    )


def seed(tick_url: str, calendar_url: str, shape: FixtureShape) -> ManifestScale:
    """Seed ``tick_url`` (migrated, empty) and return the counts."""
    universe = measured_universe()
    with psycopg.connect(tick_url, autocommit=True) as conn:
        _create_scratch_tables(conn)
        _load_days(conn, calendar_url)
        units = 0
        for entry in universe:
            for want in entry.history:
                units += _seed_units(conn, entry, want.schema, FIRST_DAY, UNIT_END)
            assert entry.tier is not None and entry.start and entry.end
            units += _seed_units(conn, entry, entry.tier, entry.start, entry.end)
        _register_units(conn)
        seed_conditions_and_edge(conn, DATASET_END)
        for entry in universe:
            base = PRODUCT_BASE[entry.product]
            seed_definitions(conn, entry.product, base)
            seed_ledgers_and_statistics(conn, shape, entry.product, base)
        compress_and_analyze(conn)
        requests = conn.execute("SELECT count(*) FROM tick_request").fetchone()
        rows = conn.execute("SELECT count(*) FROM tick_statistics").fetchone()
    assert requests is not None and rows is not None
    return ManifestScale(
        tick_url, calendar_url, universe, units, int(requests[0]), int(rows[0])
    )


@pytest.fixture(scope="session")
def manifest_scale(
    test_admin_url: str, session_migrated_db: str
) -> Iterator[ManifestScale]:
    """Build the database once per test session; drop it in the finalizer.

    The drop names only the database created here (``name``), as the
    destructive-statement rule requires."""
    name = DB_PREFIX + uuid.uuid4().hex[: _NAME_LENGTH - len(DB_PREFIX)]
    with psycopg.connect(test_admin_url, autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE "{name}"')
    url = _database_url(test_admin_url, name)
    try:
        with ConnectionPool(url, min_size=1, max_size=2) as pool:
            apply_migrations(pool, TRACKS["tick"])
        scale = seed(url, session_migrated_db, read_fixture_shape())
        print(
            f"\nmanifest-scale seed: {scale.units} units in {scale.requests} "
            f"requests, {scale.statistics_rows} statistics rows"
        )
        yield scale
    finally:
        with psycopg.connect(test_admin_url, autocommit=True) as admin:
            admin.execute(
                "SELECT pg_terminate_backend(a.pid) FROM pg_stat_activity a"
                " JOIN pg_roles r ON r.rolname = a.usename"
                " WHERE a.datname = %s AND a.pid <> pg_backend_pid()"
                " AND NOT r.rolsuper",
                (name,),
            )
            admin.execute(f'DROP DATABASE IF EXISTS "{name}"')
