"""Minute data acquisition daemon — data_gaps-driven cycle (slice 145)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

import httpx
import psycopg
from psycopg_pool import ConnectionPool, PoolTimeout

from manta_trading.api.eodhd_sync import eodhd_get
from manta_trading.config import Settings
from manta_trading.constants import (
    DAEMON_LOCK_TIMEOUT,
    DB_BULK_SESSION,
    EODHD_INTRADAY_HORIZON,
    MAX_RETRY_COUNT,
    MINUTE_PASS_MAX_CONSECUTIVE_PROVIDER_FAILURES,
    MINUTE_SEED_PROGRESS_LOG_INTERVAL,
    MINUTE_TRAILING_COMPLETE_LINE,
    MINUTE_TRAILING_MAX_CHUNKS_PER_SYMBOL,
    MINUTE_TRAILING_PRIORITY_WINDOW,
    MINUTE_TRUNCATION_LOOKBACK,
    FetchEntryPoint,
    MinutePassPhase,
)
from manta_trading.data.acquisition.daemon.daily import (
    CycleReport,
    _last_completed_session,
    _normalise,
)
from manta_trading.data.acquisition.daemon.minute_sessions import (
    judge_chunk_by_sessions,
)
from manta_trading.data.acquisition.outcomes import (
    ProviderQuotaExhausted,
    ProviderResponseError,
    classify_outcome,
    outcome_to_fetch_status,
    response_carries_an_answer,
)
from manta_trading.data.acquisition.quota import CallType, QuotaWaitAborted
from manta_trading.data.acquisition.state import (
    LastAttemptOutcome,
    MinuteFailureKind,
    MinutePassOutcome,
)
from manta_trading.data.acquisition.symbols import iter_active_instruments
from manta_trading.data.gaps import (
    GapRow,
    coalesce_data_gaps,
    pick_most_recent_actionable_gap,
    update_data_gaps,
)
from manta_trading.data.gaps.compute_missing_ranges import fetch_session_bounds
from manta_trading.data.gaps.minute_coverage import (
    build_minute_coverage_index,
    build_symbol_minute_coverage,
    compute_missing_minute_sessions,
)
from manta_trading.data.gaps.repair_921 import (
    RepairScanTimeout,
    build_truncated_day_index,
)
from manta_trading.data.locking import advisory_lock
from manta_trading.data.quality.fetch_status import FetchStatus
from manta_trading.logging import get_logger
from manta_trading.market.db_session import make_configure_connection
from manta_trading.minute_firing_schedule import (
    describe_firing_days,
    is_firing_day,
)

_logger = get_logger(__name__)

_UTC = timezone.utc
_EODHD_BASE = "https://eodhd.com/api"
_REQUEST_TIMEOUT = httpx.Timeout(connect=10.0, read=60.0, write=10.0, pool=5.0)
_PROVIDER_MAX_CHUNK_DAYS: int = 120


# --- Process exit codes for a minute pass (slice 921 Task 4.6) -------------
# There is no project-wide exit-code constant; the nearest precedent is
# EXIT_BY_OUTCOME in cli/commands/kalshi.py:48-58, which this follows: named
# constants plus ONE lookup table, never scattered conditionals and never a
# bare literal at the exit site.

MINUTE_EXIT_OK = 0
"""The pass did what the firing exists to do."""

MINUTE_EXIT_PASS_INCOMPLETE = 3
"""The pass ended without collecting the current session. Chosen as 3 to match
kalshi.py's EXIT_SYNC_PARTIAL — the same meaning at the same code."""


def minute_pass_exit_code(
    outcome: MinutePassOutcome, *, trailing_completed: bool
) -> int:
    """Map a pass outcome to the process exit code.

    ``QUOTA_EXHAUSTED`` is the one outcome whose code depends on WHEN it
    happened, which is why it cannot be a plain dict lookup on the outcome
    alone:

    - **After the trailing phase completed** the current session is already
      collected and the remaining allowance was being spent on backfill. That
      is the designed steady state, not a fault — exit 0. Alerting on it would
      alert nightly.
    - **Inside the trailing phase** the allowance ran out before every symbol's
      current session was attempted, so the data the firing exists to collect
      is missing — exit 3.

    ``PROVIDER_UNAVAILABLE`` is a fault in either phase: a provider-failure
    streak means requests are being spent to learn the same thing.
    """
    if outcome is MinutePassOutcome.COMPLETE:
        return MINUTE_EXIT_OK
    if outcome is MinutePassOutcome.QUOTA_EXHAUSTED and trailing_completed:
        return MINUTE_EXIT_OK
    return _MINUTE_EXIT_BY_OUTCOME[outcome]


_MINUTE_EXIT_BY_OUTCOME: dict[MinutePassOutcome, int] = {
    MinutePassOutcome.COMPLETE: MINUTE_EXIT_OK,
    MinutePassOutcome.QUOTA_EXHAUSTED: MINUTE_EXIT_PASS_INCOMPLETE,
    MinutePassOutcome.PROVIDER_UNAVAILABLE: MINUTE_EXIT_PASS_INCOMPLETE,
    MinutePassOutcome.SKIPPED: MINUTE_EXIT_OK,
}

# Every outcome must have a code — a new member cannot silently exit 0.
assert set(_MINUTE_EXIT_BY_OUTCOME) == set(MinutePassOutcome), (
    "the minute exit mapping is not exhaustive — update it after adding a "
    "MinutePassOutcome member"
)


@dataclass(frozen=True)
class MinuteSymbolResult:
    """What one symbol's minute fetch produced (slice 921 Task 4.2).

    Replaces the bare 5-tuple ``_do_minute_symbol`` and
    ``_process_minute_symbol`` used to return. The added field is
    ``failure_kind``: the cycle must tell a provider outage from a database
    fault to run a breaker, and neither the outcome enum nor an exception
    message can carry that. Naming the fields also means the tuple's five
    positional members stop being decoded by index at every call site.
    """

    outcome: LastAttemptOutcome
    first_chunk_end: datetime | None
    last_chunk_end: datetime | None
    chunk_count: int
    gaps_seeded: int
    failure_kind: MinuteFailureKind = MinuteFailureKind.NONE


