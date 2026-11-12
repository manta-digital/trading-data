"""The columnar ``/ticks`` body (slice 308 D6).

One block per non-empty window (session × contract), in plan order, with the
session, symbol and instrument once per block and every other field a column.
``ts_base`` is the block's first ``ts_event`` as decimal text (epoch ns exceed
2^53); ``ts_event[i]`` is the delta from tick ``i - 1`` (``ts_event[0]`` is 0),
so a prefix sum from ``ts_base`` restores every timestamp. Prices are DBN
fixed-point integers, value × 10^-``price_scale``; an undefined price is
``None``. ``side`` is one character per tick. The stream key (``ts_event``,
``sequence``, ``sequence_ordinal``) is on every tick. ``bid_ct``/``ask_ct`` are
not on the wire.

``FuturesTickBlock``, not ``TickBlock``: that name is the status tick block
(``futures.py``), and two models of one name would rename both OpenAPI
components.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime
from itertools import groupby
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from manta_trading.api_server.models.futures import (
    FuturesTarget,
    IncompleteRecord,
    SegmentRecord,
    UnresolvedRecord,
    plan_envelope,
)
from manta_trading.data.tick.dbn_values import UNDEF_INT64
from manta_trading.data.tick.get_plan import GetPlan
from manta_trading.data.tick.tick_format import PRICE_DIGITS
from manta_trading.data.tick.tick_reads import TICK_COLUMNS, Row

_AT = {name: i for i, name in enumerate(TICK_COLUMNS)}


def _defined(values: Sequence[int | None]) -> list[int | None]:
    """A price column with the provider's "undefined" as ``None``."""
    return [None if v == UNDEF_INT64 else v for v in values]


class FuturesTickBlock(BaseModel):
    """One window's ticks, columnar (D6). Every column holds ``count`` values;
    the quote columns hold ``None`` on trades-tier ticks and are never omitted."""

    session_date: date
    raw_symbol: str
    instrument_id: int
    count: int
    ts_base: str = Field(description="The first tick's ts_event, epoch ns as text.")
    ts_event: list[int] = Field(
        description="Delta in ns from the previous tick; the first is 0."
    )
    sequence: list[int]
    sequence_ordinal: list[int]
    price: list[int | None]
    size: list[int]
    side: str = Field(description="One character per tick: B, A or N.")
    bid_px: list[int | None]
    ask_px: list[int | None]
    bid_sz: list[int | None]
    ask_sz: list[int | None]

    @classmethod
    def from_rows(cls, rows: Sequence[Row]) -> list[FuturesTickBlock]:
        """Consecutive rows of one (session, instrument) make one block."""
        return [cls(**fields) for fields in block_fields(rows)]


def block_fields(rows: Sequence[Row]) -> list[dict[str, Any]]:
    """Each block's fields, pivoted from consecutive rows of one (session,
    instrument): the whole pivot, before validation."""
    session, instrument = _AT["session_date"], _AT["instrument_id"]
    return [
        _pivot(list(run))
        for _, run in groupby(rows, key=lambda r: (r[session], r[instrument]))
    ]


def _pivot(rows: list[Row]) -> dict[str, Any]:
    col = dict(zip(TICK_COLUMNS, zip(*rows, strict=True), strict=True))
    ts = col["ts_event"]
    return {
        "session_date": col["session_date"][0],
        "raw_symbol": col["raw_symbol"][0],
        "instrument_id": col["instrument_id"][0],
        "count": len(rows),
        "ts_base": str(ts[0]),
        "ts_event": [0, *(b - a for a, b in zip(ts, ts[1:], strict=False))],
        "sequence": list(col["sequence"]),
        "sequence_ordinal": list(col["sequence_ordinal"]),
        "price": _defined(col["price"]),
        "size": list(col["size"]),
        "side": "".join(col["side"]),
        "bid_px": _defined(col["bid_px"]),
        "ask_px": _defined(col["ask_px"]),
        "bid_sz": list(col["bid_sz"]),
        "ask_sz": list(col["ask_sz"]),
    }


class FuturesTicksResponse(BaseModel):
    """``GET /api/v1/futures/{contracts/{symbol} | products/{product}}/ticks``.

    ``end`` is exclusive (session dates). ``from``/``until`` echo the instant
    bounds, half-open, null when not given. ``count`` is the ticks over all
    blocks.
    """

    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)

    target: FuturesTarget
    start: date | None
    end: date | None
    # ``from`` is a Python keyword: the field is ``from_``, the wire name its alias.
    from_: datetime | None = Field(alias="from")
    until: datetime | None
    definitions_as_of: date | None
    segments: list[SegmentRecord]
    unresolved: list[UnresolvedRecord]
    incomplete: list[IncompleteRecord]
    count: int
    price_scale: int = Field(
        description="A price is its integer × 10^-price_scale (DBN fixed point)."
    )
    blocks: list[FuturesTickBlock]

    @classmethod
    def from_plan(
        cls,
        plan: GetPlan,
        rows: Sequence[Row],
        bounds: tuple[datetime | None, datetime | None],
    ) -> FuturesTicksResponse:
        # Through the alias, as ``_pass_line`` does: ``from`` is a keyword.
        return cls(
            **plan_envelope(plan)
            | {
                "from": bounds[0],
                "until": bounds[1],
                "count": len(rows),
                "price_scale": PRICE_DIGITS,
                "blocks": FuturesTickBlock.from_rows(rows),
            }
        )
