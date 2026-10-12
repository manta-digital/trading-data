"""Text and JSON rendering for ``mt data tick active`` (slice 229, TD7).

:func:`active_line_text` and :func:`active_line_dict` render one rule's
latest-session answer; ``status`` reuses both for its roll lines. Ranged
output is one block of segments per (product, rule), with unresolved sessions
and their reasons listed under it. Dates are ISO; expirations are UTC.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from rich import print as rprint
from rich.markup import escape

from manta_trading.cli.output import print_result
from manta_trading.data.tick.constants import RollMethod
from manta_trading.data.tick.roll_rules import DEFAULT_ROLL_RULE, RollRule
from manta_trading.data.tick.roll_types import (
    ActiveContract,
    ActiveLine,
    RollSchedule,
    Segment,
    Unresolved,
)

_RULE_WIDTH = len(f"{DEFAULT_ROLL_RULE} (default)")
_INDENT = "    "


def _rule_label(rule: RollRule) -> str:
    label = f"{rule} (default)" if rule == DEFAULT_ROLL_RULE else str(rule)
    return label.ljust(_RULE_WIDTH)


def _expiry(moment: datetime) -> str:
    return f"{moment.astimezone(UTC):%Y-%m-%d %H:%M} UTC"


def _contract(answer: ActiveContract) -> str:
    return f"{answer.raw_symbol} ({answer.instrument_id})"


def _next_roll_lines(line: ActiveLine) -> list[str]:
    if line.next_roll is not None:
        held = " (held volume)" if line.rule.method is RollMethod.VOLUME else ""
        roll = line.next_roll
        return [
            f"next roll: session {roll.session_date} → {_contract(roll.contract)}{held}"
        ]
    return [
        f"next roll: none through session {line.known_through} (held volume);",
        f"           no later than session {line.roll_no_later_than} (expiry)",
    ]


def active_line_text(line: ActiveLine) -> list[str]:
    """One rule's latest-session block, as TD7 shows it."""
    head = f"{line.product}  {_rule_label(line.rule)}  "
    if line.error is not None:
        return [f"{head}error: {line.error}"]
    if line.reason is not None:
        return [f"{head}no answer: {line.reason}"]
    answer = line.answer
    if isinstance(answer, Unresolved):
        return [
            f"{head}unresolved   session {answer.session_date}",
            f"{_INDENT}{answer.reason}",
        ]
    assert isinstance(answer, ActiveContract)  # error, reason or an answer
    lines = [
        f"{head}{_contract(answer)}  exp {_expiry(answer.expiration)}"
        f"   session {answer.session_date}"
    ]
    if line.rule.method is RollMethod.VOLUME:
        lines.append(
            f"{_INDENT}inputs: volume of session {answer.basis_session};"
            f" definitions as of {answer.definitions_as_of}"
        )
    return lines + [_INDENT + text for text in _next_roll_lines(line)]


def _contract_dict(answer: ActiveContract) -> dict[str, Any]:
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


def _iso(day: Any) -> str | None:
    return None if day is None else day.isoformat()


def active_line_dict(line: ActiveLine) -> dict[str, Any]:
    """One rule's latest-session answer; contract fields are null without one."""
    answer = line.answer
    contract = (
        _contract_dict(answer)
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
        "next_roll": None if roll is None else _contract_dict(roll.contract),
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
            _contract_dict(a) for a in schedule.answers if isinstance(a, ActiveContract)
        ],
        "unresolved": [_unresolved_dict(u) for u in schedule.unresolved],
    }


def schedule_text(schedule: RollSchedule) -> list[str]:
    head = f"{schedule.product}  {_rule_label(schedule.rule)}  "
    pad = " " * len(head)
    segments = schedule.segments()
    lines = [
        f"{head if i == 0 else pad}{s.first_session} → {s.last_session}"
        f"  {_contract(s.contract)}"
        for i, s in enumerate(segments)
    ]
    if not schedule.answers:
        lines.append(f"{head}no sessions in range")
    elif not segments:
        lines.append(f"{head}no session resolved")
    for u in schedule.unresolved:
        lines.append(f"{_INDENT}unresolved {u.session_date}: {u.reason}")
    return lines


def print_active_lines(
    lines: Sequence[ActiveLine], exit_code: int, *, json_mode: bool
) -> None:
    if json_mode:
        body = {"lines": [active_line_dict(line) for line in lines]}
        print_result(body | {"exit_code": exit_code}, json_mode=True)
        return
    for line in lines:
        for text in active_line_text(line):
            rprint(escape(text))


def print_schedules(
    schedules: Sequence[RollSchedule], exit_code: int, *, json_mode: bool
) -> None:
    if json_mode:
        body = {"schedules": [schedule_dict(s) for s in schedules]}
        print_result(body | {"exit_code": exit_code}, json_mode=True)
        return
    for schedule in schedules:
        for text in schedule_text(schedule):
            rprint(escape(text))
