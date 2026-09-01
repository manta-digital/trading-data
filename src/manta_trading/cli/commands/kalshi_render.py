"""Rich rendering for ``mt data kalshi`` (slice 263, Task 4.1).

Extracted from ``kalshi.py`` when the ``pass`` command pushed that module
past the project's ~300-line guideline. Presentation only: every exit-code
integer stays in ``kalshi.py`` and is passed in, so nothing here imports
the exit-code mapping back (no cycle, one definition).

``print_phase_summary`` renders a catalog run's counts from its
``SyncResult.to_dict()`` mapping — that is the single implementation of the
catalog block, printed by ``sync`` directly and by ``pass`` for its catalog
phase. ``pass`` looks each phase's renderer up by ``PassPhaseName``
(``PHASE_RENDERERS``); a phase without one fails loudly rather than printing
nothing, so a new phase cannot ship invisible (slice 264, Task 5.4).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from manta_trading.cli.output import make_table, print_result
from manta_trading.config import KALSHI_COLLECTION_ENV_PREFIX
from manta_trading.data.kalshi.collection_pass import PassPhaseName
from manta_trading.data.kalshi.constants import (
    KALSHI_SETTLEMENT_STUCK_AFTER,
    TRADE_LAG_STALE_AFTER,
)

if TYPE_CHECKING:
    from manta_trading.data.kalshi.collection_pass import PassResult
    from manta_trading.data.kalshi.historical_status import HistoricalStatus
    from manta_trading.data.kalshi.status import CandleStatus, CatalogStatus
    from manta_trading.data.kalshi.sync_types import SyncOutcome, SyncResult
    from manta_trading.data.kalshi.trade_status import TradeStatus

#: Column layout of the catalog counts table, defined once.
_CATALOG_COLUMNS = [
    ("Phase", "cyan"),
    ("Fetched", ""),
    ("Written", ""),
    ("Unchanged", ""),
    ("Skipped", ""),
]


def print_summary(
    result: SyncResult, outcome: SyncOutcome, exit_code: int, json_output: bool
) -> None:
    """The ``sync`` summary: the catalog block plus its outcome line."""
    from rich import print as rprint

    if json_output:
        payload: dict[str, Any] = {
            **result.to_dict(),
            "outcome": str(outcome),
            "exit_code": exit_code,
        }
        print_result(payload, json_mode=True)
        return
    print_phase_summary(result.to_dict())
    rprint(
        f"  item errors   {len(result.item_errors):,}    "
        f"duration {result.duration_ms} ms    "
        f"outcome [bold]{outcome}[/bold] (exit {exit_code})"
    )


def print_phase_summary(summary: dict[str, Any]) -> None:
    """A catalog run's counts, from its ``SyncResult.to_dict()`` mapping."""
    from rich import print as rprint

    table = make_table("Kalshi catalog sync", _CATALOG_COLUMNS)
    for phase, counts in summary["phases"].items():
        table.add_row(
            phase,
            f"{counts['fetched']:,}",
            f"{counts['written']:,}",
            f"{counts['unchanged']:,}",
            f"{counts['skipped']:,}",
        )
    print_result(table, json_mode=False)
    transitions = ", ".join(f"{k} {n:,}" for k, n in summary["transitions"].items())
    awaiting = summary["awaiting"]
    rprint(f"  transitions   {transitions or 'none'}")
    rprint(
        f"  settled       windows {summary['windows_completed']}  "
        f"captured {summary['settled_captured']:,}  "
        f"watermark → {summary['watermark_ts'] or 'unset'}"
    )
    rprint(
        f"  awaiting      entered {awaiting['entered']:,}  retired "
        f"{awaiting['retired']:,}  checked {awaiting['checked']:,}  "
        f"unreachable {awaiting['unreachable']:,}"
    )


