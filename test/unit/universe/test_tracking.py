"""Unit tests for manta_trading.data.universe.tracking (slice 161, T08)."""

from __future__ import annotations

import os
from datetime import date, timedelta

import psycopg
import pytest

from manta_trading.data.universe.tracking import (
    UniverseTrackingError,
    apply_universe_diff,
    get_active_members,
    import_sp500_csv,
    latest_loaded_date,
    parse_sp500_csv,
)

_DB_URL = os.environ.get(
    "MT_TIMESCALE_DB_URL",
    "postgresql://postgres:<password>@<db-host>:5432/trading_test",
)
_UNIVERSE = "sp500"
_D0 = date(2024, 1, 2)
_D1 = date(2024, 1, 3)
_D2 = date(2024, 1, 10)


@pytest.fixture()
def conn():
    with psycopg.connect(_DB_URL) as c:
        c.execute("DELETE FROM universe_members WHERE universe_name = 'sp500'")
        c.commit()
        yield c
        c.execute("DELETE FROM universe_members WHERE universe_name = 'sp500'")
        c.commit()


# ---------------------------------------------------------------------------
# parse_sp500_csv
# ---------------------------------------------------------------------------

_SAMPLE_CSV = """\
date,tickers
2024-01-02,"AAPL,MSFT,GOOG"
2024-01-03,"AAPL,MSFT,GOOG,AMZN"
2024-01-10,"MSFT,GOOG,AMZN"
"""


def test_parse_sp500_csv_returns_sorted_rows():
    rows = parse_sp500_csv(_SAMPLE_CSV)
    assert len(rows) == 3
    assert rows[0] == (_D0, {"AAPL", "MSFT", "GOOG"})
    assert rows[1] == (_D1, {"AAPL", "MSFT", "GOOG", "AMZN"})
    assert rows[2] == (_D2, {"MSFT", "GOOG", "AMZN"})


def test_parse_sp500_csv_empty_raises():
    with pytest.raises(UniverseTrackingError, match="empty"):
        parse_sp500_csv("")


def test_parse_sp500_csv_bad_header_raises():
    with pytest.raises(UniverseTrackingError, match="header"):
        parse_sp500_csv("ticker,date\n2024-01-02,AAPL\n")


def test_parse_sp500_csv_bad_date_raises():
    with pytest.raises(UniverseTrackingError, match="unparseable date"):
        parse_sp500_csv("date,tickers\nnot-a-date,AAPL\n")


def test_parse_sp500_csv_no_data_rows_raises():
    with pytest.raises(UniverseTrackingError, match="no data rows"):
        parse_sp500_csv("date,tickers\n")


# ---------------------------------------------------------------------------
# get_active_members / apply_universe_diff
# ---------------------------------------------------------------------------


def test_get_active_members_empty(conn):
    assert get_active_members(conn, _UNIVERSE) == set()


def test_get_active_members_excludes_removed(conn):
    conn.execute(
        "INSERT INTO universe_members (universe_name, symbol, added_date) VALUES ('sp500','AAPL',%s),('sp500','MSFT',%s)",
        (_D0, _D0),
    )
    conn.execute(
        "INSERT INTO universe_members (universe_name, symbol, added_date, removed_date) VALUES ('sp500','GOOG',%s,%s)",
        (_D0, _D1),
    )
    conn.commit()
    assert get_active_members(conn, _UNIVERSE) == {"AAPL", "MSFT"}


def test_apply_universe_diff_additions(conn):
    added, removed = apply_universe_diff(conn, _UNIVERSE, {"AAPL", "MSFT"}, _D0)
    assert added == 2
    assert removed == 0
    assert get_active_members(conn, _UNIVERSE) == {"AAPL", "MSFT"}


