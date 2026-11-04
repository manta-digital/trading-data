"""``StatisticsProjector.load`` over a cursor stub: what it would COPY (slice 303).

The database side (COPY, stored count, ledger, delete) is in the integration
tests; here the cursor records rows so the projector's own logic is checked.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
from tick_support.statistics_records import (
    UNDEF_PRICE,
    UNDEF_QUANTITY_V1,
    UNDEF_QUANTITY_V3,
    UNDEF_TS_REF,
    V1_SAMPLE,
    ns_of,
    referenced,
    stat,
    statistics_records,
)

from manta_trading.data.tick.constants import TickSchema
from manta_trading.data.tick.ingest_checks import IngestCheck
from manta_trading.data.tick.ingest_records import Definitions, UnitCheckFailed
from manta_trading.data.tick.provider import RecordBatch
from manta_trading.data.tick.statistics_constants import (
    STATISTICS_COLUMNS,
    StatType,
)
from manta_trading.data.tick.statistics_projector import (
    COPY_TYPES,
    STATISTICS_PROJECTOR,
)
from manta_trading.data.tick.statistics_session import ReferenceCalendar
from manta_trading.market.schema.seed_cme_hours import CME_DATASET_START

UNIT_DAY = date(2024, 12, 4)
SESSION = date(2024, 12, 3)
INSTRUMENT = 146_945
COLUMN = {name: index for index, name in enumerate(STATISTICS_COLUMNS)}
OPEN, CLOSE = (
    int(datetime(2024, 1, 1, tzinfo=UTC).timestamp()) * 1_000_000_000,
    int(datetime(2025, 6, 1, tzinfo=UTC).timestamp()) * 1_000_000_000,
)
RECV = ns_of(UNIT_DAY, 5)


class _Copy:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self.rows = rows
        self.types: list[str] = []

    def set_types(self, types: list[str]) -> None:
        self.types = types

    def write_row(self, row: tuple[Any, ...]) -> None:
        self.rows.append(row)


class _Cursor:
    """Stands in for a psycopg cursor: ``copy`` captures every written row."""

    def __init__(self) -> None:
        self.rows: list[tuple[Any, ...]] = []
        self.copy_types: list[str] = []

    @contextmanager
    def copy(self, _sql: str) -> Iterator[_Copy]:
        writer = _Copy(self.rows)
        yield writer
        self.copy_types = writer.types


class _Reader:
    """An ``ITickFileReader`` whose file yields the given batches."""

    def __init__(self, *batches: np.ndarray[Any, Any]) -> None:
        self._batches = batches

    def open_file(self, _path: Path) -> Any:
        batches = [
            RecordBatch(TickSchema.STATISTICS, records, len(records))
            for records in self._batches
        ]
        return SimpleNamespace(iter_batches=lambda: iter(batches))


def _plan(calendar: ReferenceCalendar | None = None) -> Any:
    calendar = calendar or ReferenceCalendar("CME_EQUITY", frozenset({SESSION}))
    return SimpleNamespace(
        unit=SimpleNamespace(unit_date=UNIT_DAY, unit_id=9),
        product="ES",
        definitions=Definitions.of([(INSTRUMENT, OPEN, CLOSE)]),
        reference_calendar=calendar,
    )


def _load(*batches: np.ndarray[Any, Any], plan: Any = None) -> tuple[Any, _Cursor]:
    cursor = _Cursor()
    loaded = STATISTICS_PROJECTOR.load(
        cursor,  # type: ignore[arg-type]
        plan or _plan(),
        Path("unused"),
        _Reader(*batches),
        None,
    )
    return loaded, cursor


def test_copy_types_cover_every_column() -> None:
    assert len(COPY_TYPES) == len(STATISTICS_COLUMNS)


def test_undefined_values_are_null_never_sentinels() -> None:
    records = statistics_records(
        [
            stat(
                INSTRUMENT,
                7,
                ts_recv=RECV,
                price=UNDEF_PRICE,
                quantity=UNDEF_QUANTITY_V3,
            ),
            referenced(INSTRUMENT, StatType.SETTLEMENT_PRICE, SESSION, ts_recv=RECV),
        ]
    )
    _, cursor = _load(records)
    sessionless, settled = cursor.rows
    assert sessionless[COLUMN["ts_ref"]] is None
    assert sessionless[COLUMN["price"]] is None
    assert sessionless[COLUMN["quantity"]] is None
    assert sessionless[COLUMN["session_date"]] is None
    assert sessionless[COLUMN["calendar_id"]] is None
    assert settled[COLUMN["ts_ref"]] == ns_of(SESSION, 16)
    assert settled[COLUMN["price"]] == 100_000_000_000
    assert settled[COLUMN["quantity"]] == 5
    assert (settled[COLUMN["calendar_id"]], settled[COLUMN["session_date"]]) == (
        "CME_EQUITY",
        SESSION,
    )
    assert settled[COLUMN["unit_id"]] == 9
    assert UNDEF_TS_REF not in sessionless


def test_the_v1_quantity_sentinel_is_null_too() -> None:
    records = statistics_records(
        [stat(INSTRUMENT, 7, ts_recv=RECV, quantity=UNDEF_QUANTITY_V1)],
        sample=V1_SAMPLE,
    )
    _, cursor = _load(records)
    assert cursor.rows[0][COLUMN["quantity"]] is None


def test_a_repeated_key_gets_the_next_ordinal_across_a_batch_boundary() -> None:
    same = referenced(INSTRUMENT, StatType.OPEN_INTEREST, SESSION, ts_recv=RECV)
    first = statistics_records([same, same])
    second = statistics_records([same])
    _, cursor = _load(first, second)
    assert [row[COLUMN["record_ordinal"]] for row in cursor.rows] == [0, 1, 2]


def test_stat_type_is_part_of_the_ordinal_key() -> None:
    records = statistics_records(
        [
            referenced(INSTRUMENT, StatType.OPEN_INTEREST, SESSION, ts_recv=RECV),
            referenced(INSTRUMENT, StatType.CLEARED_VOLUME, SESSION, ts_recv=RECV),
        ]
    )
    _, cursor = _load(records)
    assert [row[COLUMN["record_ordinal"]] for row in cursor.rows] == [0, 0]


def test_the_ledger_holds_referenced_records_only() -> None:
    records = statistics_records(
        [
            referenced(INSTRUMENT, StatType.OPEN_INTEREST, SESSION, ts_recv=RECV),
            referenced(
                INSTRUMENT,
                StatType.OPEN_INTEREST,
                SESSION,
                ts_recv=RECV + 5,
                sequence=2,
            ),
            referenced(INSTRUMENT, StatType.CLEARED_VOLUME, SESSION, ts_recv=RECV + 9),
            stat(INSTRUMENT, 7, ts_recv=RECV + 1),
            stat(INSTRUMENT, 5, ts_recv=RECV + 2, ts_ref=ns_of(SESSION, 16)),
        ]
    )
    loaded, _ = _load(records)
    rows = {(r.stat_type, r.session_date): r for r in loaded.bookkeeping}
    assert set(rows) == {(9, SESSION), (6, SESSION)}
    interest = rows[(9, SESSION)]
    assert (interest.record_count, interest.first_event_ns, interest.last_event_ns) == (
        2,
        RECV,
        RECV + 5,
    )
    assert loaded.decoded == 5
    assert (loaded.first_ns, loaded.last_ns) == (RECV, RECV + 9)


def test_a_reference_before_the_dataset_is_stored_sessionless_and_noted() -> None:
    before = date(CME_DATASET_START.year, 1, 4)
    assert before < CME_DATASET_START
    records = statistics_records(
        [referenced(INSTRUMENT, StatType.SETTLEMENT_PRICE, before, ts_recv=RECV)]
    )
    loaded, cursor = _load(records)
    assert cursor.rows[0][COLUMN["session_date"]] is None
    assert cursor.rows[0][COLUMN["ts_ref"]] == ns_of(before, 16)
    assert loaded.bookkeeping == []
    assert loaded.notes == [f"1 records reference dates before {CME_DATASET_START}"]


@pytest.mark.parametrize(
    ("row", "check", "text"),
    [
        (
            stat(INSTRUMENT, 3, ts_recv=RECV),
            IngestCheck.STAT_REFERENCE,
            "ts_ref undefined: undefined ts_ref",
        ),
        (
            referenced(
                INSTRUMENT, StatType.CLEARED_VOLUME, date(2024, 12, 1), ts_recv=RECV
            ),
            IngestCheck.STAT_REFERENCE,
            "not a session",
        ),
        (
            referenced(
                INSTRUMENT, StatType.CLEARED_VOLUME, date(2024, 12, 5), ts_recv=RECV
            ),
            IngestCheck.STAT_REFERENCE,
            "after the unit",
        ),
        (
            referenced(
                INSTRUMENT,
                StatType.CLEARED_VOLUME,
                SESSION,
                ts_recv=ns_of(date(2024, 12, 5), 1),
            ),
            IngestCheck.UNIT_DAY,
            "records outside 2024-12-04",
        ),
        (
            referenced(INSTRUMENT + 1, StatType.CLEARED_VOLUME, SESSION, ts_recv=RECV),
            IngestCheck.RESOLUTION,
            f"first instrument_id {INSTRUMENT + 1}",
        ),
    ],
)
def test_a_failing_record_fails_its_check(
    row: dict[str, Any], check: IngestCheck, text: str
) -> None:
    with pytest.raises(UnitCheckFailed) as raised:
        _load(statistics_records([row]))
    assert raised.value.check is check
    assert text in raised.value.reason


def test_a_failure_names_the_first_record_with_that_reference() -> None:
    rows: Sequence[dict[str, Any]] = [
        referenced(INSTRUMENT, StatType.OPEN_INTEREST, SESSION, ts_recv=RECV),
        referenced(INSTRUMENT, StatType.OPEN_INTEREST, date(2024, 12, 1), ts_recv=RECV),
        referenced(
            INSTRUMENT,
            StatType.OPEN_INTEREST,
            date(2024, 12, 1),
            ts_recv=RECV,
            sequence=2,
        ),
    ]
    with pytest.raises(UnitCheckFailed, match="stat_type 9 ts_ref 2024-12-01T16:00"):
        _load(statistics_records(rows))


def test_a_unit_plan_without_its_calendar_is_a_defect() -> None:
    plan = _plan()
    plan.reference_calendar = None
    with pytest.raises(AssertionError, match="plan carries its calendar"):
        _load(statistics_records([stat(INSTRUMENT, 7, ts_recv=RECV)]), plan=plan)
