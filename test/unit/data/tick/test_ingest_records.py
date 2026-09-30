"""Record processing on the real DBN slices (slice 225, TD6; FR8 in part).

Failure shapes a real file cannot produce are built by editing a real batch.
Sessions are CME_EQUITY's regular 17:00 → 16:00 America/Chicago, built by hand
(the database-backed calendar is exercised by the worker's integration tests).
"""

from __future__ import annotations

from collections import Counter
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import databento
import numpy as np
import pytest
from tick_support.tier_units import TRADES_DAY, real_file

from manta_trading.data.base.session_index import Session, SessionIndex
from manta_trading.data.tick import constants
from manta_trading.data.tick.constants import TickSchema
from manta_trading.data.tick.databento.dbn_file import DbnFileReader
from manta_trading.data.tick.ingest_checks import IngestCheck
from manta_trading.data.tick.ingest_records import (
    Definitions,
    LedgerAccumulator,
    OrdinalCarry,
    SessionFrame,
    UnitCheckFailed,
    locate_sessions,
    resolve,
    utc_ns,
)

CHICAGO = ZoneInfo("America/Chicago")
READER = DbnFileReader()


def _session(day: date) -> Session:
    opened = datetime.combine(day - timedelta(days=1), time(17), CHICAGO)
    closed = datetime.combine(day, time(16), CHICAGO)
    return Session("CME_EQUITY", day, opened.astimezone(UTC), closed.astimezone(UTC))


SESSIONS = [_session(TRADES_DAY), _session(TRADES_DAY + timedelta(days=1))]
FRAME = SessionFrame(
    SessionIndex(SESSIONS), SESSIONS[0].open_utc, SESSIONS[-1].close_utc, CHICAGO
)


@pytest.fixture(scope="module")
def records() -> Any:
    path = real_file(TRADES_DAY, TickSchema.TRADES)
    return np.concatenate([b.records for b in READER.open_file(path).iter_batches()])


@pytest.fixture(scope="module")
def definitions() -> Definitions:
    path = real_file(TRADES_DAY, TickSchema.DEFINITION)
    table = databento.DBNStore.from_file(path).to_ndarray()
    es = table[table["asset"] == b"ES"]
    return Definitions.of(
        list(
            zip(
                es["instrument_id"].tolist(),
                es["activation"].tolist(),
                es["expiration"].tolist(),
                strict=True,
            )
        )
    )


def _failure(check: IngestCheck, call: Any, *args: Any) -> str:
    with pytest.raises(UnitCheckFailed) as info:
        call(*args)
    assert info.value.check is check
    return info.value.reason


# -- resolution (2.3) ------------------------------------------------------------


def test_the_real_slice_resolves_fully(records: Any, definitions: Definitions) -> None:
    resolve(definitions, records["instrument_id"], records["ts_event"], "ES")


def test_an_unknown_id_is_reported_with_time_and_count(
    records: Any, definitions: Definitions
) -> None:
    ids = records["instrument_id"].astype(np.int64)
    ids[[10, 20]] = 999
    reason = _failure(
        IngestCheck.RESOLUTION, resolve, definitions, ids, records["ts_event"], "ES"
    )
    first = datetime.fromtimestamp(int(records["ts_event"][10]) / 1e9, UTC)
    assert "2 records resolve to no ES definition" in reason
    assert f"first instrument_id 999 at {first.isoformat()}" in reason


def test_a_record_outside_its_window_fails(
    records: Any, definitions: Definitions
) -> None:
    ts = records["ts_event"].astype(np.int64)
    busiest = Counter(records["instrument_id"].tolist()).most_common(1)[0][0]
    windows = definitions.instrument_id == busiest
    assert windows.sum() == 1
    edited = int(np.flatnonzero(records["instrument_id"] == busiest)[5])
    ts[edited] = int(definitions.activation_ns[windows][0]) - 1
    reason = _failure(
        IngestCheck.RESOLUTION,
        resolve,
        definitions,
        records["instrument_id"],
        ts,
        "ES",
    )
    assert (
        f"1 records resolve to no ES definition; first instrument_id {busiest}"
        in reason
    )


def test_an_id_with_two_windows_resolves_each_record_to_its_window() -> None:
    two = Definitions.of([(5, 200, 300), (5, 0, 100), (7, 0, 1_000)])
    ids = np.array([5, 7, 5], dtype=np.int64)
    resolve(two, ids, np.array([250, 150, 50], dtype=np.int64), "ES")
    reason = _failure(
        IngestCheck.RESOLUTION,
        resolve,
        two,
        ids,
        np.array([250, 150, 150], dtype=np.int64),
        "ES",
    )
    assert "1 records" in reason and "instrument_id 5" in reason


# -- sessions (2.5) --------------------------------------------------------------


