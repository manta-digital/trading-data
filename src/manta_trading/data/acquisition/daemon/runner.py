"""Long-running daemon runner (slice 146 Decisions B, F, G).

Wraps the slice 145 cycle functions in a single-thread loop with
token-bucket throttling, cycle-due gating, SIGTERM handling, and a
once-per-UTC-day CA-update step.

Public API:
  - :class:`RunnerConfig` — frozen scope/policy DTO.
  - :class:`RunnerState` — mutable cycle-timing accounting.
  - :class:`Runner` — owns the QuotaBucket, signal handlers, and
    main loop.
  - Predicate helpers (:func:`daily_cycle_due`, :func:`minute_cycle_due`,
    :func:`ca_update_due`, :func:`sleep_until_next_due_event`) — pure
    functions of ``(state, clock, conn)`` so they unit-test without a
    real loop.

The QuotaBucket itself is published via ``QUOTA_BUCKET_VAR`` (a
``contextvars.ContextVar``) so EODHD HTTP wrappers can call
``QUOTA_BUCKET_VAR.get().consume(call_type)`` without explicit
plumbing through the cycle functions (T16).
"""

from __future__ import annotations

import contextvars
import signal
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from manta_trading.constants import DAILY_CYCLE_START_OFFSET
from manta_trading.data.acquisition.quota import CallType, QuotaBucket
from manta_trading.logging import get_logger

if TYPE_CHECKING:
    import psycopg

    from manta_trading.config import Settings

_logger = get_logger(__name__)

_UTC = timezone.utc

CA_UPDATE_SENTINEL_SYMBOL: str = "__bulk_ca__"
"""Sentinel symbol whose acquisition_state row stores the once-per-UTC-day
CA-update gate timestamp (Decision G)."""

CA_UPDATE_SENTINEL_GRANULARITY: str = "daily"

QUOTA_BUCKET_VAR: contextvars.ContextVar[QuotaBucket | None] = (
    contextvars.ContextVar("manta_quota_bucket", default=None)
)
"""ContextVar holding the QuotaBucket for the current daemon process.

EODHD HTTP wrappers consume the bucket via ``QUOTA_BUCKET_VAR.get()``.
Set by :meth:`Runner.start` (and by one-shot CLI commands that need
throttled outbound calls). Defaults to ``None`` so unit tests that don't
exercise the HTTP path don't need to provide a bucket — the wrapper
raises if it's missing.
"""


# ---------------------------------------------------------------------------
# Config / state
# ---------------------------------------------------------------------------


SCOPE_ALL_ACTIVE: str = "ALL_ACTIVE"
"""Sentinel scope value meaning "iter_active_instruments at cycle entry"."""


@dataclass(frozen=True)
class RunnerConfig:
    """Runner policy.

    ``scope``: either :data:`SCOPE_ALL_ACTIVE` or an explicit list of
    tickers. The cycle functions accept ``symbols=None`` for the active
    universe and ``symbols=[...]`` otherwise.

    ``granularities``: subset of ``{"daily", "minute"}``.

    ``max_credits``: hard ceiling on rolling-24h spend; the runner exits
    when ``bucket.spent_today() >= max_credits``. ``None`` = unlimited.

    ``terminate_when_drained``: when True, the runner exits after one
    iteration in which no cycle was due. Default for ``--symbols`` /
    ``--list NAME`` invocations; the operator opts back into
    --forever for steady-state.
    """

    scope: str | tuple[str, ...] = SCOPE_ALL_ACTIVE
    granularities: frozenset[str] = field(
        default_factory=lambda: frozenset({"daily", "minute"})
    )
    max_credits: int | None = None
    terminate_when_drained: bool = False

    def is_explicit_scope(self) -> bool:
        return self.scope != SCOPE_ALL_ACTIVE

    def explicit_symbols(self) -> list[str] | None:
        if self.scope == SCOPE_ALL_ACTIVE:
            return None
        if isinstance(self.scope, tuple):
            return list(self.scope)
        raise TypeError(f"Unexpected scope type: {type(self.scope).__name__}")


@dataclass
class RunnerState:
    """Mutable cycle-timing accounting; cleared at process start."""

    last_daily_cycle_start_utc: datetime | None = None
    last_minute_cycle_start_utc: datetime | None = None
    last_minute_cycle_end_utc: datetime | None = None


