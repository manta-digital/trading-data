"""Rendered eligible SQL equals the Python mirror on every mask (slice 171).

Throwaway database (``ephemeral_db``); a plain table with the
``minute_ohlcv`` price types is enough, no migrations.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import psycopg
import pytest

from manta_trading.constants import OffTape
from manta_trading.data.quality.off_tape import eligible_bar
from manta_trading.market.schema import eligible_sql

#: One bar with four distinct prices, so every clamp is visible.
OPEN, HIGH, LOW, CLOSE = (
    Decimal("101.5"),
    Decimal("110"),
    Decimal("90"),
    Decimal("99.25"),
)
CASES: list[int | None] = [
    None,
    0,
    *(int(bit) for bit in (OffTape.OPEN, OffTape.HIGH, OffTape.LOW, OffTape.CLOSE)),
    int(OffTape.HIGH | OffTape.LOW),
    int(OffTape.OPEN | OffTape.CLOSE),
    int(OffTape.ALL),
]


@pytest.fixture()
def conn(ephemeral_db: str) -> Any:
    with psycopg.connect(ephemeral_db) as connection:
        connection.execute(
            "CREATE TABLE bars (id int, open numeric(12,4), high numeric(12,4),"
            " low numeric(12,4), close numeric(12,4), off_tape smallint)"
        )
        for index, mask in enumerate(CASES):
            connection.execute(
                "INSERT INTO bars VALUES (%s, %s, %s, %s, %s, %s)",
                (index, OPEN, HIGH, LOW, CLOSE, mask),
            )
        yield connection


def _select(
    conn: psycopg.Connection[Any], where: str = "TRUE"
) -> dict[int, tuple[float, ...]]:
    columns = ", ".join(
        eligible_sql.eligible_value(name, "b")
        for name in ("open", "high", "low", "close")
    )
    rows = conn.execute(
        f"SELECT b.id, {columns} FROM bars b WHERE {where} ORDER BY b.id"
    ).fetchall()
    return {row[0]: tuple(float(value) for value in row[1:]) for row in rows}


def test_sql_equals_python_mirror(conn: psycopg.Connection[Any]) -> None:
    eligible = _select(conn, eligible_sql.has_eligible_price("b"))
    for index, mask in enumerate(CASES):
        expected = eligible_bar(
            float(OPEN), float(HIGH), float(LOW), float(CLOSE), mask
        )
        assert eligible.get(index) == expected, f"mask={mask}"


def test_all_marked_excluded_by_predicate(conn: psycopg.Connection[Any]) -> None:
    eligible = _select(conn, eligible_sql.has_eligible_price("b"))
    assert CASES.index(int(OffTape.ALL)) not in eligible
    assert len(eligible) == len(CASES) - 1


def test_null_reads_as_clean(conn: psycopg.Connection[Any]) -> None:
    raw = (float(OPEN), float(HIGH), float(LOW), float(CLOSE))
    eligible = _select(conn)
    assert eligible[CASES.index(None)] == raw == eligible[CASES.index(0)]


def test_marked_high_falls_to_highest_eligible(conn: psycopg.Connection[Any]) -> None:
    eligible = _select(conn)
    assert eligible[CASES.index(int(OffTape.HIGH))][1] == float(OPEN)
