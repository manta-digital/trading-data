"""Integration tests for migration 067: index instruments take ``{code}.INDX`` (#46).

Every test runs on a throwaway database the conftest fixtures created, migrated
up to 066, seeded with the production shapes found on 2026-11-19, then taken
through 067.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import psycopg
import pytest
from psycopg_pool import ConnectionPool

from manta_trading.constants import US_EQUITY_CALENDAR_ID
from manta_trading.market.schema.migrations.minute import MINUTE_MIGRATIONS
from manta_trading.market.schema.runner import apply_migrations

_ID = "067_index_instruments_indx_symbols"
_EQUITY_FIRST_DATE = date(2021, 9, 30)


def _chain(through_067: bool) -> list[dict[str, Any]]:
    ids = [m["id"] for m in MINUTE_MIGRATIONS]
    end = ids.index(_ID) + (1 if through_067 else 0)
    return MINUTE_MIGRATIONS[:end]


def _migrate(url: str, migrations: list[dict[str, Any]]) -> None:
    with ConnectionPool(url, min_size=1, max_size=2) as pool:
        apply_migrations(pool, migrations)


def _instrument(
    conn: psycopg.Connection[Any],
    symbol: str,
    canonical_id: str,
    exchange: str,
    calendar: str | None,
    first_data_date: date | None = None,
) -> None:
    asset_class = "equity" if canonical_id.endswith(".US") else "index"
    conn.execute(
        "INSERT INTO instruments (canonical_id, symbol, asset_class, venue,"
        " trading_calendar_id, eodhd_type, eodhd_exchange, first_data_date)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
        (
            canonical_id,
            symbol,
            asset_class,
            exchange,
            calendar,
            "INDEX" if asset_class == "index" else "Common Stock",
            exchange,
            first_data_date,
        ),
    )


def _state(conn: psycopg.Connection[Any], symbol: str) -> None:
    conn.execute(
        "INSERT INTO acquisition_state (symbol, granularity, provider)"
        " VALUES (%s, 'minute', 'eodhd')",
        (symbol,),
    )


def _seed(url: str) -> None:
    """An equity DJT with bars and state, the index DJT that borrowed its first
    date, an index VIX with its own state row, and a US-exchange ^TNX."""
    with psycopg.connect(url) as conn:
        _instrument(conn, "DJT", "DJT.US", "NASDAQ", "NYSE", _EQUITY_FIRST_DATE)
        _instrument(conn, "DJT", "DJT.INDX", "INDX", None, _EQUITY_FIRST_DATE)
        _instrument(conn, "VIX", "VIX.INDX", "INDX", None)
        _instrument(conn, "^TNX", "^TNX.US", "US", "NYSE", date(1962, 1, 2))
        _state(conn, "DJT")
        _state(conn, "VIX")
        conn.execute(
            "INSERT INTO daily_ohlcv (time, symbol, open, high, low, close, volume)"
            " VALUES (%s, 'DJT', 1, 2, 0.5, 1.5, 100)",
            (datetime(2026, 11, 18, tzinfo=UTC),),
        )


@pytest.fixture
def seeded_db(ephemeral_db: str) -> str:
    _migrate(ephemeral_db, _chain(through_067=False))
    _seed(ephemeral_db)
    return ephemeral_db


def _rows(url: str) -> dict[str, list[tuple[Any, ...]]]:
    with psycopg.connect(url) as conn:
        return {
            # Sorted here: the cluster's collation orders '^' differently.
            "instruments": sorted(
                conn.execute(
                    "SELECT canonical_id, symbol, trading_calendar_id,"
                    " first_data_date FROM instruments"
                ).fetchall()
            ),
            "state": conn.execute(
                "SELECT symbol FROM acquisition_state ORDER BY symbol"
            ).fetchall(),
            "bars": conn.execute(
                "SELECT symbol, count(*) FROM daily_ohlcv GROUP BY symbol"
            ).fetchall(),
        }


def test_index_rows_move_and_the_equity_is_untouched(seeded_db: str) -> None:
    _migrate(seeded_db, _chain(through_067=True))
    rows = _rows(seeded_db)
    assert rows["instruments"] == [
        ("DJT.INDX", "DJT.INDX", US_EQUITY_CALENDAR_ID, None),
        ("DJT.US", "DJT", "NYSE", _EQUITY_FIRST_DATE),
        ("VIX.INDX", "VIX.INDX", US_EQUITY_CALENDAR_ID, None),
        ("^TNX.US", "^TNX", "NYSE", date(1962, 1, 2)),
    ]  # Python's ordering: uppercase letters sort before '^'.
    # DJT's state row is the equity's; VIX's followed the index.
    assert rows["state"] == [("DJT",), ("VIX.INDX",)]
    assert rows["bars"] == [("DJT", 1)]


def test_a_second_run_changes_nothing(seeded_db: str) -> None:
    _migrate(seeded_db, _chain(through_067=True))
    before = _rows(seeded_db)
    migration = MINUTE_MIGRATIONS[[m["id"] for m in MINUTE_MIGRATIONS].index(_ID)]
    with psycopg.connect(seeded_db) as conn:
        conn.execute(migration["sql"])
    assert _rows(seeded_db) == before


def test_daily_bars_under_an_unshared_bare_code_abort_it(seeded_db: str) -> None:
    """Bars are never moved: a bare-coded index with bars stops the migration."""
    with psycopg.connect(seeded_db) as conn:
        conn.execute(
            "INSERT INTO daily_ohlcv (time, symbol, open, high, low, close, volume)"
            " VALUES (%s, 'VIX', 20, 21, 19, 20.5, 0)",
            (datetime(2026, 11, 18, tzinfo=UTC),),
        )
    with pytest.raises(psycopg.errors.RaiseException, match="never moved"):
        _migrate(seeded_db, _chain(through_067=True))
    assert ("VIX.INDX", "VIX", None, None) in _rows(seeded_db)["instruments"]
