"""Unit tests for iter_active_instruments symbol-selector ordering."""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

import pytest

from manta_trading.data.acquisition.symbols import InstrumentRow, iter_active_instruments


def _make_conn(rows: list[tuple]) -> MagicMock:
    conn = MagicMock()
    cur = MagicMock()
    cur.__enter__ = MagicMock(return_value=cur)
    cur.__exit__ = MagicMock(return_value=False)
    cur.fetchall.return_value = rows
    conn.cursor.return_value = cur
    return conn


def _row(
    symbol: str = "AAPL",
    calendar: str = "US",
    listing: date | None = date(2020, 1, 1),
    first_data: date | None = None,
    delisted_at: bool = False,
    delisted_date: date | None = None,
) -> tuple:
    return (symbol, calendar, listing, first_data, delisted_at, delisted_date)


class TestIterActiveInstruments:
    def test_most_stale_first_includes_left_join_and_order(self) -> None:
        conn = _make_conn([_row("AAPL"), _row("MSFT")])
        list(iter_active_instruments(conn, ordering="most_stale_first", granularity="daily"))
        cur = conn.cursor.return_value.__enter__.return_value
        sql: str = cur.execute.call_args[0][0]
        assert "LEFT JOIN acquisition_state" in sql
        assert "last_attempt_ts ASC NULLS FIRST" in sql
        assert "i.symbol ASC" in sql

    def test_alphabetical_has_no_join_simple_order(self) -> None:
        conn = _make_conn([_row("AAPL"), _row("MSFT")])
        list(iter_active_instruments(conn, ordering="alphabetical"))
        cur = conn.cursor.return_value.__enter__.return_value
        sql: str = cur.execute.call_args[0][0]
        assert "ORDER BY i.symbol ASC" in sql
        assert "acquisition_state" not in sql

    def test_invalid_ordering_raises(self) -> None:
        conn = _make_conn([])
        with pytest.raises(ValueError, match="ordering must be"):
            list(iter_active_instruments(conn, ordering="random"))

    def test_scope_includes_active_and_newly_delisted(self) -> None:
        conn = _make_conn([_row("AAPL"), _row("BBBYQ", delisted_at=True)])
        result = list(iter_active_instruments(conn, ordering="alphabetical"))
        assert len(result) == 2

    def test_yields_instrument_rows(self) -> None:
        rows = [_row("AAPL", listing=date(1980, 12, 12))]
        conn = _make_conn(rows)
        result = list(iter_active_instruments(conn, ordering="alphabetical"))
        assert len(result) == 1
        inst = result[0]
        assert isinstance(inst, InstrumentRow)
        assert inst.symbol == "AAPL"
        assert inst.first_listing_date == date(1980, 12, 12)
        assert inst.delisted_at_eodhd is False
        assert inst.delisted_date is None

    def test_granularity_param_passed_to_join(self) -> None:
        conn = _make_conn([])
        list(iter_active_instruments(conn, ordering="most_stale_first", granularity="minute"))
        cur = conn.cursor.return_value.__enter__.return_value
        params = cur.execute.call_args[0][1]
        assert "minute" in params

    @pytest.mark.parametrize(
        "ordering,granularity",
        [
            ("most_stale_first", "daily"),
            ("most_stale_first", "minute"),
            ("alphabetical", "daily"),
        ],
    )
    def test_sql_includes_scope_filter(self, ordering: str, granularity: str) -> None:
        conn = _make_conn([])
        list(iter_active_instruments(conn, ordering=ordering, granularity=granularity))
        cur = conn.cursor.return_value.__enter__.return_value
        sql: str = cur.execute.call_args[0][0]
        assert "delisted_at_eodhd = false AND" in sql
        assert "delisted_date IS NULL" in sql
