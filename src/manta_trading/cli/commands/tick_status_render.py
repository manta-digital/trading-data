"""Rich and JSON rendering for ``mt data tick status`` and ``coverage`` (225).

The layout is the LLD's API Contracts. ``--json`` is the result's
``to_dict()`` with ``exit_code``; coverage wraps its per-product results in
``products``.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from rich import print as rprint
from rich.markup import escape

from manta_trading.cli.commands.tick_active_render import active_line_text
from manta_trading.cli.output import make_table, print_result
from manta_trading.data.tick.constants import TickSchema
from manta_trading.data.tick.cross_tier_checks import INACTIVE_CROSS_TIER_CHECKS
from manta_trading.data.tick.cross_tier_result import CrossTierResult
from manta_trading.data.tick.family_report import (
    LOAD_COMPLETE_ONLY,
    FamilyStatus,
    StatisticsFamilyStatus,
)
from manta_trading.data.tick.schema_families import SchemaFamily
from manta_trading.data.tick.statistics_constants import StatType
from manta_trading.data.tick.statistics_reads import VOLUME_FINDINGS
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


def _bucket_lines(buckets: dict[str, int], degraded: int) -> list[str]:
    lines = [
        "    " + "   ".join(f"{_label(s)} {buckets[s.value]}" for s in row)
        for row in _BUCKET_ROWS
    ]
    lines[-1] += f"   (degraded {degraded})"
    return lines


def _caught_up_line(caught_up: bool | None, holed: tuple[Any, ...]) -> str:
    if caught_up is None:
        return "  caught up: n/a (no wanted range)"
    holes = f"; holed {[str(d) for d in holed]}" if holed else ""
    return f"  caught up: {'yes' if caught_up else 'no'}{holes}"


def cross_tier_lines(results: Sequence[CrossTierResult]) -> list[str]:
    """One line per result: verdicts per class, in ``CrossTierResult`` order."""
    return [
        f"  {r.check} ({r.family.value}): agree {r.agree}   under {r.under}"
        f"   over {r.over}   no_trades {r.no_trades}"
        for r in results
    ]


def not_active_lines(checks: Mapping[str, str]) -> list[str]:
    """Each inactive cross-tier check and why (305 criterion 7)."""
    return [f"  cross-tier {name}: not active: {why}" for name, why in checks.items()]


def _family_lines(family: FamilyStatus) -> list[str]:
    """A non-tick family's section: the same buckets, headed load-complete only."""
    schemas = ", ".join(family.schemas)
    lines = [
        f"  {schemas}   {LOAD_COMPLETE_ONLY}   wanted from {family.wanted_start}",
        f"  sessions held: {family.sessions_held}",
        *_bucket_lines(family.buckets, family.degraded),
        _caught_up_line(family.caught_up, family.holed_sessions),
        *cross_tier_lines(family.cross_tier),
    ]
    if isinstance(family, StatisticsFamilyStatus):
        lines.extend(_finding_lines(family))
    return lines


def _finding_lines(family: StatisticsFamilyStatus) -> list[str]:
    """Content findings of the load-complete statistics sessions (303 D7)."""
    if not family.findings:
        return ["  content findings: none"]
    lines = [f"  content findings: {len(family.findings)}"]
    for finding in family.findings:
        missing = ", ".join(
            StatType(t).name.lower() for t in finding.missing_stat_types
        )
        symbol = finding.raw_symbol or finding.instrument_id
        lines.append(f"    {finding.session_date}  {symbol}  missing {missing}")
    return lines


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
    lines.extend(_bucket_lines(p.buckets, p.degraded))
    lines.append(_caught_up_line(p.caught_up, p.holed_sessions))
    lines.extend(
        cross_tier_lines(
            [
                r
                for f in p.families
                if f.family == SchemaFamily.TICK_TIER
                for r in f.cross_tier
            ]
        )
    )
    lines.append(f"  {COMPLETE_NOTE}")
    hidden = (
        f"outrights; {p.spreads_hidden} spreads hidden, --all-instruments to list"
        if p.spreads_hidden
        else "all instruments"
    )
    if not p.contracts and not p.spreads_hidden:
        lines.append("  contracts: none with ingested records yet")
        return [*lines, *_other_family_lines(p)]
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
    return [*lines, *_other_family_lines(p)]


