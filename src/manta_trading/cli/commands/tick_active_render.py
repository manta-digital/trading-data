"""Text and JSON rendering for ``mt data tick active`` (slice 229, TD7).

:func:`active_line_text` renders one rule's latest-session answer; ``status``
reuses it for its roll lines. JSON payloads come from ``roll_payload``. Ranged
output is one block of segments per (product, rule), with unresolved sessions
and their reasons listed under it. Dates are ISO; expirations are UTC.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from rich import print as rprint
from rich.markup import escape

from manta_trading.cli.output import print_result
from manta_trading.data.tick.constants import RollMethod
from manta_trading.data.tick.roll_payload import active_line_dict, schedule_dict
from manta_trading.data.tick.roll_rules import DEFAULT_ROLL_RULE, RollRule
from manta_trading.data.tick.roll_types import (
    ActiveContract,
    ActiveLine,
    RollSchedule,
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
