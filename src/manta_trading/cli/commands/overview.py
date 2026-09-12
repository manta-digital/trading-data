"""``mt data overview`` — what is running, what ran, and what is fresh.

One screen, answered from the database plus a single HTTPS call for the
credit line. It never shells out: ``systemctl`` and ``journalctl`` are how an
operator investigates, not how they find out whether anything is wrong.

The split here is deliberate. :func:`gather` is the only function that
performs I/O; :func:`build_overview` is pure, taking the gathered facts and
returning what to print. That is what makes the interesting cases — a pass
abandoned by a dead process, two live passes of one kind, a failed run with
its reason — testable without a database or a network.
"""

from __future__ import annotations

import socket
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

import psycopg
import typer
from psycopg import sql

from manta_trading.api.eodhd_account import CreditUsage, fetch_credit_usage
from manta_trading.api.eodhd_sync import redact_token
from manta_trading.constants import (
    DAILY_OHLCV_TABLE,
    KALSHI_CANDLES_TABLE,
    KALSHI_CANDLES_TIME_COLUMN,
    KALSHI_TRADES_TABLE,
    KALSHI_TRADES_TIME_COLUMN,
    MINUTE_OHLCV_TABLE,
)
from manta_trading.data.acquisition.daemon.pass_run_recorder import pid_is_alive
from manta_trading.data.acquisition.pass_runs import (
    PassKind,
    PassRun,
    PassRunOutcome,
    PassRunRepository,
)
from manta_trading.firing_schedule import next_firing_at, schedule_for
from manta_trading.logging import get_logger

_logger = get_logger(__name__)

_JSON_OPTION = typer.Option(False, "--json", help="Emit JSON.")
"""The command's only option (design: no others)."""

CREDITS_NO_KEY = "unavailable (MT_EODHD_API_KEY not configured)"
"""Shown when no API key is configured — a setting, not a fault."""

NEVER_RUN = "never run"
"""Shown for a pass kind with no recorded run at all."""

NO_ACCOUNTING = "never computed — run mt data accounting"
"""Shown when no accounting pass has recorded a universe line."""


def credits_unavailable(reason: str) -> str:
    """The credit line when the account endpoint could not be reached."""
    return f"unavailable ({reason})"


# ---------------------------------------------------------------------------
# Facts (what gather reads)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SourceFreshness:
    """The newest row in one source table."""

    name: str
    newest: datetime | None


@dataclass
class OverviewFacts:
    """Everything :func:`build_overview` needs, and nothing derived."""

    now: datetime
    hostname: str
    open_runs: dict[PassKind, list[PassRun]] = field(default_factory=dict)
    latest_ended: dict[PassKind, PassRun | None] = field(default_factory=dict)
    sources: list[SourceFreshness] = field(default_factory=list)
    credits: CreditUsage | None = None
    credits_error: str | None = None
    minute_firing_days: tuple[int, ...] | None = None


# ---------------------------------------------------------------------------
# The rendered shape (what build_overview returns)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RunningRow:
    """One still-open run of a pass kind."""

    phase: str | None
    done: int | None
    total: int | None
    since: datetime
    progress_at: datetime | None
    hostname: str
    pid: int
    abandoned: bool
    """True when this host owns the row and the process is gone."""


@dataclass(frozen=True)
class LastRun:
    """The most recent ended run of a pass kind."""

    started_at: datetime
    ended_at: datetime
    outcome: PassRunOutcome
    exit_code: int | None
    detail: str | None

    @property
    def failed(self) -> bool:
        return self.outcome is PassRunOutcome.FAILED


@dataclass(frozen=True)
class PassLine:
    """One row of the PASSES block."""

    kind: PassKind
    cadence: str
    running: tuple[RunningRow, ...]
    last: LastRun | None
    next_firing: datetime | None


@dataclass(frozen=True)
class Overview:
    """Everything the renderer prints."""

    now: datetime
    passes: tuple[PassLine, ...]
    sources: tuple[SourceFreshness, ...]
    health_verdict: str | None
    health_at: datetime | None
    credits: CreditUsage | None
    credits_text: str
    universe: str | None
    universe_at: datetime | None


# ---------------------------------------------------------------------------
# I/O
# ---------------------------------------------------------------------------


def _newest(conn: psycopg.Connection[Any], table: str, column: str) -> datetime | None:
    """The newest timestamp in one table, or None when it holds nothing.

    A missing table is not an error here: a database that has never run the
    Kalshi migration track still has a minute universe worth reporting on.
    """
    parts = table.split(".")
    identifier = sql.Identifier(*parts)
    query = sql.SQL("SELECT max({}) FROM {}").format(sql.Identifier(column), identifier)
    try:
        row = conn.execute(query).fetchone()
    except psycopg.errors.UndefinedTable:
        # Same reason as _read: a failed statement poisons the transaction.
        conn.rollback()
        return None
    return row[0] if row else None


