"""``mt data tick get``: stored ticks, or time bars from held bars or ticks
(slices 230, 309).

LLD 230 TD1, TD2, TD5, TD11. Provider-free, like ``status``: no Databento
key, no archive, no lock. The tick track's migrations are checked first
(``connect_migrated``), the plan is built (``get_plan``), then one
``REPEATABLE READ READ ONLY`` transaction reads the planned sessions in order
and streams each batch to the writer. A bar size reads each session from its
source (``time_bar_source``) through the API's ``TimeBarRead``, with no trade
limit: the limit protects the shared API server, and this command has never
limited a read. Registered on ``tick_app`` in ``tick.py``.

The writer's header waits for the first row, so a failure before any row
takes the ordinary error path (``run_mapped``). After it, a dropped or timed
out read finishes the output as partial and exits 4 (TD11). A reader that
closes the pipe ends the run quietly with exit 0.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Sequence
from datetime import datetime
from typing import TYPE_CHECKING

import psycopg
import typer

from manta_trading.cli.commands.tick_active_cmds import refuse
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
)
from manta_trading.data.tick.constants import TICK_GET_TICKS
from manta_trading.data.tick.get_plan import TIME_BAR_RANGE
from manta_trading.data.tick.line_state import first_line
from manta_trading.data.tick.roll_rules import DEFAULT_ROLL_RULE, RollRule
from manta_trading.data.tick.schema_families import SchemaFamily
from manta_trading.data.tick.time_bar_reads import LedgerCountExceeded, TimeBarRead
from manta_trading.data.tick.time_bar_size import (
    BAR_SIZE_FORM,
    BarSize,
    parse_bar_size,
)
from manta_trading.data.tick.time_bar_source import (
    SourceRun,
    served_plan,
    source_runs,
)

if TYPE_CHECKING:
    from manta_trading.cli.commands.tick_get_writers import GetWriter
    from manta_trading.config import Settings
    from manta_trading.data.tick.get_plan import GetPlan
    from manta_trading.data.tick.manifest_reads import Conn
    from manta_trading.data.tick.tick_reads import Row

GET_EPILOG = (
    "Bars are cut from ticks unless provider bars that can make the size are "
    "held; the output names the source of each run of sessions.  "
    "Examples: mt data tick get 1m --product ES --start 2024-09-16 --end "
    "2024-09-20;  mt data tick get ticks --contract ESZ4 --start 2024-09-18 "
    "--end 2024-09-19 --csv > esz4.csv.  "
    f"Exit codes: {EXIT_OK} every session resolved and complete; {EXIT_PARTIAL} "
    "output printed, and a session is unresolved or incomplete (listed); "
    f"{EXIT_PREFLIGHT} a bad WHAT, rule, date or option combination, an unknown "
    "product, an unknown or ambiguous contract, a range over "
    "TICK_ROLL_MAX_SESSIONS or outside the calendar (run mt data extend), a "
    f"pending tick migration, or MT_TICK_DB_URL unset; {EXIT_STORAGE} the tick "
    "or calendar database is unreachable, timed out, or dropped (after rows: "
    "the output says PARTIAL)."
)
_WHAT_ARGUMENT = typer.Argument(
    ...,
    metavar="WHAT",
    help=f"{TICK_GET_TICKS}, or a bar size: {BAR_SIZE_FORM} (1d = the whole session).",
    show_default=False,
)
CONTRACT_OPTION = typer.Option(
    None, "--contract", help="One contract by exchange symbol, e.g. ESZ4."
)
RULE_OPTION = typer.Option(
    None,
    "--rule",
    help=f"Roll rule for --product (default {DEFAULT_ROLL_RULE}); see "
    "mt data tick active --help.",
)
START_OPTION = typer.Option(
    None,
    "--start",
    formats=[DATE_FORMAT],
    help="First session date. Omitted: the first held session.",
)
END_OPTION = typer.Option(
    None,
    "--end",
    formats=[DATE_FORMAT],
    help="End session date, EXCLUSIVE (unlike mt data get, whose end is "
    "inclusive). Omitted: after the last held session.",
)
_CSV_OPTION = typer.Option(
    False, "--csv", help="CSV on stdout: header and rows only (lists on stderr)."
)


def bar_size_of(what: str, json_output: bool) -> BarSize | None:
    """``None`` for ticks; else ``what`` as a bar size, or a preflight refusal."""
    if what == TICK_GET_TICKS:
        return None
    try:
        return parse_bar_size(what)
    except ValueError as exc:
        refuse(f"WHAT must be {TICK_GET_TICKS} or {exc}", json_output)


def target_rule(
    contract: str | None, product: str | None, rule: str | None, json_output: bool
) -> RollRule:
    if (contract is None) == (product is None):
        refuse("give exactly one of --contract and --product", json_output)
    if rule is not None and product is None:
        refuse("--rule goes with --product only", json_output)
    try:
        return DEFAULT_ROLL_RULE if rule is None else RollRule.parse(rule)
    except ValueError as exc:
        refuse(str(exc), json_output)


async def plan_for_target(
    conn: Conn,
    settings: Settings,
    target: tuple[str | None, ...],
    rule: RollRule,
    span: tuple[datetime | None, ...],
    timed: bool,
) -> GetPlan:
    """Ticks plan and default over the tick tier; time bars (``timed``) plan
    over it too and default over held ticks and provider bars."""
    from manta_trading.cli.commands.tick_store_cmds import universe_entries
    from manta_trading.data.tick.get_plan import contract_plan, product_plan
    from manta_trading.data.tick.store_context import database_url
    from manta_trading.data.tick.tick_calendar import calendar_url

    contract, product = target
    start, end = (None if d is None else d.date() for d in span)
    calendar = calendar_url(settings)
    family = SchemaFamily.TICK_TIER
    range_over = TIME_BAR_RANGE if timed else None
    if contract is not None:
        return await contract_plan(
            conn, calendar, contract, start, end, family, range_over
        )
    (entry,) = universe_entries(product)
    urls = (database_url(settings), calendar)
    return await product_plan(conn, urls, entry, rule, start, end, family, range_over)


def _runs(reader: TimeBarRead | None, plan: GetPlan) -> list[SourceRun] | None:
    """The source runs of the sessions read so far; ``None`` for ticks."""
    if reader is None:
        return None
    return source_runs(plan, reader.sources, reader.returned)


async def _stream(
    conn: Conn, plan: GetPlan, reader: TimeBarRead | None, writer: GetWriter
) -> int:
    """Read every planned session in one snapshot; return the exit code.
    ``reader`` reads time bars; ``None`` reads ticks."""
    from manta_trading.data.tick.tick_reads import read_ticks

    written = 0
    shown = plan  # the plan the writer lists: provider-served sessions are held

    def on_batch(batch: Sequence[Row]) -> None:
        nonlocal written
        if written == 0:
            writer.begin(shown)
        writer.rows(batch)
        written += len(batch)

    read = read_ticks if reader is None else reader.read
    try:
        await conn.set_isolation_level(psycopg.IsolationLevel.REPEATABLE_READ)
        await conn.set_read_only(True)
        async with conn.transaction():
            if reader is not None:
                await reader.admit(conn, plan)
                shown = served_plan(plan, reader.sources)
            for window in plan.segments:
                await read(conn, window, on_batch)
    except LedgerCountExceeded as exc:
        # The stored ticks and the ledger disagree: a database fault. Rows
        # already out are closed as partial so the output is never unmarked.
        if written:
            writer.partial(written, str(exc), _runs(reader, plan))
        sys.stderr.write(
            f"session {exc.session_date} of {exc.raw_symbol}: read {exc.read:,}"
            f" trades, ledger {exc.ledger:,}\n"
        )
        return EXIT_STORAGE
    except psycopg.OperationalError as exc:  # QueryCanceled included
        if written == 0:
            raise  # nothing printed yet: the ordinary error path (run_mapped)
        # Not swallowed: rows are already on stdout, so this is the output
        # boundary. The writer reports the error in the output itself
        # (PARTIAL footer, stderr line, or "complete": false) and the run
        # exits 4 (TD11); raising here would leave a truncated, unmarked file.
        writer.partial(written, first_line(exc), _runs(reader, plan))
        return EXIT_STORAGE
    if written == 0:
        writer.begin(shown)
    code = EXIT_PARTIAL if shown.unresolved or shown.incomplete else EXIT_OK
    writer.finish(code, _runs(reader, plan))
    return code


def _reader_gone() -> None:
    """The reader closed the pipe: send what is left of stdout nowhere, so the
    interpreter's final flush cannot raise a second time."""
    devnull = os.open(os.devnull, os.O_WRONLY)
    os.dup2(devnull, sys.stdout.fileno())
    os.close(devnull)


