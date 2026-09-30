"""Rich and JSON rendering for ``mt data tick status`` and ``coverage`` (225).

The layout is the LLD's API Contracts. ``--json`` is the result's
``to_dict()`` with ``exit_code``; coverage wraps its per-product results in
``products``.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from rich import print as rprint
from rich.markup import escape

from manta_trading.cli.output import make_table, print_result
from manta_trading.data.tick.tick_coverage import TickCoverage
from manta_trading.data.tick.tick_status import TickSessionStatus
from manta_trading.data.tick.tick_status_build import ProductStatus, TickStatus

COMPLETE_NOTE = "complete = every unit ingested; raw-count proof: mt data tick coverage"
_BUCKET_ROWS = (
    (
        TickSessionStatus.COMPLETE,
        TickSessionStatus.AWAITING_INGEST,
        TickSessionStatus.IN_FLIGHT,
        TickSessionStatus.PENDING,
        TickSessionStatus.MISSING,
    ),
    (
        TickSessionStatus.FAILED,
        TickSessionStatus.RETRY_EXHAUSTED,
        TickSessionStatus.PROVIDER_HOLE,
        TickSessionStatus.EDGE_UNKNOWN,
    ),
)
assert {s for row in _BUCKET_ROWS for s in row} == set(TickSessionStatus), (
    "the status report omits a bucket — update _BUCKET_ROWS"
)


def age(seconds: float) -> str:
    """``3h 5m`` style; the largest two units."""
    minutes, _ = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)
    if days:
        return f"{days}d {hours}h"
    return f"{hours}h {minutes}m" if hours else f"{minutes}m"


def _label(status: TickSessionStatus) -> str:
    return status.value.replace("_", " ")


def _product_lines(p: ProductStatus) -> list[str]:
    tier = (
        f"tier: {p.tier}, wanted from {p.wanted_start}"
        if p.tier
        else "tier: not configured — no wanted range"
    )
    lines = [
        f"{p.product}  ({p.calendar_id}, {p.dataset}, {', '.join(p.symbols)}"
        f" {p.stype_in})   {tier}"
    ]
    if p.edge_available_end is None or p.edge_age_seconds is None:
        lines.append("  edge: never observed (run mt data tick pass)")
    else:
        edge_end = p.edge_available_end.astimezone(UTC)
        lines.append(
            f"  edge: available to {edge_end:%Y-%m-%d %H:%M} UTC,"
            f" observed {age(p.edge_age_seconds)} ago"
        )
    lines.append(f"  sessions held: {p.sessions_held}")
    for row in _BUCKET_ROWS:
        lines.append(
            "    " + "   ".join(f"{_label(s)} {p.buckets[s.value]}" for s in row)
        )
    lines[-1] += f"   (degraded {p.degraded})"
    if p.caught_up is None:
        lines.append("  caught up: n/a (no wanted range)")
    else:
        holed = (
            f"; holed {[str(d) for d in p.holed_sessions]}" if p.holed_sessions else ""
        )
        lines.append(f"  caught up: {'yes' if p.caught_up else 'no'}{holed}")
    lines.append(f"  {COMPLETE_NOTE}")
    hidden = (
        f"outrights; {p.spreads_hidden} spreads hidden, --all-instruments to list"
        if p.spreads_hidden
        else "all instruments"
    )
    if not p.contracts and not p.spreads_hidden:
        lines.append("  contracts: none with ingested records yet")
        return lines
    lines.append(f"  contracts ({hidden}):")
    for c in p.contracts:
        expiry = (
            "?"
            if c.expiration_ns is None
            else f"{datetime.fromtimestamp(c.expiration_ns / 1e9, UTC):%Y-%m-%d}"
        )
        symbol = c.raw_symbol or c.instrument_id
        lines.append(
            f"    {symbol}   exp {expiry}   sessions {c.sessions}"
            f"   records {c.records}   volume {c.volume}"
            f"   {c.first_session} → {c.last_session}"
        )
    return lines


def print_status(status: TickStatus, exit_code: int, *, json_mode: bool) -> None:
    if json_mode:
        print_result(status.to_dict() | {"exit_code": exit_code}, json_mode=True)
        return
    for product in status.products:
        for line in _product_lines(product):
            rprint(escape(line))


def coverage_to_dict(results: Sequence[TickCoverage], exit_code: int) -> dict[str, Any]:
    return {"products": [r.to_dict() for r in results], "exit_code": exit_code}


def print_coverage(
    results: Sequence[TickCoverage], exit_code: int, *, json_mode: bool
) -> None:
    if json_mode:
        print_result(coverage_to_dict(results, exit_code), json_mode=True)
        return
    for result in results:
        table = make_table(
            f"{result.product}  {result.calendar_id}",
            [
                ("session", "cyan"),
                ("condition", ""),
                ("status", "bold"),
                ("units", ""),
                ("ledger", ""),
                ("raw", ""),
                ("check", "bold"),
            ],
        )
        for s in result.sessions:
            table.add_row(
                str(s.session_date),
                s.condition,
                s.status,
                ",".join(map(str, s.unit_ids)) or "-",
                str(s.ledger),
                str(s.raw),
                s.check,
            )
        print_result(table, json_mode=False)
        for s in result.sessions:
            for m in s.mismatches:
                rprint(
                    f"  [red]{s.session_date} instrument {m.instrument_id}:"
                    f" ledger {m.ledger}, raw {m.raw}[/red]"
                )
