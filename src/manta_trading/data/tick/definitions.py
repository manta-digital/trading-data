"""Definition projection: verified definition units → ``tick_definition`` (224).

LLD 224 Technical Decision 5. The one schema the acquisition pass projects
(tier data and ``statistics`` always go through 225's ingest). Per unit, one
transaction on the run's connection:

1. decode the file through the reader and map each record through
   ``TICK_DEFINITION_COLUMNS``; the provider's "undefined" values and empty
   strings become ``NULL``;
2. a record whose ``activation`` or ``expiration`` is undefined fails the unit,
   naming ``instrument_id`` and ``raw_symbol`` (222 code-review F007: a window
   cannot be placed and an unbounded one is never invented);
3. two records with one key ``(instrument_id, activation_ns)`` in a file, or a
   stored row with that key, are compared on every kept column except
   ``unit_id`` and ``ts_recv_ns``: all equal is a no-op (the daily re-send);
   any difference fails the unit naming the instrument and fields — never a
   bare ``ON CONFLICT DO NOTHING``;
4. a new key is inserted; an exclusion violation (an overlapping window for a
   reused id) fails the unit naming both windows;
5. success marks the unit *ingested* with ``decoded_record_count``.

A failed unit is ``RETRY_EXHAUSTED`` (deterministic: retrying reads the same
bytes). Storage and provider errors propagate to the phase.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from psycopg import errors

from manta_trading.data.tick.constants import DEFINITION_UNDEFINED, UnitState
from manta_trading.data.tick.manifest_reads import (
    Conn,
    UnitRow,
    verified_definition_units,
)
from manta_trading.data.tick.manifest_repo import mark_ingested, record_failure
from manta_trading.data.tick.provider import ITickFileReader
from manta_trading.data.tick.run_context import TickRun
from manta_trading.data.tick.storage_columns import TICK_DEFINITION_COLUMNS

Row = dict[str, Any]
Key = tuple[int, int]

_SOURCE_BY_COLUMN = {
    column: source for source, column in TICK_DEFINITION_COLUMNS.items()
}
_KEY_COLUMNS = ("instrument_id", "activation_ns")
#: Columns compared on a same-key re-send: everything kept but the key and the
#: receive time (which changes with every send).
_COMPARED = tuple(
    column
    for column in TICK_DEFINITION_COLUMNS.values()
    if column not in (*_KEY_COLUMNS, "ts_recv_ns")
)
_INSERT_COLUMNS = (*TICK_DEFINITION_COLUMNS.values(), "unit_id")


class DefinitionRejected(Exception):
    """A unit's definitions cannot be stored; the message names why."""


@dataclass
class DefinitionsTally:
    projected: int = 0
    inserted: int = 0
    noops: int = 0
    failed: int = 0

    def to_dict(self) -> dict[str, int]:
        return {
            "projected": self.projected,
            "inserted": self.inserted,
            "noops": self.noops,
            "failed": self.failed,
        }


def _value(source: str, raw: object) -> object:
    """A record field as a column value: sentinels and empty strings → ``None``."""
    if isinstance(raw, bytes):
        return raw.decode("ascii") or None
    value = int(raw)  # type: ignore[call-overload]
    return None if DEFINITION_UNDEFINED.get(source) == value else value


def _label(row: Row) -> str:
    return f"instrument {row['instrument_id']} ({row['raw_symbol']})"


def _decode(path: Path, reader: ITickFileReader) -> tuple[list[Row], int]:
    """Every record of the file as a column-keyed row. Blocking."""
    rows: list[Row] = []
    for batch in reader.open_file(path).iter_batches():
        for record in batch.records:
            rows.append(
                {
                    column: _value(source, record[source])
                    for source, column in TICK_DEFINITION_COLUMNS.items()
                }
            )
    return rows, len(rows)


def _differences(first: Row, second: Row) -> list[str]:
    return [column for column in _COMPARED if first[column] != second[column]]