# ---------------------------------------------------------------------------
# Predicates (T17 — pure functions)
# ---------------------------------------------------------------------------


def _utc_today(now: datetime) -> date:
    return now.astimezone(_UTC).date()


def daily_cycle_due(state: RunnerState, now: datetime) -> bool:
    """True iff a daily cycle has not started yet on the current UTC day
    AND the current UTC time is past ``00:00 + DAILY_CYCLE_START_OFFSET``.
    """
    today = _utc_today(now)
    midnight = datetime(today.year, today.month, today.day, tzinfo=_UTC)
    if now < midnight + DAILY_CYCLE_START_OFFSET:
        return False
    if state.last_daily_cycle_start_utc is None:
        return True
    return state.last_daily_cycle_start_utc.astimezone(_UTC).date() < today


def minute_cycle_due(state: RunnerState, now: datetime) -> bool:
    """True iff at least one minute has passed since the previous minute
    cycle's end (or no minute cycle has run yet).

    Whether any scope member has actionable minute gaps is determined
    inside ``run_minute_cycle`` itself (data_gaps-driven); the runner
    just gates on cadence so it doesn't busy-loop.
    """
    if state.last_minute_cycle_end_utc is None:
        return True
    return now - state.last_minute_cycle_end_utc >= timedelta(minutes=1)


def ca_update_due(
    conn: "psycopg.Connection[Any]",
    now: datetime,
) -> bool:
    """Once-per-UTC-day gate for ``mt data ca update`` (Decision G).

    Reads the sentinel row from ``acquisition_state`` keyed by
    ``(CA_UPDATE_SENTINEL_SYMBOL, CA_UPDATE_SENTINEL_GRANULARITY)``.

    Treats missing-row and ``last_attempt_ts IS NULL`` identically as
    "never updated" → returns True (subject to the same grace gate as
    daily). NEVER calls ``.date()`` on None.
    """
    today = _utc_today(now)
    midnight = datetime(today.year, today.month, today.day, tzinfo=_UTC)
    if now < midnight + DAILY_CYCLE_START_OFFSET:
        return False

    with conn.cursor() as cur:
        cur.execute(
            "SELECT last_attempt_ts FROM acquisition_state "
            "WHERE symbol = %s AND granularity = %s AND provider = 'eodhd'",
            (CA_UPDATE_SENTINEL_SYMBOL, CA_UPDATE_SENTINEL_GRANULARITY),
        )
        row = cur.fetchone()

    if row is None or row[0] is None:
        return True
    last_ts: datetime = row[0]
    return last_ts.astimezone(_UTC).date() < today


def sleep_until_next_due_event(
    state: RunnerState,
    now: datetime,
    sleep: Callable[[float], None] = time.sleep,
    *,
    cap_seconds: float = 60.0,
) -> None:
    """Sleep until the soonest-due event, capped at ``cap_seconds``.

    The cap exists so SIGTERM has bounded latency: at worst a SIGTERM
    arriving immediately after a sleep starts must wait ``cap_seconds``
    before the loop checks ``should_exit`` again.
    """
    today = _utc_today(now)
    midnight = datetime(today.year, today.month, today.day, tzinfo=_UTC)
    next_daily_start = midnight + DAILY_CYCLE_START_OFFSET + timedelta(days=1)

    candidates: list[float] = []
    if state.last_minute_cycle_end_utc is not None:
        next_minute = state.last_minute_cycle_end_utc + timedelta(minutes=1)
        candidates.append((next_minute - now).total_seconds())
    candidates.append((next_daily_start - now).total_seconds())

    wait = min(c for c in candidates if c > 0) if candidates else cap_seconds
    sleep(min(max(wait, 0.0), cap_seconds))


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


