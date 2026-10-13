"""The futures response models (slice 231, TD5).

Catalog models are compared with ``roll_payload``'s dictionaries over sample
dataclasses: equal JSON means equal keys at every level, in order, and equal
values, so the CLI's ``--json`` and the API cannot drift.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from typing import Any

import pytest

from manta_trading.api_server.models.futures import (
    FuturesBarRecord,
    FuturesBarsResponse,
    FuturesTicksResponse,
    TickRecord,
)
from manta_trading.api_server.models.futures_catalog import (
    ActiveLineRecord,
    ContractAnswerRecord,
    FuturesScheduleResponse,
    ScheduleSegmentRecord,
)
from manta_trading.data.tick.constants import UNDEF_INT64
from manta_trading.data.tick.get_plan import (
    DisplaySegment,
    GetPlan,
    Incomplete,
    ProductTarget,
)
from manta_trading.data.tick.roll_payload import (
    _segment_dict,
    active_line_dict,
    contract_dict,
    schedule_dict,
    unresolved_dict,
)
from manta_trading.data.tick.roll_rules import DEFAULT_ROLL_RULE, RollRule
from manta_trading.data.tick.roll_types import (
    ActiveContract,
    ActiveLine,
    NextRoll,
    RollSchedule,
    Segment,
    Unresolved,
)
from manta_trading.data.tick.tick_reads import BAR_COLUMNS, TICK_COLUMNS
from manta_trading.data.tick.tick_status import TickSessionStatus

#: 2024-09-18 14:00 UTC plus 123456789 ns: past 2^53, as every 2024 stamp is.
TS_2024 = 1_726_668_000_123_456_789
ESU4 = ActiveContract(
    session_date=date(2024, 9, 17),
    instrument_id=118,
    raw_symbol="ESU4",
    expiration=datetime(2024, 9, 20, 13, 30, tzinfo=UTC),
    basis_session=date(2024, 9, 16),
    definitions_as_of=date(2024, 12, 31),
    definition_unit_id=7,
    basis_unit_ids=(11, 12),
)
ESZ4 = ActiveContract(
    session_date=date(2024, 9, 19),
    instrument_id=183748,
    raw_symbol="ESZ4",
    expiration=datetime(2024, 12, 20, 14, 30, tzinfo=UTC),
    basis_session=date(2024, 9, 18),
    definitions_as_of=date(2024, 12, 31),
    definition_unit_id=7,
    basis_unit_ids=(13,),
)
GAP = Unresolved(date(2024, 9, 18), "no volume held for the lag session")
SCHEDULE = RollSchedule("ES", DEFAULT_ROLL_RULE, (ESU4, GAP, ESZ4))


def _json(model: Any) -> Any:
    return json.loads(model.model_dump_json())


def test_row_fields_are_the_read_columns_in_order() -> None:
    assert tuple(TickRecord.model_fields) == TICK_COLUMNS
    assert tuple(FuturesBarRecord.model_fields) == BAR_COLUMNS


def test_a_contract_answer_equals_contract_dict() -> None:
    assert _json(ContractAnswerRecord.from_answer(ESU4)) == contract_dict(ESU4)


def test_a_schedule_segment_equals_the_schedule_dict_segment() -> None:
    segment = Segment(ESU4.session_date, ESU4.session_date, ESU4)

    assert _json(ScheduleSegmentRecord.from_segment(segment)) == _segment_dict(segment)


def test_the_schedule_equals_schedule_dict() -> None:
    body = _json(FuturesScheduleResponse.from_schedule(SCHEDULE))

    assert body == schedule_dict(SCHEDULE)
    assert body["unresolved"] == [unresolved_dict(GAP)]


@pytest.mark.parametrize(
    "line",
    [
        ActiveLine(
            "ES",
            DEFAULT_ROLL_RULE,
            session_date=ESU4.session_date,
            answer=ESU4,
            next_roll=NextRoll(ESZ4.session_date, ESZ4),
            known_through=date(2024, 9, 25),
            roll_no_later_than=date(2024, 9, 19),
        ),
        ActiveLine(
            "ES", RollRule.parse("c.0"), session_date=GAP.session_date, answer=GAP
        ),
        ActiveLine("ES", DEFAULT_ROLL_RULE, reason="no sessions held for ES"),
        ActiveLine("ES", DEFAULT_ROLL_RULE, error="ESU4 has two definitions"),
    ],
    ids=["contract", "unresolved", "reason", "error"],
)
def test_an_active_line_equals_active_line_dict(line: ActiveLine) -> None:
    assert _json(ActiveLineRecord.from_line(line)) == active_line_dict(line)


def _plan() -> GetPlan:
    return GetPlan(
        target=ProductTarget("ES", DEFAULT_ROLL_RULE),
        start=date(2024, 9, 18),
        end=date(2024, 9, 19),
        segments=(),
        display=(DisplaySegment(date(2024, 9, 18), date(2024, 9, 18), "ESZ4", 1),),
        unresolved=(),
        incomplete=(Incomplete(date(2024, 9, 18), TickSessionStatus.MISSING),),
        definitions_as_of=date(2024, 12, 31),
    )


def _tick(price: int) -> tuple[Any, ...]:
    return (TS_2024, date(2024, 9, 18), "ESZ4", 1, price, 2, "A")


def test_prices_are_exact_text_and_undefined_is_null() -> None:
    row = (*_tick(5_950_250_000_000), UNDEF_INT64, None, None, None, None, None, 9)

    tick = _json(FuturesTicksResponse.from_plan(_plan(), [row], (None, None)))["ticks"][
        0
    ]

    assert (tick["price"], tick["bid_px"], tick["ask_px"]) == ("5950.25", None, None)
    assert tick["ts_event"] == "2024-09-18T14:00:00.123456789Z"


def _integers(value: Any) -> list[int]:
    if isinstance(value, bool):
        return []
    if isinstance(value, int):
        return [value]
    if isinstance(value, dict):
        return [n for v in value.values() for n in _integers(v)]
    if isinstance(value, list):
        return [n for v in value for n in _integers(v)]
    return []


def test_no_integer_on_the_wire_reaches_2_to_the_53() -> None:
    row = (*_tick(5_950_250_000_000), None, None, None, None, None, None, 9)
    bar = (TS_2024, date(2024, 9, 18), "ESZ4", 1, *[5_950_250_000_000] * 4, 10)

    ticks = _json(FuturesTicksResponse.from_plan(_plan(), [row], (None, None)))
    bars = _json(FuturesBarsResponse.from_plan(_plan(), "1m", [bar]))

    assert max(_integers(ticks) + _integers(bars)) < 2**53


def test_from_and_until_are_on_the_wire_under_their_names() -> None:
    bounds = (
        datetime(2024, 9, 18, 14, tzinfo=UTC),
        datetime(2024, 9, 18, 15, tzinfo=UTC),
    )

    body = _json(FuturesTicksResponse.from_plan(_plan(), [], bounds))

    assert (body["from"], body["until"]) == (
        "2024-09-18T14:00:00Z",
        "2024-09-18T15:00:00Z",
    )
    assert "from_" not in body
    assert body["incomplete"] == [{"session_date": "2024-09-18", "status": "missing"}]
