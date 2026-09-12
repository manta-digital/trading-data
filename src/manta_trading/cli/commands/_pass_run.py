"""Building a PassRunRecorder from settings, in one place (slice 922).

Four commands record a pass run — the daemon's minute and daily cycles,
``mt data health`` and ``mt data accounting`` — and each needs the same
connection pool and the same failure posture. That construction lives here so
those commands do not each carry a copy of it.

Recording is best-effort by design: a command whose database is unreachable
still does its real work and reports its own result. So a recorder that
cannot be built is simply absent, and every call site already tolerates
``None`` (a ``--symbols`` scope passes ``None`` deliberately).
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

from psycopg_pool import ConnectionPool

from manta_trading.config import Settings
from manta_trading.data.acquisition.daemon.pass_run_recorder import PassRunRecorder
from manta_trading.data.acquisition.pass_runs import PassRunRepository
from manta_trading.logging import get_logger

_logger = get_logger(__name__)

_POOL_MIN_SIZE = 1
_POOL_MAX_SIZE = 2


def _utc_now() -> datetime:
    return datetime.now(UTC)


@contextmanager
def pass_run_recorder(settings: Settings) -> Iterator[PassRunRecorder | None]:
    """Yield a recorder backed by its own small pool, or None if unavailable.

    The pool is closed on exit, so a short-lived command does not leave
    connections behind. ``None`` is yielded when the database URL is unset or
    the pool cannot be opened; the caller carries on unrecorded.
    """
    if not settings.timescale_db_url:
        _logger.warning(
            "pass_runs: MT_TIMESCALE_DB_URL is not set — this pass will not be recorded"
        )
        yield None
        return
    try:
        pool = ConnectionPool(
            settings.timescale_db_url,
            min_size=_POOL_MIN_SIZE,
            max_size=_POOL_MAX_SIZE,
            open=True,
        )
    except Exception:
        # Any pool-construction failure (bad URL, unreachable host, auth)
        # leaves the pass unrecorded rather than unrun.
        _logger.exception(
            "pass_runs: could not open a connection pool — this pass will not "
            "be recorded"
        )
        yield None
        return
    try:
        yield PassRunRecorder(PassRunRepository(pool), _utc_now)
    finally:
        pool.close()


def make_pass_run_recorder(settings: Settings) -> PassRunRecorder | None:
    """A recorder over a pool that lives as long as the process.

    For the daemon, whose runner outlives any single ``with`` block. Returns
    ``None`` when the database URL is unset or the pool cannot be opened.
    """
    if not settings.timescale_db_url:
        _logger.warning(
            "pass_runs: MT_TIMESCALE_DB_URL is not set — passes will not be recorded"
        )
        return None
    try:
        pool = ConnectionPool(
            settings.timescale_db_url,
            min_size=_POOL_MIN_SIZE,
            max_size=_POOL_MAX_SIZE,
            open=True,
        )
    except Exception:
        _logger.exception(
            "pass_runs: could not open a connection pool — passes will not be recorded"
        )
        return None
    return PassRunRecorder(PassRunRepository(pool), _utc_now)
