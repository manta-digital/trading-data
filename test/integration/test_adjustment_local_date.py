"""Intraday adjustment dated in the calendar's timezone (slice 174 task 7.2, D5).

A winter split with ex-date Wednesday 2026-01-14: the seeded minutes of the
Tuesday session (09:30-10:29 ET) and its 19:00-19:59 EST minutes (00:00-00:59Z
on 01-14) take Tuesday's factor; so does the ``utc`` 1h bucket at 00:00Z.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta

import psycopg
import pytest

from manta_trading.constants import Granularity
from manta_trading.data.base.symbol_calendar import UnknownSymbolError, symbol_calendar
from manta_trading.market.timescale_minute_db import TimescaleMinuteDataDB

SYMBOL = "SPLT"
EX_DATE = date(2026, 1, 14)
RAW = 100.0
ADJUSTED = 25.0  # 4-for-1


@pytest.fixture
def minute_db(migrated_db: str) -> Iterator[TimescaleMinuteDataDB]:
    session_minutes = [
        datetime(2026, 1, 13, 14, 30, tzinfo=UTC) + timedelta(minutes=i)
        for i in range(60)
    ]
    evening = [  # 19:00-19:59 EST on 2026-01-13
        datetime(2026, 1, 14, 0, 0, tzinfo=UTC) + timedelta(minutes=i)
        for i in range(60)
    ]
    ex_day = [datetime(2026, 1, 14, 14, 30, tzinfo=UTC)]
    with psycopg.connect(migrated_db) as conn:
        conn.execute(
            "INSERT INTO instruments (canonical_id, symbol, asset_class, venue, "
            "trading_calendar_id, eodhd_type, eodhd_exchange, first_data_date) "
            "VALUES (%s, %s, 'equity', 'US', 'NYSE', 'Common Stock', 'NYSE', "
            "'2020-01-02')",
            (f"{SYMBOL}.US", SYMBOL),
        )
        conn.execute(
            "INSERT INTO splits (symbol, ex_date, ratio_to, ratio_from) "
            "VALUES (%s, %s, 4, 1)",
            (SYMBOL, EX_DATE),
        )
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO minute_ohlcv (time, symbol, open, high, low, close, "
                "volume) VALUES (%s, %s, %s, %s, %s, %s, 10)",
                [
                    (ts, SYMBOL, RAW, RAW, RAW, RAW)
                    for ts in session_minutes + evening + ex_day
                ],
            )
    with psycopg.connect(migrated_db, autocommit=True) as conn:
        conn.execute(
            "CALL refresh_continuous_aggregate('minute_hourly_ohlcv', %s, %s)",
            (datetime(2026, 1, 13, tzinfo=UTC), datetime(2026, 1, 15, tzinfo=UTC)),
        )
    db = TimescaleMinuteDataDB(migrated_db)
    try:
        yield db
    finally:
        db.close()


def test_evening_minutes_take_their_sessions_factor(
    minute_db: TimescaleMinuteDataDB,
) -> None:
    df = minute_db.get_minute_data(
        SYMBOL,
        datetime(2026, 1, 13, 14, 30, tzinfo=UTC),
        datetime(2026, 1, 14, 0, 59, tzinfo=UTC),
    )
    assert len(df) == 120
    assert list(df["close"].unique()) == [ADJUSTED]


def test_ex_date_session_is_unadjusted(minute_db: TimescaleMinuteDataDB) -> None:
    df = minute_db.get_minute_data(
        SYMBOL,
        datetime(2026, 1, 14, 14, 30, tzinfo=UTC),
        datetime(2026, 1, 14, 14, 30, tzinfo=UTC),
    )
    assert list(df["close"]) == [RAW]


def test_utc_hour_bucket_of_evening_minutes_takes_sessions_factor(
    minute_db: TimescaleMinuteDataDB,
) -> None:
    df = minute_db.get_minute_data(
        SYMBOL,
        datetime(2026, 1, 14, 0, 0, tzinfo=UTC),
        datetime(2026, 1, 14, 0, 0, tzinfo=UTC),
        Granularity.H1,
    )
    assert list(df["close"]) == [ADJUSTED]


def test_symbol_without_an_instrument_is_unknown(migrated_db: str) -> None:
    """Bars under a symbol with no instrument (production holds 468 such
    orphans) cannot be dated: an adjusted read is the API's unknown-symbol
    404, an unadjusted one is served as before."""
    from tick_support.api import client_for

    with psycopg.connect(migrated_db) as conn:
        conn.execute(
            "INSERT INTO minute_ohlcv (time, symbol, open, high, low, close, volume) "
            "VALUES ('2026-01-13 15:00+00', 'ORPH', 1, 1, 1, 1, 10)"
        )
        conn.execute(
            "INSERT INTO instruments (canonical_id, symbol, asset_class, venue, "
            "eodhd_type, eodhd_exchange) VALUES ('NOCAL.US', 'NOCAL', 'equity', "
            "'US', 'Common Stock', 'NYSE')"
        )
    with psycopg.connect(migrated_db) as conn:
        with pytest.raises(UnknownSymbolError):
            symbol_calendar(conn, "ORPH")
        with pytest.raises(ValueError, match="NOCAL"):
            symbol_calendar(conn, "NOCAL")
    url = "/api/v1/bars/ORPH?granularity=1m&start=2026-01-13&end=2026-01-13"
    for client in client_for(migrated_db, None):
        assert client.get(url).status_code == 404
        assert client.get(url + "&adjusted=false").status_code == 200
