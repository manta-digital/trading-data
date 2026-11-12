"""A client's decoder for the columnar ``/ticks`` body (slice 308 D6).

Prefix-sum ``ts_event`` from ``ts_base`` and turn columns back into rows.
Prices stay DBN integers (``price_scale`` says how to scale them). A decoded
row holds the read row's columns minus ``bid_ct``/``ask_ct``, which are not on
the wire: compare it with :func:`wire_row` of a ``read_ticks`` row.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from manta_trading.data.tick.tick_reads import TICK_COLUMNS, Row

#: The read columns the wire carries, in read order.
WIRE_COLUMNS = tuple(n for n in TICK_COLUMNS if n not in ("bid_ct", "ask_ct"))


def decode_block(block: dict[str, Any]) -> list[Row]:
    ts, out = int(block["ts_base"]), []
    for i in range(block["count"]):
        ts += block["ts_event"][i]
        cells: dict[str, Any] = {
            "ts_event": ts,
            "session_date": date.fromisoformat(block["session_date"]),
            "raw_symbol": block["raw_symbol"],
            "instrument_id": block["instrument_id"],
            "side": block["side"][i],
        }
        for name in WIRE_COLUMNS:
            if name not in cells:
                cells[name] = block[name][i]
        out.append(tuple(cells[name] for name in WIRE_COLUMNS))
    return out


def decode_body(body: dict[str, Any]) -> list[Row]:
    """Every block of a ``/ticks`` body, decoded, in order."""
    assert body["price_scale"] == 9
    return [row for block in body["blocks"] for row in decode_block(block)]


def wire_row(row: Row) -> Row:
    """A ``read_ticks`` row as the wire carries it (``bid_ct``/``ask_ct`` gone)."""
    cells = dict(zip(TICK_COLUMNS, row, strict=True))
    return tuple(cells[name] for name in WIRE_COLUMNS)