def print_candle_summary(summary: dict[str, Any]) -> None:
    """A candle phase's counts, from its ``CandleResult.to_dict()`` mapping."""
    from rich import print as rprint

    pending = summary["pending"]
    rprint("[bold]Kalshi candles[/bold]")
    rprint(
        f"  requests      {summary['requests']:,}    markets requested "
        f"{summary['markets_requested']:,}  advanced {summary['markets_advanced']:,}"
    )
    rprint(
        f"  candles       fetched {summary['candles_fetched']:,}  "
        f"written {summary['candles_written']:,}"
    )
    rprint(
        f"  pending       live {pending['live']:,}  "
        f"finishing {pending['finishing']:,}  backlog {pending['backlog']:,} "
        f"(remaining {pending['backlog_remaining']:,})"
    )
    rprint(
        f"  item errors   {len(summary['item_errors']):,}    "
        f"cutoff {summary['cutoff'] or 'unset'}"
    )


def print_trade_summary(summary: dict[str, Any]) -> None:
    """A trades phase's counts, from its ``TradeResult.to_dict()`` mapping.

    ``requests`` and ``capped`` share one line — the supervised firing's
    stdout lands in the journal, and the deploy check greps that line for
    the cap's only production observation.
    """
    from rich import print as rprint

    watermark = summary["watermark"]
    capped = " (capped)" if summary["capped"] else ""
    rprint("[bold]Kalshi trades[/bold]")
    rprint(
        f"  windows       {summary['windows_completed']:,}    "
        f"requests {summary['requests']:,}{capped}"
    )
    rprint(
        f"  watermark     {watermark['before'] or 'unset'} → "
        f"{watermark['after'] or 'unset'}"
    )
    rprint(
        f"  trades        fetched {summary['trades_fetched']:,}  "
        f"written {summary['trades_written']:,}  "
        f"unknown {summary['unknown_market']:,}  "
        f"excluded {summary['excluded_by_rule']:,}  "
        f"duplicates {summary['duplicates']:,}"
    )
    note = "    no completed catalog walk" if summary["catalog_missing"] else ""
    rprint(
        f"  cutoff        {summary['cutoff'] or 'unset'}    "
        f"coverage from {summary['coverage_from'] or 'unset'}{note}"
    )


def print_historical_summary(summary: dict[str, Any]) -> None:
    """A historical phase's counts, from ``HistoricalResult.to_dict()``.

    ``requests`` and the cap share the first line — the deploy check greps
    it for the cap's production observation (267 Criterion 6).
    """
    from rich import print as rprint

    archive, candles, watermark = (
        summary["archive"],
        summary["candles"],
        summary["watermark"],
    )
    capped = " (capped)" if summary["capped"] else ""
    rprint("[bold]Kalshi historical[/bold]")
    rprint(f"  requests      {summary['requests']:,} / cap {summary['cap']:,}{capped}")
    if archive["walked"]:
        walk = (
            "walked"
            if archive["pages"] == 0
            else (
                f"walked: pages {archive['pages']:,} · markets "
                f"{archive['markets_fetched']:,} "
                f"(written {archive['markets_written']:,})"
            )
        )
    else:
        walk = (
            f"pages {archive['pages']:,} · markets {archive['markets_fetched']:,} "
            "· cursor saved"
        )
    restarted = " · restarted" if archive["restarted"] else ""
    rprint(f"  archive       {walk}{restarted}")
    rprint(
        f"  candles       markets completed {candles['markets_completed']:,} · "
        f"requests {candles['requests']:,} · candles written "
        f"{candles['candles_written']:,} · remaining {candles['markets_remaining']:,}"
        f" · slow {candles['slow_markets']:,}"
    )
    floor = "  floor reached" if summary["floor_reached"] else ""
    rprint(
        f"  watermark     {watermark['before'] or 'unset'} → "
        f"{watermark['after'] or 'unset'}{floor}    (floor {summary['floor']})"
    )
    rprint(
        f"  trades        fetched {summary['trades_fetched']:,}  "
        f"written {summary['trades_written']:,}  "
        f"unknown {summary['unknown_market']:,}  "
        f"excluded {summary['excluded_by_rule']:,}  "
        f"duplicates {summary['duplicates']:,}"
    )
    rprint(f"  item errors   {len(summary['item_errors']):,}")
    for error in summary["item_errors"]:
        rprint(f"    {error['ticker']}: {error['reason']}")
    if summary["trades_row_missing"]:
        rprint("    no live trades row: the tape floor is seeded from it")


class NoPhaseRendererError(LookupError):
    """A pass reported a phase this module has no summary renderer for."""


