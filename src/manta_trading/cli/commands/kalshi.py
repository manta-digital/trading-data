"""``mt data kalshi`` — Kalshi catalog commands (slices 262 and 263).

``sync`` runs :class:`~manta_trading.data.kalshi.sync.CatalogSync` over the
real client and repository with its operator levers; ``pass`` (263) runs
every registered phase over one shared context and is what the timer fires;
``status`` reads the database only. All three share one preflight
(``kalshi_run``) and therefore one preflight-failure path. Exit codes
(262 Decision 11) are defined here and nowhere else: the core reports a
``SyncOutcome`` and this module maps it. Rich rendering lives in
``kalshi_render.py``.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING

import psycopg
import typer

from manta_trading.cli.commands.kalshi_render import (
    print_pass_summary,
    print_summary,
)
from manta_trading.cli.commands.kalshi_status_render import print_status
from manta_trading.cli.output import print_error, print_result
from manta_trading.data.acquisition.pass_runs import PassKind, PassRunOutcome
from manta_trading.data.kalshi.constants import DB_CONNECT_TIMEOUT_SECONDS
from manta_trading.data.kalshi.sync_types import SyncOutcome
from manta_trading.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from uuid import UUID

    from manta_trading.config import Settings
    from manta_trading.data.acquisition.daemon.pass_run_recorder import (
        PassRunRecorder,
    )
    from manta_trading.data.kalshi.collection_pass import PassPhaseName, PassResult
    from manta_trading.data.kalshi.run_context import KalshiRun

logger = get_logger(__name__)

kalshi_app = typer.Typer(
    name="kalshi",
    help="Kalshi event-contract catalog: pass, sync, and status.",
    no_args_is_help=True,
)

# Exit codes (design Decision 11). Integers live here only.
EXIT_OK = 0
EXIT_PREFLIGHT = 1
EXIT_PROVIDER = 2
EXIT_SYNC_PARTIAL = 3
EXIT_STORAGE = 4

EXIT_BY_OUTCOME: dict[SyncOutcome, int] = {
    SyncOutcome.OK: EXIT_OK,
    SyncOutcome.PARTIAL: EXIT_SYNC_PARTIAL,
    SyncOutcome.PROVIDER_ABORT: EXIT_PROVIDER,
    SyncOutcome.STORAGE_ABORT: EXIT_STORAGE,
}

# Every outcome must have a code — a new member cannot silently exit 0.
assert set(EXIT_BY_OUTCOME) == set(SyncOutcome), (
    "the kalshi exit mapping is not exhaustive — update it after adding a "
    "SyncOutcome member"
)

# What a Kalshi pass records in pass_runs (slice 922, Decision 2). The one
# place a SyncOutcome becomes a PassRunOutcome. Kalshi has no provider quota,
# so COMPLETE_QUOTA never arises here; a storage abort is this process's own
# failure, not the provider's, so it records FAILED.
PASS_RUN_OUTCOME_BY_SYNC_OUTCOME: dict[SyncOutcome, PassRunOutcome] = {
    SyncOutcome.OK: PassRunOutcome.COMPLETE,
    SyncOutcome.PARTIAL: PassRunOutcome.INCOMPLETE,
    SyncOutcome.PROVIDER_ABORT: PassRunOutcome.PROVIDER_UNAVAILABLE,
    SyncOutcome.STORAGE_ABORT: PassRunOutcome.FAILED,
}

assert set(PASS_RUN_OUTCOME_BY_SYNC_OUTCOME) == set(SyncOutcome), (
    "the kalshi pass_runs mapping is not exhaustive — update it after adding "
    "a SyncOutcome member"
)


def pass_run_outcome_for_kalshi(outcome: SyncOutcome) -> PassRunOutcome:
    """Map a Kalshi pass outcome to the value recorded in ``pass_runs``."""
    return PASS_RUN_OUTCOME_BY_SYNC_OUTCOME[outcome]


def parse_settled_since(value: str) -> datetime:
    """An ISO-8601 instant with an explicit offset; naive input is rejected."""
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"--settled-since is not ISO-8601: {value!r}") from exc
    if parsed.tzinfo is None:
        raise ValueError(
            f"--settled-since must carry a UTC offset (e.g. {value}Z or {value}+00:00)"
        )
    return parsed.astimezone(UTC)


# ---------------------------------------------------------------------------
# sync
# ---------------------------------------------------------------------------


_SETTLED_SINCE_OPTION = typer.Option(
    None,
    "--settled-since",
    help="Drain the settled stream from this ISO-8601 instant (with offset) "
    "instead of the stored watermark / historical cutoff. This run only.",
)
_EVENTS_FILE_OPTION = typer.Option(
    None, "--events-file", help="Append structured run events as JSONL here."
)
_JSON_OPTION = typer.Option(False, "--json", help="Emit JSON instead of Rich text.")


@kalshi_app.command("sync")
def kalshi_sync(
    ctx: typer.Context,
    settled_since: str | None = _SETTLED_SINCE_OPTION,
    events_file: Path | None = _EVENTS_FILE_OPTION,
    json_output: bool = _JSON_OPTION,
) -> None:
    """Full walk of the live catalog, the settled stream, and the awaiting set."""
    settings: Settings = ctx.obj["settings"]
    if not settings.timescale_db_url:
        print_error("MT_TIMESCALE_DB_URL not configured.", json_mode=json_output)
        raise typer.Exit(EXIT_PREFLIGHT)
    since: datetime | None = None
    if settled_since is not None:
        try:
            since = parse_settled_since(settled_since)
        except ValueError as exc:
            print_error(str(exc), json_mode=json_output)
            raise typer.Exit(EXIT_PREFLIGHT) from exc
    raise typer.Exit(asyncio.run(run_sync(settings, since, events_file, json_output)))


@contextlib.asynccontextmanager
async def kalshi_run(
    settings: Settings, events_file: Path | None, json_output: bool
) -> AsyncIterator[KalshiRun | None]:
    """The shared preflight context, with the only preflight→exit-1 mapping.

    Yields ``None`` when preflight failed (the message is already printed);
    every caller returns ``EXIT_PREFLIGHT`` for that case.
    """
    from manta_trading.data.kalshi.auth import KalshiCredentialError
    from manta_trading.data.kalshi.db import PreflightError
    from manta_trading.data.kalshi.run_context import open_kalshi_run

    try:
        async with open_kalshi_run(settings, events_file) as run:
            yield run
    except (KalshiCredentialError, PreflightError) as exc:
        print_error(str(exc), json_mode=json_output)
        yield None


async def run_sync(
    settings: Settings,
    settled_since: datetime | None,
    events_file: Path | None,
    json_output: bool,
) -> int:
    """Preflight, run, summarize; returns the exit code."""
    from manta_trading.data.kalshi.repository import CatalogRepository
    from manta_trading.data.kalshi.sync import CatalogSync, classify
    from manta_trading.providers.errors import ProviderError

    async with kalshi_run(settings, events_file, json_output) as run:
        if run is None:
            return EXIT_PREFLIGHT
        sync = CatalogSync(run.client, CatalogRepository(run.conn), run.sink)
        failure: ProviderError | psycopg.OperationalError | None = None
        try:
            await sync.run(settled_since=settled_since)
        except ProviderError as exc:
            failure = exc
            print_error(f"provider abort: {exc}", json_mode=json_output)
        except psycopg.OperationalError as exc:
            failure = exc
            logger.exception("kalshi sync storage failure")
            print_error(f"storage abort: {exc}", json_mode=json_output)
        outcome = classify(sync.result, failure)
    exit_code = EXIT_BY_OUTCOME[outcome]
    print_summary(sync.result, outcome, exit_code, json_output)
    return exit_code


# ---------------------------------------------------------------------------
# pass
# ---------------------------------------------------------------------------


@kalshi_app.command("pass")
def kalshi_pass(
    ctx: typer.Context,
    events_file: Path | None = _EVENTS_FILE_OPTION,
    json_output: bool = _JSON_OPTION,
) -> None:
    """One bounded collection pass: every registered phase, in order.

    This is what ``mt-kalshi-pass.service`` runs. It takes no phase options
    on purpose (design 263, Decision 1) — replay and repair levers live on
    ``sync``.
    """
    settings: Settings = ctx.obj["settings"]
    if not settings.timescale_db_url:
        print_error("MT_TIMESCALE_DB_URL not configured.", json_mode=json_output)
        raise typer.Exit(EXIT_PREFLIGHT)
    raise typer.Exit(asyncio.run(run_pass(settings, events_file, json_output)))


async def run_pass(
    settings: Settings, events_file: Path | None, json_output: bool
) -> int:
    """Preflight, run every phase, summarize; returns the exit code.

    The pass records itself in ``pass_runs`` (slice 922) so ``mt data
    overview`` can report it alongside the other passes. Recording is
    best-effort: a recorder that cannot be built is absent, and the recorder
    itself never raises, so the pass runs and reports its result either way.

    Every recorder call goes through ``asyncio.to_thread``. The recorder is
    synchronous psycopg and opens a short-lived connection per write, so on
    a slow or unreachable database a direct call would block the event loop
    — and with it the Kalshi client's rate-limit scheduling — for up to the
    connect timeout, several times per pass (922 review F003).
    """
    from manta_trading.cli.commands._pass_run import pass_run_recorder
    from manta_trading.data.kalshi.collection_pass import PASS_PHASES, CollectionPass

    with pass_run_recorder(settings) as recorder:
        run_id = (
            await asyncio.to_thread(recorder.open, PassKind.KALSHI)
            if recorder is not None
            else None
        )

        async def _close(
            outcome: PassRunOutcome, exit_code: int, detail: str | None
        ) -> None:
            """Close the row off the loop, or do nothing when unrecorded."""
            if recorder is None:
                return
            await asyncio.to_thread(
                partial(
                    recorder.close,
                    run_id,
                    outcome=outcome,
                    exit_code=exit_code,
                    detail=detail,
                )
            )

        try:
            async with kalshi_run(settings, events_file, json_output) as run:
                if run is None:
                    await _close(
                        PassRunOutcome.FAILED, EXIT_PREFLIGHT, "preflight failed"
                    )
                    return EXIT_PREFLIGHT
                result = await CollectionPass(
                    run, PASS_PHASES, on_phase=_phase_reporter(recorder, run_id)
                ).run()
        except Exception as exc:
            # The row must not be left open: the next pass would sweep it as
            # abandoned and the operator would see a failure with no cause.
            await _close(PassRunOutcome.FAILED, EXIT_STORAGE, type(exc).__name__)
            raise
        exit_code = EXIT_BY_OUTCOME[result.outcome]
        await _close(
            pass_run_outcome_for_kalshi(result.outcome),
            exit_code,
            _pass_detail(result),
        )
    print_pass_summary(result, exit_code, json_output)
    return exit_code


def _phase_reporter(
    recorder: "PassRunRecorder | None", run_id: "UUID | None"
) -> "Callable[[PassPhaseName], Awaitable[None]] | None":
    """Report each phase start to the pass_runs row, or None when unrecorded.

    Async because it is awaited from inside the pass loop: see ``run_pass``
    on why the write must not happen on the event loop.
    """
    if recorder is None or run_id is None:
        return None

    async def _report(phase: "PassPhaseName") -> None:
        await asyncio.to_thread(
            partial(recorder.progress, run_id, phase=str(phase))
        )

    return _report


def _pass_detail(result: "PassResult") -> str:
    """The per-phase outcome list, the same text the pass already logs."""
    return " ".join(f"{r.name}={r.outcome}" for r in result.reports)


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------

NEVER_SYNCED = "Kalshi catalog has never synced."


@kalshi_app.command("status")
def kalshi_status(ctx: typer.Context, json_output: bool = _JSON_OPTION) -> None:
    """Catalog counts, settlement watermark, and the awaiting-settlement set.

    Reads the database only (no API call); reports before any sync has run.
    """
    from manta_trading.data.kalshi.historical_status import read_historical_status
    from manta_trading.data.kalshi.status import (
        read_candle_status,
        read_catalog_status,
    )
    from manta_trading.data.kalshi.trade_status import read_trade_status

    settings: Settings = ctx.obj["settings"]
    if not settings.timescale_db_url:
        print_error("MT_TIMESCALE_DB_URL not configured.", json_mode=json_output)
        raise typer.Exit(EXIT_PREFLIGHT)
    try:
        with psycopg.connect(
            str(settings.timescale_db_url), connect_timeout=DB_CONNECT_TIMEOUT_SECONDS
        ) as conn:
            status = read_catalog_status(conn)
            # The rule in force comes from the same Settings the pass reads
            # (264 Decision 2), so collection and reporting cannot disagree.
            rule = settings.collection_rule()
            candles = read_candle_status(conn, rule)
            trades = read_trade_status(
                conn, rule, settings.kalshi_trades_excluded_categories
            )
            historical = read_historical_status(conn)
    except psycopg.OperationalError as exc:
        print_error(f"database unreachable: {exc}", json_mode=json_output)
        raise typer.Exit(EXIT_PREFLIGHT) from exc
    if status is None:
        print_result(
            {"synced": False} if json_output else NEVER_SYNCED, json_mode=json_output
        )
        raise typer.Exit(EXIT_OK)
    if json_output:
        payload = {"synced": True, **status.to_dict()}
        payload["candles"] = candles.to_dict() if candles is not None else None
        payload["trades"] = trades.to_dict() if trades is not None else None
        # The behind-cutoff count is read once, in the candle block.
        payload["historical"] = (
            {
                **historical.to_dict(),
                "behind_cutoff_candles_remaining": (
                    candles.behind_cutoff_uncollected if candles is not None else None
                ),
            }
            if historical is not None
            else None
        )
        print_result(payload, json_mode=True)
    else:
        print_status(status, datetime.now(UTC), candles, trades, historical)
    raise typer.Exit(EXIT_OK)