def test_apply_universe_diff_departures(conn):
    conn.execute(
        "INSERT INTO universe_members (universe_name, symbol, added_date) VALUES ('sp500','AAPL',%s),('sp500','MSFT',%s)",
        (_D0, _D0),
    )
    conn.commit()
    added, removed = apply_universe_diff(conn, _UNIVERSE, {"AAPL"}, _D1)
    assert added == 0
    assert removed == 1
    assert get_active_members(conn, _UNIVERSE) == {"AAPL"}


def test_apply_universe_diff_idempotent(conn):
    apply_universe_diff(conn, _UNIVERSE, {"AAPL", "MSFT"}, _D0)
    added, removed = apply_universe_diff(conn, _UNIVERSE, {"AAPL", "MSFT"}, _D0)
    assert added == 0
    assert removed == 0


# ---------------------------------------------------------------------------
# latest_loaded_date
# ---------------------------------------------------------------------------


def test_latest_loaded_date_none_when_empty(conn):
    assert latest_loaded_date(conn) is None


def test_latest_loaded_date_returns_max(conn):
    conn.execute(
        "INSERT INTO universe_members (universe_name, symbol, added_date) VALUES ('sp500','AAPL',%s),('sp500','MSFT',%s)",
        (_D0, _D1),
    )
    conn.commit()
    assert latest_loaded_date(conn) == _D1


# ---------------------------------------------------------------------------
# import_sp500_csv
# ---------------------------------------------------------------------------


def test_import_sp500_csv_seeds_first_row(conn):
    imported, skipped = import_sp500_csv(conn, _SAMPLE_CSV)
    assert imported == 3
    assert skipped == 0
    # After replaying all 3 rows, active members should match the last row.
    assert get_active_members(conn, _UNIVERSE) == {"MSFT", "GOOG", "AMZN"}


def test_import_sp500_csv_skips_already_loaded(conn):
    # Seed the first two rows manually.
    import_sp500_csv(conn, _SAMPLE_CSV)
    # Re-import the same CSV — all rows already loaded, should skip all.
    imported, skipped = import_sp500_csv(conn, _SAMPLE_CSV)
    assert imported == 0
    assert skipped == 3


def test_import_sp500_csv_partial_update(conn):
    # Load only first row via a trimmed CSV.
    first_only = "date,tickers\n2024-01-02,\"AAPL,MSFT,GOOG\"\n"
    import_sp500_csv(conn, first_only)
    # Now import the full CSV — should import only the 2 new rows.
    imported, skipped = import_sp500_csv(conn, _SAMPLE_CSV)
    assert imported == 2
    assert skipped == 1


def test_import_sp500_csv_progress_callback(conn):
    calls: list[tuple] = []
    import_sp500_csv(conn, _SAMPLE_CSV, on_progress=lambda d, t, dt: calls.append((d, t, dt)))
    assert len(calls) == 3
    assert calls[0][2] == _D0


def test_import_sp500_csv_as_of_semantics(conn):
    import_sp500_csv(conn, _SAMPLE_CSV)
    # AAPL was in on D0, added on D1 diff is a no-op (already in), removed on D2.
    # Query as-of D1: AAPL should be present.
    with conn.cursor() as cur:
        cur.execute(
            "SELECT symbol FROM universe_members "
            "WHERE universe_name='sp500' AND added_date<=%s AND (removed_date IS NULL OR removed_date>%s) "
            "ORDER BY symbol",
            (_D1, _D1),
        )
        symbols = {r[0] for r in cur.fetchall()}
    assert "AAPL" in symbols
    # Query as-of D2: AAPL should be gone (removed_date = D2).
    with conn.cursor() as cur:
        cur.execute(
            "SELECT symbol FROM universe_members "
            "WHERE universe_name='sp500' AND added_date<=%s AND (removed_date IS NULL OR removed_date>%s) "
            "ORDER BY symbol",
            (_D2, _D2),
        )
        symbols = {r[0] for r in cur.fetchall()}
    assert "AAPL" not in symbols
    assert "MSFT" in symbols
