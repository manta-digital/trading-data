"""Rich rendering for ``mt data kalshi`` (slice 263, Task 4.1).

Extracted from ``kalshi.py`` when the ``pass`` command pushed that module
past the project's ~300-line guideline. Presentation only: every exit-code
integer stays in ``kalshi.py`` and is passed in, so nothing here imports
the exit-code mapping back (no cycle, one definition).

``print_phase_summary`` renders a catalog run's counts from its
``SyncResult.to_dict()`` mapping — that is the single implementation of the
catalog block, printed by ``sync`` directly and by ``pass`` once per phase
that produced one.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from manta_trading.cli.output import make_table, print_result
from manta_trading.data.kalshi.constants import KALSHI_SETTLEMENT_STUCK_AFTER

if TYPE_CHECKING:
    from manta_trading.data.kalshi.collection_pass import PassResult
    from manta_trading.data.kalshi.status import CatalogStatus
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
            print_phase_summary(report.summary)
    rprint(
        f"  pass          outcome [bold]{result.outcome}[/bold] "
        f"(exit {exit_code})    duration {result.duration_ms} ms"
    )


def print_status(status: CatalogStatus, now: datetime) -> None:
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


def _when(value: datetime | None, now: datetime) -> str:
    if value is None:
        return "never"
    utc = value.astimezone(UTC)  # psycopg returns the session's zone
    minutes = int((now - utc).total_seconds() // 60)
    return f"{utc:%Y-%m-%d %H:%M:%S} UTC  ({minutes:,} min ago)"