def _other_family_lines(p: ProductStatus) -> list[str]:
    """Sections for every wanted family but the tick tier (printed above), then
    the checks that do not run (only a product with another family has any)."""
    others = [f for f in p.families if f.family != SchemaFamily.TICK_TIER]
    lines = [line for family in others for line in _family_lines(family)]
    return lines + (not_active_lines(INACTIVE_CROSS_TIER_CHECKS) if others else [])


def print_status(status: TickStatus, exit_code: int, *, json_mode: bool) -> None:
    if json_mode:
        print_result(status.to_dict() | {"exit_code": exit_code}, json_mode=True)
        return
    for product in status.products:
        for line in _product_lines(product):
            rprint(escape(line))
        for roll in product.rolls:
            for line in active_line_text(roll):
                rprint(escape(f"  {line}"))


def _volume_lines(result: TickCoverage) -> list[str]:
    """Statistics only: verdict counts per family, then every finding."""
    if result.volume_checks is None:
        return []
    counted = Counter((v.family.value, v.verdict.value) for v in result.volume_checks)
    lines = [
        f"  volume {family}: {verdict} {count}"
        for (family, verdict), count in sorted(counted.items())
    ] or ["  volume: no session complete in statistics and a volume family"]
    for v in result.volume_checks:
        if v.verdict in VOLUME_FINDINGS:
            symbol = v.raw_symbol or v.instrument_id
            lines.append(
                f"  [red]{v.session_date} {symbol} {v.family.value}:"
                f" {v.verdict.value} (traded {v.traded}, cleared {v.cleared})[/red]"
            )
    return lines


def coverage_to_dict(results: Sequence[TickCoverage], exit_code: int) -> dict[str, Any]:
    return {"products": [r.to_dict() for r in results], "exit_code": exit_code}


def print_coverage(
    results: Sequence[TickCoverage], exit_code: int, *, json_mode: bool
) -> None:
    if json_mode:
        print_result(coverage_to_dict(results, exit_code), json_mode=True)
        return
    for result in results:
        # A bar family also compares volume; statistics compares counts only.
        bars = result.schema not in (None, TickSchema.STATISTICS)
        schema = f"  {result.schema}" if bars else ""
        volume_columns = [("ledger vol", ""), ("raw vol", "")] if bars else []
        table = make_table(
            f"{result.product}  {result.calendar_id}{schema}",
            [
                ("session", "cyan"),
                ("condition", ""),
                ("status", "bold"),
                ("units", ""),
                ("ledger", ""),
                ("raw", ""),
                *volume_columns,
                ("check", "bold"),
            ],
        )
        for s in result.sessions:
            volumes = [str(s.ledger_volume), str(s.raw_volume)] if bars else []
            table.add_row(
                str(s.session_date),
                s.condition,
                s.status,
                ",".join(map(str, s.unit_ids)) or "-",
                str(s.ledger),
                str(s.raw),
                *volumes,
                s.check,
            )
        print_result(table, json_mode=False)
        for s in result.sessions:
            for m in s.mismatches:
                extra = (
                    f", volume ledger {m.ledger_volume}, raw {m.raw_volume}"
                    if bars
                    else ""
                )
                kind = "" if m.stat_type is None else f" stat type {m.stat_type}"
                rprint(
                    f"  [red]{s.session_date} instrument {m.instrument_id}{kind}:"
                    f" ledger {m.ledger}, raw {m.raw}{extra}[/red]"
                )
        for line in _volume_lines(result):
            rprint(line)
