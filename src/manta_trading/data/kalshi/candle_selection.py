"""The candle phase's own SQL fragments (slice 264).

The collection rule itself — ``CollectionRule`` and ``selection_sql`` —
lives in ``selection`` since slice 265 split it out so the trades phase can
share it. What stays here is candle-specific: the join extended with the
candle state table, and the two conditions that split finalized markets on
either side of the historical cutoff.
"""

from __future__ import annotations

from psycopg import sql

from manta_trading.data.kalshi.selection import CATALOG_JOIN

#: The join every candle-phase query and every candle ``status`` count runs
#: over, at the collected period (bound as ``%(period)s``): the shared
#: catalog join **composed** with the candle state table, never re-spelled.
MARKET_JOIN = CATALOG_JOIN + sql.SQL(
    "LEFT JOIN kalshi.market_candle_state st "
    "ON st.market_ticker = m.ticker AND st.period = %(period)s "
)

#: Finalized with no state row, on either side of the cutoff (``%(cutoff)s``,
#: ``%(finalized)s`` bound by the caller): ``BACKLOG`` is still served live
#: and drains under Decision 6; ``BEHIND_CUTOFF`` is slice 266's input.
BACKLOG_CONDITION = sql.SQL(
    "m.status = %(finalized)s AND m.settlement_ts >= %(cutoff)s "
    "AND st.market_ticker IS NULL"
)
BEHIND_CUTOFF_CONDITION = sql.SQL(
    "m.status = %(finalized)s AND m.settlement_ts < %(cutoff)s "
    "AND st.market_ticker IS NULL"
)
