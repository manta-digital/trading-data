"""Column contract: DBN record field → tick table column (slice 222).

Slices 223 (definitions) and 224 (trades) write through these maps, and the
tick-track migrations define the tables in the same order, so a renamed
provider field or a dropped column fails a parity test instead of a load.

Field names are plain strings: only the adapter and the DBN reader import
``databento``. This module states *which* field lands in *which* column; the
value conversion is the writer's (223/224), under slice 222 Technical
Decision 3:

- ``tick_trade`` is a raw-record table. Provider sentinels (``UNDEF_PRICE``
  in a BBO price, ``4294967295`` as an undefined size) are stored as
  delivered.
- ``tick_definition`` is a model table. A provider "undefined" value is
  stored as SQL ``NULL``.

The DBN framing fields ``length`` and ``rtype`` are never stored.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

#: ``tick_trade`` source fields in table column order: the ``trades`` body,
#: then the six ``tbbo`` fields (NULL on a trades-tier row).
TICK_TRADE_COLUMNS: Final[Mapping[str, str]] = {
    "ts_event": "ts_event",
    "ts_recv": "ts_recv",
    "instrument_id": "instrument_id",
    "publisher_id": "publisher_id",
    "sequence": "sequence",
    "price": "price",
    "size": "size",
    "action": "action",
    "side": "side",
    "flags": "flags",
    "depth": "depth",
    "ts_in_delta": "ts_in_delta",
    "bid_px_00": "bid_px_00",
    "ask_px_00": "ask_px_00",
    "bid_sz_00": "bid_sz_00",
    "ask_sz_00": "ask_sz_00",
    "bid_ct_00": "bid_ct_00",
    "ask_ct_00": "ask_ct_00",
}

#: The six ``tbbo`` columns: all NULL (trades tier) or all set (tbbo tier).
TICK_TRADE_BBO_COLUMNS: Final[tuple[str, ...]] = (
    "bid_px_00",
    "ask_px_00",
    "bid_sz_00",
    "ask_sz_00",
    "bid_ct_00",
    "ask_ct_00",
)

#: ``tick_trade`` columns with no DBN source, after the mapped ones:
#: the delivery-order ordinal (TD1) and the archive unit (TD6).
TICK_TRADE_DERIVED_COLUMNS: Final[tuple[str, ...]] = ("sequence_ordinal", "unit_id")

#: ``tick_trade``'s primary key (TD1): the provider triple is not unique.
TICK_TRADE_KEY: Final[tuple[str, ...]] = (
    "instrument_id",
    "ts_event",
    "sequence",
    "sequence_ordinal",
)

#: ``tick_definition`` source fields in table column order (TD5).
#: ``contract_multiplier`` is the architecture's "multiplier".
TICK_DEFINITION_COLUMNS: Final[Mapping[str, str]] = {
    "instrument_id": "instrument_id",
    "activation": "activation_ns",
    "expiration": "expiration_ns",
    "raw_symbol": "raw_symbol",
    "asset": "asset",
    "exchange": "exchange",
    "instrument_class": "instrument_class",
    "security_type": "security_type",
    "cfi": "cfi",
    "currency": "currency",
    "min_price_increment": "min_price_increment",
    "display_factor": "display_factor",
    "unit_of_measure": "unit_of_measure",
    "unit_of_measure_qty": "unit_of_measure_qty",
    "contract_multiplier": "contract_multiplier",
    "ts_recv": "ts_recv_ns",
}

#: ``tick_definition`` columns with no DBN source: the supplying unit.
TICK_DEFINITION_DERIVED_COLUMNS: Final[tuple[str, ...]] = ("unit_id",)