class _MissingPassRuns:
    """Tracks whether this gather already reported the table as absent.

    Ten reads over one missing table would otherwise log ten identical
    warnings above the screen the operator is trying to read.
    """

    def __init__(self) -> None:
        self.seen = False

    def note(self) -> None:
        if not self.seen:
            self.seen = True
            _logger.warning(
                "overview: pass_runs does not exist — run `mt data migrate "
                "apply`; reporting no recorded passes"
            )


def _read(
    conn: psycopg.Connection[Any],
    read: Any,
    *,
    default: Any,
    missing: _MissingPassRuns,
) -> Any:
    """Read one fact, treating a missing pass_runs table as "nothing recorded".

    An operator whose database predates migration 055 should see an overview
    saying no pass has run, not a traceback: the sources, credits and
    cadences on the rest of the screen are still true and still useful.

    The rollback matters — a failed statement poisons the transaction, so
    without it every later read would fail too and the whole screen would be
    lost to one missing table.
    """
    try:
        return read()
    except psycopg.errors.UndefinedTable:
        conn.rollback()
        missing.note()
        return default


def read_source_freshness(
    conn: psycopg.Connection[Any],
) -> list[SourceFreshness]:
    """The newest row in each source table (slice 922).

    Shared by ``mt data overview`` and ``mt data status``'s default summary,
    so the two cannot disagree about what "newest" means or which tables
    count as sources.
    """
    return [
        SourceFreshness("minute bars", _newest(conn, MINUTE_OHLCV_TABLE, "time")),
        SourceFreshness("daily bars", _newest(conn, DAILY_OHLCV_TABLE, "time")),
        SourceFreshness(
            "kalshi candles",
            _newest(conn, KALSHI_CANDLES_TABLE, KALSHI_CANDLES_TIME_COLUMN),
        ),
        SourceFreshness(
            "kalshi trades",
            _newest(conn, KALSHI_TRADES_TABLE, KALSHI_TRADES_TIME_COLUMN),
        ),
    ]


def gather(
    conn: psycopg.Connection[Any],
    settings: Any,
    *,
    now: datetime | None = None,
    hostname: str | None = None,
    fetch_credits: Any = fetch_credit_usage,
) -> OverviewFacts:
    """Read every fact the overview shows. The only function here with I/O.

    The credit call is guarded: the overview's job is to report, so a
    provider that will not answer becomes a line saying so rather than a
    failed command.
    """
    at = now or datetime.now(UTC)
    facts = OverviewFacts(
        at,
        hostname if hostname is not None else socket.gethostname(),
        minute_firing_days=getattr(settings, "minute_firing_days", None),
    )
    repo = PassRunRepository(lambda: _borrowed(conn))
    missing = _MissingPassRuns()
    for kind in PassKind:
        facts.open_runs[kind] = _read(
            conn, lambda kind=kind: repo.open_runs(kind), default=[], missing=missing
        )
        facts.latest_ended[kind] = _read(
            conn,
            lambda kind=kind: repo.latest_ended(kind),
            default=None,
            missing=missing,
        )

    facts.sources = read_source_freshness(conn)

    api_key = getattr(settings, "eodhd_api_key", None)
    if not api_key:
        facts.credits_error = CREDITS_NO_KEY
    else:
        try:
            facts.credits = fetch_credits(str(api_key))
        except Exception as exc:  # noqa: BLE001 — reported, never raised
            # Any failure to reach the account endpoint becomes a line the
            # operator can read. The rest of the overview is still true.
            #
            # Redacted here as well as at the raise site: this text is
            # printed on a screen meant for pasting into an issue and
            # written to the journal, so it must not carry a token even if
            # some future raiser forgets (922 review F001).
            reason = redact_token(str(exc))
            _logger.warning("overview: credit lookup failed: %s", reason)
            facts.credits_error = credits_unavailable(reason)
    return facts


class _borrowed:
    """Lend an already-open connection to the repository for one statement.

    ``gather`` holds the caller's connection; the repository wants a context
    manager. This yields the same connection and never closes it.
    """

    def __init__(self, conn: psycopg.Connection[Any]) -> None:
        self._conn = conn

    def __enter__(self) -> psycopg.Connection[Any]:
        return self._conn

    def __exit__(self, *_exc: object) -> Literal[False]:
        # Never True: this must not swallow an exception the caller should see.
        return False


