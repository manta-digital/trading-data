"""``mt data tick active``: which contract a roll rule picks (slice 229, TD7).

Provider-free, like ``status``: no Databento key, no archive, no lock. The
tick database's migrations are checked first (``connect_migrated``); the roll
path then opens its own bounded connections (``active_contract``). Without a
range it answers the latest complete session per rule; with one, it prints
the segments of each schedule. Registered on ``tick_app`` in ``tick.py``.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING, NoReturn

import typer

from manta_trading.cli.commands.tick_exit import (
    EXIT_OK,
    EXIT_PARTIAL,
    EXIT_PREFLIGHT,
    EXIT_STORAGE,
)
from manta_trading.cli.commands.tick_store_cmds import (
    DATE_FORMAT,
    JSON_OPTION,
    PRODUCT_OPTION,
    read_storage,
    run_mapped,
    universe_entries,
)
from manta_trading.cli.output import print_error
from manta_trading.data.tick.constants import RollMethod
from manta_trading.data.tick.roll_rules import (
    DEFAULT_ROLL_RULE,
    STATUS_ROLL_RULES,
    RollRule,
)

if TYPE_CHECKING:
    from manta_trading.config import Settings
    from manta_trading.data.tick.roll_types import ActiveLine, RollSchedule
    from manta_trading.data.tick.universe import TickUniverseEntry

ACTIVE_EPILOG = (
    f"Exit codes: {EXIT_OK} every session resolved; {EXIT_PARTIAL} a session is "
    f"unresolved (output still printed); {EXIT_PREFLIGHT} a bad --rule, an unknown "
    "--product, a range over TICK_ROLL_MAX_SESSIONS or outside the calendar (run "
    f"mt data extend), or a broken invariant in the stored inputs; {EXIT_STORAGE} "
    "the tick or production (calendar) database is unreachable, timed out, or "
    "dropped the connection."
)
_RULE_OPTION = typer.Option(
    None,
    "--rule",
    help=(
        "Roll rule {method}.{rank}[+{offset}s]; methods "
        f"{', '.join(m.value for m in RollMethod)}, offset on calendar only. "
        f"Omitted: {', '.join(map(str, STATUS_ROLL_RULES))} "
        f"(default {DEFAULT_ROLL_RULE})."
    ),
)
_START_OPTION = typer.Option(
    None, "--start", formats=[DATE_FORMAT], help="First session date (with --end)."
)
_END_OPTION = typer.Option(
    None, "--end", formats=[DATE_FORMAT], help="End session date, EXCLUSIVE."
)


def _refuse(message: str, json_output: bool) -> NoReturn:
    print_error(message, json_mode=json_output)
    raise typer.Exit(EXIT_PREFLIGHT)


def _rules(rule: str | None, json_output: bool) -> tuple[RollRule, ...]:
    if rule is None:
        return STATUS_ROLL_RULES
    try:
        return (RollRule.parse(rule),)
    except ValueError as exc:
        _refuse(str(exc), json_output)


def _range(
    start: datetime | None, end: datetime | None, json_output: bool
) -> tuple[datetime, datetime] | None:
    if start is None and end is None:
        return None
    if start is None or end is None:
        _refuse("--start and --end go together", json_output)
    if end <= start:
        _refuse(
            f"--end {end:%Y-%m-%d} must be after --start {start:%Y-%m-%d}"
            " (end is exclusive)",
            json_output,
        )
    return start, end


async def _targets(
    settings: Settings, product: str | None
) -> tuple[list[TickUniverseEntry], str, str]:
    """The products, then both URLs once the tick track is checked (refusals
    as ``status``)."""
    from manta_trading.data.tick.store_context import connect_migrated, database_url
    from manta_trading.data.tick.tick_calendar import calendar_url

    entries = universe_entries(product)
    tick_url = database_url(settings)
    conn = await connect_migrated(tick_url)
    await conn.close()
    return entries, tick_url, calendar_url(settings)


async def _schedules(
    settings: Settings,
    product: str | None,
    rules: tuple[RollRule, ...],
    span: tuple[date, date],
) -> list[RollSchedule]:
    from manta_trading.data.tick.active_contract import build_schedule

    entries, tick_url, calendar = await _targets(settings, product)
    return [
        await build_schedule(tick_url, calendar, e, r, *span)
        for e in entries
        for r in rules
    ]


async def _latest(
    settings: Settings, product: str | None, rules: tuple[RollRule, ...]
) -> list[ActiveLine]:
    from manta_trading.data.tick.active_contract import latest_lines

    entries, tick_url, calendar = await _targets(settings, product)
    return [
        line
        for e in entries
        for line in await latest_lines(tick_url, calendar, e, rules)
    ]


def _lines_exit(lines: list[ActiveLine]) -> int:
    from manta_trading.data.tick.roll_types import Unresolved

    if any(line.error for line in lines):
        return EXIT_PREFLIGHT  # a rule's broken invariant (shared failures raise)
    if any(line.reason or isinstance(line.answer, Unresolved) for line in lines):
        return EXIT_PARTIAL
    return EXIT_OK


def tick_active(
    ctx: typer.Context,
    product: str | None = PRODUCT_OPTION,
    rule: str | None = _RULE_OPTION,
    start: datetime | None = _START_OPTION,
    end: datetime | None = _END_OPTION,
    json_output: bool = JSON_OPTION,
) -> None:
    """The active contract per roll rule: the latest complete session, or the
    schedule's segments over --start/--end. Reads only stored state."""
    from manta_trading.cli.commands.tick_active_render import (
        print_active_lines,
        print_schedules,
    )

    rules = _rules(rule, json_output)
    span = _range(start, end, json_output)
    settings: Settings = ctx.obj["settings"]
    if span is None:
        answered = run_mapped(
            lambda: _latest(settings, product, rules), json_output, read_storage()
        )
        exit_code = _lines_exit(answered)
        print_active_lines(answered, exit_code, json_mode=json_output)
    else:
        days = (span[0].date(), span[1].date())
        built = run_mapped(
            lambda: _schedules(settings, product, rules, days),
            json_output,
            read_storage(),
        )
        exit_code = EXIT_PARTIAL if any(s.unresolved for s in built) else EXIT_OK
        print_schedules(built, exit_code, json_mode=json_output)
    if exit_code != EXIT_OK:
        raise typer.Exit(exit_code)
