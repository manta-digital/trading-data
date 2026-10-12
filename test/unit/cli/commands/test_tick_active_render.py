"""``mt data tick active`` rendering (229 TD7): text and JSON shapes."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime

from manta_trading.cli.commands.tick_active_render import (
    active_line_text,
    schedule_text,
)
from manta_trading.data.tick.roll_payload import (
    CONTRACT_KEYS,
    active_line_dict,
    contract_dict,
    schedule_dict,
)
from manta_trading.data.tick.roll_rules import RollRule
from manta_trading.data.tick.roll_types import (
    ActiveContract,
    ActiveLine,
    NextRoll,
    RollSchedule,
    Unresolved,
)

L = date(2024, 12, 31)
V0, C0 = RollRule.parse("v.0"), RollRule.parse("c.0")


def _contract(symbol: str, iid: int, day: date, basis: date) -> ActiveContract:
    return ActiveContract(
        session_date=day,
        instrument_id=iid,
        raw_symbol=symbol,
        expiration=datetime(2025, 3, 21, 13, 30, tzinfo=UTC),
        basis_session=basis,
        definitions_as_of=L,
        definition_unit_id=131,
        basis_unit_ids=(301, 302),
    )


ESH5 = _contract("ESH5", 5002, L, date(2024, 12, 27))
VOLUME = ActiveLine(
    "ES",
    V0,
    L,
    ESH5,
    known_through=date(2025, 1, 3),
    roll_no_later_than=date(2025, 3, 24),
)
CALENDAR = ActiveLine(
    "ES",
    C0,
    L,
    _contract("ESH5", 5002, L, L),
    next_roll=NextRoll(
        date(2025, 3, 24), _contract("ESM5", 4916, date(2025, 3, 24), date(2025, 3, 24))
    ),
    known_through=date(2027, 12, 31),
)


def test_volume_line_matches_td7() -> None:
    assert active_line_text(VOLUME) == [
        "ES  v.0 (default)  ESH5 (5002)  exp 2025-03-21 13:30 UTC   session 2024-12-31",
        "    inputs: volume of session 2024-12-27; definitions as of 2024-12-31",
        "    next roll: none through session 2025-01-03 (held volume);",
        "               no later than session 2025-03-24 (expiry)",
    ]


def test_calendar_line_matches_td7() -> None:
    assert active_line_text(CALENDAR) == [
        "ES  c.0            ESH5 (5002)  exp 2025-03-21 13:30 UTC   session 2024-12-31",
        "    next roll: session 2025-03-24 → ESM5 (4916)",
    ]


def test_error_reason_and_unresolved_lines() -> None:
    assert active_line_text(ActiveLine("ES", V0, error="boom")) == [
        "ES  v.0 (default)  error: boom"
    ]
    assert active_line_text(ActiveLine("ES", C0, reason="nothing held")) == [
        "ES  c.0            no answer: nothing held"
    ]
    unresolved = ActiveLine("ES", V0, L, Unresolved(L, "volume inputs: x"))
    assert active_line_text(unresolved)[1] == "    volume inputs: x"


def test_line_json_carries_step_seven_fields() -> None:
    body = active_line_dict(VOLUME)
    assert json.loads(json.dumps(body)) == body
    assert {
        "rule",
        "is_default",
        "instrument_id",
        "raw_symbol",
        "expiration",
        "basis_session",
        "basis_unit_ids",
        "definition_unit_id",
        "next_roll",
        "known_through",
        "roll_no_later_than",
        "error",
    } <= set(body)
    assert body["is_default"] is True
    assert body["expiration"] == "2025-03-21T13:30:00+00:00"
    assert body["basis_unit_ids"] == [301, 302]
    assert active_line_dict(CALENDAR)["next_roll"]["raw_symbol"] == "ESM5"


def test_null_contract_keys_match_a_real_contract() -> None:
    assert tuple(contract_dict(ESH5)) == CONTRACT_KEYS
    errored = active_line_dict(ActiveLine("ES", V0, error="boom"))
    assert errored["instrument_id"] is None
    assert errored["error"] == "boom"


def test_schedule_text_and_json() -> None:
    days = [date(2024, 9, d) for d in (16, 17, 18, 19)]
    answers = (
        Unresolved(days[0], "volume inputs: session 2024-09-12 is missing"),
        _contract("ESU4", 118, days[1], days[1]),
        _contract("ESZ4", 183748, days[2], days[2]),
        _contract("ESZ4", 183748, days[3], days[3]),
    )
    schedule = RollSchedule("ES", V0, answers)
    assert schedule_text(schedule) == [
        "ES  v.0 (default)  2024-09-17 → 2024-09-17  ESU4 (118)",
        "                   2024-09-18 → 2024-09-19  ESZ4 (183748)",
        "    unresolved 2024-09-16: volume inputs: session 2024-09-12 is missing",
    ]
    body = schedule_dict(schedule)
    assert json.loads(json.dumps(body)) == body
    assert [s["raw_symbol"] for s in body["segments"]] == ["ESU4", "ESZ4"]
    assert len(body["answers"]) == 3
    assert body["unresolved"] == [
        {
            "session_date": "2024-09-16",
            "reason": "volume inputs: session 2024-09-12 is missing",
        }
    ]
