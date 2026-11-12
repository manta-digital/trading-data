"""The futures tick-bar response models (slice 308 D2, D7)."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime

from manta_trading.api_server.models.futures_tick_bars import (
    FuturesTickBarRecord,
    FuturesTickBarsResponse,
)
from manta_trading.data.tick.get_plan import ContractTarget, GetPlan, Incomplete
from manta_trading.data.tick.tick_bar_reads import TICK_BAR_COLUMNS
from manta_trading.data.tick.tick_status import TickSessionStatus

#: 2024-09-18 14:00 UTC plus 123456789 ns: past 2^53, as every 2024 stamp is.
OPEN_NS = 1_726_668_000_123_456_789
SESSION = date(2024, 9, 18)
PLAN = GetPlan(
    ContractTarget("ESZ4", 183748, "ES"),
    SESSION,
    date(2024, 9, 19),
    (),
    (),
    (),
    (Incomplete(SESSION, TickSessionStatus.AWAITING_INGEST),),
    None,
)


def _row(bid: int | None, ask: int | None, partial: bool = False) -> tuple[object, ...]:
    return (
        SESSION, 3, OPEN_NS, OPEN_NS + 1_000, "ESZ4", 183748,
        5_950_250_000_000, 5_951_000_000_000, 5_949_750_000_000, 5_950_500_000_000,
        12, 4, 7, 4, bid, ask, partial,
    )  # fmt: skip


def test_row_order_is_the_record_field_order() -> None:
    assert tuple(FuturesTickBarRecord.model_fields) == TICK_BAR_COLUMNS


def test_integers_become_decimal_strings_and_nine_digit_times() -> None:
    record = FuturesTickBarRecord.from_row(_row(5_950_250_000_000, 5_950_500_000_000))

    assert (record.open, record.high, record.low, record.close) == (
        "5950.25",
        "5951",
        "5949.75",
        "5950.5",
    )
    assert record.open_time == "2024-09-18T14:00:00.123456789Z"
    assert record.close_time == "2024-09-18T14:00:00.123457789Z"
    assert (record.bid_px, record.ask_px) == ("5950.25", "5950.5")
    assert (record.volume, record.trade_count) == (12, 4)
    assert (record.buy_volume, record.sell_volume) == (7, 4)


def test_absent_quotes_stay_none() -> None:
    record = FuturesTickBarRecord.from_row(_row(None, None, partial=True))

    assert (record.bid_px, record.ask_px, record.partial) == (None, None, True)


def test_response_serialises_from_under_its_wire_name() -> None:
    lower = datetime(2024, 9, 18, 14, tzinfo=UTC)
    model = FuturesTickBarsResponse.from_plan(
        PLAN, 4, [_row(None, None)], (lower, None)
    )

    body = json.loads(model.model_dump_json())

    assert "from" in body and "from_" not in body
    assert body["from"] == "2024-09-18T14:00:00Z"
    assert body["ticks_per_bar"] == 4 and body["count"] == 1
    assert body["target"]["kind"] == "contract"
    assert body["incomplete"] == [
        {"session_date": "2024-09-18", "status": "awaiting_ingest"}
    ]
    assert "source" not in body
    assert list(body) == [
        "target", "ticks_per_bar", "start", "end", "from", "until",
        "definitions_as_of", "segments", "unresolved", "incomplete", "count", "bars",
    ]  # fmt: skip