def _place(rows: list[Row]) -> dict[Key, Row]:
    """Rows by key, each with a window; identical duplicates collapse."""
    placed: dict[Key, Row] = {}
    for row in rows:
        if row["activation_ns"] is None or row["expiration_ns"] is None:
            raise DefinitionRejected(
                f"{_label(row)} has no validity window (activation or expiration "
                "undefined); one is never invented"
            )
        key = (row["instrument_id"], row["activation_ns"])
        seen = placed.setdefault(key, row)
        if seen is not row and (changed := _differences(seen, row)):
            raise DefinitionRejected(
                f"{_label(row)} appears twice in one file with different "
                f"{', '.join(changed)}"
            )
    return placed


async def _stored(conn: Conn, instrument_ids: list[int]) -> dict[Key, Row]:
    cursor = await conn.execute(
        f"SELECT {', '.join(_INSERT_COLUMNS[:-1])} FROM tick_definition"
        " WHERE instrument_id = ANY(%s)",
        (instrument_ids,),
    )
    columns = _INSERT_COLUMNS[:-1]
    stored = [
        dict(zip(columns, values, strict=True)) for values in await cursor.fetchall()
    ]
    return {(row["instrument_id"], row["activation_ns"]): row for row in stored}


def _new_rows(placed: dict[Key, Row], stored: dict[Key, Row]) -> tuple[list[Row], int]:
    """The rows to insert and the count already stored identically."""
    new, noops = [], 0
    for key, row in placed.items():
        existing = stored.get(key)
        if existing is None:
            new.append(row)
        elif changed := _differences(existing, row):
            raise DefinitionRejected(
                f"{_label(row)} was re-sent with different {', '.join(changed)} "
                f"for the same window start {key[1]}"
            )
        else:
            noops += 1
    return new, noops


async def _overlap(conn: Conn, row: Row) -> str:
    cursor = await conn.execute(
        "SELECT activation_ns, expiration_ns FROM tick_definition"
        " WHERE instrument_id = %s AND int8range(activation_ns, expiration_ns, '[]')"
        " && int8range(%s, %s, '[]')",
        (row["instrument_id"], row["activation_ns"], row["expiration_ns"]),
    )
    windows = [f"[{a}, {e}]" for a, e in await cursor.fetchall()]
    return (
        f"{_label(row)} window [{row['activation_ns']}, {row['expiration_ns']}] "
        f"overlaps stored window {', '.join(windows) or '(none found)'}"
    )


async def _insert(conn: Conn, row: Row, unit_id: int) -> None:
    columns = ", ".join(_INSERT_COLUMNS)
    marks = ", ".join(["%s"] * len(_INSERT_COLUMNS))
    await conn.execute(
        f"INSERT INTO tick_definition ({columns}) VALUES ({marks})",
        [*(row[c] for c in _INSERT_COLUMNS[:-1]), unit_id],
    )


async def project_unit(
    run: TickRun, unit: UnitRow, reader: ITickFileReader
) -> tuple[int, int]:
    """Project one verified unit: ``(rows inserted, no-ops)``.

    Raises :class:`DefinitionRejected` when the unit cannot be stored; nothing
    is written then.
    """
    assert unit.file is not None, f"unit {unit.unit_id} has no file to project"
    path = run.archive_root / unit.file.path
    rows, decoded = await asyncio.to_thread(_decode, path, reader)
    placed = _place(rows)
    stored = await _stored(run.conn, sorted({key[0] for key in placed}))
    new, noops = _new_rows(placed, stored)
    current: Row | None = None
    try:
        async with run.conn.transaction():
            for current in new:
                await _insert(run.conn, current, unit.unit_id)
            await mark_ingested(run.conn, unit.unit_id, decoded, run.clock())
    except errors.ExclusionViolation as exc:
        assert current is not None  # only an insert can violate the exclusion
        raise DefinitionRejected(await _overlap(run.conn, current)) from exc
    return len(new), noops


async def project_definitions(
    run: TickRun, reader: ITickFileReader
) -> DefinitionsTally:
    """Project every verified definition unit, earliest day first."""
    tally = DefinitionsTally()
    for unit in await verified_definition_units(run.conn):
        try:
            inserted, noops = await project_unit(run, unit, reader)
        except DefinitionRejected as exc:
            await record_failure(
                run.conn,
                unit.unit_id,
                UnitState.VERIFIED,
                str(exc),
                run.clock(),
                deterministic=True,
            )
            tally.failed += 1
            continue
        tally.projected += 1
        tally.inserted += inserted
        tally.noops += noops
    return tally
