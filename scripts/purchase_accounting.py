"""The accounting-document side of the purchase cutover's evidence (307 D6).

Reads ``mt data accounting --json`` (``.tick.products[].families[]``): the
per-family narration lines, the ``traded_vs_cleared`` counts, the ``over``
failure and the record totals before and after. Every key it needs is required;
a missing one raises ``CutoverError`` (never a default).
"""

from __future__ import annotations

import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cutover_common import CutoverError  # noqa: E402

from manta_trading.data.tick.cross_tier_checks import TRADED_VS_CLEARED  # noqa: E402
from manta_trading.data.tick.statistics_reads import VolumeClass  # noqa: E402

CROSS_TIER_COUNTS = (
    VolumeClass.AGREE.value,
    VolumeClass.UNDER.value,
    VolumeClass.OVER.value,
    VolumeClass.NO_TRADES.value,
)


@dataclass(frozen=True)
class FamilyRow:
    product: str
    family: str
    schema: str | None  # None: a tick tier nothing wants
    judged: int
    complete: int
    source_edge: str | None
    caught_up: bool | None
    records: int
    cross_tier: dict[str, int] | None  # the traded_vs_cleared counts, volume families

    @property
    def label(self) -> str:
        return self.schema or self.family


def _cross_tier(line: Mapping[str, Any]) -> dict[str, int] | None:
    for result in line["cross_tier"]:
        if result["check"] == TRADED_VS_CLEARED:
            return {name: result[name] for name in CROSS_TIER_COUNTS}
    return None


def accounting_rows(document: Mapping[str, Any]) -> list[FamilyRow]:
    """One row per product and family; an unavailable tick section is a stop."""
    try:
        tick = document["tick"]
        if not tick["products"]:
            raise CutoverError(
                f"tick accounting is unavailable: {tick.get('state', 'no products')}"
            )
        return [
            FamilyRow(
                product["product"],
                line["family"],
                line["schema"],
                line["sessions_in_scope"],
                line["sessions_complete"],
                line["source_edge"],
                line["caught_up"],
                line["records"],
                _cross_tier(line),
            )
            for product in tick["products"]
            for line in product["families"]
        ]
    except KeyError as exc:
        raise CutoverError(f"accounting output has no {exc} key") from exc


def row_for(rows: Sequence[FamilyRow], product: str, schema: str) -> FamilyRow:
    for row in rows:
        if (row.product, row.schema) == (product, schema):
            return row
    raise CutoverError(f"accounting has no {product} {schema} family")


def narration(rows: Sequence[FamilyRow]) -> list[str]:
    """Item 2 and 4: sessions complete over judged, source edge, caught up, counts."""
    lines = []
    for row in rows:
        line = (
            f"{row.product} {row.label}: sessions {row.complete}/{row.judged},"
            f" source edge {row.source_edge}, caught up {row.caught_up}"
        )
        if row.cross_tier is not None:
            counts = " ".join(f"{k} {v}" for k, v in row.cross_tier.items())
            line += f"; {TRADED_VS_CLEARED}: {counts}"
        lines.append(line)
    return lines


def over_sessions(coverage: Mapping[str, Any]) -> list[str]:
    """Names of the ``over`` sessions in a statistics ``coverage`` document."""
    try:
        return [
            f"{v['session_date']} {v['raw_symbol']} (traded {v['traded']},"
            f" cleared {v['cleared']})"
            for v in coverage.get("volume_checks") or ()
            if v["verdict"] == VolumeClass.OVER.value
        ]
    except KeyError as exc:
        raise CutoverError(f"coverage volume check has no {exc} key") from exc


def over_failures(
    rows: Sequence[FamilyRow], named: Mapping[str, Sequence[str]]
) -> list[str]:
    """An ``over`` session is a defect (305 D5): fail, naming what coverage names."""
    return [
        f"{row.product} {row.label}: {row.cross_tier[VolumeClass.OVER.value]} sessions"
        f" over in {TRADED_VS_CLEARED}: {', '.join(named.get(row.product, ())) or '?'}"
        for row in rows
        if row.cross_tier and row.cross_tier[VolumeClass.OVER.value]
    ]


def record_totals(rows: Sequence[FamilyRow]) -> dict[str, int]:
    """Ledger records per ``product/family``."""
    return {f"{row.product}/{row.family}": row.records for row in rows}


def totals_delta(
    before: Mapping[str, int], after: Mapping[str, int]
) -> dict[str, tuple[int, int, int]]:
    """``(before, after, delta)`` per product and family; a family absent before
    the purchase is a stop, not zero."""
    missing = sorted(set(after) - set(before))
    if missing:
        raise CutoverError(f"the baseline holds no record total for {missing}")
    return {key: (before[key], after[key], after[key] - before[key]) for key in after}
