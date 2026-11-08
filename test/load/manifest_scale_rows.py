"""The rows behind the manifest-scale seed's units (slice 305 D9, tasks 9.2, 9.3).

Proportions come from the real 2024 fixtures under
``test/fixtures/databento/real/`` (the ones ``test_tick_statistics_real.py``
seeds from): instruments per day and their class, stat types per instrument and
records per stat type, and the bars of the derived ``ohlcv-1m`` day. The
statistics rows are then scaled so the table reaches the architecture's 19.4 M
records for ES and GC. Everything is set-based SQL over temp tables the seed
built: ``seed_unit`` (one row per seeded unit) and ``seed_day`` (the days and
the session each belongs to).

Instrument ids are synthetic but deterministic. Outright ``c`` is listed for the
21 months ``c - 20 .. c``, so the k-th outright listed in month ``m`` is
``base + m + k``; a spread is listed for 20 weeks the same way. Both are
defined in ``tick_definition`` with those windows.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Final

import databento as db
import psycopg

from manta_trading.data.tick.constants import CME_DATASET, calendar_for_product
from manta_trading.data.tick.statistics_constants import REQUIRED_STAT_TYPES, StatType

REAL = Path(__file__).resolve().parents[1] / "fixtures" / "databento" / "real"
DEFINITION_DAY = "20241203"
STATISTICS_DAYS = ("20241203", "20241204")
BARS_DAY = "20241203"
#: The architecture's cost table: ES and GC statistics over the full range.
TARGET_STATISTICS_ROWS: Final = 19_400_000
#: Outright and spread class codes in a definition record.
OUTRIGHT, SPREAD = "F", "S"
#: Bar instruments per day: the outrights the fixture's bar day holds.
EPOCH_NS: Final = 1_000_000_000
TBBO_RECORDS: Final = 20_000  # records (and volume) per instrument-day
TBBO_INSTRUMENTS: Final = 5
CLEARED_VOLUME_QUANTITY: Final = 10_000_000
SPREAD_ID_OFFSET: Final = 1_000_000
#: Months since 2010-01 and weeks since 2010-01-04, as SQL for a ``day`` column.
MONTH_INDEX: Final = (
    "((extract(year FROM u.day)::int - 2010) * 12 + extract(month FROM u.day)::int - 1)"
)
WEEK_INDEX: Final = "((u.day - date '2010-01-04') / 7)"


@dataclass(frozen=True)
class FixtureShape:
    """What one real day looks like: per instrument rank, its stat types'
    records, and the front bar instruments' records and volume."""

    #: (kind, rank, stat_type) -> mean records per day over the fixture days.
    statistics: dict[tuple[str, int, int], float]
    #: Per front instrument rank: (bar records, volume) of the derived bar day.
    bars: list[tuple[int, int]]


def read_fixture_shape() -> FixtureShape:
    """The shape of the real fixture days (instrument classes from the
    definitions; ranks by instrument id within each class)."""
    definitions = db.DBNStore.from_file(
        REAL / f"glbx-mdp3-{DEFINITION_DAY}.definition.dbn.zst"
    ).to_df()
    kinds = definitions.drop_duplicates("instrument_id").set_index("instrument_id")[
        "instrument_class"
    ]
    rank = {
        kind: {int(i): n for n, i in enumerate(sorted(ids.index))}
        for kind, ids in kinds.groupby(kinds)
    }
    totals: dict[tuple[str, int, int], float] = {}
    for day in STATISTICS_DAYS:
        stats = db.DBNStore.from_file(
            REAL / f"glbx-mdp3-{day}.statistics.dbn.zst"
        ).to_df()
        for (instrument, stat_type), n in (
            stats.groupby(["instrument_id", "stat_type"]).size().items()
        ):
            kind = OUTRIGHT if kinds[instrument] == OUTRIGHT else SPREAD
            key = (kind, rank[kinds[instrument]][int(instrument)], int(stat_type))
            totals[key] = totals.get(key, 0.0) + n / len(STATISTICS_DAYS)
    # A listed outright reports every required type on some day, so a contract
    # that moves from one listing rank to another (as every contract does over
    # its 21 months) is never owed a type its current rank lacks: a seed that
    # only copied the fixture's per-rank mix would invent findings.
    for outright_rank in range(len(rank[OUTRIGHT])):
        for stat_type in REQUIRED_STAT_TYPES:
            totals.setdefault((OUTRIGHT, outright_rank, int(stat_type)), 1.0)
    bars = db.DBNStore.from_file(
        REAL / f"glbx-mdp3-{BARS_DAY}.ohlcv-1m.dbn.zst"
    ).to_df()
    per_instrument = bars.groupby("instrument_id").agg(
        records=("volume", "size"), volume=("volume", "sum")
    )
    ordered = per_instrument.sort_values("volume", ascending=False)
    return FixtureShape(
        totals, [(int(r.records), int(r.volume)) for r in ordered.itertuples()]
    )


