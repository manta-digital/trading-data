"""Integration tests for manta_trading.data.locking.

Requires a live PostgreSQL connection (MT_TIMESCALE_DB_URL).
Tests exercise actual pg_advisory_xact_lock behavior including contention
and timeout semantics.
"""

from __future__ import annotations

import threading
import time

import psycopg
import pytest

import manta_trading.data.locking as locking_mod
from manta_trading.data.locking import (
    _lock_key_cache,
    advisory_lock,
    lock_key,
    try_advisory_lock,
)


@pytest.fixture(autouse=True)
def clear_cache() -> None:
    _lock_key_cache.clear()
    yield
    _lock_key_cache.clear()


@pytest.fixture(autouse=True)
def enable_assertions(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(locking_mod, "_DAEMON_LOCK_ASSERTIONS", True)


def _open(db_url: str) -> psycopg.Connection:  # type: ignore[type-arg]
    return psycopg.connect(db_url, autocommit=False)


# ---------------------------------------------------------------------------
# Basic locking behavior
# ---------------------------------------------------------------------------


class TestAdvisoryLockIntegration:
    def test_lock_and_release(self, timescale_db_url: str) -> None:
        """Lock acquired and released within a transaction."""
        with _open(timescale_db_url) as conn:
            with conn.transaction():
                with advisory_lock(conn, "AAPL", "daily"):
                    pass  # lock held here; auto-released at txn end

    def test_disjoint_scope_does_not_block(self, timescale_db_url: str) -> None:
        """Locks on different (symbol, granularity) pairs don't block each other."""
        results: dict[str, float] = {}
        barrier = threading.Barrier(2)

        def hold_aapl() -> None:
            with _open(timescale_db_url) as conn:
                with conn.transaction():
                    with advisory_lock(conn, "AAPL", "daily"):
                        barrier.wait()  # signal MSFT thread
                        time.sleep(1.0)  # hold AAPL for 1 second

        def acquire_msft() -> None:
            barrier.wait()  # wait until AAPL is held
            t0 = time.monotonic()
            with _open(timescale_db_url) as conn:
                with conn.transaction():
                    with advisory_lock(conn, "MSFT", "daily"):
                        pass
            results["msft_wait"] = time.monotonic() - t0

        t1 = threading.Thread(target=hold_aapl)
        t2 = threading.Thread(target=acquire_msft)
        t1.start()
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)

        assert "msft_wait" in results, "MSFT thread did not complete"
        assert results["msft_wait"] < 0.5, (
            f"MSFT lock took {results['msft_wait']:.3f}s — "
            "should not block while AAPL is held"
        )

    def test_same_key_blocks_second_connection(self, timescale_db_url: str) -> None:
        """Second connection blocks on the same (symbol, granularity) until first commits."""
        _lock_key_cache.clear()
        acquired_order: list[str] = []
        barrier = threading.Barrier(2)

        def hold_aapl() -> None:
            with _open(timescale_db_url) as conn:
                with conn.transaction():
                    with advisory_lock(conn, "AAPL", "daily"):
                        acquired_order.append("A")
                        barrier.wait()
                        time.sleep(0.3)
            # txn committed; lock released

        def wait_for_aapl() -> None:
            barrier.wait()  # wait until A holds the lock
            with _open(timescale_db_url) as conn:
                with conn.transaction():
                    with advisory_lock(conn, "AAPL", "daily"):
                        acquired_order.append("B")

        t1 = threading.Thread(target=hold_aapl)
        t2 = threading.Thread(target=wait_for_aapl)
        t1.start()
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)

        assert acquired_order == ["A", "B"], (
            f"Expected A then B; got {acquired_order!r}"
        )

    def test_timeout_raises_lock_not_available(self, timescale_db_url: str) -> None:
        """advisory_lock with timeout raises LockNotAvailable when contended."""
        _lock_key_cache.clear()
        barrier = threading.Barrier(2)
        error_holder: list[Exception] = []

        def hold_googl() -> None:
            with _open(timescale_db_url) as conn:
                with conn.transaction():
                    with advisory_lock(conn, "GOOGL", "daily"):
                        barrier.wait()
                        time.sleep(1.0)  # hold long enough for timeout to fire

        def try_with_timeout() -> None:
            barrier.wait()
            try:
                with _open(timescale_db_url) as conn:
                    with conn.transaction():
                        with advisory_lock(conn, "GOOGL", "daily", timeout="100ms"):
                            pass
            except psycopg.errors.LockNotAvailable as exc:
                error_holder.append(exc)

        t1 = threading.Thread(target=hold_googl)
        t2 = threading.Thread(target=try_with_timeout)
        t1.start()
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)

        assert len(error_holder) == 1, (
            f"Expected LockNotAvailable; got {error_holder!r}"
        )
        assert isinstance(error_holder[0], psycopg.errors.LockNotAvailable)


# ---------------------------------------------------------------------------
# try_advisory_lock
# ---------------------------------------------------------------------------


class TestTryAdvisoryLockIntegration:
    def test_returns_true_on_free_key(self, timescale_db_url: str) -> None:
        _lock_key_cache.clear()
        with _open(timescale_db_url) as conn:
            with conn.transaction():
                with try_advisory_lock(conn, "TSLA", "minute") as acquired:
                    assert acquired is True

    def test_returns_false_on_contention(self, timescale_db_url: str) -> None:
        _lock_key_cache.clear()
        barrier = threading.Barrier(2)
        result_holder: list[bool] = []

        def hold_tsla() -> None:
            with _open(timescale_db_url) as conn:
                with conn.transaction():
                    with advisory_lock(conn, "TSLA", "minute"):
                        barrier.wait()
                        time.sleep(0.5)

        def try_tsla() -> None:
            barrier.wait()
            with _open(timescale_db_url) as conn:
                with conn.transaction():
                    with try_advisory_lock(conn, "TSLA", "minute") as acquired:
                        result_holder.append(acquired)

        t1 = threading.Thread(target=hold_tsla)
        t2 = threading.Thread(target=try_tsla)
        t1.start()
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)

        assert result_holder == [False], (
            f"Expected [False] on contention; got {result_holder!r}"
        )


# ---------------------------------------------------------------------------
# Single-lock invariant
# ---------------------------------------------------------------------------


class TestSingleLockInvariantIntegration:
    def test_second_lock_same_conn_raises(self, timescale_db_url: str) -> None:
        with _open(timescale_db_url) as conn:
            with conn.transaction():
                with advisory_lock(conn, "AAPL", "daily"):
                    with pytest.raises(AssertionError, match="≤ 1 advisory lock"):
                        with advisory_lock(conn, "MSFT", "daily"):
                            pass

    def test_assertions_disabled_allows_second_lock(
        self,
        timescale_db_url: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(locking_mod, "_DAEMON_LOCK_ASSERTIONS", False)
        with _open(timescale_db_url) as conn:
            with conn.transaction():
                with advisory_lock(conn, "AAPL", "daily"):
                    # No AssertionError when assertions disabled
                    with advisory_lock(conn, "MSFT", "daily"):
                        pass