def day_end_utc(moment: datetime) -> datetime:
    """Return the UTC midnight that ENDS ``moment``'s UTC date.

    Slice 921, Scope 1: the gap range ends at ``session_close_utc``, but the
    provider request must reach the end of the day. EODHD honors ``to``
    exactly and 1-minute bars are published for extended hours — AAPL traded
    08:00-23:59 UTC on 2026-08-27 — so a request ending at the 20:00 close
    would silently drop pre- and post-market bars that the midnight-anchored
    legacy rows used to collect.

    This widening is a FETCH-LAYER MAPPING only: ``chunk_end``, the range
    handed to ``classify_outcome``, ``_advance_minute_gap``'s arguments, and
    the trailing-tolerance comparison all keep the un-extended value, so gap
    accounting and classification semantics do not shift with the request
    window.

    A ``moment`` already at UTC midnight is returned unchanged — it is
    already that date's end boundary, and adding a day would request an extra
    calendar day of bars.
    """
    midnight = moment.astimezone(_UTC).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    if moment.astimezone(_UTC) == midnight:
        return midnight
    return midnight + timedelta(days=1)


def _resolve_minute_history_start(
    conn: psycopg.Connection,
    symbol: str,
    *,
    operator_floor: date | None,
) -> datetime:
    """Resolve the earliest UTC datetime to fetch 1-minute bars from for one symbol.

    Effective floor =
        max(EODHD_INTRADAY_HORIZON,
            operator_floor (MT_MINUTE_HISTORY_START),
            instruments.first_listing_date or instruments.first_data_date).

    The provider horizon is the absolute backstop — EODHD has no 1-minute
    data before it. The operator override narrows the window for cost
    control or testing. The per-symbol date prevents wasted calls before
    a symbol existed (or before we have any record of trading for it).

    Args:
        conn:           Open psycopg connection (used to query instruments).
        symbol:         Instrument ticker.
        operator_floor: settings.minute_history_start; None when unset.

    Returns:
        UTC midnight datetime of the resolved start date.
    """
    floors: list[date] = [EODHD_INTRADAY_HORIZON]
    if operator_floor is not None:
        floors.append(operator_floor)

    with conn.cursor() as cur:
        cur.execute(
            "SELECT first_listing_date, first_data_date "
            "FROM instruments WHERE symbol = %s",
            (symbol,),
        )
        row = cur.fetchone()
    if row is not None:
        per_symbol = row[0] or row[1]
        if per_symbol is not None:
            floors.append(per_symbol)

    start = max(floors)
    return datetime(start.year, start.month, start.day, tzinfo=_UTC)


def run_minute_cycle(
    *,
    symbols: list[str] | None = None,
    should_continue: Callable[[], bool] | None = None,
    on_symbol: Callable[[str, str, datetime | None, datetime | None, int], None]
    | None = None,
) -> CycleReport:
    """Drive one minute-data acquisition pass over the instrument universe.

    The pass walks the universe TWICE (slice 921 SC6): a **trailing** phase
    that attempts every symbol's current session first — floored at
    ``MINUTE_TRAILING_PRIORITY_WINDOW``, bounded to
    ``MINUTE_TRAILING_MAX_CHUNKS_PER_SYMBOL`` per symbol, and the only phase
    that seeds — then a **backfill** phase over everything older with neither
    bound and no seeding. A pass that runs out of quota or wall clock has
    therefore already collected the day that matters.

    Reads MT_TIMESCALE_DB_URL and MT_EODHD_API_KEY from the environment.
    Per-symbol transient failures are caught and recorded; HTTP 4xx (non-429)
    propagates and crashes the cycle.

    Args:
        symbols: Optional explicit scope; defaults to ``iter_active_instruments``.
        should_continue: Optional zero-arg callable polled at the top of each
            per-symbol iteration. When it returns False the cycle exits
            cleanly between symbols (slice 146 SIGTERM hook).
        on_symbol: Optional callback invoked after each symbol completes.
            Receives (symbol, outcome_str, chunk_start, chunk_end) where
            chunk_start/chunk_end are the last chunk's datetime window or
            None if no chunk was attempted.
    """
    t0 = datetime.now(_UTC)
    settings = Settings()
    report = CycleReport()

    if not settings.timescale_db_url:
        raise RuntimeError("MT_TIMESCALE_DB_URL is not set")
    if not settings.eodhd_api_key:
        raise RuntimeError("MT_EODHD_API_KEY is not set")

    # The timer fires daily; MT_MINUTE_FIRING_DAYS says which firings run.
    # Decided here, before any connection or request, so a non-firing day
    # costs nothing and the operator can change cadence without a rebuild.
    if not is_firing_day(t0.date(), settings.minute_firing_days):
        _logger.info(
            "minute pass: %s is not a firing day (MT_MINUTE_FIRING_DAYS=%s) — skipping",
            t0.date(),
            describe_firing_days(settings.minute_firing_days),
        )
        report.minute_pass_outcome = MinutePassOutcome.SKIPPED
        report.wall_clock_seconds = (datetime.now(_UTC) - t0).total_seconds()
        return report

    with ConnectionPool(
        settings.timescale_db_url,
        min_size=1,
        max_size=4,
        configure=make_configure_connection(DB_BULK_SESSION),
    ) as pool:
        with httpx.Client(timeout=_REQUEST_TIMEOUT) as http:
            if symbols is not None:
                symbol_list = symbols
            else:
                with pool.connection() as conn:
                    symbol_list = [
                        row.symbol
                        for row in iter_active_instruments(
                            conn, ordering="most_stale_first", granularity="minute"
                        )
                    ]

            with pool.connection() as conn:
                coverage_index = build_minute_coverage_index(conn)
            if coverage_index is None:
                _logger.error(
                    "run_minute_cycle: coverage index unavailable this cycle — "
                    "seeding will use existing gap rows only (no full-window fallback)"
                )

            # Slice 921 SC6: two phases over the same universe. The TRAILING
            # phase attempts every symbol's current session (one chunk each)
            # before any backfill chunk is requested, so a pass that runs out
            # of quota or time has already collected the day that matters. The
            # 2026-09-07 13:05 UTC pass spent its whole run on deep backfill
            # and never reached the current session.
            trailing_floor = datetime.now(_UTC) - MINUTE_TRAILING_PRIORITY_WINDOW
            with pool.connection() as conn:
                truncated_index = _build_truncated_index(
                    conn, since=datetime.now(_UTC) - MINUTE_TRUNCATION_LOOKBACK
                )

            trailing_scanned, trailing_outcome = _run_minute_phase(
                symbol_list,
                phase=MinutePassPhase.TRAILING,
                truncated_index=truncated_index,
                pool=pool,
                http=http,
                settings=settings,
                coverage_index=coverage_index,
                report=report,
                should_continue=should_continue,
                on_symbol=on_symbol,
                min_gap_end=trailing_floor,
                max_chunks=MINUTE_TRAILING_MAX_CHUNKS_PER_SYMBOL,
                seed=True,
            )
            # The completion line is a signal (the cutover stops the firing on
            # it), so it is emitted only when the phase actually completed;
            # an aborted or stopped phase says so instead (#22 review F001).
            if trailing_outcome is MinutePassOutcome.COMPLETE:
                _logger.info(
                    MINUTE_TRAILING_COMPLETE_LINE.format(count=trailing_scanned)
                )
            else:
                _logger.info(
                    "trailing phase ended early: %s after %d symbols",
                    trailing_outcome.value if trailing_outcome else "shutdown",
                    trailing_scanned,
                )

            # Only a phase that walked its whole scope hands over to the next
            # one. A fault ends the pass because every further request is
            # wasted; a shutdown (None) ends it because the operator said so.
            pass_outcome = trailing_outcome or MinutePassOutcome.COMPLETE
            trailing_completed = trailing_outcome is MinutePassOutcome.COMPLETE

            if trailing_completed:
                _, backfill_outcome = _run_minute_phase(
                    symbol_list,
                    phase=MinutePassPhase.BACKFILL,
                    pool=pool,
                    http=http,
                    settings=settings,
                    coverage_index=coverage_index,
                    report=report,
                    should_continue=should_continue,
                    on_symbol=on_symbol,
                    min_gap_end=None,
                    max_chunks=None,
                    # Task 3.3 option (a): seeding belongs to the trailing walk.
                    seed=False,
                )
                pass_outcome = backfill_outcome or MinutePassOutcome.COMPLETE

            report.minute_pass_outcome = pass_outcome
            report.minute_trailing_completed = trailing_completed

    report.wall_clock_seconds = (datetime.now(_UTC) - t0).total_seconds()
    return report