#: Per-phase summary renderers, keyed by the phase's registered name.
PHASE_RENDERERS: dict[PassPhaseName, Callable[[dict[str, Any]], None]] = {
    PassPhaseName.CATALOG: print_phase_summary,
    PassPhaseName.CANDLES: print_candle_summary,
    PassPhaseName.TRADES: print_trade_summary,
    PassPhaseName.HISTORICAL: print_historical_summary,
}


def render_phase_summary(name: PassPhaseName, summary: dict[str, Any]) -> None:
    """Render one phase's summary with its registered renderer, loudly
    refusing a phase nobody registered — silently skipping is how a phase
    would ship invisible."""
    renderer = PHASE_RENDERERS.get(name)
    if renderer is None:
        raise NoPhaseRendererError(f"no summary renderer registered for phase {name!r}")
    renderer(summary)


def print_pass_summary(result: PassResult, exit_code: int, json_output: bool) -> None:
    """The ``pass`` summary: one row per phase, then each phase's own block."""
    from rich import print as rprint

    if json_output:
        print_result({**result.to_dict(), "exit_code": exit_code}, json_mode=True)
        return
    table = make_table(
        "Kalshi collection pass",
        [("Phase", "cyan"), ("Outcome", ""), ("Duration", "")],
    )
    for report in result.reports:
        table.add_row(
            str(report.name), str(report.outcome), f"{report.duration_ms:,} ms"
        )
    print_result(table, json_mode=False)
    for report in result.reports:
        if report.summary:
            render_phase_summary(report.name, report.summary)
    rprint(
        f"  pass          outcome [bold]{result.outcome}[/bold] "
        f"(exit {exit_code})    duration {result.duration_ms} ms"
    )


NEVER_COLLECTED = "Candlesticks: never collected"
NEVER_COLLECTED_TRADES = "Trades: never collected"
NEVER_RUN_HISTORICAL = "Historical: never run"


def print_status(
    status: CatalogStatus,
    now: datetime,
    candles: CandleStatus | None,
    trades: TradeStatus | None,
    historical: HistoricalStatus | None,
) -> None:
    from rich import print as rprint

    from manta_trading.data.kalshi.status import age_bucket_labels

    awaiting = status.awaiting
    by_status = " · ".join(
        f"{s.value} {n:,}" for s, n in status.markets_by_status.items()
    )
    histogram = " · ".join(
        f"{label} {n:,}"
        for label, n in zip(age_bucket_labels(), awaiting.age_histogram, strict=True)
    )
    oldest = (
        f"{awaiting.oldest_ticker} ({awaiting.oldest_age.days:,} d)"
        if awaiting.oldest_ticker and awaiting.oldest_age is not None
        else "none"
    )
    rprint("[bold]Kalshi catalog[/bold]")
    rprint(f"  last full sync      {_when(status.last_full_sync_at, now)}")
    rprint(f"  settled watermark   {_when(status.watermark_ts, now)}")
    rprint(f"  series / events     {status.series:,} / {status.events:,}")
    rprint(f"[bold]Markets by status[/bold]     {by_status}")
    rprint(f"[bold]Awaiting settlement[/bold]   {awaiting.total:,} markets")
    rprint(f"  age                 {histogram}")
    rprint(
        f"  past {KALSHI_SETTLEMENT_STUCK_AFTER.days}d threshold   "
        f"{awaiting.past_threshold:,}   oldest {oldest}"
    )
    rprint(
        f"  checked directly    {awaiting.checked_directly:,}  "
        "(looked up by ticker; still unsettled)"
    )
    if candles is None:
        rprint(f"[bold]{NEVER_COLLECTED}[/bold]")
    else:
        print_candle_status(candles, now)
    if trades is None:
        rprint(f"[bold]{NEVER_COLLECTED_TRADES}[/bold]")
    else:
        print_trade_status(trades, now)
    if historical is None:
        rprint(f"[bold]{NEVER_RUN_HISTORICAL}[/bold]")
    else:
        # The behind-cutoff count is read once, in the candle block.
        remaining = candles.behind_cutoff_uncollected if candles else None
        print_historical_status(historical, now, remaining)


