"""The per-session rows of a coverage result (slices 225, 302, 303).

Split from ``tick_coverage`` so the statistics comparison can build rows
without importing the module that dispatches to it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

MISMATCH, OK = "mismatch", "ok"


@dataclass(frozen=True)
class Mismatch:
    instrument_id: int
    ledger: int
    raw: int
    ledger_volume: int | None = None  # bars only
    raw_volume: int | None = None  # bars only
    stat_type: int | None = None  # statistics only


@dataclass(frozen=True)
class CoverageSession:
    session_date: date
    condition: str
    status: str
    unit_ids: tuple[int, ...]
    ledger: int
    raw: int
    mismatches: tuple[Mismatch, ...]
    ledger_volume: int | None = None  # bars only
    raw_volume: int | None = None  # bars only

    @property
    def check(self) -> str:
        return MISMATCH if self.mismatches else OK


#: Keys that exist only on a non-tick family's result: dropped when ``None``.
NON_TICK_ONLY_KEYS = ("ledger_volume", "raw_volume", "stat_type")


def drop_unset_keys(row: dict[str, Any]) -> None:
    for key in NON_TICK_ONLY_KEYS:
        if row.get(key) is None:
            row.pop(key, None)
