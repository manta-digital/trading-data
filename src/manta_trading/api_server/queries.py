"""SQL shared by more than one route module.

Instrument and gap SQL lives in the route layer by convention (slices 183/184);
this module holds the fragments that would otherwise be written twice. The
OHLCV DB classes are not the place for it — ``instruments`` is not their table.
"""

from __future__ import annotations

from typing import Any

import psycopg

_SYMBOL_EXISTS_SQL = """
    SELECT 1
    FROM instruments
    WHERE symbol = %s
"""
"""Primary-key seek, no projection: the caller only needs existence."""


def symbol_exists(conn: psycopg.Connection[Any], symbol: str) -> bool:
    """Return whether ``symbol`` is a known instrument (slice 186 D5).

    Deliberately no ``try/except``. This answer *decides a status code* — 404
    versus an empty 200 — and a failed lookup means the server does not know
    which is true. Neither "assume it exists" nor "assume it doesn't" is
    acceptable, so failures propagate to the app-level handlers:
    ``QueryCanceled`` becomes a ``504`` (D10) and any other ``psycopg.Error``
    a sanitized ``500``. Both are retryable and assert nothing about the symbol.
    """
    return conn.execute(_SYMBOL_EXISTS_SQL, (symbol,)).fetchone() is not None
