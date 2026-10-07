"""The ``tick_trade`` fingerprint for the slice 228 restore drill (TD3).

One definition, run against production, the restored database and the
rebuilt one. Per ``(instrument_id, UTC day of ts_event)`` it returns the row
count and the md5 of the rows' text in key order. Two equal fingerprint sets
mean the same rows in every group; compression state is not part of the claim.

The hashed columns come from ``storage_columns.py``, never typed here, so a
column added to the contract is hashed automatically. ``unit_id`` is left
out: a rebuild numbers units in adopt order, so it is not comparable across
databases. TD4 checks which unit wrote each row, through the ledger.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import date, timedelta
from typing import Any

import psycopg
from psycopg import sql

from manta_trading.data.tick.storage_columns import (
    TICK_TRADE_COLUMNS,
    TICK_TRADE_DERIVED_COLUMNS,
    TICK_TRADE_KEY,
)
from manta_trading.market.schema.migrations.tick import interval_to_ns

TABLE = "tick_trade"
#: Identity values that differ between databases holding the same rows.
EXCLUDED_COLUMNS: frozenset[str] = frozenset({"unit_id"})
#: The time column the day groups are cut from (integer nanoseconds, UTC).
TIME_COLUMN = "ts_event"
#: The column naming a group's instrument.
INSTRUMENT_COLUMN = "instrument_id"
_NS_PER_DAY = interval_to_ns(timedelta(days=1))
_EPOCH = date(1970, 1, 1)

Group = tuple[int, date]
Fingerprint = dict[Group, tuple[int, str]]


def hashed_columns(
    trade_columns: Mapping[str, str] = TICK_TRADE_COLUMNS,
    derived_columns: Iterable[str] = TICK_TRADE_DERIVED_COLUMNS,
) -> list[str]:
    """Every ``tick_trade`` column, in table order, except the excluded ones."""
    columns = [*trade_columns.values(), *derived_columns]
    return [c for c in columns if c not in EXCLUDED_COLUMNS]


def fingerprint_sql(columns: Iterable[str]) -> sql.Composed:
    """Per-group count and md5 over ``columns``, ordered by the table's key."""
    row = sql.SQL(", ").join(sql.Identifier(c) for c in columns)
    order = sql.SQL(", ").join(sql.Identifier(c) for c in TICK_TRADE_KEY)
    time, instrument = sql.Identifier(TIME_COLUMN), sql.Identifier(INSTRUMENT_COLUMN)
    return sql.SQL(
        "SELECT {instrument}, {time} / {ns_per_day} AS day_index, count(*),"
        " md5(string_agg(ROW({row})::text, E'\\n' ORDER BY {order}))"
        " FROM {table} GROUP BY 1, 2 ORDER BY 1, 2"
    ).format(
        instrument=instrument,
        time=time,
        ns_per_day=sql.Literal(_NS_PER_DAY),
        row=row,
        order=order,
        table=sql.Identifier(TABLE),
    )


#: The one fingerprint query (TD3).
FINGERPRINT_SQL = fingerprint_sql(hashed_columns())


def fingerprint(conn: psycopg.Connection[Any]) -> Fingerprint:
    """``{(instrument_id, UTC day): (row count, md5)}`` for one database."""
    rows = conn.execute(FINGERPRINT_SQL).fetchall()
    return {
        (int(inst), _EPOCH + timedelta(days=int(day))): (int(count), str(digest))
        for inst, day, count, digest in rows
    }


def diff_fingerprints(expected: Fingerprint, seen: Fingerprint) -> list[str]:
    """Each group that differs or exists on one side only, named; ``[]`` if equal."""
    problems: list[str] = []
    for group in sorted(expected.keys() | seen.keys()):
        name = f"instrument {group[0]} day {group[1].isoformat()}"
        if group not in seen:
            problems.append(f"{name}: missing (expected {expected[group][0]} rows)")
        elif group not in expected:
            problems.append(f"{name}: unexpected ({seen[group][0]} rows)")
        elif expected[group] != seen[group]:
            (e_count, e_md5), (s_count, s_md5) = expected[group], seen[group]
            problems.append(
                f"{name}: expected {e_count} rows md5 {e_md5}, "
                f"seen {s_count} rows md5 {s_md5}"
            )
    return problems
