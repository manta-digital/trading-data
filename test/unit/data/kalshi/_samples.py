"""Hand-fetched live Kalshi samples shared by the kalshi unit tests.

Fetched from the live API on 2026-08-24 and trimmed for size; values are
verbatim. The recorded fixture set (Section 6) supersedes these as coverage.
"""

from __future__ import annotations

from typing import Any

SERIES_SAMPLE: dict[str, Any] = {
    "category": "Economics",
    "contract_url": "https://assets.kalshi.com/regulatory/FED.pdf",
    "exchange_index": 0,
    "fee_multiplier": 1,
    "fee_type": "quadratic",
    "frequency": "custom",
    "last_updated_ts": "2026-02-26T08:50:31.901806Z",
    "settlement_sources": [
        {
            "name": "Federal Reserve Board of Governors",
            "url": "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm",
        }
    ],
    "tags": ["Interest rates"],
    "ticker": "FED",
    "title": "Fed funds rate",
}

EVENT_SAMPLE: dict[str, Any] = {
    "available_on_brokers": False,
    "category": "Science and Technology",
    "collateral_return_type": "",
    "event_ticker": "KXELONMARS-99",
    "exchange_index": 0,
    "last_updated_ts": "2026-08-20T12:00:00Z",
    "mutually_exclusive": False,
    "series_ticker": "KXELONMARS",
    "settlement_sources": [],
    "strike_period": "",
    "sub_title": "Before 2100",
    "title": "Elon Musk lands on Mars?",
}

MARKET_SAMPLE: dict[str, Any] = {
    "can_close_early": True,
    "close_time": "2026-08-24T15:01:53Z",
    "created_time": "2026-08-24T15:00:53.670659Z",
    "event_ticker": "KXMVECROSSCATEGORY-SHARD1-S2026F6CFB2B05E5",
    "exchange_index": 1,
    "expected_expiration_time": "2026-08-24T23:20:00Z",
    "expiration_time": "2026-09-07T15:00:00Z",
    "expiration_value": "",
    "is_provisional": True,
    "last_price_dollars": "0.0000",
    "latest_expiration_time": "2026-09-07T15:00:00Z",
    "liquidity_dollars": "0.0000",
    "market_type": "binary",
    "no_ask_dollars": "1.0000",
    "no_bid_dollars": "1.0000",
    "notional_value_dollars": "1.0000",
    "open_interest_fp": "0.00",
    "open_time": "2026-08-24T15:00:53Z",
    "previous_price_dollars": "0.0000",
    "previous_yes_ask_dollars": "0.0000",
    "previous_yes_bid_dollars": "0.0000",
    "price_level_structure": "deci_cent",
    "price_ranges": [{"end": "1.0000", "start": "0.0000", "step": "0.0010"}],
    "result": "no",
    "rules_primary": "If ... then the market resolves to Yes.",
    "settlement_timer_seconds": 5,
    "settlement_ts": "2026-08-24T15:01:53.258486Z",
    "settlement_value_dollars": "0.0000",
    "status": "finalized",
    "strike_type": "custom",
    "ticker": "KXMVECROSSCATEGORY-SHARD1-S2026F6CFB2B05E5-E36558BA209",
    "title": "Multi-leg parlay",
    "updated_time": "2026-08-24T15:01:53.402359Z",
    "volume_24h_fp": "0.00",
    "volume_fp": "0.00",
    "yes_ask_dollars": "0.0000",
    "yes_ask_size_fp": "0.00",
    "yes_bid_dollars": "0.0000",
    "yes_bid_size_fp": "0.00",
}

TRADE_SAMPLE: dict[str, Any] = {
    "count_fp": "43.00",
    "created_time": "2026-08-24T15:01:57.667166Z",
    "is_block_trade": False,
    "no_price_dollars": "0.9200",
    "taker_book_side": "bid",
    "taker_outcome_side": "yes",
    "taker_side": "yes",
    "ticker": "KXDFBPOKALSCORE-26AUG24WURKOE-WUR0KOE2",
    "trade_id": "0720e561-a081-b12a-c5de-65629e4f3b27",
    "yes_price_dollars": "0.0800",
}

CANDLE_SAMPLE: dict[str, Any] = {
    "end_period_ts": 1787504400,
    "open_interest_fp": "40101.16",
    "price": {"previous_dollars": "0.1000"},
    "volume_fp": "0.00",
    "yes_ask": {
        "close_dollars": "0.1200",
        "high_dollars": "0.1200",
        "low_dollars": "0.1200",
        "open_dollars": "0.1200",
    },
    "yes_bid": {
        "close_dollars": "0.1000",
        "high_dollars": "0.1000",
        "low_dollars": "0.1000",
        "open_dollars": "0.1000",
    },
}

CUTOFF_SAMPLE: dict[str, Any] = {
    "market_positions_last_updated_ts": "2026-06-25T00:00:00Z",
    "market_settled_ts": "2026-06-25T00:00:00Z",
    "orders_updated_ts": "2026-06-25T00:00:00Z",
    "trades_created_ts": "2026-06-25T00:00:00Z",
}
