"""Unit tests for manta_trading.data.equity_universe (slice 130)."""

from __future__ import annotations

from datetime import date

import psycopg
import pytest

from manta_trading.data.equity_universe import UniverseQueryError, equity_universe

# Test date anchors
_D_PAST = date(2020, 1, 2)   # listing date for most fixtures
_D_MID = date(2021, 6, 1)    # delisted_date boundary tests
_D_QUERY = date(2022, 1, 1)  # as_of_date for most queries

_TEST_UNIVERSE = "test_universe"

# Unique prefix to avoid collisions with real data
_SYM_ACTIVE = "TSTACT"
_SYM_DELISTED_AFTER = "TSTDLA"   # delisted after query date → included
_SYM_DELISTED_BEFORE = "TSTDLB"  # delisted on/before query date → excluded
_SYM_FALLBACK = "TSTFBK"          # NULL first_listing_date, first_data_date set
_SYM_NO_DATE = "TSTNODT"          # both dates NULL → excluded
_SYM_MEMBER = "TSTMBR"            # in universe_members
_SYM_NONMEMBER = "TSTNMB"         # active but NOT in universe_members


@pytest.fixture()
def conn(migrated_db):
    """Connection to a fresh throwaway database.

    Previously connected to MT_TIMESCALE_DB_URL and deleted/inserted rows
    there (2026-08-04 incident class). Nothing to clean now: the database is
    created for the test and dropped after it.
    """
    with psycopg.connect(migrated_db) as c:
        yield c


def _insert_instrument(
    conn: psycopg.Connection,  # type: ignore[type-arg]
    symbol: str,
    *,
    first_listing_date: date | None = None,
    first_data_date: date | None = None,
    delisted_date: date | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO instruments
            (symbol, canonical_id, asset_class, venue,
             eodhd_exchange, eodhd_type,
             first_listing_date, first_data_date, delisted_date)
        VALUES (%s, %s, 'equity', 'NYSE', 'NYSE', 'Common Stock',
                %s, %s, %s)
        """,
        (symbol, symbol, first_listing_date, first_data_date, delisted_date),
    )


def _insert_member(
    conn: psycopg.Connection,  # type: ignore[type-arg]
    symbol: str,
    *,
    added_date: date,
    removed_date: date | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO universe_members (universe_name, symbol, added_date, removed_date)
        VALUES (%s, %s, %s, %s)
        """,
        (_TEST_UNIVERSE, symbol, added_date, removed_date),
    )


# ---------------------------------------------------------------------------
# Active filter tests (no universe)
# ---------------------------------------------------------------------------


def test_active_symbol_included(conn: psycopg.Connection) -> None:  # type: ignore[type-arg]
    """Symbol with first_listing_date <= query date and no delisted_date is included."""
    _insert_instrument(conn, _SYM_ACTIVE, first_listing_date=_D_PAST)
    conn.commit()

    result = equity_universe(conn, _D_QUERY)
    assert _SYM_ACTIVE in result


def test_delisted_after_query_date_included(conn: psycopg.Connection) -> None:  # type: ignore[type-arg]
    """Symbol with delisted_date > query date is still included."""
    _insert_instrument(
        conn,
        _SYM_DELISTED_AFTER,
        first_listing_date=_D_PAST,
        delisted_date=date(2023, 1, 1),  # after _D_QUERY
    )
    conn.commit()

    result = equity_universe(conn, _D_QUERY)
    assert _SYM_DELISTED_AFTER in result


def test_delisted_on_query_date_excluded(conn: psycopg.Connection) -> None:  # type: ignore[type-arg]
    """Symbol delisted on exactly the query date is excluded (strict >)."""
    _insert_instrument(
        conn,
        _SYM_DELISTED_BEFORE,
        first_listing_date=_D_PAST,
        delisted_date=_D_QUERY,
    )
    conn.commit()

    result = equity_universe(conn, _D_QUERY)
    assert _SYM_DELISTED_BEFORE not in result


def test_first_data_date_fallback_included(conn: psycopg.Connection) -> None:  # type: ignore[type-arg]
    """Symbol with NULL first_listing_date but first_data_date <= query is included."""
    _insert_instrument(conn, _SYM_FALLBACK, first_data_date=_D_PAST)
    conn.commit()

    result = equity_universe(conn, _D_QUERY)
    assert _SYM_FALLBACK in result


