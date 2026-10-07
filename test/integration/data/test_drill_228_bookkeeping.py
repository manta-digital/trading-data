"""The restore drill's bookkeeping comparison on real tick databases (228 TD4).

The first migrated database plays "restored", the second "rebuilt". Both get
the same bookkeeping by natural key; the rebuilt side's identity ids are
shifted first, so every case also proves matching never uses them.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import psycopg
import pytest

SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import drill_228_bookkeeping as bk  # noqa: E402
from tick_support.rows import (  # noqa: E402
    FILE_COLUMNS,
    insert_dataset_edge,
    insert_day_condition,
    insert_definition,
    insert_ledger_row,
    insert_request,
    insert_unit,
)

from manta_trading.data.quality.fetch_status import FetchStatus  # noqa: E402
from manta_trading.data.tick.constants import UnitState  # noqa: E402

Conn = psycopg.Connection[Any]
JOB = "GLBX-20240930-AAAA"
DAY_1, DAY_2 = date(2024, 9, 3), date(2024, 9, 4)
_COST = Decimal("0.0041")
_AT = datetime(2024, 9, 30, tzinfo=UTC)


def _ingested(conn: Conn, request_id: int, day: date) -> int:
    return insert_unit(
        conn, request_id, unit_date=day, state=UnitState.INGESTED.value, **FILE_COLUMNS
    )


def seed(conn: Conn, *, shift_ids: bool = False) -> dict[str, int]:
    """One adopted job with two ingested days, a definition, two ledger rows,
    the dataset edge and one day condition. ``shift_ids`` burns identity
    values first so the same rows get different ids."""
    if shift_ids:
        burned = insert_request(conn)
        insert_unit(conn, burned)
        conn.execute("DELETE FROM tick_archive_unit")
        conn.execute("DELETE FROM tick_request")
    request = insert_request(
        conn,
        provider_job_id=JOB,
        is_adopted=True,
        estimated_cost_usd=_COST,
        actual_cost_usd=_COST,
        requested_at=_AT,
        committed_at=_AT,
    )
    unit_1, unit_2 = _ingested(conn, request, DAY_1), _ingested(conn, request, DAY_2)
    insert_definition(conn, unit_1)
    insert_ledger_row(conn, unit_1, session_date=DAY_1)
    insert_ledger_row(conn, unit_2, session_date=DAY_2)
    insert_dataset_edge(conn)
    insert_day_condition(conn)
    return {"request": request, "unit_1": unit_1, "unit_2": unit_2}


@pytest.fixture
def pair(
    migrated_tick_db: str, second_migrated_tick_db: str
) -> Iterator[tuple[Conn, Conn]]:
    with (
        psycopg.connect(migrated_tick_db, autocommit=True) as restored,
        psycopg.connect(second_migrated_tick_db, autocommit=True) as rebuilt,
    ):
        yield restored, rebuilt


@pytest.fixture
def seeded(
    pair: tuple[Conn, Conn],
) -> tuple[Conn, Conn, dict[str, int], dict[str, int]]:
    restored, rebuilt = pair
    return restored, rebuilt, seed(restored), seed(rebuilt, shift_ids=True)


def compare(restored: Conn, rebuilt: Conn) -> bk.BookkeepingResult:
    return bk.compare_bookkeeping(restored, rebuilt)


# --- 3.3: columns and renumbered ids -------------------------------------------


def test_renumbered_ids_with_equal_natural_keys_pass(seeded: Any) -> None:
    restored, rebuilt, ids_a, ids_b = seeded
    assert ids_a["unit_1"] != ids_b["unit_1"]
    result = compare(restored, rebuilt)
    assert result.failures == []
    assert sum(result.allowed_differences.values()) == 0


def test_an_allowed_column_differing_passes_and_is_counted(seeded: Any) -> None:
    restored, rebuilt, _, _ = seeded
    rebuilt.execute("UPDATE tick_request SET requested_at = now(), is_adopted = FALSE")
    rebuilt.execute("UPDATE tick_archive_unit SET attempt_count = 3")
    rebuilt.execute("UPDATE tick_day_condition SET observed_at = now()")
    result = compare(restored, rebuilt)
    assert result.failures == []
    assert result.allowed_differences[(bk.REQUEST, "requested_at")] == 1
    assert result.allowed_differences[(bk.UNIT, "attempt_count")] == 2
    assert result.allowed_differences[(bk.CONDITION, "observed_at")] == 1


@pytest.mark.parametrize(
    ("statement", "column"),
    [
        ("UPDATE tick_request SET actual_cost_usd = 1", "actual_cost_usd"),
        ("UPDATE tick_request SET committed_at = now()", "committed_at"),
        ("UPDATE tick_archive_unit SET file_sha256 = repeat('1', 64)", "file_sha256"),
        ("UPDATE tick_ingest_ledger SET volume = 99", "volume"),
        ("UPDATE tick_definition SET raw_symbol = 'ESH5'", "raw_symbol"),
    ],
)
def test_any_other_column_differing_fails(
    seeded: Any, statement: str, column: str
) -> None:
    restored, rebuilt, _, _ = seeded
    rebuilt.execute(statement)
    failures = compare(restored, rebuilt).failures
    assert failures and all(f"column {column}:" in f for f in failures)


def test_a_ledger_row_moved_to_another_unit_fails(seeded: Any) -> None:
    restored, rebuilt, _, ids_b = seeded
    rebuilt.execute(
        "UPDATE tick_ingest_ledger SET unit_id = %s WHERE unit_id = %s",
        (ids_b["unit_2"], ids_b["unit_1"]),
    )
    failures = compare(restored, rebuilt).failures
    assert any(
        "tick_ingest_ledger" in f and "missing from the rebuild" in f for f in failures
    )
    assert any(
        "tick_ingest_ledger" in f and "only in the rebuild" in f for f in failures
    )


def test_a_definition_on_another_unit_fails(seeded: Any) -> None:
    restored, rebuilt, _, ids_b = seeded
    rebuilt.execute("UPDATE tick_definition SET unit_id = %s", (ids_b["unit_2"],))
    failures = compare(restored, rebuilt).failures
    assert (
        len(failures) == 1
        and "tick_definition" in failures[0]
        and "unit_id" in failures[0]
    )


# --- 3.5: missing-row rules and condition coverage -------------------------------


def test_a_missing_row_whose_unit_has_a_file_fails(seeded: Any) -> None:
    restored, rebuilt, _, ids_b = seeded
    rebuilt.execute(
        "DELETE FROM tick_ingest_ledger WHERE unit_id = %s", (ids_b["unit_2"],)
    )
    failures = compare(restored, rebuilt).failures
    assert len(failures) == 1 and "missing from the rebuild" in failures[0]


def test_a_jobless_request_missing_from_the_rebuild_passes_and_is_counted(
    seeded: Any,
) -> None:
    restored, rebuilt, _, _ = seeded
    jobless = insert_request(restored)
    insert_unit(restored, jobless)
    result = compare(restored, rebuilt)
    assert result.failures == []
    assert result.allowed_missing_requests == 1
    assert result.allowed_missing[(bk.UNIT, bk.MissingRule.REQUEST_WITHOUT_JOB)] == 1


def test_a_rebuilt_request_without_a_job_id_fails(seeded: Any) -> None:
    restored, rebuilt, _, _ = seeded
    insert_request(rebuilt)
    failures = compare(restored, rebuilt).failures
    assert len(failures) == 1 and "no provider job id" in failures[0]


def test_a_restored_condition_absent_from_the_rebuild_fails(seeded: Any) -> None:
    restored, rebuilt, _, _ = seeded
    rebuilt.execute("DELETE FROM tick_day_condition")
    failures = compare(restored, rebuilt).failures
    assert len(failures) == 1 and "tick_day_condition" in failures[0]


def test_an_extra_reobserved_condition_in_the_rebuild_passes(seeded: Any) -> None:
    restored, rebuilt, _, _ = seeded
    insert_day_condition(rebuilt, condition_date=DAY_2)
    assert compare(restored, rebuilt).failures == []


def test_a_missing_no_file_unit_passes(seeded: Any) -> None:
    restored, rebuilt, ids_a, _ = seeded
    insert_unit(
        restored,
        ids_a["request"],
        unit_date=date(2024, 9, 5),
        state=UnitState.DELIVERED.value,
        fetch_status=FetchStatus.RETRY_EXHAUSTED.value,
        failure_reason="expired",
    )
    result = compare(restored, rebuilt)
    assert result.failures == []
    assert result.allowed_missing[(bk.UNIT, bk.MissingRule.UNIT_WITHOUT_FILE)] == 1


def test_a_paid_job_with_no_files_missing_from_the_rebuild_passes(seeded: Any) -> None:
    restored, rebuilt, _, _ = seeded
    expired = insert_request(restored, provider_job_id="GLBX-20240930-EXPD")
    insert_unit(restored, expired, state=UnitState.DELIVERED.value)
    result = compare(restored, rebuilt)
    assert result.failures == []
    assert result.allowed_missing[(bk.REQUEST, bk.MissingRule.JOB_WITHOUT_FILES)] == 1
    assert result.allowed_missing[(bk.UNIT, bk.MissingRule.JOB_WITHOUT_FILES)] == 1


def test_a_file_unit_whose_state_differs_fails(seeded: Any) -> None:
    restored, rebuilt, _, ids_b = seeded
    rebuilt.execute(
        "UPDATE tick_archive_unit SET state = %s WHERE unit_id = %s",
        (UnitState.VERIFIED.value, ids_b["unit_2"]),
    )
    failures = compare(restored, rebuilt).failures
    assert len(failures) == 1 and "column state:" in failures[0]


# --- 3.7: table_md5 ------------------------------------------------------------------


@pytest.mark.parametrize("table", bk.BOOKKEEPING_TABLES)
def test_table_md5_equal_for_identical_tables(pair: Any, table: str) -> None:
    restored, rebuilt = pair
    seed(restored)
    seed(rebuilt)
    assert bk.table_md5(restored, table) == bk.table_md5(rebuilt, table) is not None


def test_table_md5_differs_after_one_changed_value(pair: Any) -> None:
    restored, rebuilt = pair
    seed(restored)
    seed(rebuilt)
    rebuilt.execute(
        "UPDATE tick_ingest_ledger SET volume = 99 WHERE session_date = %s", (DAY_2,)
    )
    assert bk.table_md5(restored, bk.LEDGER) != bk.table_md5(rebuilt, bk.LEDGER)


def test_table_md5_differs_after_one_missing_row(pair: Any) -> None:
    restored, rebuilt = pair
    seed(restored)
    seed(rebuilt)
    rebuilt.execute("DELETE FROM tick_ingest_ledger WHERE session_date = %s", (DAY_2,))
    assert bk.table_md5(restored, bk.LEDGER) != bk.table_md5(rebuilt, bk.LEDGER)
