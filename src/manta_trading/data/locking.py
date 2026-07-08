"""PostgreSQL advisory lock primitives for the data acquisition daemon.

Two entry-points:
  advisory_lock      — exclusive xact-level lock for daemon writers (≤ 1 held at a time)
  try_advisory_lock  — non-blocking test for backtest read-path callers

Lock keys are derived from (symbol, granularity) via hashtextextended, cached
per-process in an LRU cache keyed on the pair.

Callers must be inside an open transaction; locks release automatically on
transaction end.
"""

from __future__ import annotations

import os
from collections.abc import Generator
from contextlib import contextmanager
from functools import lru_cache
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import psycopg

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_DAEMON_LOCK_ASSERTIONS: bool = os.environ.get("MT_DAEMON_DEBUG", "1") != "0"
"""When True, advisory_lock raises AssertionError if a second lock is requested
while the connection already holds one.  Flip to False via MT_DAEMON_DEBUG=0
once the invariant is proven stable in production."""

_LOCK_KEY_CACHE_SIZE: int = 4096


# ---------------------------------------------------------------------------
# Key derivation
# ---------------------------------------------------------------------------


@lru_cache(maxsize=_LOCK_KEY_CACHE_SIZE)
def _lock_key_cached(symbol: str, granularity: str) -> int | None:
    """Return None; the actual value is fetched from the DB on first call.

    This cache stores nothing on its own — see lock_key() which populates it
    by re-binding via lru_cache after the first DB round-trip.
    """
    return None


def lock_key(conn: "psycopg.Connection[object]", symbol: str, granularity: str) -> int:
    """Return the advisory lock key for (symbol, granularity).

    Computed as hashtextextended(symbol || '|' || granularity, 0) on the
    first call per (symbol, granularity) pair; subsequent calls return the
    cached value without a DB round-trip.
    """
    cached = _lock_key_cache.get((symbol, granularity))
    if cached is not None:
        return cached

    with conn.cursor() as cur:
        cur.execute(
            "SELECT hashtextextended(%s || '|' || %s, 0)",
            (symbol, granularity),
        )
        row = cur.fetchone()
        if row is None:
            raise RuntimeError(
                f"hashtextextended returned no row for ({symbol!r}, {granularity!r})"
            )
        key: int = int(row[0])

    _lock_key_cache[(symbol, granularity)] = key
    return key


# Module-level cache dict (simple dict avoids lru_cache's immutability
# requirement on arguments and lets us inspect/clear it in tests).
_lock_key_cache: dict[tuple[str, str], int] = {}


# ---------------------------------------------------------------------------
# Advisory lock context managers
# ---------------------------------------------------------------------------


@contextmanager
def advisory_lock(
    conn: "psycopg.Connection[object]",
    symbol: str,
    granularity: str,
    *,
    timeout: str | None = None,
) -> Generator[None, None, None]:
    """Acquire an exclusive transaction-level advisory lock for (symbol, granularity).

    The lock is released automatically when the caller's transaction ends
    (commit or rollback).  Caller must be inside an open transaction.

    Args:
        conn:        Open psycopg connection with an active transaction.
        symbol:      Instrument ticker.
        granularity: 'daily' or 'minute'.
        timeout:     Optional PostgreSQL lock_timeout string (e.g. '30 seconds').
                     If supplied, SET LOCAL lock_timeout is issued before the
                     lock attempt; psycopg.errors.LockNotAvailable is raised
                     on timeout (SQLSTATE 55P03).

    Raises:
        AssertionError:                    If _DAEMON_LOCK_ASSERTIONS is True and
                                           this connection already holds a lock
                                           (daemon "≤ 1 lock at a time" invariant).
        psycopg.errors.LockNotAvailable:   If timeout is set and the lock is not
                                           available within the budget.
    """
    key = lock_key(conn, symbol, granularity)

    if _DAEMON_LOCK_ASSERTIONS:
        held: set[int] = _held_keys(conn)
        if held:
            raise AssertionError(
                f"advisory_lock({symbol!r}, {granularity!r}): connection already "
                f"holds key(s) {held!r}. Daemon must hold ≤ 1 advisory lock at a "
                f"time. Ensure the previous lock's transaction was committed or "
                f"rolled back before acquiring a new one."
            )

    with conn.cursor() as cur:
        if timeout is not None:
            # SET LOCAL does not accept parameterized values in PostgreSQL.
            # Timeout is an internal constant (never user-supplied), validated
            # to contain only alphanumeric chars, spaces, and underscores.
            if not all(c.isalnum() or c in (" ", "_") for c in timeout):
                raise ValueError(f"Unsafe lock timeout value: {timeout!r}")
            cur.execute(f"SET LOCAL lock_timeout = '{timeout}'")  # noqa: S608
        cur.execute("SELECT pg_advisory_xact_lock(%s)", (key,))

    if _DAEMON_LOCK_ASSERTIONS:
        _held_keys(conn).add(key)

    try:
        yield
    finally:
        if _DAEMON_LOCK_ASSERTIONS:
            _held_keys(conn).discard(key)


@contextmanager
def try_advisory_lock(
    conn: "psycopg.Connection[object]",
    symbol: str,
    granularity: str,
) -> Generator[bool, None, None]:
    """Attempt a non-blocking exclusive advisory lock for (symbol, granularity).

    Intended for the backtest read-path where multiple locks may be held
    concurrently (sorted-acquisition discipline); skips the single-lock
    assertion.

    Yields:
        True  — lock acquired; caller may proceed.
        False — lock not available; caller should skip or retry.
    """
    key = lock_key(conn, symbol, granularity)
    with conn.cursor() as cur:
        cur.execute("SELECT pg_try_advisory_xact_lock(%s)", (key,))
        row = cur.fetchone()
        acquired: bool = bool(row[0]) if row is not None else False

    yield acquired


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _held_keys(conn: "psycopg.Connection[object]") -> set[int]:
    """Return (or initialise) the per-connection set of held advisory key ints."""
    held = getattr(conn, "_mt_held_lock_keys", None)
    if not isinstance(held, set):
        held = set()
        conn._mt_held_lock_keys = held  # type: ignore[attr-defined]
    return held