def test_both_dates_null_excluded(conn: psycopg.Connection) -> None:  # type: ignore[type-arg]
    """Symbol with neither first_listing_date nor first_data_date is excluded."""
    _insert_instrument(conn, _SYM_NO_DATE)
    conn.commit()

    result = equity_universe(conn, _D_QUERY)
    assert _SYM_NO_DATE not in result


# ---------------------------------------------------------------------------
# Universe filter tests
# ---------------------------------------------------------------------------


def test_universe_filter_excludes_nonmembers(conn: psycopg.Connection) -> None:  # type: ignore[type-arg]
    """With universe given, only universe_members as-of are returned; instruments filter not applied."""
    _insert_member(conn, _SYM_MEMBER, added_date=_D_PAST)
    # _SYM_NONMEMBER has no universe_members row — excluded even if active in instruments
    _insert_instrument(conn, _SYM_NONMEMBER, first_listing_date=_D_PAST)
    conn.commit()

    result = equity_universe(conn, _D_QUERY, universe=_TEST_UNIVERSE)
    assert _SYM_MEMBER in result
    assert _SYM_NONMEMBER not in result


def test_universe_added_after_query_date_excluded(conn: psycopg.Connection) -> None:  # type: ignore[type-arg]
    """Symbol added to universe after query date is excluded."""
    _insert_member(conn, _SYM_MEMBER, added_date=date(2023, 6, 1))  # after _D_QUERY
    conn.commit()

    result = equity_universe(conn, _D_QUERY, universe=_TEST_UNIVERSE)
    assert _SYM_MEMBER not in result


def test_universe_removed_before_query_date_excluded(conn: psycopg.Connection) -> None:  # type: ignore[type-arg]
    """Symbol removed from universe on or before query date is excluded."""
    _insert_member(conn, _SYM_MEMBER, added_date=_D_PAST, removed_date=_D_QUERY)
    conn.commit()

    result = equity_universe(conn, _D_QUERY, universe=_TEST_UNIVERSE)
    assert _SYM_MEMBER not in result


def test_universe_included_without_instruments_row(conn: psycopg.Connection) -> None:  # type: ignore[type-arg]
    """Universe member is returned even if it has no row in instruments (e.g. old delisted name)."""
    _insert_member(conn, _SYM_MEMBER, added_date=_D_PAST)
    # No instruments row for _SYM_MEMBER
    conn.commit()

    result = equity_universe(conn, _D_QUERY, universe=_TEST_UNIVERSE)
    assert _SYM_MEMBER in result


def test_no_universe_returns_all_active(conn: psycopg.Connection) -> None:  # type: ignore[type-arg]
    """Without universe arg, all active instruments are returned regardless of membership."""
    _insert_instrument(conn, _SYM_ACTIVE, first_listing_date=_D_PAST)
    _insert_instrument(conn, _SYM_NONMEMBER, first_listing_date=_D_PAST)
    # Neither is in _TEST_UNIVERSE
    conn.commit()

    result = equity_universe(conn, _D_QUERY)
    assert _SYM_ACTIVE in result
    assert _SYM_NONMEMBER in result


# ---------------------------------------------------------------------------
# Unknown universe raises
# ---------------------------------------------------------------------------


def test_unknown_universe_raises(conn: psycopg.Connection) -> None:  # type: ignore[type-arg]
    """UniverseQueryError raised when universe name has no rows in universe_members."""
    with pytest.raises(UniverseQueryError, match="no rows in universe_members"):
        equity_universe(conn, _D_QUERY, universe="definitely_unknown_xyz")


# ---------------------------------------------------------------------------
# Result is sorted
# ---------------------------------------------------------------------------


def test_result_is_sorted(conn: psycopg.Connection) -> None:  # type: ignore[type-arg]
    """Return value is a sorted list."""
    for sym in [_SYM_ACTIVE, _SYM_FALLBACK]:
        _insert_instrument(conn, sym, first_listing_date=_D_PAST)
    conn.commit()

    result = equity_universe(conn, _D_QUERY)
    active_subset = [s for s in result if s in {_SYM_ACTIVE, _SYM_FALLBACK}]
    assert active_subset == sorted(active_subset)
