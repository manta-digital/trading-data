"""The futures tick line in ``mt data accounting`` (slice 230, TD7, FR12).

One :class:`TickAccountingLine` per universe product. The session figures are
``status``'s own buckets, from the same function (``product_scope``), so the
two cannot disagree: the sessions in scope are the wanted range plus every
session a current tier unit touches, as ``status`` counts them. Records are
the ledger sum over current units. No CLI import: 231's ``/api/v1/status``
reuses it.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from manta_trading.data.tick.constants import CME_DATASET
from manta_trading.data.tick.manifest_reads import Conn
from manta_trading.data.tick.status_reads import dataset_edge, ledger_totals, shape_of
from manta_trading.data.tick.tick_status import TickSessionStatus
from manta_trading.data.tick.tick_status_build import bucket_counts, product_scope
from manta_trading.data.tick.universe import TickUniverseEntry


@dataclass(frozen=True)
class TickAccountingLine:
    product: str
    sessions_in_scope: int
    sessions_complete: int
    buckets: dict[str, int]
    records: int


async def accounting_lines(
    conn: Conn, calendar_url: str, universe: Sequence[TickUniverseEntry]
) -> list[TickAccountingLine]:
    edge = await dataset_edge(conn, CME_DATASET)
    lines = []
    for entry in universe:
        scope = await product_scope(conn, calendar_url, entry, edge)
        buckets = bucket_counts(scope.verdicts)
        totals = await ledger_totals(conn, shape_of(entry), date.min, date.max)
        lines.append(
            TickAccountingLine(
                product=entry.product,
                sessions_in_scope=len(scope.verdicts),
                sessions_complete=buckets[TickSessionStatus.COMPLETE.value],
                buckets=buckets,
                records=sum(totals.values()),
            )
        )
    return lines
