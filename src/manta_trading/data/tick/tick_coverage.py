"""``build_coverage``: the raw-count proof for a date range (slice 225, TD10).

For each session dated ``[start, end)``: its status and condition (TD9), the
current units' ledger sum, the raw ``tick_trade`` count (the one scan, bounded
by the range) and the check. Raw rows change after commit only through
supersession (whose ledger is excluded) or by hand, so a mismatch is evidence
of an out-of-band edit or a defect; the CLI exits 3 on one.
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from typing import Any

from manta_trading.data.tick.constants import calendar_for_product
from manta_trading.data.tick.ingest_records import utc_ns
from manta_trading.data.tick.manifest_reads import Conn
from manta_trading.data.tick.session_days import utc_midnight
from manta_trading.data.tick.status_reads import ledger_totals, raw_counts
from manta_trading.data.tick.tick_status import SessionVerdict
from manta_trading.data.tick.tick_status_build import (
    calendar_sessions,
    jsonable,
    session_verdicts,
    shape_of,
)
from manta_trading.data.tick.universe import TickUniverseEntry

MISMATCH, OK = "mismatch", "ok"


@dataclass(frozen=True)
class Mismatch:
    instrument_id: int
    ledger: int
    raw: int


@dataclass(frozen=True)
class CoverageSession:
    session_date: date
    condition: str
    status: str
    unit_ids: tuple[int, ...]
    ledger: int
    raw: int
    mismatches: tuple[Mismatch, ...]

    @property
    def check(self) -> str:
        return MISMATCH if self.mismatches else OK


@dataclass(frozen=True)
class TickCoverage:
    product: str
    calendar_id: str
    start: date
    end: date
    sessions: tuple[CoverageSession, ...]

    @property
    def mismatched(self) -> bool:
        return any(s.mismatches for s in self.sessions)

    def to_dict(self) -> dict[str, Any]:
        body = jsonable(asdict(self))
        for session, out in zip(self.sessions, body["sessions"], strict=True):
            out["check"] = session.check
        return body | {"mismatched": self.mismatched}


def _session_row(
    v: SessionVerdict,
    ledger: dict[tuple[int, date], int],
    raw: dict[tuple[int, date], int],
) -> CoverageSession:
    day = v.session_date
    instruments = sorted({i for i, d in (*ledger, *raw) if d == day})
    pairs = [(i, ledger.get((i, day), 0), raw.get((i, day), 0)) for i in instruments]
    return CoverageSession(
        session_date=day,
        condition=v.condition,
        status=v.status.value,
        unit_ids=tuple(sorted({f.unit.unit_id for f in v.days if f.unit})),
        ledger=sum(p[1] for p in pairs),
        raw=sum(p[2] for p in pairs),
        mismatches=tuple(Mismatch(*p) for p in pairs if p[1] != p[2]),
    )


async def build_coverage(
    tick_conn: Conn, calendar_url: str, entry: TickUniverseEntry, start: date, end: date
) -> TickCoverage:
    """Sessions dated ``[start, end)``: status, ledger sum, raw count, check."""
    calendar_id = calendar_for_product(entry.product)
    found = await asyncio.to_thread(
        calendar_sessions,
        calendar_url,
        calendar_id,
        utc_midnight(start - timedelta(days=1)),
        utc_midnight(end),
    )
    sessions = [s for s in found if start <= s.session_date < end]
    verdicts = await session_verdicts(tick_conn, entry, sessions)
    last = end - timedelta(days=1)
    ledger = await ledger_totals(tick_conn, shape_of(entry), start, last)
    raw = await raw_counts(
        tick_conn,
        [(s.session_date, utc_ns(s.open_utc), utc_ns(s.close_utc)) for s in sessions],
    )
    rows = tuple(_session_row(v, ledger, raw) for v in verdicts)
    return TickCoverage(entry.product, calendar_id, start, end, rows)