def _ns(column: str) -> str:
    return f"(extract(epoch FROM {column})::bigint * {EPOCH_NS})"


def _instrument(kind: str, rank: str, base: int) -> str:
    """SQL for the instrument id of ``rank`` listed on ``u.day``."""
    if kind == OUTRIGHT:
        return f"({base} + {MONTH_INDEX} + {rank})"
    return f"({base + SPREAD_ID_OFFSET} + {WEEK_INDEX} + {rank})"


def _month_ns(offset: str) -> str:
    """SQL: the epoch ns of the start of month ``offset`` (a SQL expression in
    ``c``) counted from 2010-01."""
    return _ns(f"(date '2010-01-01' + (({offset}) * interval '1 month'))")


def _week_ns(offset: str) -> str:
    return _ns(f"(date '2010-01-04' + (({offset}) * 7))")


def seed_definitions(conn: psycopg.Connection[Any], product: str, base: int) -> None:
    """Outright and spread windows covering every seeded day, per instrument."""
    unit = conn.execute(
        "SELECT min(unit_id) FROM seed_unit"
        " WHERE product = %s AND schema = 'definition'",
        (product,),
    ).fetchone()
    days = conn.execute(
        "SELECT min(day), max(day) FROM seed_day WHERE product = %s", (product,)
    ).fetchone()
    assert unit is not None and unit[0] is not None and days is not None
    first_day, last_day = days
    first_month = (first_day.year - 2010) * 12 + first_day.month - 1
    last_month = (last_day.year - 2010) * 12 + last_day.month - 1
    last_week = (last_day - date(2010, 1, 4)).days // 7
    insert = (
        "INSERT INTO tick_definition (instrument_id, activation_ns, expiration_ns,"
        " raw_symbol, asset, instrument_class, ts_recv_ns, unit_id)"
    )
    common = {"product": product, "unit": unit[0]}
    conn.execute(
        f"{insert} SELECT %(base)s + c, {_month_ns('c - 20')},"
        f" {_month_ns('c + 1')} - 1, %(product)s || c, %(product)s, %(kind)s, 0,"
        " %(unit)s FROM generate_series(%(first)s, %(last)s + 20) c",
        common | {"base": base, "kind": OUTRIGHT, "first": first_month,
                  "last": last_month},
    )  # fmt: skip
    conn.execute(
        f"{insert} SELECT %(base)s + c, {_week_ns('c - 19')},"
        f" {_week_ns('c + 1')} - 1, %(product)s || 'S' || c, %(product)s, %(kind)s,"
        " 0, %(unit)s FROM generate_series(0, %(last)s + 20) c",
        common | {"base": base + SPREAD_ID_OFFSET, "kind": SPREAD,
                  "last": last_week},
    )  # fmt: skip


def seed_conditions_and_edge(conn: psycopg.Connection[Any], end: datetime) -> None:
    """A day condition for every day, and the one dataset edge row."""
    conn.execute(
        "INSERT INTO tick_day_condition (dataset, condition_date, condition,"
        " observed_at) SELECT %s, d::date, 'available', now()"
        " FROM generate_series(date '2010-06-06', %s::date, interval '1 day') d",
        (CME_DATASET, end.date()),
    )
    conn.execute(
        "INSERT INTO tick_dataset_edge (dataset, available_start, available_end,"
        " observed_at) VALUES (%s, %s, %s, now())",
        (CME_DATASET, datetime(2010, 6, 6, tzinfo=UTC), end),
    )


def _mix_rows(shape: FixtureShape, units: int) -> list[tuple[str, int, int, int]]:
    """(kind, rank, stat type, records per unit) scaled to the target total."""
    scale = TARGET_STATISTICS_ROWS / (units * sum(shape.statistics.values()))
    return [
        (kind, rank, stat_type, max(1, round(mean * scale)))
        for (kind, rank, stat_type), mean in sorted(shape.statistics.items())
    ]


