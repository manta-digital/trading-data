"""``mt data tick parity``: tick-derived against provider minutes (slice 306, D7).

Read-only and provider-free, like ``get``. One ``REPEATABLE READ READ ONLY``
snapshot holds the whole run: the plan (``family=TICK_TIER``), the bar verdicts
for the same sessions, and every comparison. The report is built in memory and
printed after the last session, so a failed run prints none, and exit 3 always
means a finished comparison with findings. Failures go through
``tick_exit.exit_code_for`` (``run_mapped``): a bad target or range exits 1, a
database failure at any point exits 4. Registered on ``tick_app`` in ``tick.py``.
"""

from __future__ import annotations

import sys
from datetime import datetime
from typing import TYPE_CHECKING

import psycopg
import typer

from manta_trading.cli.commands.tick_exit import EXIT_OK, EXIT_PARTIAL
from manta_trading.cli.commands.tick_get_cmds import (
    CONTRACT_OPTION,
    END_OPTION,
    RULE_OPTION,
    START_OPTION,
    plan_for_target,
    target_rule,
)
from manta_trading.cli.commands.tick_parity_render import json_text, text
from manta_trading.cli.commands.tick_store_cmds import (
    JSON_OPTION,
    PRODUCT_OPTION,
    read_storage,
    run_mapped,
)
from manta_trading.constants import Granularity
from manta_trading.data.tick.bar_parity import classify_minutes, read_tick_minute_bars
from manta_trading.data.tick.bar_parity_report import (
    ParityReport,
    build_report,
    session_report,
)
from manta_trading.data.tick.bar_reads import read_provider_bars
from manta_trading.data.tick.parity_plan import ParityInputs, parity_inputs
from manta_trading.data.tick.schema_families import SchemaFamily

if TYPE_CHECKING:
    from manta_trading.config import Settings
    from manta_trading.data.tick.get_plan import GetPlan
    from manta_trading.data.tick.manifest_reads import Conn
    from manta_trading.data.tick.roll_rules import RollRule
    from manta_trading.data.tick.tick_reads import Row

PARITY_EPILOG = (
    "Sets the minutes built from ticks against the provider's ohlcv-1m minutes, "
    "for the sessions complete in both tiers; any other planned session is "
    "listed as skipped with both statuses. Marks minutes stamped at the session "
    "close. Examples: mt data tick parity --product ES --start 2024-12-03 --end "
    "2024-12-04 --json.  "
    f"Exit codes: {EXIT_OK} every compared minute agrees; {EXIT_PARTIAL} a "
    "finished comparison with a minute that does not; 1 a bad rule, date, "
    "option combination, product or contract; 4 the tick or calendar database "
    "failed (no report is printed)."
)


async def _compare(conn: Conn, inputs: ParityInputs) -> ParityReport:
    """Compare every window, both sides in memory, one session at a time."""
    sessions = []
    for window in inputs.compared:
        tick: list[Row] = []
        provider: list[Row] = []
        await read_tick_minute_bars(conn, window, tick.extend)
        await read_provider_bars(conn, window, Granularity.M1, provider.extend)
        minutes = classify_minutes(tick, provider, window.close_ns)
        sessions.append(session_report(window.session_date, minutes))
    return build_report(sessions, inputs.skipped)


async def _run(
    settings: Settings,
    target: tuple[str | None, str | None],
    rule: RollRule,
    span: tuple[datetime | None, datetime | None],
) -> tuple[GetPlan, ParityReport]:
    from manta_trading.data.tick.active_contract import roll_conninfo
    from manta_trading.data.tick.store_context import connect_migrated, database_url
    from manta_trading.data.tick.tick_calendar import calendar_url

    conn = await connect_migrated(roll_conninfo(database_url(settings)))
    try:
        await conn.set_isolation_level(psycopg.IsolationLevel.REPEATABLE_READ)
        await conn.set_read_only(True)
        async with conn.transaction():
            plan = await plan_for_target(
                conn, settings, target, rule, span, SchemaFamily.TICK_TIER
            )
            inputs = await parity_inputs(conn, calendar_url(settings), plan)
            return plan, await _compare(conn, inputs)
    finally:
        await conn.close()


def tick_parity(
    ctx: typer.Context,
    contract: str | None = CONTRACT_OPTION,
    product: str | None = PRODUCT_OPTION,
    rule: str | None = RULE_OPTION,
    start: datetime | None = START_OPTION,
    end: datetime | None = END_OPTION,
    json_output: bool = JSON_OPTION,
) -> None:
    """Compare tick-derived one-minute bars with the provider's, per session.

    Dates are session dates; --end is EXCLUSIVE, as in mt data tick get.
    """
    parsed = target_rule(contract, product, rule, json_output)
    settings: Settings = ctx.obj["settings"]

    async def main() -> tuple[GetPlan, ParityReport]:
        return await _run(settings, (contract, product), parsed, (start, end))

    plan, report = run_mapped(main, json_output, read_storage())
    code = EXIT_OK if report.all_agree else EXIT_PARTIAL
    sys.stdout.write(
        json_text(plan, report, code) if json_output else text(plan, report)
    )
    if code != EXIT_OK:
        raise typer.Exit(code)
