"""The bookkeeping comparison for the slice 228 restore drill (TD4).

Every tick bookkeeping table is compared, restored against rebuilt, by
natural key, never by identity id: a rebuild renumbers ``request_id`` and
``unit_id``. Each id-valued column is translated to the natural key of the
row it points at on its own side (the restored→rebuilt id map, expressed as
keys), then compared. What may differ and what may be missing are the
constants below, defined once; anything else fails, named by table, column
and natural key. Read only: no SQL here writes.

``table_md5`` serves step 5 instead (restored against production): a
physical restore keeps the ids, so there each table must match exactly.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from manta_trading.data.tick.constants import UNIT_STATES_WITH_FILE

REQUEST = "tick_request"
UNIT = "tick_archive_unit"
DEFINITION = "tick_definition"
LEDGER = "tick_ingest_ledger"
EDGE = "tick_dataset_edge"
CONDITION = "tick_day_condition"

#: Every tick bookkeeping table: every tick table except ``tick_trade``.
BOOKKEEPING_TABLES: tuple[str, ...] = (
    REQUEST,
    UNIT,
    DEFINITION,
    LEDGER,
    EDGE,
    CONDITION,
)

#: ``(table, column)`` pairs a rebuild may set differently (TD4): the 224
#: design's adopt-path losses plus the times a rebuild sets anew. The pass
#: re-observes the availability tables at drill time, so all their value
#: columns may differ. Pinned against the migrated schema by a test.
ALLOWED_DIFFERENCES: frozenset[tuple[str, str]] = frozenset(
    {
        (REQUEST, "is_adopted"),
        (REQUEST, "estimated_cost_usd"),
        (REQUEST, "download_deadline"),
        (REQUEST, "requested_at"),
        (UNIT, "superseded_by_unit_id"),
        (UNIT, "repurchase_of_unit_id"),
        (UNIT, "reopened_at"),
        (UNIT, "state_changed_at"),
        (UNIT, "last_attempt_at"),
        (UNIT, "attempt_count"),
        (UNIT, "failure_reason"),
        (EDGE, "available_start"),
        (EDGE, "available_end"),
        (EDGE, "observed_at"),
        (CONDITION, "condition"),
        (CONDITION, "last_modified_date"),
        (CONDITION, "observed_at"),
    }
)

#: Tables the rebuild re-observes: it may hold rows the restore lacks, but
#: every restored key must exist in it (TD4).
REOBSERVED_TABLES: frozenset[str] = frozenset({EDGE, CONDITION})

#: Identity columns, renumbered by a rebuild and never compared.
IDENTITY: dict[str, str] = {REQUEST: "request_id", UNIT: "unit_id"}

#: Id-valued columns and the table whose identity they hold.
ID_REFERENCES: dict[tuple[str, str], str] = {
    (UNIT, "request_id"): REQUEST,
    (UNIT, "superseded_by_unit_id"): UNIT,
    (UNIT, "repurchase_of_unit_id"): UNIT,
    (DEFINITION, "unit_id"): UNIT,
    (LEDGER, "unit_id"): UNIT,
}

#: Natural key columns per table, read after id translation.
NATURAL_KEYS: dict[str, tuple[str, ...]] = {
    REQUEST: ("provider_job_id",),
    UNIT: ("request_id", "unit_date"),
    DEFINITION: ("instrument_id", "activation_ns"),
    LEDGER: ("unit_id", "instrument_id", "session_date"),
    EDGE: ("dataset",),
    CONDITION: ("dataset", "condition_date"),
}

_FILE_STATES = frozenset(s.value for s in UNIT_STATES_WITH_FILE)


class MissingRule(StrEnum):
    """Why a restored row may be absent from the rebuild (TD4)."""

    REQUEST_WITHOUT_JOB = "request never accepted (no provider job id)"
    JOB_WITHOUT_FILES = "paid job with no archived files"
    UNIT_WITHOUT_FILE = "unit with no archived file"


Rows = dict[str, list[dict[str, Any]]]
Keyed = dict[tuple[Any, ...], dict[str, Any]]


@dataclass
class BookkeepingResult:
    failures: list[str] = field(default_factory=list)
    allowed_differences: Counter[tuple[str, str]] = field(default_factory=Counter)
    allowed_missing: Counter[tuple[str, MissingRule]] = field(default_factory=Counter)

    @property
    def ok(self) -> bool:
        return not self.failures

    @property
    def allowed_missing_requests(self) -> int:
        return sum(
            n for (table, _), n in self.allowed_missing.items() if table == REQUEST
        )


# --- Reading ------------------------------------------------------------------


def table_columns(conn: psycopg.Connection[Any], table: str) -> list[str]:
    rows = conn.execute(
        "SELECT column_name FROM information_schema.columns"
        " WHERE table_schema = 'public' AND table_name = %s ORDER BY ordinal_position",
        (table,),
    ).fetchall()
    if not rows:
        raise LookupError(f"table {table} has no columns (is it missing?)")
    return [str(r[0]) for r in rows]


def read_tables(conn: psycopg.Connection[Any]) -> Rows:
    """Every bookkeeping row, as column→value dicts, per table."""
    tables: Rows = {}
    with conn.cursor(row_factory=dict_row) as cur:
        for table in BOOKKEEPING_TABLES:
            columns = sql.SQL(", ").join(
                map(sql.Identifier, table_columns(conn, table))
            )
            query = sql.SQL("SELECT {} FROM {}").format(columns, sql.Identifier(table))
            tables[table] = cur.execute(query).fetchall()
    return tables


def _primary_key(conn: psycopg.Connection[Any], table: str) -> list[str]:
    rows = conn.execute(
        "SELECT a.attname FROM pg_index i JOIN pg_attribute a"
        " ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)"
        " WHERE i.indrelid = %s::regclass AND i.indisprimary"
        " ORDER BY array_position(i.indkey::int2[], a.attnum)",
        (table,),
    ).fetchall()
    if not rows:
        raise LookupError(f"table {table} has no primary key")
    return [str(r[0]) for r in rows]


def table_md5(conn: psycopg.Connection[Any], table: str) -> str | None:
    """md5 of the table's rows in primary-key order; ``None`` for an empty table."""
    if table not in BOOKKEEPING_TABLES:
        raise ValueError(f"{table} is not a bookkeeping table")
    order = sql.SQL(", ").join(map(sql.Identifier, _primary_key(conn, table)))
    query = sql.SQL("SELECT md5(string_agg(t::text, E'\\n' ORDER BY {})) FROM {} t")
    row = conn.execute(query.format(order, sql.Identifier(table))).fetchone()
    assert row is not None  # an aggregate always returns one row
    return None if row[0] is None else str(row[0])