def seed_ledgers_and_statistics(
    conn: psycopg.Connection[Any], shape: FixtureShape, product: str, base: int
) -> None:
    """The statistics and bar ledgers and ``tick_statistics`` for one product,
    one year of raw rows per statement; plus its held-window tick ledger."""
    calendar = calendar_for_product(product)
    stat_units = conn.execute(
        "SELECT count(*) FROM seed_unit WHERE schema = 'statistics'"
    ).fetchone()
    assert stat_units is not None
    mix = _mix_rows(shape, int(stat_units[0]))
    conn.execute("CREATE TEMP TABLE IF NOT EXISTS seed_mix (kind text, rank int,"
                 " stat_type smallint, rows int)")  # fmt: skip
    conn.execute("TRUNCATE seed_mix")
    with conn.cursor() as cur:
        cur.executemany("INSERT INTO seed_mix VALUES (%s, %s, %s, %s)", mix)
    outright, spread = (
        _instrument(OUTRIGHT, "m.rank", base),
        _instrument(SPREAD, "m.rank", base),
    )
    instrument = f"CASE m.kind WHEN '{OUTRIGHT}' THEN {outright} ELSE {spread} END"
    conn.execute(
        "INSERT INTO tick_statistics_ledger (unit_id, instrument_id, calendar_id,"
        " session_date, stat_type, record_count, first_event_ns, last_event_ns)"
        f" SELECT u.unit_id, {instrument}, %s, u.session_date, m.stat_type, m.rows,"
        f" {_ns('u.day')}, {_ns('u.day')} + m.rows"
        " FROM seed_unit u CROSS JOIN seed_mix m"
        " WHERE u.product = %s AND u.schema = 'statistics'",
        (calendar, product),
    )
    _seed_raw_statistics(conn, product, calendar, instrument)
    for rank, (records, volume) in enumerate(shape.bars):
        conn.execute(
            "INSERT INTO tick_bar_ledger (unit_id, instrument_id, calendar_id,"
            " session_date, record_count, volume, first_event_ns, last_event_ns)"
            f" SELECT u.unit_id, {_instrument(OUTRIGHT, str(rank), base)}, %s,"
            f" u.session_date, %s, %s, {_ns('u.day')}, {_ns('u.day')} + 1"
            " FROM seed_unit u WHERE u.product = %s AND u.schema = 'ohlcv-1m'",
            (calendar, records, volume, product),
        )
    for rank in range(TBBO_INSTRUMENTS):
        conn.execute(
            "INSERT INTO tick_ingest_ledger (unit_id, instrument_id, calendar_id,"
            " session_date, record_count, volume, first_event_ns, last_event_ns)"
            f" SELECT u.unit_id, {_instrument(OUTRIGHT, str(rank), base)}, %s,"
            f" u.session_date, %s, %s, {_ns('u.day')}, {_ns('u.day')} + 1"
            " FROM seed_unit u WHERE u.product = %s AND u.schema = 'tbbo'",
            (
                calendar,
                TBBO_RECORDS,
                TBBO_RECORDS,
                product,
            ),
        )


def _seed_raw_statistics(
    conn: psycopg.Connection[Any], product: str, calendar: str, instrument: str
) -> None:
    """``tick_statistics`` rows, one calendar year per statement."""
    years = conn.execute(
        "SELECT min(extract(year FROM day))::int, max(extract(year FROM day))::int"
        " FROM seed_day WHERE product = %s",
        (product,),
    ).fetchone()
    assert years is not None
    cleared = int(StatType.CLEARED_VOLUME)
    for year in range(years[0], years[1] + 1):
        conn.execute(
            "INSERT INTO tick_statistics (ts_recv, ts_event, ts_ref, instrument_id,"
            " publisher_id, stat_type, update_action, stat_flags, price, quantity,"
            " sequence, channel_id, ts_in_delta, record_ordinal, calendar_id,"
            " session_date, unit_id)"
            f" SELECT {_ns('u.day')} + g.n, {_ns('u.day')} + g.n, {_ns('u.day')} - 1,"
            f" {instrument}, 1, m.stat_type, 1, 0,"
            " CASE WHEN m.stat_type = 3 THEN 5600250000000 END,"
            f" CASE WHEN m.stat_type = {cleared} THEN {CLEARED_VOLUME_QUANTITY} END,"
            " g.n, 13, 0, 0, %s, u.session_date, u.unit_id"
            " FROM seed_unit u CROSS JOIN seed_mix m"
            " CROSS JOIN LATERAL generate_series(1, m.rows) g(n)"
            " WHERE u.product = %s AND u.schema = 'statistics'"
            " AND extract(year FROM u.day) = %s",
            (calendar, product, year),
        )


def compress_and_analyze(conn: psycopg.Connection[Any]) -> None:
    """Compress every ``tick_statistics`` chunk (the test cluster has no
    scheduler; production's history is compressed), then refresh statistics."""
    conn.execute("SELECT compress_chunk(c) FROM show_chunks('tick_statistics') c")
    conn.execute("ANALYZE")
