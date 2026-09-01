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

The ``status`` blocks live in ``kalshi_status_render.py``, extracted when the
historical renderers pushed this module past the guideline again.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from manta_trading.cli.output import make_table, print_result
from manta_trading.data.kalshi.collection_pass import PassPhaseName

if TYPE_CHECKING:
    from manta_trading.data.kalshi.collection_pass import PassResult
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


def _capped_suffix(summary: dict[str, Any]) -> str:
    return " (capped)" if summary["capped"] else ""


def _trade_counts_line(summary: dict[str, Any]) -> str:
    """The fetched/written/unknown/excluded/duplicates line — one spelling
    for the live and historical renderers (267 code review, DRY)."""
    return (
        f"  trades        fetched {summary['trades_fetched']:,}  "
        f"written {summary['trades_written']:,}  "
        f"unknown {summary['unknown_market']:,}  "
        f"excluded {summary['excluded_by_rule']:,}  "
        f"duplicates {summary['duplicates']:,}"
    )


def print_trade_summary(summary: dict[str, Any]) -> None:
    """A trades phase's counts, from its ``TradeResult.to_dict()`` mapping.

    ``requests`` and ``capped`` share one line — the supervised firing's
    stdout lands in the journal, and the deploy check greps that line for
    the cap's only production observation.
    """
    from rich import print as rprint

    watermark = summary["watermark"]
    rprint("[bold]Kalshi trades[/bold]")
    rprint(
        f"  windows       {summary['windows_completed']:,}    "
        f"requests {summary['requests']:,}{_capped_suffix(summary)}"
    )
    rprint(
        f"  watermark     {watermark['before'] or 'unset'} → "
        f"{watermark['after'] or 'unset'}"
    )
    rprint(_trade_counts_line(summary))
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
    rprint("[bold]Kalshi historical[/bold]")
    rprint(
        f"  requests      {summary['requests']:,} / cap "
        f"{summary['cap']:,}{_capped_suffix(summary)}"
    )
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
    rprint(_trade_counts_line(summary))
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
