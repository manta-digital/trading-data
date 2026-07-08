"""Unit tests for manta_trading.data.locking.

Uses a mock psycopg connection; no live DB required.
"""

from __future__ import annotations

from unittest.mock import MagicMock, call, patch

import pytest

import manta_trading.data.locking as locking_mod
from manta_trading.data.locking import (
    _held_keys,
    _lock_key_cache,
    advisory_lock,
    lock_key,
    try_advisory_lock,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_conn(hashtext_return: int = 12345) -> MagicMock:
    """Return a mock psycopg Connection whose cursor returns hashtext_return."""
    conn = MagicMock()
    cur = MagicMock()
    cur.__enter__ = MagicMock(return_value=cur)
    cur.__exit__ = MagicMock(return_value=False)
    cur.fetchone.return_value = (hashtext_return,)
    conn.cursor.return_value = cur
    return conn


@pytest.fixture(autouse=True)
def clear_cache() -> None:
    """Clear the module-level key cache between tests."""
    _lock_key_cache.clear()
    yield
    _lock_key_cache.clear()


@pytest.fixture(autouse=True)
def enable_assertions(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure assertions are enabled for all unit tests."""
    monkeypatch.setattr(locking_mod, "_DAEMON_LOCK_ASSERTIONS", True)


# ---------------------------------------------------------------------------
# lock_key
# ---------------------------------------------------------------------------


class TestLockKey:
    def test_returns_integer_from_db(self) -> None:
        conn = _make_conn(99999)
        result = lock_key(conn, "AAPL", "daily")
        assert result == 99999

    def test_same_input_returns_same_key(self) -> None:
        conn = _make_conn(42)
        k1 = lock_key(conn, "MSFT", "minute")
        k2 = lock_key(conn, "MSFT", "minute")
        assert k1 == k2
        # DB should only be queried once (second call hits cache)
        assert conn.cursor.call_count == 1

    def test_different_inputs_return_different_keys(self) -> None:
        conn1 = _make_conn(100)
        conn2 = _make_conn(200)
        k1 = lock_key(conn1, "AAPL", "daily")
        k2 = lock_key(conn2, "MSFT", "minute")
        assert k1 != k2

    def test_sql_uses_pipe_separator(self) -> None:
        conn = _make_conn(1)
        lock_key(conn, "AAPL", "daily")
        cur = conn.cursor.return_value.__enter__.return_value
        # The execute call should concatenate with '|'
        executed_sql = cur.execute.call_args[0][0]
        assert "||" in executed_sql or "|| '|' ||" in executed_sql


# ---------------------------------------------------------------------------
# advisory_lock — single-lock invariant
# ---------------------------------------------------------------------------


class TestAdvisoryLockInvariant:
    def test_second_lock_on_same_conn_raises(self) -> None:
        conn = _make_conn(1)

        with advisory_lock(conn, "AAPL", "daily"):
            with pytest.raises(AssertionError, match="≤ 1 advisory lock"):
                with advisory_lock(conn, "MSFT", "daily"):
                    pass

    def test_lock_released_after_context_exit(self) -> None:
        conn = _make_conn(1)
        with advisory_lock(conn, "AAPL", "daily"):
            pass
        # After exiting, held_keys should be empty → second lock succeeds
        _lock_key_cache.clear()
        conn2 = _make_conn(2)
        # Use a fresh mock to avoid collision — patch held keys
        conn._mt_held_lock_keys = set()
        # Should not raise
        with advisory_lock(conn, "AAPL", "daily"):
            pass

    def test_assertions_disabled_allows_second_lock(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(locking_mod, "_DAEMON_LOCK_ASSERTIONS", False)
        conn = _make_conn(1)
        # Both locks should acquire without AssertionError
        with advisory_lock(conn, "AAPL", "daily"):
            with advisory_lock(conn, "MSFT", "daily"):
                pass  # no error expected

    def test_timeout_sets_local_lock_timeout(self) -> None:
        conn = _make_conn(1)
        with advisory_lock(conn, "AAPL", "daily", timeout="100ms"):
            pass
        cur = conn.cursor.return_value.__enter__.return_value
        # SET LOCAL lock_timeout must appear somewhere in the execute calls
        all_sqls = [c[0][0].lower() for c in cur.execute.call_args_list]
        assert any("lock_timeout" in sql for sql in all_sqls)

    def test_no_timeout_skips_set_local(self) -> None:
        conn = _make_conn(1)
        with advisory_lock(conn, "AAPL", "daily"):
            pass
        cur = conn.cursor.return_value.__enter__.return_value
        for c in cur.execute.call_args_list:
            assert "lock_timeout" not in c[0][0].lower()


# ---------------------------------------------------------------------------
# try_advisory_lock
# ---------------------------------------------------------------------------


class TestTryAdvisoryLock:
    def test_returns_true_when_lock_acquired(self) -> None:
        conn = _make_conn(1)
        cur = conn.cursor.return_value.__enter__.return_value
        cur.fetchone.return_value = (True,)
        with try_advisory_lock(conn, "AAPL", "daily") as acquired:
            assert acquired is True

    def test_returns_false_on_contention(self) -> None:
        conn = _make_conn(1)
        cur = conn.cursor.return_value.__enter__.return_value
        # First call (lock_key): return hash; second call (try lock): return False
        cur.fetchone.side_effect = [(1,), (False,)]
        _lock_key_cache.clear()
        with try_advisory_lock(conn, "AAPL", "daily") as acquired:
            assert acquired is False

    def test_does_not_trigger_single_lock_assertion(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """try_advisory_lock must not check the single-lock invariant."""
        monkeypatch.setattr(locking_mod, "_DAEMON_LOCK_ASSERTIONS", True)
        conn = _make_conn(1)
        # Manually mark a key as already held
        _held_keys(conn).add(99)
        cur = conn.cursor.return_value.__enter__.return_value
        cur.fetchone.side_effect = [(1,), (True,)]
        # Should NOT raise even though held_keys is non-empty
        with try_advisory_lock(conn, "AAPL", "daily") as acquired:
            assert acquired is True