def tick_get(
    ctx: typer.Context,
    what: str = _WHAT_ARGUMENT,
    contract: str | None = CONTRACT_OPTION,
    product: str | None = PRODUCT_OPTION,
    rule: str | None = RULE_OPTION,
    start: datetime | None = START_OPTION,
    end: datetime | None = END_OPTION,
    csv_output: bool = _CSV_OPTION,
    json_output: bool = JSON_OPTION,
) -> None:
    """Stored ticks, or time bars from held bars or ticks, for one contract or
    for a product under a roll rule (one contract per session, never merged).

    Dates are session dates; --end is EXCLUSIVE, unlike mt data get.
    """
    from manta_trading.cli.commands.tick_get_writers import writer_for
    from manta_trading.data.tick.active_contract import roll_conninfo
    from manta_trading.data.tick.store_context import connect_migrated, database_url
    from manta_trading.data.tick.tick_calendar import calendar_url

    size = bar_size_of(what, json_output)
    if csv_output and json_output:
        refuse("--csv and --json are exclusive", json_output)
    parsed = target_rule(contract, product, rule, json_output)
    settings: Settings = ctx.obj["settings"]
    what = what if size is None else size.token
    writer = writer_for(
        what,
        json_output=json_output,
        csv_output=csv_output,
        out=sys.stdout,
        err=sys.stderr,
    )

    async def main() -> int:
        conn = await connect_migrated(roll_conninfo(database_url(settings)))
        try:
            plan = await plan_for_target(
                conn,
                settings,
                (contract, product),
                parsed,
                (start, end),
                size is not None,
            )
            reader = (
                None
                if size is None
                else TimeBarRead(size, calendar_url(settings), max_trades=None)
            )
            return await _stream(conn, plan, reader, writer)
        finally:
            await conn.close()

    try:
        exit_code = run_mapped(main, json_output, read_storage())
        sys.stdout.flush()
    except BrokenPipeError:
        _reader_gone()
        raise typer.Exit(EXIT_OK) from None
    if exit_code != EXIT_OK:
        raise typer.Exit(exit_code)
