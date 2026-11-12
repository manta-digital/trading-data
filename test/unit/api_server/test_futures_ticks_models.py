"""The columnar ``/ticks`` body: pivot and round trip (slice 308 D6)."""

from __future__ import annotations

import json
from datetime import date
from typing import Any

import msgpack
from tick_support.columnar import decode_body, wire_row

from manta_trading.api_server.models.futures_ticks import (
    FuturesTickBlock,
    FuturesTicksResponse,
)
from manta_trading.data.tick.dbn_values import UNDEF_INT64
from manta_trading.data.tick.get_plan import ContractTarget, GetPlan
from manta_trading.data.tick.tick_reads import TICK_COLUMNS, Row

D18, D19 = date(2024, 9, 18), date(2024, 9, 19)
#: Past 2^53, as every 2024 stamp is.
TS = 1_726_668_000_123_456_789
PX = 5_650_250_000_000
PLAN = GetPlan(ContractTarget("ESZ4", 183748, "ES"), D18, D19, (), (), (), (), None)


def tbbo(ts: int, seq: int, ordinal: int = 0, side: str = "B", **kw: Any) -> Row:
    cells = {
        "ts_event": ts, "session_date": D18, "raw_symbol": "ESZ4",
        "instrument_id": 183748, "price": PX, "size": 2, "side": side,
        "bid_px": PX - 250_000_000, "ask_px": PX, "bid_sz": 5, "ask_sz": 6,
        "bid_ct": 1, "ask_ct": 2, "sequence": seq, "sequence_ordinal": ordinal,
    } | kw  # fmt: skip
    return tuple(cells[name] for name in TICK_COLUMNS)


def trades(ts: int, seq: int, **kw: Any) -> Row:
    quote: dict[str, Any] = dict.fromkeys(
        ("bid_px", "ask_px", "bid_sz", "ask_sz", "bid_ct", "ask_ct")
    )
    return tbbo(ts, seq, **(quote | kw))


TBBO_ROWS = [
    tbbo(TS, 10, side="B"),
    tbbo(TS, 10, 1, side="A", price=PX + 250_000_000),
    tbbo(TS + 1_250, 11, side="N"),
    tbbo(TS + 9_000_000_000, 12, side="A"),
]
TRADES_ROWS = [
    trades(TS, 1, session_date=D19, instrument_id=7, raw_symbol="ESH5"),
    trades(TS + 5, 2, session_date=D19, instrument_id=7, raw_symbol="ESH5"),
]


def _body(rows: list[Row]) -> Any:
    return json.loads(
        FuturesTicksResponse.from_plan(PLAN, rows, (None, None)).model_dump_json()
    )


def test_two_windows_make_two_blocks_and_one_window_one() -> None:
    blocks = FuturesTickBlock.from_rows(TBBO_ROWS + TRADES_ROWS)

    assert [(b.session_date, b.raw_symbol, b.count) for b in blocks] == [
        (D18, "ESZ4", 4),
        (D19, "ESH5", 2),
    ]
    assert len(FuturesTickBlock.from_rows(TBBO_ROWS[:2])) == 1
    assert FuturesTickBlock.from_rows([]) == []


def test_timestamps_are_a_text_base_and_non_negative_deltas() -> None:
    (block,) = _body(TBBO_ROWS)["blocks"]

    assert block["ts_base"] == str(TS) and TS > 2**53
    assert block["ts_event"] == [0, 0, 1_250, 9_000_000_000 - 1_250]
    assert all(delta >= 0 for delta in block["ts_event"])


def test_side_is_one_character_per_tick() -> None:
    (block,) = _body(TBBO_ROWS)["blocks"]

    assert block["side"] == "BANA"
    assert len(block["side"]) == block["count"]


def test_quote_columns_are_null_for_trades_rows_and_never_dropped() -> None:
    _, block = _body(TBBO_ROWS + TRADES_ROWS)["blocks"]

    for name in ("bid_px", "ask_px", "bid_sz", "ask_sz"):
        assert block[name] == [None, None]
    assert "bid_ct" not in block and "ask_ct" not in block


def test_an_undefined_price_is_null() -> None:
    rows = [tbbo(TS, 1, price=UNDEF_INT64, ask_px=UNDEF_INT64)]

    (block,) = _body(rows)["blocks"]

    assert (block["price"], block["ask_px"]) == ([None], [None])


def test_envelope_counts_every_tick_and_states_the_scale() -> None:
    body = _body(TBBO_ROWS + TRADES_ROWS)

    assert body["count"] == 6 and body["price_scale"] == 9
    assert list(body)[-3:] == ["count", "price_scale", "blocks"]


def test_decoding_reproduces_the_rows_through_json_and_msgpack() -> None:
    rows = TBBO_ROWS + TRADES_ROWS
    model = FuturesTicksResponse.from_plan(PLAN, rows, (None, None))
    payload = model.model_dump(mode="json")
    for body in (
        json.loads(json.dumps(payload)),
        msgpack.unpackb(msgpack.packb(payload)),
    ):
        assert decode_body(body) == [wire_row(r) for r in rows]