def test_real_records_land_in_the_session_that_holds_them(records: Any) -> None:
    positions = locate_sessions(FRAME, records["ts_event"])
    hours = (records["ts_event"].astype(np.int64) // 3_600_000_000_000) % 24
    # 00:00 UTC is inside the session that opened at 22:00 UTC the day before.
    assert set(positions[hours < 21].tolist()) == {0}
    assert set(positions[hours >= 22].tolist()) == {1}
    assert (hours == 0).any() and (hours >= 22).any()


def test_a_record_in_the_daily_break_is_in_no_session(records: Any) -> None:
    ts = records["ts_event"].astype(np.int64)
    ts[3] = utc_ns(datetime(2024, 9, 3, 21, 30, tzinfo=UTC))
    reason = _failure(IngestCheck.SESSION_BOUNDARY, locate_sessions, FRAME, ts)
    assert "1 records in no session" in reason
    assert "2024-09-03T16:30:00-05:00 America/Chicago" in reason
    assert "between session 2024-09-03" in reason
    assert "and session 2024-09-04" in reason


def test_a_record_past_the_populated_span_is_outside_not_a_break(
    records: Any,
) -> None:
    ts = records["ts_event"].astype(np.int64)
    ts[3] = utc_ns(SESSIONS[-1].close_utc) + 1
    reason = _failure(IngestCheck.SESSION_BOUNDARY, locate_sessions, FRAME, ts)
    assert "outside the populated calendar range" in reason


# -- ordinals (2.7) --------------------------------------------------------------


def _brute_force(records: Any) -> list[int]:
    seen: Counter[tuple[int, int, int]] = Counter()
    out = []
    for key in zip(
        records["instrument_id"].tolist(),
        records["ts_event"].tolist(),
        records["sequence"].tolist(),
        strict=True,
    ):
        out.append(seen[key])
        seen[key] += 1
    return out


def _ordinals(carry: OrdinalCarry, part: Any) -> list[int]:
    return carry.ordinals(
        part["instrument_id"], part["ts_event"], part["sequence"]
    ).tolist()


def test_a_non_adjacent_repeat_gets_zero_then_one() -> None:
    ids = np.array([1, 2, 1, 1], dtype=np.int64)
    ts = np.array([10, 10, 10, 11], dtype=np.int64)
    seq = np.array([5, 5, 5, 5], dtype=np.int64)
    assert OrdinalCarry().ordinals(ids, ts, seq).tolist() == [0, 0, 1, 0]


def test_a_repeat_straddling_a_batch_boundary_carries(records: Any) -> None:
    ordinals = _brute_force(records)
    split = ordinals.index(1)  # the second record of some triple: cut before it
    carry = OrdinalCarry()
    got = _ordinals(carry, records[:split]) + _ordinals(carry, records[split:])
    assert got[split] == 1
    assert got == ordinals


def test_ordinals_over_the_real_file_in_many_batches_match_brute_force(
    records: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(constants, "TICK_DECODE_BATCH_BYTES", 48 * 97)
    carry, got = OrdinalCarry(), []
    batches = list(
        READER.open_file(real_file(TRADES_DAY, TickSchema.TRADES)).iter_batches()
    )
    assert len(batches) > 30
    for batch in batches:
        got += _ordinals(carry, batch.records)
    assert got == _brute_force(records)
    assert max(got) >= 1


# -- ledger (2.9) ----------------------------------------------------------------


def _ledger(records: Any, definitions: Definitions) -> list[Any]:
    accumulator = LedgerAccumulator()
    for part in np.array_split(records, 4):
        positions = locate_sessions(FRAME, part["ts_event"])
        accumulator.add(
            part["instrument_id"], positions, part["size"], part["ts_event"]
        )
    return accumulator.rows(definitions, FRAME, "CME_EQUITY")


def test_ledger_counts_sum_to_the_decoded_count(
    records: Any, definitions: Definitions
) -> None:
    rows = _ledger(records, definitions)
    assert sum(row.record_count for row in rows) == len(records)
    assert sum(row.volume for row in rows) == int(records["size"].sum())


def test_ledger_has_zero_rows_for_valid_instruments_without_records(
    records: Any, definitions: Definitions
) -> None:
    rows = _ledger(records, definitions)
    empty = [row for row in rows if row.record_count == 0]
    assert empty
    assert all(r.first_event_ns is None and r.last_event_ns is None for r in empty)
    full = [row for row in rows if row.record_count]
    assert all(r.first_event_ns <= r.last_event_ns for r in full)  # type: ignore[operator]


def test_ledger_instrument_set_is_the_definitions_valid_in_each_session(
    records: Any, definitions: Definitions
) -> None:
    rows = _ledger(records, definitions)
    for session in SESSIONS:
        opened, closed = utc_ns(session.open_utc), utc_ns(session.close_utc)
        meets = (definitions.activation_ns <= closed) & (
            definitions.expiration_ns >= opened
        )
        expected = set(definitions.instrument_id[meets].tolist())
        got = {r.instrument_id for r in rows if r.session_date == session.session_date}
        assert got == expected
        assert len(expected) > 30
