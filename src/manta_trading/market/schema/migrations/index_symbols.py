"""Migration SQL: index instruments take their ``{code}.INDX`` symbol (issue #46).

The EODHD ``INDX`` instruments were stored under the bare code (``VIX``,
``DJT``) with no trading calendar. Bars and instrument updates are keyed by
symbol alone, so 24 of them share a symbol with an equity and show the equity's
``first_data_date``. The bars under a shared symbol are the equity's own and
stay where they are.

What the statement does, for instruments on EODHD's index exchange whose symbol
is not yet their ``canonical_id``:

1. Refuses (raises) if any daily bars are stored under an unshared bare symbol:
   none were on 2026-11-19, and bars are never moved by this migration.
2. Renames the symbol-keyed bookkeeping rows (``acquisition_state``,
   ``data_gaps``, ``dividends``, ``splits``, ``universe_members``) of the
   unshared codes to ``{code}.INDX``. Rows under a shared code are the equity's.
3. Clears ``first_data_date`` and ``delisted_date`` on the shared ones, which
   came from the equity.
4. Sets ``symbol = canonical_id`` and the US equity calendar.

Idempotent: once every index row's symbol is its canonical_id, step 1–4 select
nothing.
"""

from __future__ import annotations

from typing import Final

from manta_trading.constants import US_EQUITY_CALENDAR_ID
from manta_trading.data.eodhd_ticker import EODHD_INDEX_EXCHANGE

#: Symbol-keyed tables whose rows follow an unshared index code to its new symbol.
#: Bar tables are not listed: bars are never moved (step 1 refuses instead).
_RENAMED_TABLES: Final = (
    "acquisition_state",
    "data_gaps",
    "dividends",
    "splits",
    "universe_members",
)

_MOVING = f"""
    SELECT i.instrument_id, i.symbol AS old_symbol, i.canonical_id AS new_symbol,
           EXISTS (
               SELECT 1 FROM instruments o
               WHERE o.symbol = i.symbol AND o.instrument_id <> i.instrument_id
           ) AS shared
    FROM instruments i
    WHERE i.eodhd_exchange = '{EODHD_INDEX_EXCHANGE}'
      AND i.symbol <> i.canonical_id
"""

_RENAMES = "\n".join(
    f"UPDATE {table} t SET symbol = m.new_symbol FROM _index_moving m"
    f" WHERE t.symbol = m.old_symbol AND NOT m.shared;"
    for table in _RENAMED_TABLES
)

INDEX_SYMBOLS_SQL: Final = f"""
CREATE TEMP TABLE _index_moving ON COMMIT DROP AS {_MOVING};

DO $$
DECLARE stray bigint;
BEGIN
    SELECT count(*) INTO stray FROM daily_ohlcv d
    JOIN _index_moving m ON d.symbol = m.old_symbol AND NOT m.shared;
    IF stray > 0 THEN
        RAISE EXCEPTION
            'index symbols migration: % daily bars under unshared bare index '
            'symbols; bars are never moved, resolve them first', stray;
    END IF;
END $$;

{_RENAMES}

UPDATE instruments i SET first_data_date = NULL, delisted_date = NULL
FROM _index_moving m WHERE i.instrument_id = m.instrument_id AND m.shared;

UPDATE instruments i
SET symbol = m.new_symbol,
    trading_calendar_id = '{US_EQUITY_CALENDAR_ID}',
    updated_at = NOW()
FROM _index_moving m WHERE i.instrument_id = m.instrument_id;
"""
