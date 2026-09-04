"""Rich rendering for ``mt data kalshi status`` (extracted from
``kalshi_render.py`` when the historical block pushed that module past the
project's ~300-line guideline — 267 code review). Presentation only, same
contract as its sibling: nothing here imports the exit-code mapping.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from manta_trading.config import KALSHI_COLLECTION_ENV_PREFIX, KALSHI_TRADES_FILTER_ENV
from manta_trading.data.kalshi.constants import (
    KALSHI_SETTLEMENT_STUCK_AFTER,
    TRADE_LAG_STALE_AFTER,
)
from manta_trading.data.kalshi.selection import describe_trades_filter

if TYPE_CHECKING:
    from manta_trading.data.kalshi.historical_status import HistoricalStatus
    from manta_trading.data.kalshi.status import CandleStatus, CatalogStatus
    from manta_trading.data.kalshi.trade_status import TradeStatus

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
    rprint(
        f"  trades filter       "
        f"{describe_trades_filter(trades.excluded_categories)} "
        f"({KALSHI_TRADES_FILTER_ENV})"
    )
    if trades.excluded_categories:
        rprint(
            f"                      tape-filtered {trades.tape_filtered_markets:,} "
            "closed markets (stored history kept; completeness not evaluated)"
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