def _build_truncated_index(
    conn: psycopg.Connection, *, since: datetime
) -> dict[str, frozenset[date]] | None:
    """Truncated symbol-days since ``since`` for the trailing seed, or None.

    Slice 921 / #22: the coverage index reports a day as covered when it
    holds any bar, so a session truncated to one bar (the 2026-09-10 cutover
    left 4,278 of them) is never re-seeded. The repair's index sees exactly
    those days; the trailing walk passes them as ``uncovered_days``. Fails
    safe the way a None coverage index does: on a scan timeout, log at ERROR
    and seed without it — the health check still reports the truncation, and
    the repair script remains the operator's path.
    """
    try:
        return build_truncated_day_index(conn, since=since)
    except RepairScanTimeout:
        _logger.exception(
            "run_minute_cycle: truncated-day scan since %s timed out — "
            "seeding without it this cycle; truncated sessions will not be "
            "re-fetched until a cycle's scan completes or the repair runs",
            since,
        )
        return None


def _failed(kind: MinuteFailureKind) -> MinuteSymbolResult:
    """A symbol that produced no fetch, tagged with WHY (slice 921 Task 4.2).

    Every handler in ``_process_minute_symbol`` returned the same bare tuple,
    so the cycle could not tell a provider outage from a Postgres pool
    exhaustion — and a breaker built on that value would abort the pass with
    PROVIDER_UNAVAILABLE on a database fault.
    """
    return MinuteSymbolResult(
        outcome=LastAttemptOutcome.TRANSIENT_FAILURE,
        first_chunk_end=None,
        last_chunk_end=None,
        chunk_count=0,
        gaps_seeded=0,
        failure_kind=kind,
    )


def _run_minute_phase(
    symbol_list: list[str],
    *,
    phase: MinutePassPhase,
    pool: ConnectionPool,
    http: httpx.Client,
    settings: Settings,
    coverage_index: dict[str, set[date]] | None,
    report: CycleReport,
    should_continue: Callable[[], bool] | None,
    on_symbol: Callable[[str, str, datetime | None, datetime | None, int], None] | None,
    min_gap_end: datetime | None,
    max_chunks: int | None,
    seed: bool,
    truncated_index: dict[str, frozenset[date]] | None = None,
) -> tuple[int, MinutePassOutcome | None]:
    """Walk the universe once for one phase.

    Both phases of a minute pass share this walk (slice 921 SC6) — they differ
    only in the three knobs the caller supplies: the trailing floor, the
    per-symbol chunk bound, and whether the phase seeds. Sharing the loop keeps
    the outcome tallying, the SIGTERM hook, the quota-abort handling and the
    progress logging identical between phases rather than duplicated.

    Returns:
        ``(symbols_scanned, outcome)``. COMPLETE means the phase walked the
        whole scope and the next phase may run. QUOTA_EXHAUSTED and
        PROVIDER_UNAVAILABLE are faults that end the PASS — every further
        request would be wasted. ``None`` means the phase stopped at the
        operator's request (a shutdown, or an aborted quota wait): the pass
        also ends, but nothing failed, so it must not colour the exit code.
    """
    symbols_scanned = 0
    gaps_seeded_total = 0
    outcome_of_phase = MinutePassOutcome.COMPLETE
    consecutive_provider_failures = 0
    # A shutdown request or an aborted quota wait stops the pass WITHOUT being
    # a fault — the operator asked for it — so it is tracked separately from
    # outcome_of_phase, which drives the exit code.
    stopped_early = False

    for sym in symbol_list:
        if should_continue is not None and not should_continue():
            _logger.info(
                "run_minute_cycle[%s]: should_continue=False — exiting "
                "between symbols (scanned=%d, remaining=%d)",
                phase,
                symbols_scanned,
                len(symbol_list) - symbols_scanned,
            )
            stopped_early = True
            break
        try:
            result = _process_minute_symbol(
                sym,
                pool=pool,
                http=http,
                settings=settings,
                coverage_index=coverage_index,
                via=FetchEntryPoint.CYCLE,
                should_continue=should_continue,
                min_gap_end=min_gap_end,
                max_chunks=max_chunks,
                seed=seed,
                uncovered_days=(truncated_index or {}).get(sym),
            )
        except QuotaWaitAborted:
            _logger.info(
                "run_minute_cycle[%s]: quota wait aborted by shutdown — "
                "exiting (scanned=%d, remaining=%d)",
                phase,
                symbols_scanned,
                len(symbol_list) - symbols_scanned,
            )
            stopped_early = True
            break

        # --- Slice 921 Tasks 4.3/4.4: abort conditions, before tallying ---
        if result.failure_kind is MinuteFailureKind.PROVIDER_QUOTA:
            # HTTP 402: the daily allowance is spent. Every further request
            # this pass makes is wasted, so end it here rather than walking
            # the remaining universe one 402 at a time.
            # The symbol that hit the 402 WAS attempted, so it counts as
            # scanned — otherwise the line understates the work done and
            # overstates what is left, which is what an operator acts on.
            _logger.error(
                "minute pass aborted: %s in the %s phase — "
                "%d symbols scanned, %d not attempted",
                MinutePassOutcome.QUOTA_EXHAUSTED,
                phase,
                symbols_scanned + 1,
                len(symbol_list) - symbols_scanned - 1,
            )
            outcome_of_phase = MinutePassOutcome.QUOTA_EXHAUSTED
            break

        if result.failure_kind is MinuteFailureKind.PROVIDER:
            consecutive_provider_failures += 1
        elif result.failure_kind is MinuteFailureKind.NONE:
            # Only a real provider ANSWER clears the streak. A database fault
            # neither increments nor resets it: it is not evidence either way.
            consecutive_provider_failures = 0

        if (
            consecutive_provider_failures
            >= MINUTE_PASS_MAX_CONSECUTIVE_PROVIDER_FAILURES
        ):
            _logger.error(
                "minute pass aborted: %s in the %s phase after %d consecutive "
                "provider failures — %d symbols scanned, %d not attempted",
                MinutePassOutcome.PROVIDER_UNAVAILABLE,
                phase,
                consecutive_provider_failures,
                symbols_scanned + 1,
                len(symbol_list) - symbols_scanned - 1,
            )
            outcome_of_phase = MinutePassOutcome.PROVIDER_UNAVAILABLE

        report.symbol_outcomes[sym] = str(result.outcome)
        if result.outcome == LastAttemptOutcome.SUCCESS:
            report.success_count += 1
        elif result.outcome == LastAttemptOutcome.PARTIAL:
            report.partial_count += 1
        elif result.outcome == LastAttemptOutcome.EMPTY:
            report.empty_count += 1
        else:
            report.transient_failure_count += 1
        if on_symbol is not None:
            on_symbol(
                sym,
                str(result.outcome),
                result.first_chunk_end,
                result.last_chunk_end,
                result.chunk_count,
            )

        symbols_scanned += 1
        gaps_seeded_total += result.gaps_seeded
        if outcome_of_phase is not MinutePassOutcome.COMPLETE:
            break
        if symbols_scanned % MINUTE_SEED_PROGRESS_LOG_INTERVAL == 0:
            _logger.info(
                "minute %s: %d/%d symbols scanned, %d gap rows seeded",
                phase,
                symbols_scanned,
                len(symbol_list),
                gaps_seeded_total,
            )

    _logger.info(
        "minute %s: complete — %d symbols, %d gap rows seeded",
        phase,
        symbols_scanned,
        gaps_seeded_total,
    )
    if stopped_early and outcome_of_phase is MinutePassOutcome.COMPLETE:
        # Signal "do not start the next phase" without claiming a fault.
        return symbols_scanned, None
    return symbols_scanned, outcome_of_phase