class Runner:
    """Long-running daemon (Decision B).

    Single-threaded loop; alternates between daily, minute, and the
    once-per-UTC-day CA-update step. Owns the :class:`QuotaBucket` and
    the SIGTERM/SIGINT handlers.

    The runner does NOT open its own connection pool; cycle functions
    open their own pools (slice 145 contract). The ``conn_factory``
    callable is used only for runner-owned reads (``ca_update_due``)
    so tests can inject a mock conn.
    """

    def __init__(
        self,
        config: RunnerConfig,
        bucket: QuotaBucket,
        conn_factory: Callable[[], "psycopg.Connection[Any]"],
        *,
        run_daily_cycle: Callable[..., Any] | None = None,
        run_minute_cycle: Callable[..., Any] | None = None,
        run_ca_update: Callable[[QuotaBucket], None] | None = None,
        clock: Callable[[], datetime] | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._config = config
        self._bucket = bucket
        self._conn_factory = conn_factory
        self._idle_hooks: list[Callable[[], None]] = []
        # Late-bind cycle functions so tests can inject mocks.
        if run_daily_cycle is None:
            from manta_trading.data.acquisition.daemon.daily import (
                run_daily_cycle as _run_daily,
            )
            run_daily_cycle = _run_daily
        if run_minute_cycle is None:
            from manta_trading.data.acquisition.daemon.minute import (
                run_minute_cycle as _run_minute,
            )
            run_minute_cycle = _run_minute
        self._run_daily_cycle = run_daily_cycle
        self._run_minute_cycle = run_minute_cycle
        self._run_ca_update = run_ca_update or _ca_update_noop
        self._clock = clock or (lambda: datetime.now(_UTC))
        self._sleep = sleep
        self._state = RunnerState()
        self._should_exit: bool = False

    # T19 — SIGTERM/SIGINT handling
    def _install_signal_handlers(
        self,
    ) -> tuple[Any, Any]:
        def handler(signum: int, _frame: Any) -> None:
            _logger.info("runner: received signal %d — initiating clean exit", signum)
            self._should_exit = True

        prev_term = signal.signal(signal.SIGTERM, handler)
        prev_int = signal.signal(signal.SIGINT, handler)
        return prev_term, prev_int

    def _restore_signal_handlers(self, prev_term: Any, prev_int: Any) -> None:
        signal.signal(signal.SIGTERM, prev_term)
        signal.signal(signal.SIGINT, prev_int)

    def register_idle_hook(self, fn: Callable[[], None]) -> None:
        """Register a callable to be invoked between cycles in the main loop.

        The hook is called synchronously; exceptions are caught and logged at
        ERROR level without stopping the loop. The hook itself is responsible
        for any internal gating (e.g., 24h rate-limiting).
        """
        self._idle_hooks.append(fn)

    def _run_idle_hooks(self) -> None:
        for hook in self._idle_hooks:
            try:
                hook()
            except Exception:
                _logger.exception("runner: idle hook %r raised — continuing", hook)

    def _should_continue(self) -> bool:
        return not self._should_exit

    def _max_credits_exhausted(self) -> bool:
        if self._config.max_credits is None:
            return False
        return self._bucket.spent_today() >= self._config.max_credits

    def _check_should_exit(self) -> bool:
        if self._should_exit:
            return True
        if self._max_credits_exhausted():
            _logger.info(
                "runner: max_credits=%d exhausted (spent_today=%d) — exiting",
                self._config.max_credits,
                self._bucket.spent_today(),
            )
            return True
        return False

    def start(self) -> int:
        """Run the main loop; returns process exit code (0 = normal)."""
        prev_term, prev_int = self._install_signal_handlers()
        token = QUOTA_BUCKET_VAR.set(self._bucket)
        try:
            return self._loop()
        finally:
            QUOTA_BUCKET_VAR.reset(token)
            self._restore_signal_handlers(prev_term, prev_int)

    def _loop(self) -> int:
        symbols_arg = self._config.explicit_symbols()
        granularities = self._config.granularities

        while True:
            if self._check_should_exit():
                return 0

            now = self._clock()
            did_anything = False

            # CA update (once per UTC day)
            try:
                with self._conn_factory() as conn:
                    if ca_update_due(conn, now):
                        self._run_ca_update(self._bucket)
                        did_anything = True
            except Exception:
                _logger.exception("runner: ca_update_due check failed — continuing")

            if self._check_should_exit():
                return 0

            # Daily cycle
            if "daily" in granularities and daily_cycle_due(self._state, now):
                _logger.info("runner: starting daily cycle")
                self._state.last_daily_cycle_start_utc = now
                try:
                    self._run_daily_cycle(
                        symbols=symbols_arg,
                        should_continue=self._should_continue,
                    )
                except Exception:
                    _logger.exception("runner: run_daily_cycle raised")
                did_anything = True

            if self._check_should_exit():
                return 0

            # Minute cycle
            if "minute" in granularities and minute_cycle_due(self._state, now):
                _logger.info("runner: starting minute cycle")
                self._state.last_minute_cycle_start_utc = now
                try:
                    self._run_minute_cycle(
                        symbols=symbols_arg,
                        should_continue=self._should_continue,
                    )
                except Exception:
                    _logger.exception("runner: run_minute_cycle raised")
                self._state.last_minute_cycle_end_utc = self._clock()
                did_anything = True

            # Idle hooks (e.g., auto-extend trading_sessions horizon).
            self._run_idle_hooks()

            if not did_anything:
                if self._config.terminate_when_drained:
                    _logger.info(
                        "runner: scope drained and terminate_when_drained=True — exiting"
                    )
                    return 0
                sleep_until_next_due_event(
                    self._state, self._clock(), self._sleep
                )


def _ca_update_noop(_bucket: QuotaBucket) -> None:
    """Injected default when no real CA-update function is provided (tests)."""
    _logger.debug("runner: ca_update noop (no real function wired)")



def make_ca_update_fn(
    settings: "Settings",
) -> "Callable[[QuotaBucket], None]":
    """Return a ``run_ca_update`` function closed over ``settings`` (T25).

    The returned callable fetches yesterday's bulk splits + dividends via
    EODHD, upserts them, and advances the sentinel row's
    ``last_attempt_ts`` on success.  On failure it logs at WARNING and
    leaves the sentinel row un-advanced so the next iteration retries.

    Args:
        settings: Application settings (needs ``timescale_db_url``,
            ``eodhd_api_key``).

    Returns:
        Callable matching ``(bucket: QuotaBucket) -> None``.
    """
    def _run_ca_update(bucket: QuotaBucket) -> None:
        from datetime import date, timedelta

        import httpx

        from manta_trading.data.adjustment.ingest import (
            upsert_dividends,
            upsert_splits,
        )
        from manta_trading.data.adjustment.providers.bulk_ca import (
            fetch_bulk_dividends,
            fetch_bulk_splits,
        )

        yesterday = date.today() - timedelta(days=1)
        api_key = settings.eodhd_api_key
        if not api_key:
            _logger.warning("run_ca_update: MT_EODHD_API_KEY not set — skipping")
            return
        if not settings.timescale_db_url:
            _logger.warning("run_ca_update: MT_TIMESCALE_DB_URL not set — skipping")
            return

        try:
            with httpx.Client(timeout=30.0) as client:
                splits = fetch_bulk_splits(client, yesterday, api_key=api_key)
                divs = fetch_bulk_dividends(client, yesterday, api_key=api_key)
            upsert_splits(str(settings.timescale_db_url), splits)
            upsert_dividends(str(settings.timescale_db_url), divs)
        except Exception:
            _logger.warning(
                "run_ca_update: bulk CA fetch/upsert failed for %s — "
                "sentinel not advanced; will retry next iteration",
                yesterday,
                exc_info=True,
            )
            return

        # Advance the sentinel so this UTC day is not retried.
        _advance_ca_sentinel(settings.timescale_db_url, yesterday)
        _logger.info(
            "run_ca_update: bulk CA complete for %s "
            "(%d splits, %d dividends)",
            yesterday, len(splits), len(divs),
        )

    return _run_ca_update




def _advance_ca_sentinel(timescale_db_url: str, for_date: object) -> None:
    """Upsert the CA-update sentinel row to stamp today's UTC timestamp.

    Uses a raw psycopg connection (not the pool) so this function has no
    dependency on the runner's conn_factory.
    """
    import psycopg

    sql = """
        INSERT INTO acquisition_state
            (symbol, granularity, provider, last_attempt_ts, updated_at)
        VALUES (%s, %s, 'eodhd', NOW(), NOW())
        ON CONFLICT (symbol, granularity, provider) DO UPDATE SET
            last_attempt_ts = NOW(),
            updated_at      = NOW()
    """
    with psycopg.connect(timescale_db_url, autocommit=True) as conn:
        conn.execute(sql, (CA_UPDATE_SENTINEL_SYMBOL, CA_UPDATE_SENTINEL_GRANULARITY))
