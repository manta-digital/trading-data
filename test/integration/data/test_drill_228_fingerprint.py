"""The restore drill's ``tick_trade`` fingerprint on real tick databases (228 TD3).

Two migrated tick databases stand in for "restored" and "rebuilt": equal rows
give equal fingerprints even when ``unit_id`` differs; one changed price or
one missing row is named as a differing group.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import psycopg
import pytest

SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import drill_228_fingerprint as fp  # noqa: E402
from tick_support.rows import TBBO_TRADE, insert_trade  # noqa: E402

#: A second day's trade: one UTC day after the default.
_NEXT_DAY = TBBO_TRADE["ts_event"] + 86_400 * 1_000_000_000


def _seed(conn: psycopg.Connection[Any], unit_id: int) -> None:
    insert_trade(conn, unit_id)
    insert_trade(conn, unit_id, sequence=2, ts_event=TBBO_TRADE["ts_event"] + 5)
    insert_trade(conn, unit_id, ts_event=_NEXT_DAY, ts_recv=_NEXT_DAY + 1)


@pytest.fixture
def pair(
    migrated_tick_db: str, second_migrated_tick_db: str
) -> Iterator[tuple[psycopg.Connection[Any], psycopg.Connection[Any]]]:
    with (
        psycopg.connect(migrated_tick_db, autocommit=True) as first,
        psycopg.connect(second_migrated_tick_db, autocommit=True) as second,
    ):
        yield first, second


def test_identical_rows_give_equal_fingerprints(pair: Any) -> None:
    first, second = pair
    _seed(first, 1)
    _seed(second, 1)
    a, b = fp.fingerprint(first), fp.fingerprint(second)
    assert len(a) == 2
    assert fp.diff_fingerprints(a, b) == []


def test_only_unit_id_differing_gives_equal_fingerprints(pair: Any) -> None:
    first, second = pair
    _seed(first, 1)
    _seed(second, 77)
    assert fp.diff_fingerprints(fp.fingerprint(first), fp.fingerprint(second)) == []


def test_one_changed_price_is_named(pair: Any) -> None:
    first, second = pair
    _seed(first, 1)
    _seed(second, 1)
    second.execute("UPDATE tick_trade SET price = price + 1 WHERE sequence = 2")
    problems = fp.diff_fingerprints(fp.fingerprint(first), fp.fingerprint(second))
    assert len(problems) == 1
    assert "2024-09-01" in problems[0] and "md5" in problems[0]


def test_one_missing_row_is_named(pair: Any) -> None:
    first, second = pair
    _seed(first, 1)
    _seed(second, 1)
    second.execute("DELETE FROM tick_trade WHERE ts_event = %s", (_NEXT_DAY,))
    problems = fp.diff_fingerprints(fp.fingerprint(first), fp.fingerprint(second))
    assert problems == ["instrument 42035063 day 2024-09-02: missing (expected 1 rows)"]
