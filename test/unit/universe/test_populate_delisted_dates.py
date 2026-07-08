"""Unit tests for populate_delisted_dates (slice 159, T4)."""

from __future__ import annotations

import json
from datetime import date
from unittest.mock import MagicMock, call, patch

import httpx
import pytest

from manta_trading.data.universe.populate_delisted_dates import (
    PopulateDelistedDatesReport,
    populate_delisted_dates,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_conn(symbols: list[str]) -> MagicMock:
    """Mock psycopg Connection whose cursor returns the given symbol list."""
    cur = MagicMock()
    cur.fetchall.return_value = [(s,) for s in symbols]
    cur.__enter__ = lambda s: s
    cur.__exit__ = MagicMock(return_value=False)

    conn = MagicMock()
    conn.cursor.return_value = cur
    return conn


def _make_resp(bars: list[dict]) -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.text = json.dumps(bars)
    resp.status_code = 200
    return resp


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_happy_path() -> None:
    conn = _make_conn(["SYM1"])
    http = MagicMock(spec=httpx.Client)
    resp = _make_resp([{"date": "2003-07-15", "close": 1.23}])

    with patch(
        "manta_trading.data.universe.populate_delisted_dates.eodhd_get",
        return_value=resp,
    ):
        report = populate_delisted_dates(conn, http, api_key="key")

    assert report.updated == 1
    assert report.skipped_empty == 0
    assert report.error_count == 0

    # Verify the UPDATE was issued with the right args.
    execute_calls = [
        c
        for c in conn.cursor.return_value.execute.call_args_list
        if "UPDATE" in str(c)
    ]
    assert len(execute_calls) == 1
    assert execute_calls[0] == call(
        "\n    UPDATE instruments SET delisted_date = %s WHERE symbol = %s\n",
        (date(2003, 7, 15), "SYM1"),
    )


def test_empty_response() -> None:
    conn = _make_conn(["SYM1"])
    http = MagicMock(spec=httpx.Client)
    resp = _make_resp([])

    with patch(
        "manta_trading.data.universe.populate_delisted_dates.eodhd_get",
        return_value=resp,
    ):
        report = populate_delisted_dates(conn, http, api_key="key")

    assert report.updated == 0
    assert report.skipped_empty == 1
    assert report.error_count == 0

    # No UPDATE should have been called.
    execute_calls = [
        c
        for c in conn.cursor.return_value.execute.call_args_list
        if "UPDATE" in str(c)
    ]
    assert len(execute_calls) == 0


def test_http_error() -> None:
    conn = _make_conn(["SYM1"])
    http = MagicMock(spec=httpx.Client)

    err_resp = MagicMock(spec=httpx.Response)
    err_resp.status_code = 404

    with patch(
        "manta_trading.data.universe.populate_delisted_dates.eodhd_get",
        side_effect=httpx.HTTPStatusError("Not Found", request=MagicMock(), response=err_resp),
    ):
        report = populate_delisted_dates(conn, http, api_key="key")

    assert report.error_count == 1
    assert report.updated == 0


def test_dry_run() -> None:
    conn = _make_conn(["SYM1"])
    http = MagicMock(spec=httpx.Client)
    resp = _make_resp([{"date": "2003-07-15", "close": 1.23}])

    with patch(
        "manta_trading.data.universe.populate_delisted_dates.eodhd_get",
        return_value=resp,
    ):
        report = populate_delisted_dates(conn, http, api_key="key", dry_run=True)

    assert report.updated == 0

    execute_calls = [
        c
        for c in conn.cursor.return_value.execute.call_args_list
        if "UPDATE" in str(c)
    ]
    assert len(execute_calls) == 0


def test_progress_callback() -> None:
    conn = _make_conn(["SYM1"])
    http = MagicMock(spec=httpx.Client)
    resp = _make_resp([{"date": "2003-07-15", "close": 1.23}])
    received: list[tuple] = []

    def _cb(processed: int, total: int, sym: str, last_bar: date | None) -> None:
        received.append((processed, total, sym, last_bar))

    with patch(
        "manta_trading.data.universe.populate_delisted_dates.eodhd_get",
        return_value=resp,
    ):
        populate_delisted_dates(conn, http, api_key="key", on_progress=_cb)

    assert received == [(1, 1, "SYM1", date(2003, 7, 15))]