def _process_minute_symbol(
    symbol: str,
    *,
    pool: ConnectionPool,
    http: httpx.Client,
    settings: Settings,
    via: FetchEntryPoint,
    coverage_index: dict[str, set[date]] | None = None,
    should_continue: Callable[[], bool] | None = None,
    min_gap_end: datetime | None = None,
    max_chunks: int | None = None,
    seed: bool = True,
    uncovered_days: frozenset[date] | None = None,
) -> MinuteSymbolResult:
    try:
        return _do_minute_symbol(
            symbol,
            pool=pool,
            http=http,
            settings=settings,
            coverage_index=coverage_index,
            via=via,
            should_continue=should_continue,
            min_gap_end=min_gap_end,
            max_chunks=max_chunks,
            seed=seed,
            uncovered_days=uncovered_days,
        )
    except QuotaWaitAborted:
        # Shutdown, not a failure — must reach the cycle loop, so it cannot
        # fall through to the except Exception below.
        raise
    except ProviderQuotaExhausted as exc:
        # HTTP 402: the account's daily allowance is spent, so every further
        # request this pass makes is wasted. Reported as its own kind; the
        # cycle ends the pass on it (slice 921 Task 4.3). Caught before the
        # ProviderResponseError handler below, which is its base class.
        _logger.error("EODHD quota exhausted at %s minute via=%s: %s", symbol, via, exc)
        return _failed(MinuteFailureKind.PROVIDER_QUOTA)
    except ProviderResponseError as exc:
        # Non-404 4xx from EODHD — unexpected but skip this symbol rather than
        # crashing the entire cycle. Log at ERROR so it surfaces for investigation.
        _logger.error(
            "ProviderResponseError for %s minute — skipping: %s via=%s",
            symbol,
            exc,
            via,
        )
        return _failed(MinuteFailureKind.PROVIDER)
    except psycopg.errors.LockNotAvailable:
        _logger.warning(
            "Advisory lock timeout for %s minute — skipping via=%s", symbol, via
        )
        # A lock held by another writer is a LOCAL condition. Counting it as
        # provider evidence would abort the pass on our own contention.
        return _failed(MinuteFailureKind.DATABASE)
    except PoolTimeout:
        _logger.warning(
            "DB pool timeout for %s minute — DB unreachable, skipping via=%s",
            symbol,
            via,
        )
        return _failed(MinuteFailureKind.DATABASE)
    except (httpx.HTTPError, httpx.TimeoutException) as exc:
        _logger.warning(
            "HTTP transient failure for %s minute (retries exhausted): %s via=%s",
            symbol,
            exc,
            via,
        )
        return _failed(MinuteFailureKind.PROVIDER)
    except Exception:
        # Last-resort boundary so one symbol cannot crash the pass. The cause
        # is unknown, so it is NOT attributed to the provider — an unknown
        # fault must not trip a breaker that exists to stop wasting credits.
        _logger.exception("Transient failure for %s minute via=%s", symbol, via)
        return _failed(MinuteFailureKind.DATABASE)