# ---------------------------------------------------------------------------
# Pure build
# ---------------------------------------------------------------------------


def build_overview(
    facts: OverviewFacts,
    *,
    pid_alive: Any = pid_is_alive,
) -> Overview:
    """Turn gathered facts into what the renderer prints. No I/O.

    ``pid_alive`` decides whether an open row on this host has been
    abandoned; it is only consulted for rows this host owns, because a pid on
    another machine says nothing about a process here.
    """
    passes = tuple(_pass_line(kind, facts, pid_alive=pid_alive) for kind in PassKind)
    health = facts.latest_ended.get(PassKind.HEALTH)
    accounting = facts.latest_ended.get(PassKind.ACCOUNTING)
    return Overview(
        now=facts.now,
        passes=passes,
        sources=tuple(facts.sources),
        health_verdict=health.detail if health is not None else None,
        health_at=health.ended_at if health is not None else None,
        credits=facts.credits,
        credits_text=_credits_text(facts),
        universe=accounting.detail if accounting is not None else None,
        universe_at=accounting.ended_at if accounting is not None else None,
    )


def _pass_line(kind: PassKind, facts: OverviewFacts, *, pid_alive: Any) -> PassLine:
    schedule = schedule_for(kind, facts.minute_firing_days)
    try:
        next_firing: datetime | None = next_firing_at(facts.now, schedule)
    except RuntimeError:
        # A schedule that can never fire: report no next firing rather than
        # failing the whole overview over one cadence.
        next_firing = None
    return PassLine(
        kind=kind,
        cadence=schedule.describe(),
        running=tuple(
            _running_row(run, facts.hostname, pid_alive)
            for run in facts.open_runs.get(kind, ())
        ),
        last=_last_run(facts.latest_ended.get(kind)),
        next_firing=next_firing,
    )


def _running_row(run: PassRun, hostname: str, pid_alive: Any) -> RunningRow:
    mine = run.hostname == hostname
    return RunningRow(
        phase=run.phase,
        done=run.progress_done,
        total=run.progress_total,
        since=run.started_at,
        progress_at=run.progress_updated_at,
        hostname=run.hostname,
        pid=run.pid,
        abandoned=mine and not pid_alive(run.pid),
    )


def _last_run(run: PassRun | None) -> LastRun | None:
    if run is None or run.ended_at is None or run.outcome is None:
        return None
    return LastRun(
        started_at=run.started_at,
        ended_at=run.ended_at,
        outcome=run.outcome,
        exit_code=run.exit_code,
        detail=run.detail,
    )


def _credits_text(facts: OverviewFacts) -> str:
    if facts.credits_error is not None:
        return facts.credits_error
    usage = facts.credits
    if usage is None:
        return credits_unavailable("no reading")
    extra = f" (extra {usage.extra:,})" if usage.extra else ""
    return f"{usage.used:,} / {usage.daily_limit:,} used today{extra}"


# ---------------------------------------------------------------------------
# The command
# ---------------------------------------------------------------------------


def data_overview(
    ctx: typer.Context,
    json_output: bool = _JSON_OPTION,
) -> None:
    """One screen: what is running, what ran, and what is fresh.

    Exit 0 whenever the database answered — including when the credit
    endpoint did not, which shows as a line rather than a failure. Exit 2
    when the database could not be reached, because then nothing on the
    screen would be true.
    """
    from manta_trading.cli.commands.health import EXIT_UNAVAILABLE
    from manta_trading.cli.output import print_error, print_result
    from manta_trading.cli.rendering.overview import (
        overview_payload,
        render_overview,
    )
    from manta_trading.data.kalshi.constants import DB_CONNECT_TIMEOUT_SECONDS

    settings = ctx.obj["settings"]
    if not settings.timescale_db_url:
        print_error("MT_TIMESCALE_DB_URL must be configured.", json_mode=json_output)
        raise typer.Exit(EXIT_UNAVAILABLE)
    try:
        with psycopg.connect(
            str(settings.timescale_db_url),
            connect_timeout=DB_CONNECT_TIMEOUT_SECONDS,
        ) as conn:
            facts = gather(conn, settings)
    except (psycopg.OperationalError, psycopg.errors.QueryCanceled) as exc:
        print_error(f"overview could not run: {exc}", json_mode=json_output)
        raise typer.Exit(EXIT_UNAVAILABLE) from exc

    overview = build_overview(facts)
    if json_output:
        print_result(overview_payload(overview), json_mode=True)
        return
    # Written straight to stdout rather than through Rich: the screen is
    # already aligned to fixed columns, and Rich would rewrap it to the
    # terminal width and treat square brackets as markup.
    sys.stdout.write(render_overview(overview) + "\n")