# --- Translation to natural keys ------------------------------------------------


@dataclass
class Translated:
    """One side's rows keyed by natural key, plus what could not be keyed."""

    keyed: dict[str, Keyed]
    jobless: Counter[str]
    jobs_with_files: set[str]
    units_with_file: set[tuple[Any, ...]]


def _references(tables: Rows) -> dict[str, dict[int, Any]]:
    request_key = {r["request_id"]: r["provider_job_id"] for r in tables[REQUEST]}
    unit_key = {
        u["unit_id"]: (request_key[u["request_id"]], u["unit_date"])
        for u in tables[UNIT]
    }
    return {REQUEST: request_key, UNIT: unit_key}


def _translate_row(
    table: str, row: dict[str, Any], refs: dict[str, Any]
) -> dict[str, Any]:
    out = {c: v for c, v in row.items() if c != IDENTITY.get(table)}
    for (ref_table, column), target in ID_REFERENCES.items():
        if ref_table == table and out[column] is not None:
            out[column] = refs[target][out[column]]
    return out


#: The column leading from a row to its request's job id after translation:
#: the job id itself, or a unit key whose first element is the job id.
JOB_COLUMN: dict[str, str] = {
    REQUEST: "provider_job_id",
    UNIT: "request_id",
    DEFINITION: "unit_id",
    LEDGER: "unit_id",
}


def job_of(table: str, row: dict[str, Any]) -> Any:
    """The provider job id a translated row belongs to (tables with one)."""
    value = row[JOB_COLUMN[table]]
    return value[0] if table in (DEFINITION, LEDGER) else value