def _do_minute_symbol(
    symbol: str,
    *,
    pool: ConnectionPool,
    http: httpx.Client,
    settings: Settings,
    via: FetchEntryPoint,
    force_reset_terminal: bool = False,
    window: tuple[date, date] | None = None,
    coverage_index: dict[str, set[date]] | None = None,
    should_continue: Callable[[], bool] | None = None,
    min_gap_end: datetime | None = None,
    max_chunks: int | None = None,
    seed: bool = True,
    uncovered_days: frozenset[date] | None = None,
) -> MinuteSymbolResult:
    now_midnight = datetime.now(_UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    with pool.connection() as conn:
        default_history_start = _resolve_minute_history_start(
            conn, symbol, operator_floor=settings.minute_history_start
        )

    if window is not None:
        # Clamp window start to provider history limit.
        window_start = datetime(
            window[0].year, window[0].month, window[0].day, tzinfo=_UTC
        )
        history_start = max(window_start, default_history_start)
        # Clamp window end to today midnight (last completed session close UTC).
        window_end = datetime(
            window[1].year, window[1].month, window[1].day, tzinfo=_UTC
        )
        target_end = min(window_end, now_midnight)
    else:
        history_start = default_history_start
        target_end = now_midnight

    # Happy-path via marker (slice 165): without this line a successful fetch
    # emits nothing carrying via=, and log output could not identify which
    # entry point drove it — the exact ambiguity this slice exists to close.
    _logger.info(
        "minute fetch: %s window=[%s → %s] via=%s",
        symbol,
        history_start.date(),
        target_end.date(),
        via,
    )

    last_outcome = LastAttemptOutcome.SUCCESS
    failure_kind = MinuteFailureKind.NONE
    first_chunk_end: datetime | None = None
    first_chunk_outcome: LastAttemptOutcome | None = None
    last_chunk_end: datetime | None = None
    chunk_count: int = 0
    gaps_seeded: int = 0

    # Check and seed use a short-lived connection that is returned to the pool
    # before the chunk loop starts. Holding conn open across the chunk loop
    # leaves a connection idle-in-transaction, which holds advisory locks.
    with pool.connection() as conn:
        with conn.cursor() as _cur:
            _cur.execute(
                "SELECT EXISTS (SELECT 1 FROM minute_ohlcv WHERE symbol = %s LIMIT 1) AS has_bars,"
                "       EXISTS (SELECT 1 FROM data_gaps WHERE symbol = %s"
                "               AND granularity = 'minute' AND fetch_status = 'UNKNOWN' LIMIT 1)"
                "       AS has_unknown_gaps,"
                "       EXISTS (SELECT 1 FROM data_gaps WHERE symbol = %s"
                "               AND granularity = 'minute' LIMIT 1)"
                "       AS has_any_gaps,"
                "       (SELECT MAX(gap_end) FROM data_gaps WHERE symbol = %s"
                "               AND granularity = 'minute') AS gap_frontier",
                (symbol, symbol, symbol, symbol),
            )
            _row = _cur.fetchone()
        _has_bars = _row[0] if _row else False
        _has_unknown_gaps = _row[1] if _row else False
        _has_any_gaps = _row[2] if _row else False
        _gap_frontier = _row[3] if _row else None
        # Seed the FULL [history_start, target_end] window when: no bars yet,
        # OR no gap rows at all (gap table out of sync with bars — e.g. after a
        # DB migration or manual gap-row deletion), OR there are unknown gaps
        # to fill, OR force_reset_terminal requested.
        _needs_full_seed = (
            force_reset_terminal
            or not _has_bars
            or not _has_any_gaps
            or _has_unknown_gaps
        )
        seed_from: datetime | None = None
        if not seed:
            # Slice 921 Task 3.3, option (a): the BACKFILL phase does not seed.
            # Seeding belongs to the trailing walk, which ran first. A second
            # seed would recompute coverage from the CYCLE-START index, which
            # still reports the session the trailing phase just fetched as
            # uncovered, re-insert it as UNKNOWN, and hand it straight back to
            # this phase's selector (ORDER BY gap_end DESC picks it first) —
            # ~13,000 redundant /intraday calls at 5 credits each, roughly 65k
            # of the 100k daily allowance spent re-fetching the day just
            # fetched. With no second seed there is no row to re-pick, so the
            # double fetch is impossible by construction rather than by
            # ordering. This is a property of the PHASE: run_minute_refetch and
            # the single-symbol operator path never pass it and seed as before.
            pass
        elif _needs_full_seed:
            seed_from = history_start
        elif _gap_frontier is not None and _gap_frontier < target_end:
            # Issue #19: every gap row is terminal (PROVIDER_HOLE /
            # RETRY_EXHAUSTED) and none is UNKNOWN, so the full-seed gate above
            # never fires again and the symbol's minute data freezes at its
            # last fetch. Seed ONLY the uncovered trailing window — from the
            # gap frontier (MAX(gap_end)) forward. The window must stay
            # trailing: update_data_gaps deletes the rows CONTAINED in its
            # window and re-inserts missing sessions as UNKNOWN, so a
            # history_start window here would resurrect every genuine
            # provider hole behind the frontier.
            seed_from = _gap_frontier

        if seed_from is not None:
            # Coverage-aware seeding (slice 162): when the caller has a coverage
            # index, seed only genuinely-missing sessions instead of a single
            # [history_start, target_end] span. When coverage_index is None (the
            # index build failed this cycle, or the caller — e.g. run_minute_refetch
            # — didn't build one), precomputed_ranges stays None and
            # update_data_gaps falls back to its legacy single-span behavior —
            # never a silent full-window re-seed beyond what already happens today.
            precomputed_ranges = None
            if coverage_index is not None:
                # Slice 921 / #22: a session truncated to a single bar reads
                # as covered in the coarse cagg, so without this the seed
                # never re-creates its row and the truncation is permanent.
                precomputed_ranges = compute_missing_minute_sessions(
                    conn,
                    symbol,
                    coverage_index,
                    seed_from,
                    target_end,
                    uncovered_days=set(uncovered_days) if uncovered_days else None,
                )

            # Seed gap rows and commit before entering the fetch loop.
            # Each per-chunk write must commit independently so a Ctrl-C between
            # chunks does not roll back already-fetched bars.  pg_advisory_xact_lock
            # is transaction-scoped, so we release it here and re-acquire per chunk.
            with conn.transaction():
                with advisory_lock(conn, symbol, "minute", timeout=DAEMON_LOCK_TIMEOUT):
                    seed_result = update_data_gaps(
                        conn,
                        symbol,
                        "minute",
                        seed_from,
                        target_end,
                        fetch_status_for_unfilled=FetchStatus.UNKNOWN,
                        outcome=LastAttemptOutcome.PARTIAL,
                        force_reset_terminal=force_reset_terminal,
                        precomputed_ranges=precomputed_ranges,
                    )
            gaps_seeded = seed_result.gaps_inserted
    # conn is returned to pool here — chunk loop uses fresh connections per chunk.

    while True:
        # Slice 921: the TRAILING phase bounds itself to one chunk per symbol
        # so a single symbol's deep backlog cannot delay the current session of
        # every symbol behind it. The bound belongs to the phase, not to
        # _do_minute_symbol generally — the backfill phase and
        # run_minute_refetch pass nothing and walk as many chunks as they have.
        if max_chunks is not None and chunk_count >= max_chunks:
            _logger.debug(
                "minute fetch: %s — reached this phase's chunk bound (%d)",
                symbol,
                max_chunks,
            )
            break

        # A deep backfill walks ~69 chunks per symbol; checking only between
        # symbols left Ctrl-C unanswered for 10+ minutes (20260807). Exiting
        # here is identical to the gap-is-None break: per-chunk commits make
        # the remaining UNKNOWN gaps resume on the next cycle.
        if should_continue is not None and not should_continue():
            _logger.info(
                "minute fetch: %s — should_continue=False, exiting between "
                "chunks (chunks done=%d)",
                symbol,
                chunk_count,
            )
            break

        # Re-read actionable gaps each iteration (prior chunk may have
        # filled some).  Advisory lock re-acquired per transaction.
        with pool.connection() as chunk_conn:
            gap = pick_most_recent_actionable_gap(
                chunk_conn,
                symbol,
                "minute",
                history_start,
                target_end,
                min_gap_end=min_gap_end,
            )
            if gap is None:
                break

        chunk_start = max(
            gap.gap_start, gap.gap_end - timedelta(days=_PROVIDER_MAX_CHUNK_DAYS)
        )
        chunk_end = gap.gap_end
        if first_chunk_end is None:
            first_chunk_end = chunk_end
        last_chunk_end = chunk_end
        chunk_count += 1
        # outcome not yet known for this chunk — set after classify_outcome below

        url = (
            f"{_EODHD_BASE}/intraday/{_normalise(symbol)}"
            f"?api_token={settings.eodhd_api_key}&fmt=json&interval=1m"
            f"&from={int(chunk_start.timestamp())}"
            f"&to={int(day_end_utc(chunk_end).timestamp())}"
        )
        response = eodhd_get(http, url, CallType.INTRADAY)
        outcome = classify_outcome(response, chunk_start, chunk_end)

        # Slice 921 Decision 4: no response, no accounting. classify_outcome
        # RETURNS (does not raise) TRANSIENT_FAILURE for HTTP 429, any 5xx, an
        # unparseable body and EODHD's 200-with-{"error": …} quirk. Writing
        # accounting for those consumed retries the provider never answered
        # and could promote a live gap to RETRY_EXHAUSTED. Leave the row
        # exactly as it was and stop: there is no point asking the next chunk
        # of a provider that just failed to answer this one.
        if not response_carries_an_answer(response):
            # The breaker's headline trigger arrives HERE, not through any
            # except handler: classify_outcome returns (never raises) for 5xx
            # and 429, so the symbol exits normally. The kind must therefore
            # ride the normal return path too (slice 921 Task 4.2).
            failure_kind = MinuteFailureKind.PROVIDER
            _logger.warning(
                "minute fetch: %s chunk [%s → %s] got no usable answer "
                "(HTTP %s) — leaving gap accounting untouched and ending the "
                "symbol's chunk loop",
                symbol,
                chunk_start,
                chunk_end,
                response.status_code,
            )
            last_outcome = outcome
            if first_chunk_outcome is None:
                first_chunk_outcome = outcome
            break

        bars: list[dict] = []
        if outcome not in (
            LastAttemptOutcome.TRANSIENT_FAILURE,
            LastAttemptOutcome.EMPTY,
        ):
            try:
                bars = response.json()
            except Exception:
                bars = []

        # Slice 921 / #22: judge the chunk by the SESSIONS it covers, not by
        # the latest bar's date. EODHD dates session D-1's 20:00 ET
        # after-hours bar as 00:00 UTC on day D, so a response holding only
        # that spillover bar satisfied classify_outcome's date comparison and
        # the row for D's session was deleted with no session bars behind it
        # (the 2026-09-10 cutover, 4,278 symbols). A session counts only when
        # a bar lands in (open, close]. This also subsumes the former
        # trailing-weekend tolerance: a weekend or holiday tail has no session
        # to be missing, so a chunk whose sessions are all covered is SUCCESS
        # however far its last bar sits before chunk_end.
        with pool.connection() as bounds_conn:
            session_bounds = fetch_session_bounds(
                bounds_conn, symbol, chunk_start, chunk_end
            )
        judgement = judge_chunk_by_sessions(outcome, bars, session_bounds, chunk_end)
        outcome = judgement.outcome
        if judgement.unpublished:
            _logger.info(
                "minute fetch: %s chunk [%s → %s] carried %d bars, none inside "
                "session(s) %s — provider has not published them; row kept",
                symbol,
                chunk_start,
                chunk_end,
                len(bars),
                ", ".join(f"{s:%Y-%m-%d}" for s in judgement.unpublished),
            )
        if judgement.empty:
            _logger.info(
                "minute fetch: %s chunk [%s → %s] carried %d bars, none inside "
                "session(s) %s, closed beyond the publication lag — recorded as "
                "PROVIDER_HOLE",
                symbol,
                chunk_start,
                chunk_end,
                len(bars),
                ", ".join(f"{s:%Y-%m-%d}" for s in judgement.empty),
            )

        fetch_status = outcome_to_fetch_status(outcome)
        last_outcome = outcome
        if first_chunk_outcome is None:
            first_chunk_outcome = outcome

        with pool.connection() as chunk_conn:
            with chunk_conn.transaction():
                with advisory_lock(
                    chunk_conn, symbol, "minute", timeout=DAEMON_LOCK_TIMEOUT
                ):
                    if bars:
                        _insert_minute_bars(chunk_conn, symbol, bars)

                    _advance_minute_gap(
                        chunk_conn,
                        picked=gap,
                        chunk_start=chunk_start,
                        chunk_end=chunk_end,
                        outcome=outcome,
                        fetch_status=fetch_status,
                    )
                    if outcome is LastAttemptOutcome.SUCCESS and judgement.empty:
                        _record_empty_sessions(
                            chunk_conn,
                            symbol,
                            [(s, session_bounds[s]) for s in judgement.empty],
                        )
                    _record_minute_attempt(chunk_conn, symbol, outcome)

    with pool.connection() as chunk_conn:
        with chunk_conn.transaction():
            coalesce_data_gaps(chunk_conn, symbol, "minute")

    # Display outcome: first chunk (most recent window) is the meaningful signal.
    # last_outcome (oldest chunk) is often empty for pre-IPO periods.
    display_outcome = (
        first_chunk_outcome if first_chunk_outcome is not None else last_outcome
    )
    return MinuteSymbolResult(
        outcome=display_outcome,
        first_chunk_end=first_chunk_end,
        last_chunk_end=last_chunk_end,
        chunk_count=chunk_count,
        gaps_seeded=gaps_seeded,
        failure_kind=failure_kind,
    )


def run_minute_refetch(
    symbol: str,
    *,
    from_date: date | None = None,
    to_date: date | None = None,
) -> CycleReport:
    """Re-fetch minute bars for a symbol in the given window.

    Resets terminal gap rows (PROVIDER_HOLE / RETRY_EXHAUSTED) to UNKNOWN
    before re-attempting. Runs coalesce after the chunk loop (existing behavior).
    Intended as an operator escape valve; runs outside daemon quota.

    Args:
        symbol:    Instrument ticker.
        from_date: Start of window; clamped up by the resolved per-symbol
                   history floor (see ``_resolve_minute_history_start``).
        to_date:   End of window; defaults to last completed trading session.
    """
    t0 = datetime.now(_UTC)
    settings = Settings()
    report = CycleReport()

    if not settings.timescale_db_url:
        raise RuntimeError("MT_TIMESCALE_DB_URL is not set")
    if not settings.eodhd_api_key:
        raise RuntimeError("MT_EODHD_API_KEY is not set")

    with ConnectionPool(
        settings.timescale_db_url,
        min_size=1,
        max_size=2,
        configure=make_configure_connection(DB_BULK_SESSION),
    ) as pool:
        with httpx.Client(timeout=_REQUEST_TIMEOUT) as http:
            with pool.connection() as conn:
                history_floor = _resolve_minute_history_start(
                    conn, symbol, operator_floor=settings.minute_history_start
                )

            # Resolve from_date: clamp up to the per-symbol history floor.
            if from_date is None:
                resolved_from = history_floor.date()
            else:
                resolved_from = max(from_date, history_floor.date())

            # Resolve to_date: default to last completed session.
            if to_date is None:
                with pool.connection() as conn:
                    last_session = _last_completed_session(conn, symbol)
                resolved_to = (
                    last_session.date() if last_session is not None else date.today()
                )
            else:
                resolved_to = to_date

            # Per-symbol coverage (slice 165 amendment): a single-symbol
            # command must not pay the universe-wide scan run_minute_cycle
            # amortizes across ~11.6k symbols.
            with pool.connection() as conn:
                coverage_index = build_symbol_minute_coverage(conn, symbol)
            if coverage_index is None:
                _logger.error(
                    "run_minute_refetch: coverage unavailable for %s — "
                    "seeding will use legacy single-span fallback via=refetch",
                    symbol,
                )

            window = (resolved_from, resolved_to)
            refetch_result = _do_minute_symbol(
                symbol,
                pool=pool,
                http=http,
                settings=settings,
                force_reset_terminal=True,
                window=window,
                coverage_index=coverage_index,
                via=FetchEntryPoint.REFETCH,
            )
            outcome = refetch_result.outcome
            report.symbol_outcomes[symbol] = str(outcome)
            if outcome == LastAttemptOutcome.SUCCESS:
                report.success_count += 1
            elif outcome == LastAttemptOutcome.PARTIAL:
                report.partial_count += 1
            elif outcome == LastAttemptOutcome.EMPTY:
                report.empty_count += 1
            else:
                report.transient_failure_count += 1

    report.wall_clock_seconds = (datetime.now(_UTC) - t0).total_seconds()
    return report


def _record_empty_sessions(
    conn: psycopg.Connection,
    symbol: str,
    sessions: list[tuple[datetime, datetime]],
) -> None:
    """Persist PROVIDER_HOLE for sessions the provider answered with no bars.

    The judgement must outlive this chunk: the coarse coverage index still
    reports the day covered (an after-hours bar sits on it), and only a
    terminal row keeps the seed and the repair's truncated-day index from
    asking for the session again on every walk (#22). Caller holds the
    advisory lock inside the chunk's transaction.
    """
    now_utc = datetime.now(tz=_UTC)
    with conn.cursor() as cur:
        for session_open, session_close in sessions:
            cur.execute(
                """
                INSERT INTO data_gaps
                    (symbol, granularity, gap_start, gap_end,
                     fetch_status, last_attempt_ts, attempt_count)
                VALUES (%s, 'minute', %s, %s, %s, %s, 1)
                ON CONFLICT DO NOTHING
                """,
                (
                    symbol,
                    session_open,
                    session_close,
                    str(FetchStatus.PROVIDER_HOLE),
                    now_utc,
                ),
            )


def _advance_minute_gap(
    conn: psycopg.Connection,
    *,
    picked: GapRow,
    chunk_start: datetime,
    chunk_end: datetime,
    outcome: LastAttemptOutcome,
    fetch_status: FetchStatus | None,
) -> None:
    """Shrink/split the picked gap row to reflect one chunk's outcome.

    Caller must hold the advisory lock and be inside an open transaction.
    Bar inserts (via _insert_minute_bars) must occur in the same transaction
    so a crash between bar COPY and gap update cannot diverge data_gaps from
    minute_ohlcv.

    The picked gap is what pick_most_recent_actionable_gap returned. The
    chunk window [chunk_start, chunk_end] is the trailing slice of that
    gap that we just attempted (newest-first). chunk_end == picked.gap_end
    by construction; chunk_start >= picked.gap_start.

    Behavior by outcome:
        SUCCESS:
            - chunk_start <= gap_start: DELETE the picked row (whole gap covered).
            - chunk_start  > gap_start: shrink picked.gap_end down to chunk_start
              (UPDATE PK; older portion remains UNKNOWN for a future chunk).
        PARTIAL / TRANSIENT_FAILURE / EMPTY:
            - chunk covers full gap: UPDATE picked row's status/attempt_count
              in place (PK unchanged).
            - chunk is a tail slice: UPDATE picked.gap_end = chunk_start
              (older portion stays UNKNOWN), then INSERT a new row for
              [chunk_start, chunk_end] with the chunk's status and
              attempt_count carried forward + 1, possibly promoted to
              RETRY_EXHAUSTED.

    No fixed-period assumption — operates on whatever bounds picked carries.
    """
    sym = picked.symbol
    gran = picked.granularity
    gap_start = picked.gap_start
    gap_end = picked.gap_end

    if outcome == LastAttemptOutcome.SUCCESS:
        if chunk_start <= gap_start:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    DELETE FROM data_gaps
                     WHERE symbol = %s AND granularity = %s
                       AND gap_start = %s AND gap_end = %s
                    """,
                    (sym, gran, gap_start, gap_end),
                )
        else:
            # The PK includes (gap_start, gap_end), so we DELETE+INSERT to
            # update gap_end. Carry over the picked row's attempt_count and
            # last_attempt_ts so the older portion still reflects its history.
            with conn.cursor() as cur:
                cur.execute(
                    """
                    DELETE FROM data_gaps
                     WHERE symbol = %s AND granularity = %s
                       AND gap_start = %s AND gap_end = %s
                    """,
                    (sym, gran, gap_start, gap_end),
                )
                cur.execute(
                    """
                    INSERT INTO data_gaps
                        (symbol, granularity, gap_start, gap_end,
                         fetch_status, last_attempt_ts, attempt_count)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        sym,
                        gran,
                        gap_start,
                        chunk_start,
                        picked.fetch_status,
                        picked.last_attempt_ts,
                        picked.attempt_count,
                    ),
                )
        return

    # Non-success: assign a status to the chunk portion.
    assert fetch_status is not None, (
        f"non-success outcome {outcome!r} must map to a FetchStatus"
    )
    chunk_attempts = picked.attempt_count + 1
    chunk_status: FetchStatus = fetch_status
    # Defense in depth: any retryable status hitting the cap promotes to
    # RETRY_EXHAUSTED so the chunk loop cannot spin forever on a window
    # the provider will never fully cover (e.g. trailing weekend / holiday).
    if (
        chunk_status in (FetchStatus.FAILED_RETRYABLE, FetchStatus.UNKNOWN)
        and chunk_attempts >= MAX_RETRY_COUNT
    ):
        chunk_status = FetchStatus.RETRY_EXHAUSTED
    now_utc = datetime.now(tz=_UTC)

    if chunk_start <= gap_start:
        # Chunk covers the whole picked gap — UPDATE in place.
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE data_gaps
                   SET fetch_status   = %s,
                       last_attempt_ts = %s,
                       attempt_count  = %s
                 WHERE symbol = %s AND granularity = %s
                   AND gap_start = %s AND gap_end = %s
                """,
                (
                    str(chunk_status),
                    now_utc,
                    chunk_attempts,
                    sym,
                    gran,
                    gap_start,
                    gap_end,
                ),
            )
        return

    # Tail slice — split into older [gap_start, chunk_start] (UNKNOWN, prior
    # attempt history preserved) and chunk [chunk_start, chunk_end] (new status).
    with conn.cursor() as cur:
        cur.execute(
            """
            DELETE FROM data_gaps
             WHERE symbol = %s AND granularity = %s
               AND gap_start = %s AND gap_end = %s
            """,
            (sym, gran, gap_start, gap_end),
        )
        cur.execute(
            """
            INSERT INTO data_gaps
                (symbol, granularity, gap_start, gap_end,
                 fetch_status, last_attempt_ts, attempt_count)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                sym,
                gran,
                gap_start,
                chunk_start,
                picked.fetch_status,
                picked.last_attempt_ts,
                picked.attempt_count,
            ),
        )
        cur.execute(
            """
            INSERT INTO data_gaps
                (symbol, granularity, gap_start, gap_end,
                 fetch_status, last_attempt_ts, attempt_count)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                sym,
                gran,
                chunk_start,
                chunk_end,
                str(chunk_status),
                now_utc,
                chunk_attempts,
            ),
        )


def _record_minute_attempt(
    conn: psycopg.Connection,
    symbol: str,
    outcome: LastAttemptOutcome,
) -> None:
    """Upsert acquisition_state for one minute-fetch attempt."""
    now_utc = datetime.now(tz=_UTC)
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO acquisition_state
                (symbol, granularity, provider, last_attempt_ts, last_attempt_outcome)
            VALUES (%s, %s, 'eodhd', %s, %s)
            ON CONFLICT (symbol, granularity, provider) DO UPDATE SET
                last_attempt_ts      = EXCLUDED.last_attempt_ts,
                last_attempt_outcome = EXCLUDED.last_attempt_outcome,
                updated_at           = NOW()
            """,
            (symbol, "minute", now_utc, str(outcome)),
        )


