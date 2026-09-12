"""Building a PassRunRecorder from settings, in one place (slice 922).

Five passes record themselves — the daemon's minute and daily cycles, the
Kalshi pass, ``mt data health`` and ``mt data accounting`` — and each needs
the same failure posture. That construction lives here so those commands do
not each carry a copy of it.

Recording is best-effort by design: a command whose database is unreachable
still does its real work and reports its own result. So a recorder that
cannot be built is simply absent, and every call site already tolerates
``None`` (a ``--symbols`` scope passes ``None`` deliberately).

Two shapes, because the two kinds of caller differ:

- a **short-lived command** connects per write, with a short timeout. It
  makes two or three writes in its whole life, so a pool would buy nothing
  and cost real harm: a pool that fills eagerly blocks the command for its
  own connect timeout when the host does not resolve, and one that
  fills lazily still runs background workers a one-shot process must wait on.
- the **daemon** already holds a pool for the life of the process and hands
  the repository its ``connection``.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

import psycopg
from psycopg_pool import ConnectionPool

from manta_trading.config import Settings
from manta_trading.constants import PASS_RUN_DB_CONNECT_TIMEOUT_SECONDS
from manta_trading.data.acquisition.daemon.pass_run_recorder import PassRunRecorder
from manta_trading.data.acquisition.pass_runs import PassRunRepository
from manta_trading.logging import get_logger

_logger = get_logger(__name__)

_POOL_MIN_SIZE = 1
_POOL_MAX_SIZE = 2


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _recorder_over_connect(url: str) -> PassRunRecorder:
    """A recorder that opens one short-lived connection per write."""

    @contextmanager
    def _connect() -> Iterator[psycopg.Connection]:
        with psycopg.connect(
            url, connect_timeout=PASS_RUN_DB_CONNECT_TIMEOUT_SECONDS
        ) as conn:
            yield conn

    return PassRunRecorder(PassRunRepository(_connect), _utc_now)


@contextmanager
def pass_run_recorder(settings: Settings) -> Iterator[PassRunRecorder | None]:
    """Yield a recorder for the duration of one command, or None.

    Nothing is connected here: the first write pays the connect cost, and a
    command that records nothing touches the database not at all.
    """
    url = settings.timescale_db_url
    if not url:
        _logger.warning(
            "pass_runs: MT_TIMESCALE_DB_URL is not set — this pass will not be recorded"
        )
        yield None
        return
    yield _recorder_over_connect(str(url))


def make_pass_run_recorder(
    settings: Settings,
) -> tuple[PassRunRecorder, ConnectionPool] | tuple[None, None]:
    """A recorder over a pool that lives as long as the process.

    For the daemon, whose runner outlives any single ``with`` block and whose
    repeated writes are worth a pool.

    Returns the recorder and the pool it owns, or ``(None, None)`` when the
    database URL is unset or the pool cannot be opened; either way the daemon
    runs. The pool comes back so the caller can close it on the way out
    rather than leaving its background workers to interpreter exit — which
    the ``--stop-when-done`` shape, unlike ``--forever``, actually reaches
    (922 review F009).
    """
    url = settings.timescale_db_url
    if not url:
        _logger.warning(
            "pass_runs: MT_TIMESCALE_DB_URL is not set — passes will not be recorded"
        )
        return None, None
    try:
        pool = ConnectionPool(
            str(url),
            min_size=_POOL_MIN_SIZE,
            max_size=_POOL_MAX_SIZE,
            kwargs={"connect_timeout": PASS_RUN_DB_CONNECT_TIMEOUT_SECONDS},
        )
    except Exception:  # noqa: BLE001
        # A pool this process cannot build leaves passes unrecorded, never
        # unrun: the daemon's job is acquisition, not bookkeeping. Broader
        # than psycopg.Error because psycopg_pool raises more than that here
        # (a malformed URL is a ValueError), and none of it may stop
        # acquisition. The sibling catch in overview.gather is suppressed the
        # same way; this one was missing its suppression (922 review F009).
        _logger.exception(
            "pass_runs: could not open a connection pool — passes will not be recorded"
        )
        return None, None
    return PassRunRecorder(PassRunRepository.from_pool(pool), _utc_now), pool