def translate(tables: Rows) -> Translated:
    """Key every row by natural key. Rows of a request with no job id can't be
    keyed (the key would be NULL) and are counted per table instead."""
    refs = _references(tables)
    keyed: dict[str, Keyed] = {t: {} for t in BOOKKEEPING_TABLES}
    jobless: Counter[str] = Counter()
    for table in BOOKKEEPING_TABLES:
        for row in tables[table]:
            out = _translate_row(table, row, refs)
            if table in JOB_COLUMN and job_of(table, out) is None:
                jobless[table] += 1
                continue
            keyed[table][tuple(out[c] for c in NATURAL_KEYS[table])] = out
    units_with_file = {k for k, u in keyed[UNIT].items() if u["state"] in _FILE_STATES}
    return Translated(
        keyed=keyed,
        jobless=jobless,
        jobs_with_files={k[0] for k in units_with_file},
        units_with_file=units_with_file,
    )


# --- Comparison -----------------------------------------------------------------


def missing_rule(
    table: str, row: dict[str, Any], restored: Translated
) -> MissingRule | None:
    """The allowed-missing rule a restored row falls under, or ``None``."""
    if table not in JOB_COLUMN:
        return None
    if job_of(table, row) not in restored.jobs_with_files:
        return MissingRule.JOB_WITHOUT_FILES
    unit = (
        (row["request_id"], row["unit_date"]) if table == UNIT else row.get("unit_id")
    )
    if table != REQUEST and unit not in restored.units_with_file:
        return MissingRule.UNIT_WITHOUT_FILE
    return None


def _label(table: str, key: tuple[Any, ...]) -> str:
    named = ", ".join(
        f"{c}={v!r}" for c, v in zip(NATURAL_KEYS[table], key, strict=True)
    )
    return f"{table} ({named})"


def _compare_row(
    table: str,
    key: tuple[Any, ...],
    a: dict[str, Any],
    b: dict[str, Any],
    result: BookkeepingResult,
) -> None:
    for column in sorted(a.keys() | b.keys()):
        if a.get(column) == b.get(column):
            continue
        if (table, column) in ALLOWED_DIFFERENCES:
            result.allowed_differences[(table, column)] += 1
            continue
        result.failures.append(
            f"{_label(table, key)} column {column}: restored {a.get(column)!r}, "
            f"rebuilt {b.get(column)!r}"
        )


def _compare_table(
    table: str, restored: Translated, rebuilt: Translated, result: BookkeepingResult
) -> None:
    a_rows, b_rows = restored.keyed[table], rebuilt.keyed[table]
    for key in sorted(a_rows.keys() | b_rows.keys(), key=repr):
        if key not in b_rows:
            rule = missing_rule(table, a_rows[key], restored)
            if rule is None:
                result.failures.append(
                    f"{_label(table, key)}: missing from the rebuild"
                )
            else:
                result.allowed_missing[(table, rule)] += 1
        elif key not in a_rows:
            if table not in REOBSERVED_TABLES:
                result.failures.append(f"{_label(table, key)}: only in the rebuild")
        else:
            _compare_row(table, key, a_rows[key], b_rows[key], result)


def compare_translated(restored: Translated, rebuilt: Translated) -> BookkeepingResult:
    result = BookkeepingResult()
    for table, count in restored.jobless.items():
        result.allowed_missing[(table, MissingRule.REQUEST_WITHOUT_JOB)] += count
    for table, count in rebuilt.jobless.items():
        result.failures.append(
            f"{table}: {count} rebuilt row(s) under a request with no provider job id "
            "(adopt only creates requests from job directories)"
        )
    for table in BOOKKEEPING_TABLES:
        _compare_table(table, restored, rebuilt, result)
    return result


def compare_bookkeeping(
    restored_conn: psycopg.Connection[Any], rebuilt_conn: psycopg.Connection[Any]
) -> BookkeepingResult:
    """TD4: the restored database's bookkeeping against the rebuilt one's."""
    for table in BOOKKEEPING_TABLES:
        a, b = table_columns(restored_conn, table), table_columns(rebuilt_conn, table)
        if a != b:
            raise ValueError(f"{table} columns differ: restored {a}, rebuilt {b}")
    return compare_translated(
        translate(read_tables(restored_conn)), translate(read_tables(rebuilt_conn))
    )