_OHLC_FIELDS = ("open", "high", "low", "close")


def _bar_to_row(symbol: str, bar: dict) -> tuple | None:
    """Convert one provider bar to a stage-table row, or None to skip it.

    Provider payloads occasionally carry null or absent price fields (EODHD
    sent open=null for CVR and LFWD, 2026-08-14). A price that is missing,
    non-numeric, non-finite, or <= 0 makes the bar unusable: skip it with a
    warning. Never substitute a default — a fabricated 0.00 price poisons
    every consumer downstream. InvalidOperation is in the except tuple
    because Decimal raises it (an ArithmeticError, not a ValueError) on
    unparseable input like str(None); the finite/positive check guards the
    values Decimal accepts but the table must never hold ("NaN", "Inf", 0).
    """
    try:
        ts_epoch = bar.get("timestamp")
        if ts_epoch is not None:
            bar_ts = datetime.fromtimestamp(int(ts_epoch), tz=_UTC)
        else:
            bar_ts = datetime.fromisoformat(bar.get("datetime", "")).replace(
                tzinfo=_UTC
            )
        prices: dict[str, Decimal] = {}
        for field in _OHLC_FIELDS:
            value = Decimal(str(bar[field]))
            if not value.is_finite() or value <= 0:
                raise ValueError(f"unusable {field} price: {value}")
            prices[field] = value
        volume = int(bar.get("volume") or 0)
    except (KeyError, ValueError, TypeError, InvalidOperation):
        _logger.warning("Skipping malformed minute bar for %s: %r", symbol, bar)
        return None
    return (
        bar_ts,
        symbol,
        prices["open"],
        prices["high"],
        prices["low"],
        prices["close"],
        volume,
    )