def print_candle_status(candles: CandleStatus, now: datetime) -> None:
    """The candle block (design *CLI and rendering*), one line per fact."""
    from rich import print as rprint

    cutoff = candles.cutoff_observed
    cutoff_text = f"{cutoff.astimezone(UTC):%Y-%m-%d}" if cutoff else "unset"
    oldest = candles.open_oldest_watermark
    oldest_text = (
        f" (oldest watermark {oldest.astimezone(UTC):%Y-%m-%d %H:%M} UTC)"
        if oldest
        else ""
    )
    rprint(
        f"[bold]Kalshi candlesticks[/bold]   period {candles.period_minutes} min   "
        f"last phase {_when(candles.last_phase_at, now)}   cutoff {cutoff_text}"
    )
    rprint(
        f"  rule                {candles.rule.describe()}   "
        f"({KALSHI_COLLECTION_ENV_PREFIX}*)"
    )
    rprint(f"  selected open       {candles.selected_open:,}")
    rprint(
        f"  tracked             {candles.markets_tracked:,} markets   "
        f"complete through close {candles.complete_through_close:,}   "
        f"partial history {candles.partial_history:,}"
    )
    rprint(f"  open lagging        {candles.open_lagging:,}{oldest_text}")
    rprint(
        f"  short of close      {candles.closed_short_of_close:,}        "
        f"backlog remaining {candles.backlog_remaining:,}        "
        f"behind cutoff, uncollected {candles.behind_cutoff_uncollected:,}"
    )
    rprint(
        f"  excluded by rule    {candles.closed_excluded_by_rule:,} closed markets "
        "(never traded, or an excluded category or pattern)"
    )


def print_trade_status(trades: TradeStatus, now: datetime) -> None:
    """The trades block (design *CLI and rendering*), one line per fact."""
    from rich import print as rprint

    lag_minutes = int(trades.lag.total_seconds() // 60)
    stale_minutes = int(TRADE_LAG_STALE_AFTER.total_seconds() // 60)
    behind = f"; behind, past {stale_minutes:,} min" if trades.behind else ""
    rprint(
        f"[bold]Kalshi trades[/bold]              "
        f"last phase {_when(trades.last_phase_at, now)}"
    )
    through = f"{trades.tape_through.astimezone(UTC):%Y-%m-%d %H:%M:%S} UTC"
    coverage = f"{trades.coverage_from.astimezone(UTC):%Y-%m-%d %H:%M} UTC"
    rprint(
        f"  tape through        {through}  ({lag_minutes:,} min behind{behind})"
        f"        coverage from {coverage}"
    )
    rprint(
        f"  closed markets      complete through close "
        f"{trades.complete_through_close:,}   "
        f"partial history {trades.partial_history:,}   "
        f"short of close {trades.short_of_close:,}"
    )
    rprint(
        f"  before coverage     {trades.before_coverage:,} closed markets "
        f"(closed before the effective floor {coverage})"
    )


def print_historical_status(
    historical: HistoricalStatus, now: datetime, remaining: int | None
) -> None:
    """The historical line (design *Implementation Details*): the tape range
    walking down, the floor, the behind-cutoff candles left, the last phase."""
    from rich import print as rprint

    def day(value: datetime | None) -> str:
        return f"{value.astimezone(UTC):%Y-%m-%d}" if value else "unset"

    floor = day(historical.floor)
    if historical.archive_in_progress:
        tape = "archive walk in progress"
    elif historical.floor_reached:
        tape = f"floor reached ({floor})"
    else:
        tape = (
            f"{day(historical.tape_to)} → {day(historical.tape_from)} (floor {floor})"
        )
    left = f"{remaining:,}" if remaining is not None else "n/a"
    rprint(
        f"[bold]Kalshi historical[/bold]          tape {tape} · behind-cutoff "
        f"candles remaining {left} · last phase {_when(historical.last_phase_at, now)}"
    )


def _when(value: datetime | None, now: datetime) -> str:
    if value is None:
        return "never"
    utc = value.astimezone(UTC)  # psycopg returns the session's zone
    minutes = int((now - utc).total_seconds() // 60)
    return f"{utc:%Y-%m-%d %H:%M:%S} UTC  ({minutes:,} min ago)"
