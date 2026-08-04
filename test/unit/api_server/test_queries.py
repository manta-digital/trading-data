"""Unit tests for ``api_server.queries`` — the shared symbol-existence seek."""

from __future__ import annotations

from unittest.mock import MagicMock

import psycopg
import pytest

from manta_trading.api_server.queries import _SYMBOL_EXISTS_SQL, symbol_exists


def _conn(fetchone_result: object) -> MagicMock:
    conn = MagicMock(spec=psycopg.Connection)
    conn.execute.return_value.fetchone.return_value = fetchone_result
    return conn


def test_returns_true_when_a_row_is_found() -> None:
    assert symbol_exists(_conn((1,)), "AAPL") is True


def test_returns_false_when_no_row_is_found() -> None:
    assert symbol_exists(_conn(None), "ZZZZ") is False


def test_symbol_is_passed_as_a_bound_parameter() -> None:
    """Never interpolated: ``symbol`` is caller-supplied path input."""
    conn = _conn((1,))
    symbol_exists(conn, "AAPL")
    sql, params = conn.execute.call_args.args
    assert params == ("AAPL",)
    assert "AAPL" not in sql


def test_query_is_a_bare_existence_seek() -> None:
    """No projection and no join — the caller needs one bit, on the empty path
    of a request that already did its real work."""
    assert "FROM instruments" in _SYMBOL_EXISTS_SQL
    assert "WHERE symbol = %s" in _SYMBOL_EXISTS_SQL
    assert "SELECT 1" in _SYMBOL_EXISTS_SQL


@pytest.mark.parametrize(
    "error",
    [
        psycopg.errors.QueryCanceled("cancelled"),
        psycopg.OperationalError("connection lost"),
    ],
)
def test_failures_propagate_rather_than_defaulting(error: Exception) -> None:
    """The function must not decide 404-vs-200 on a failed lookup; the
    app-level handlers turn these into 504 and 500 respectively (D5 addendum)."""
    conn = MagicMock(spec=psycopg.Connection)
    conn.execute.side_effect = error
    with pytest.raises(type(error)):
        symbol_exists(conn, "AAPL")