def _insert_minute_bars(
    conn: psycopg.Connection, symbol: str, bars: list[dict]
) -> None:
    """Bulk-insert minute bars via COPY (fastest path for large payloads)."""
    rows: list[tuple] = []
    for bar in bars:
        row = _bar_to_row(symbol, bar)
        if row is not None:
            rows.append(row)

    if not rows:
        return

    # COPY into a temp table then INSERT ... ON CONFLICT DO NOTHING so we
    # don't overwrite existing bars on re-fetch.
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TEMP TABLE IF NOT EXISTS _minute_stage (
                time        TIMESTAMPTZ NOT NULL,
                symbol      TEXT        NOT NULL,
                open        NUMERIC,
                high        NUMERIC,
                low         NUMERIC,
                close       NUMERIC,
                volume      BIGINT
            ) ON COMMIT DROP
        """)
        with cur.copy(
            "COPY _minute_stage (time, symbol, open, high, low, close, volume) FROM STDIN"
        ) as copy:
            for row in rows:
                copy.write_row(row)
        cur.execute("""
            INSERT INTO minute_ohlcv (time, symbol, open, high, low, close, volume)
            SELECT time, symbol, open, high, low, close, volume FROM _minute_stage
            ON CONFLICT (symbol, time) DO NOTHING
        """)
