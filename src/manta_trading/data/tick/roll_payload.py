"""JSON payloads for roll answers (slice 229, TD7): ``active --json``,
``status --json``'s ``rolls``, and 231's API serialize these. Dates are ISO;
expirations are ISO UTC.
"""

from __future__ import annotations

from datetime import UTC, date
from typing import Any

from manta_trading.data.tick.roll_rules import DEFAULT_ROLL_RULE
from manta_trading.data.tick.roll_types import (
    ActiveContract,
    ActiveLine,
    RollSchedule,
    Segment,
    Unresolved,
)


def contract_dict(answer: ActiveContract) -> dict[str, Any]:
    return {
        "session_date": answer.session_date.isoformat(),
        "instrument_id": answer.instrument_id,
        "raw_symbol": answer.raw_symbol,
        "expiration": answer.expiration.astimezone(UTC).isoformat(),
        "basis_session": answer.basis_session.isoformat(),
        "definitions_as_of": answer.definitions_as_of.isoformat(),
        "definition_unit_id": answer.definition_unit_id,
        "basis_unit_ids": list(answer.basis_unit_ids),
    }


#: ``_contract_dict``'s keys, null, for a line with no contract (pinned by a test).
CONTRACT_KEYS = (
    "session_date",
    "instrument_id",
    "raw_symbol",
    "expiration",
    "basis_session",
    "definitions_as_of",
    "definition_unit_id",
    "basis_unit_ids",
)


def _unresolved_dict(answer: Unresolved) -> dict[str, str]:
    return {"session_date": answer.session_date.isoformat(), "reason": answer.reason}


def _iso(day: date | None) -> str | None:
    return None if day is None else day.isoformat()


def active_line_dict(line: ActiveLine) -> dict[str, Any]:
    """One rule's latest-session answer; contract fields are null without one."""
    answer = line.answer
    contract = (
        contract_dict(answer)
        if isinstance(answer, ActiveContract)
        else dict.fromkeys(CONTRACT_KEYS) | {"session_date": _iso(line.session_date)}
    )
    roll = line.next_roll
    return {
        "product": line.product,
        "rule": str(line.rule),
        "is_default": line.rule == DEFAULT_ROLL_RULE,
        **contract,
        "unresolved": (
            [_unresolved_dict(answer)] if isinstance(answer, Unresolved) else []
        ),
        "next_roll": None if roll is None else contract_dict(roll.contract),
        "known_through": _iso(line.known_through),
        "roll_no_later_than": _iso(line.roll_no_later_than),
        "reason": line.reason,
        "error": line.error,
    }


def _segment_dict(segment: Segment) -> dict[str, Any]:
    c = segment.contract
    return {
        "first_session": segment.first_session.isoformat(),
        "last_session": segment.last_session.isoformat(),
        "instrument_id": c.instrument_id,
        "raw_symbol": c.raw_symbol,
        "expiration": c.expiration.astimezone(UTC).isoformat(),
    }


def schedule_dict(schedule: RollSchedule) -> dict[str, Any]:
    return {
        "product": schedule.product,
        "rule": str(schedule.rule),
        "is_default": schedule.rule == DEFAULT_ROLL_RULE,
        "segments": [_segment_dict(s) for s in schedule.segments()],
        "answers": [
            contract_dict(a) for a in schedule.answers if isinstance(a, ActiveContract)
        ],
        "unresolved": [_unresolved_dict(u) for u in schedule.unresolved],
    }
