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
from manta_trading.data.kalshi.collection_pass import PassPhaseName
from manta_trading.data.kalshi.constants import KALSHI_SETTLEMENT_STUCK_AFTER

if TYPE_CHECKING:
    from manta_trading.data.kalshi.collection_pass import PassResult
    from manta_trading.data.kalshi.status import CandleStatus, CatalogStatus
    from manta_trading.data.kalshi.sync_types import SyncOutcome, SyncResult

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


class NoPhaseRendererError(LookupError):
    """A pass reported a phase this module has no summary renderer for."""


#: Per-phase summary renderers, keyed by the phase's registered name.
PHASE_RENDERERS: dict[PassPhaseName, Callable[[dict[str, Any]], None]] = {
    PassPhaseName.CATALOG: print_phase_summary,
    PassPhaseName.CANDLES: print_candle_summary,
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


def print_status(
    status: CatalogStatus, now: datetime, candles: CandleStatus | None
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
        return
    print_candle_status(candles, now)


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
    rprint(f"  rule                {candles.rule.describe()}   (MT_KALSHI_CANDLE_*)")
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


def _when(value: datetime | None, now: datetime) -> str:
    if value is None:
        return "never"
    utc = value.astimezone(UTC)  # psycopg returns the session's zone
    minutes = int((now - utc).total_seconds() // 60)
    return f"{utc:%Y-%m-%d %H:%M:%S} UTC  ({minutes:,} min ago)"
