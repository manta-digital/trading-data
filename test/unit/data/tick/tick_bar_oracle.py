"""The tick-bar oracle and the real fixture trades (slice 308 D9). Not a test file.

``fixture_trades`` decodes the committed 2024-12-03 ``tbbo`` cut (or its derived
``trades`` twin) and returns the busiest instrument's records as ``Trade``s,
with ``sequence_ordinal`` and ``session_date`` assigned as ingest assigns them.
``oracle_bars`` is the rule written the obvious way, independent of
``TickBarCutter``: sort by key, split per session, slice by N, summarise.

The fixture cut took 1,000 records of all instruments on each side of the daily
break, plus a head and a tail block (``scripts/cut_tick_fixtures.py``). For the
busiest instrument (ESZ4) that is 982 trades before the 2024-12-03 close and
997 after the 2024-12-04 open; the helper asserts these counts, so a changed
fixture fails loudly.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from datetime import UTC, date, datetime
from functools import cache
from itertools import groupby

import numpy as np
from tick_support.tier_units import TBBO_DAY, real_file

from manta_trading.data.base.session_index import Session, SessionIndex
from manta_trading.data.base.session_population import populate_trading_sessions
from manta_trading.data.tick.constants import TickSchema
from manta_trading.data.tick.databento.dbn_file import DbnFileReader
from manta_trading.data.tick.dbn_values import TRADE_SIDE_BUY, TRADE_SIDE_SELL
from manta_trading.data.tick.tick_bars import TickBar, Trade
from manta_trading.market.schema.seed_cme_calendar import (
    CME_EQUITY_CALENDAR,
    CME_EQUITY_CALENDAR_ID,
    CME_EQUITY_EXCEPTIONS,
)

BUSIEST_INSTRUMENT = 183748  # ESZ4
FIRST_SESSION = date(2024, 12, 3)
SECOND_SESSION = date(2024, 12, 4)
#: Trades of the busiest instrument per session, and each side of the break.
SESSION_COUNTS = {FIRST_SESSION: 1968, SECOND_SESSION: 1491}
BEFORE_CLOSE, AFTER_OPEN = 982, 997
_NS = 1_000_000_000


def _sessions() -> SessionIndex:
    rows = populate_trading_sessions(
        CME_EQUITY_CALENDAR_ID,
        date(2024, 11, 25),
        date(2024, 12, 10),
        CME_EQUITY_CALENDAR,
        [
            {**row, "market_status": row["market_status"].value}
            for row in CME_EQUITY_EXCEPTIONS
        ],
    )
    return SessionIndex(
        [
            Session(
                r["calendar_id"],
                r["session_date"],
                r["session_open_utc"],
                r["session_close_utc"],
            )
            for r in rows
        ]
    )


def _ordinals(ts_event: list[int], sequence: list[int]) -> list[int]:
    """Earlier records sharing ``(ts_event, sequence)``, as ingest counts them."""
    seen: Counter[tuple[int, int]] = Counter()
    out = []
    for pair in zip(ts_event, sequence, strict=True):
        out.append(seen[pair])
        seen[pair] += 1
    return out


@cache
def fixture_trades(schema: TickSchema) -> tuple[Trade, ...]:
    """The busiest instrument's trades in the 2024-12-03 cut, in file order."""
    tick_file = DbnFileReader().open_file(real_file(TBBO_DAY, schema))
    records = np.concatenate([b.records for b in tick_file.iter_batches()])
    counts = Counter(records["instrument_id"].tolist())
    assert counts.most_common(1)[0][0] == BUSIEST_INSTRUMENT
    mine = records[records["instrument_id"] == BUSIEST_INSTRUMENT]
    index = _sessions()
    positions = index.locate_ns(mine["ts_event"].astype(np.int64)).tolist()
    ts_event = [int(t) for t in mine["ts_event"].tolist()]
    sequence = [int(s) for s in mine["sequence"].tolist()]
    quoted = schema is TickSchema.TBBO
    trades = tuple(
        Trade(
            ts_event[i],
            sequence[i],
            ordinal,
            index.sessions[positions[i]].session_date,
            int(mine["price"][i]),
            int(mine["size"][i]),
            mine["side"][i].decode(),
            int(mine["bid_px_00"][i]) if quoted else None,
            int(mine["ask_px_00"][i]) if quoted else None,
        )
        for i, ordinal in enumerate(_ordinals(ts_event, sequence))
    )
    _assert_premise(trades)
    return trades


def _assert_premise(trades: Sequence[Trade]) -> None:
    per_session = Counter(t.session_date for t in trades)
    assert per_session == SESSION_COUNTS, per_session
    close = int(datetime(2024, 12, 3, 22, tzinfo=UTC).timestamp()) * _NS
    hour, half = 3600 * _NS, 1800 * _NS
    before = sum(close - hour <= t.ts_event <= close for t in trades)
    after = sum(close + hour <= t.ts_event < close + hour + half for t in trades)
    assert (before, after) == (BEFORE_CLOSE, AFTER_OPEN), (before, after)


def _summary(session: date, index: int, chunk: list[Trade], n: int) -> TickBar:
    prices = [t.price for t in chunk]
    return TickBar(
        session_date=session,
        index=index,
        open_time=chunk[0].ts_event,
        close_time=chunk[-1].ts_event,
        open=prices[0],
        high=max(prices),
        low=min(prices),
        close=prices[-1],
        volume=sum(t.size for t in chunk),
        trade_count=len(chunk),
        buy_volume=sum(t.size for t in chunk if t.side == TRADE_SIDE_BUY),
        sell_volume=sum(t.size for t in chunk if t.side == TRADE_SIDE_SELL),
        bid_px=chunk[-1].bid_px,
        ask_px=chunk[-1].ask_px,
        partial=len(chunk) < n,
    )


def oracle_bars(trades: Sequence[Trade], n: int) -> list[TickBar]:
    """The expected bars for ``trades`` at ``n`` ticks per bar."""
    ordered = sorted(trades, key=lambda t: (t.ts_event, t.sequence, t.sequence_ordinal))
    bars = []
    for session, group in groupby(ordered, key=lambda t: t.session_date):
        run = list(group)
        for index, start in enumerate(range(0, len(run), n)):
            bars.append(_summary(session, index, run[start : start + n], n))
    return bars
