"""Daily data acquisition daemon — data_gaps-driven cycle (slice 145/154)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal

import httpx
import psycopg
from psycopg_pool import ConnectionPool, PoolTimeout

from manta_trading.api.eodhd_sync import eodhd_get
from manta_trading.config import Settings
from manta_trading.constants import (
    DAEMON_LOCK_TIMEOUT,
    DAILY_HISTORY_FLOOR,
    DB_BULK_SESSION,
    CycleGranularity,
    DailyMode,
    FetchEntryPoint,
)
from manta_trading.market.db_session import make_configure_connection
from manta_trading.providers.types import ProviderType
from manta_trading.data.acquisition.daemon.cadence import daily_pass_boundary
from manta_trading.data.acquisition.quota import CallType, QuotaWaitAborted
from manta_trading.data.acquisition.outcomes import (
    ProviderResponseError,
    classify_outcome,
    outcome_to_fetch_status,
)
from manta_trading.data.acquisition.state import LastAttemptOutcome
from manta_trading.data.acquisition.symbols import iter_active_instruments

from manta_trading.data.gaps import coalesce_data_gaps, update_data_gaps
from manta_trading.data.locking import advisory_lock
from manta_trading.logging import get_logger

_logger = get_logger(__name__)

_UTC = timezone.utc
_EODHD_BASE = "https://eodhd.com/api"
_REQUEST_TIMEOUT = httpx.Timeout(connect=10.0, read=60.0, write=10.0, pool=5.0)

_DAILY_GRANULARITY: str = str(CycleGranularity.DAILY)
"""The granularity token this module writes and reads, as the DB stores it.

Derived from the enum rather than re-typed as a literal at each call site: the
work-list query reads back exactly what ``update_data_gaps`` and
``advisory_lock`` write here, and a mismatch produces a silent no-match — an
empty ``pending`` list, i.e. a daemon that quietly does nothing (912 review
F005). psycopg is handed the plain ``str`` because parameter adaptation is by
exact type."""

_WARM_OUTCOMES: tuple[LastAttemptOutcome, ...] = (
    LastAttemptOutcome.SUCCESS,
    LastAttemptOutcome.PARTIAL,
)
"""Outcomes proving the last recorded fetch attempt wrote bars for a symbol.

``_select_daily_mode`` treats a pending symbol as warm only when its
``acquisition_state`` row carries one of these; EMPTY and TRANSIENT_FAILURE
(and no row at all) read as cold. Defined once so the mode query and its
tests cannot drift apart."""


@dataclass
class CycleReport:
    """Summary of one run_daily_cycle or run_minute_cycle pass."""

    success_count: int = 0
    partial_count: int = 0
    empty_count: int = 0
    transient_failure_count: int = 0
    wall_clock_seconds: float = 0.0
    symbol_outcomes: dict[str, str] = field(default_factory=dict)

    unactionable_no_calendar: int = 0
    """Scope members excluded because their calendar resolves to no completed
    trading session (912 D6). Counted so the daemon can say what it could not
    act on instead of silently dropping it; see GitHub issue #4."""

    unknown_symbols: int = 0
    """Scope members with no ``instruments`` row (912 review F008). Separate
    from the count above because it means the request was wrong, not the
    reference data."""

    nothing_actionable: bool = False
    """True when the cycle derived an empty work list and made no provider
    call. Lets the runner distinguish a drained scope from a closed cadence
    gate without re-deriving the work list (912 D4).

    **Always False on a minute report.** ``run_minute_cycle`` returns EMPTY for
    a symbol with no actionable gap, which is indistinguishable from "fetched
    and got nothing", so the minute path publishes no drained signal and this
    slice deliberately does not add one. Consequently the runner can only reach
    NO_ACTIONABLE_WORK for daily-only scopes."""

    @property
    def total(self) -> int:
        return (
            self.success_count
            + self.partial_count
            + self.empty_count
            + self.transient_failure_count
        )


@dataclass(frozen=True)
class DailyWorkList:
    """Scope members split by what a daily cycle can actually do with them.

    Slice 912 D1/D6. The three buckets are mutually disjoint, and together with
    the symbols already attempted in this pass they partition the requested
    scope. Nothing is dropped: a scope member the cycle cannot act on is
    reported rather than silently omitted.
    """

    pending: list[str]
    """Symbols not yet attempted in the current pass, in the caller's order."""

    unactionable_no_calendar: list[str]
    """Symbols whose calendar resolves to no completed trading session, so no
    fetch window can be computed for them. Excluded from ``pending``
    deliberately: leaving them there would make the work list non-terminating,
    re-triggering the billable bulk EOD call on every cadence tick. Reported,
    not retried — fixing them is GitHub issue #4's job, not this slice's."""

    unknown_symbols: list[str]
    """Scope members with no ``instruments`` row at all.

    Also unactionable, but for an entirely different reason, and kept separate
    so the operator is told which one they have (912 review F008). A typo'd
    ``--symbols AAPLL`` is a mistake in the invocation; reporting it as a
    missing trading calendar points at issue #4, which has nothing to do with
    it. Counted rather than dropped either way — silently ignoring a requested
    symbol is how a scope quietly stops covering what the operator asked for."""

    @property
    def unactionable(self) -> list[str]:
        """Every scope member the cycle cannot act on, whatever the reason."""
        return self.unactionable_no_calendar + self.unknown_symbols


_PENDING_DAILY_SYMBOLS_SQL = """
    WITH scope(symbol, ord) AS (
        SELECT * FROM unnest(%(symbols)s::text[]) WITH ORDINALITY
    ),
    calendars_with_sessions AS (
        SELECT DISTINCT calendar_id
          FROM trading_sessions
         WHERE session_open_utc < NOW()
    ),
    symbol_calendar AS (
        SELECT i.symbol,
               bool_or(cw.calendar_id IS NOT NULL) AS has_calendar
          FROM instruments i
          LEFT JOIN calendars_with_sessions cw
                 ON cw.calendar_id = i.trading_calendar_id
         WHERE i.symbol = ANY(%(symbols)s::text[])
         GROUP BY i.symbol
    )
    SELECT s.symbol,
           sc.symbol IS NOT NULL AS is_known,
           COALESCE(sc.has_calendar, false) AS has_calendar,
           a.last_attempt_ts
      FROM scope s
      LEFT JOIN symbol_calendar sc
             ON sc.symbol = s.symbol
      LEFT JOIN acquisition_state a
             ON a.symbol = s.symbol
            AND a.granularity = %(granularity)s
            AND a.provider = %(provider)s
     ORDER BY s.ord
"""
"""Exactly one row per scope entry, by construction.

``instruments.symbol`` is **not unique** — the primary key is
``instrument_id``, ``canonical_id`` carries the UNIQUE constraint, and symbol
has only a non-unique index. Joining scope directly to ``instruments`` would
therefore emit one row per instrument row per symbol, which would put a symbol
into ``pending`` more than once (fetched repeatedly in a single pass) and could
put the same symbol into *both* buckets when one of its rows resolves a
calendar and another does not.

``symbol_calendar`` collapses that with ``GROUP BY i.symbol``, and ``bool_or``
makes a symbol actionable if *any* of its instrument rows resolves a calendar.

``session_open_utc < NOW()`` is what makes ``has_calendar`` mean the same thing
as ``_last_completed_session``, which applies that same bound. Without it a
calendar holding only *future* sessions satisfies this query and fails that
one, so the symbol lands in ``pending``, gets skipped by the cycle for want of
a fetch window, is never stamped, and returns to ``pending`` on the next
cadence tick — a work list that cannot terminate, re-issuing the billable bulk
EOD call every tick (912 review F002). The two must agree, and now do by
construction rather than by coincidence.

``WHERE i.symbol = ANY(...)`` bounds the aggregate to the requested scope.
Without it a ``--symbols AAPL`` invocation grouped the entire instrument
universe before the scope restriction was applied by the join, on every tick
under the new cadence (912 review F007).

``acquisition_state`` cannot fan out: ``(symbol, granularity, provider)`` is its
primary key.
"""


def pending_daily_symbols(
    conn: psycopg.Connection,
    symbol_list: list[str],
    pass_boundary: datetime,
) -> DailyWorkList:
    """Derive which scope members still need a daily fetch in the current pass.

    This is the slice 912 D1 replacement for the runner's in-memory
    once-per-day timer. Remaining work is *derived* from durable per-symbol
    state rather than tracked, so an interrupted pass resumes at exactly the
    symbols it never reached, and a process restart changes nothing.

    The signal is ``acquisition_state.last_attempt_ts``, which
    ``update_data_gaps`` already writes on both the STEADY_STATE and BACKFILL
    paths. A symbol legitimately carrying no bar for the session is stamped
    ``empty`` and drops out, which is what makes the derived list terminate —
    see D1 for why coverage-versus-session was rejected in its favour.

    Args:
        conn:          Open psycopg connection.
        symbol_list:   Requested scope, in the caller's preferred processing
                       order (``iter_active_instruments`` yields
                       ``most_stale_first``). Order is preserved in the result.
        pass_boundary: Start of the current daily pass — today's UTC midnight
                       plus ``DAILY_CYCLE_START_OFFSET``. A symbol stamped at
                       or after this instant has already been attempted in this
                       pass; one stamped before it (or never) has not.

    Returns:
        A :class:`DailyWorkList`. An empty ``pending`` means the pass is
        complete for this scope, and the caller must make no provider call.
    """
    if not symbol_list:
        return DailyWorkList(
            pending=[], unactionable_no_calendar=[], unknown_symbols=[]
        )

    with conn.cursor() as cur:
        cur.execute(
            _PENDING_DAILY_SYMBOLS_SQL,
            {
                "symbols": list(symbol_list),
                "granularity": _DAILY_GRANULARITY,
                "provider": str(ProviderType.EODHD),
            },
        )
        rows = cur.fetchall()

    pending: list[str] = []
    no_calendar: list[str] = []
    unknown: list[str] = []
    for symbol, is_known, has_calendar, last_attempt_ts in rows:
        if not is_known:
            unknown.append(symbol)
        elif not has_calendar:
            no_calendar.append(symbol)
        elif last_attempt_ts is not None and last_attempt_ts >= pass_boundary:
            continue  # already attempted in this pass
        else:
            pending.append(symbol)

    return DailyWorkList(
        pending=pending,
        unactionable_no_calendar=no_calendar,
        unknown_symbols=unknown,
    )


def run_daily_cycle(
    *,
    symbols: list[str] | None = None,
    should_continue: Callable[[], bool] | None = None,
    on_symbol: Callable[[str, str, "datetime | None", "datetime | None", int], None] | None = None,
) -> CycleReport:
    """Drive one daily-data acquisition pass over the instrument universe.

    Reads MT_TIMESCALE_DB_URL and MT_EODHD_API_KEY from the environment.
    Per-symbol transient failures are caught and recorded; HTTP 4xx (non-429)
    propagates and crashes the cycle.

    Mode selection (slice 154):
    - STEADY_STATE: all scope symbols have no UNKNOWN gaps → one bulk
      /eod-bulk-last-day call for the full US exchange.
    - BACKFILL: any symbol has UNKNOWN gaps → per-symbol /eod calls.

    Args:
        symbols: Optional explicit scope; defaults to ``iter_active_instruments``.
        should_continue: Optional zero-arg callable polled at the top of each
            per-symbol iteration. When it returns False the cycle exits
            cleanly between symbols. Default ``None`` preserves the slice 145
            behavior (cycle always processes its full scope). The slice 146
            runner supplies a callback that flips on SIGTERM.
        on_symbol: Optional callback invoked after each symbol completes in
            BACKFILL mode. Receives (symbol, outcome_str, None, None) — the
            trailing Nones match the minute cycle's chunk window signature so
            the same callback works for both. Not called in STEADY_STATE.
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
                            conn, ordering="most_stale_first", granularity=_DAILY_GRANULARITY
                        )
                    ]

            # Derive remaining work from durable per-symbol state (912 D1).
            # An interrupted pass resumes here at exactly the symbols it never
            # reached; a restart changes nothing, because nothing is tracked.
            with pool.connection() as conn:
                work = pending_daily_symbols(
                    conn, symbol_list, daily_pass_boundary(t0)
                )

            scope_size = len(symbol_list)
            # From here on, `symbol_list` is the *pending* set: every downstream
            # loop, count, and remaining-work message should describe work still
            # to do, not the full scope. `scope_size` keeps the original for
            # logging.
            symbol_list = work.pending

            report.unactionable_no_calendar = len(work.unactionable_no_calendar)
            report.unknown_symbols = len(work.unknown_symbols)
            # One line per cycle each, never one per symbol (912 D6): ~906
            # instruments are in this state on prod, and per-symbol warnings are
            # precisely how the condition stayed invisible. The two causes get
            # their own message because they need different actions from the
            # operator (912 review F008).
            if work.unactionable_no_calendar:
                _logger.warning(
                    "run_daily_cycle: %d of %d scope symbols have no completed "
                    "trading session on their calendar and cannot be fetched "
                    "(e.g. %s) — see issue #4",
                    len(work.unactionable_no_calendar),
                    scope_size,
                    ", ".join(work.unactionable_no_calendar[:5]),
                )
            if work.unknown_symbols:
                _logger.warning(
                    "run_daily_cycle: %d of %d requested symbols are not in "
                    "`instruments` and were skipped (e.g. %s) — check the "
                    "symbol list or seed the universe; unrelated to issue #4",
                    len(work.unknown_symbols),
                    scope_size,
                    ", ".join(work.unknown_symbols[:5]),
                )

            if not symbol_list:
                report.nothing_actionable = True
                report.wall_clock_seconds = (datetime.now(_UTC) - t0).total_seconds()
                _logger.info(
                    "run_daily_cycle: no actionable work — %d scope symbols "
                    "all attempted this pass or unactionable (%d no calendar, "
                    "%d unknown); no provider call made",
                    scope_size,
                    len(work.unactionable_no_calendar),
                    len(work.unknown_symbols),
                )
                return report

            # Determine mode: STEADY_STATE iff all pending symbols are caught up.
            with pool.connection() as conn:
                mode = _select_daily_mode(conn, symbol_list)

            _logger.info(
                "run_daily_cycle: mode=%s scope=%d symbols "
                "(%d pending, %d unactionable)",
                mode,
                scope_size,
                len(symbol_list),
                len(work.unactionable_no_calendar),
            )

            if mode == DailyMode.STEADY_STATE:
                # Mutates the caller's report rather than returning a new one:
                # rebinding here discarded the un-actionable counts set above,
                # while the BACKFILL branch below preserved them, so the two
                # modes disagreed on what a report contained (912 review F001).
                try:
                    _run_steady_state_cycle(
                        report=report,
                        symbol_list=symbol_list,
                        pool=pool,
                        http=http,
                        settings=settings,
                        should_continue=should_continue,
                        t0=t0,
                    )
                except QuotaWaitAborted:
                    _logger.info(
                        "run_daily_cycle: quota wait aborted by shutdown — "
                        "exiting"
                    )
            else:
                for sym in symbol_list:
                    if should_continue is not None and not should_continue():
                        _logger.info(
                            "run_daily_cycle: should_continue=False — exiting "
                            "between symbols (processed=%d, remaining=%d)",
                            report.total,
                            len(symbol_list) - report.total,
                        )
                        break
                    try:
                        outcome = _process_daily_symbol(
                            sym,
                            pool=pool,
                            http=http,
                            settings=settings,
                            via=FetchEntryPoint.CYCLE,
                        )
                    except QuotaWaitAborted:
                        _logger.info(
                            "run_daily_cycle: quota wait aborted by shutdown — "
                            "exiting (processed=%d, remaining=%d)",
                            report.total,
                            len(symbol_list) - report.total,
                        )
                        break
                    report.symbol_outcomes[sym] = str(outcome)
                    if outcome == LastAttemptOutcome.SUCCESS:
                        report.success_count += 1
                    elif outcome == LastAttemptOutcome.PARTIAL:
                        report.partial_count += 1
                    elif outcome == LastAttemptOutcome.EMPTY:
                        report.empty_count += 1
                    else:
                        report.transient_failure_count += 1
                    if on_symbol is not None:
                        on_symbol(sym, str(outcome), None, None, 0)

    report.wall_clock_seconds = (datetime.now(_UTC) - t0).total_seconds()
    return report


def _select_daily_mode(conn: psycopg.Connection, symbol_list: list[str]) -> DailyMode:
    """Return STEADY_STATE only when all symbols are caught up with bar data.

    BACKFILL is selected if any symbol has:
    - UNKNOWN gap rows (gaps to fetch), OR
    - no recorded fetch attempt that wrote bars (cold symbol).

    A symbol with only terminal gaps (PROVIDER_HOLE / RETRY_EXHAUSTED) but
    no bars is cold and needs a per-symbol /eod attempt, not a bulk call.

    Cold detection reads ``acquisition_state`` (an anti-join against
    ``_WARM_OUTCOMES``), never the raw ``daily_ohlcv`` hypertable. The
    previous shape — ``COUNT(DISTINCT symbol)`` over ``daily_ohlcv`` with a
    ~31k-element ``ANY`` — could not finish *planning* against the table's
    3,371 chunks and, with no ``statement_timeout``, wedged the daemon for
    15+ hours the first time an emptied ``data_gaps`` let it run at full
    scale (journal 20260806). ``acquisition_state`` is a small plain table
    keyed on ``(symbol, granularity, provider)``, so this stays bounded
    regardless of hypertable chunk count — including after ``daily_ohlcv``
    is rechunked.

    The proxy shifts semantics slightly: "has bars in daily_ohlcv" becomes
    "last recorded attempt wrote bars". A symbol whose last attempt was
    EMPTY or TRANSIENT_FAILURE now reads as cold, which errs toward
    BACKFILL — the conservative direction (a spurious BACKFILL costs
    per-symbol calls; a spurious STEADY_STATE would silently strand cold
    symbols behind the bulk call).
    """
    if not symbol_list:
        return DailyMode.STEADY_STATE

    with conn.cursor() as cur:
        # Any UNKNOWN gaps → backfill.
        cur.execute(
            "SELECT COUNT(*) FROM data_gaps "
            "WHERE granularity = %s AND symbol = ANY(%s) "
            "AND fetch_status = 'UNKNOWN'",
            (_DAILY_GRANULARITY, symbol_list),
        )
        row = cur.fetchone()
        if row and int(row[0]) > 0:
            return DailyMode.BACKFILL

        # Any pending symbol with no warm acquisition on record → backfill.
        cur.execute(
            "SELECT EXISTS ("
            "  SELECT 1 FROM unnest(%s::text[]) AS pending(symbol) "
            "  WHERE NOT EXISTS ("
            "    SELECT 1 FROM acquisition_state a "
            "    WHERE a.symbol = pending.symbol "
            "      AND a.granularity = %s "
            "      AND a.last_attempt_outcome = ANY(%s) "
            "  )"
            ")",
            (
                symbol_list,
                _DAILY_GRANULARITY,
                [str(outcome) for outcome in _WARM_OUTCOMES],
            ),
        )
        row = cur.fetchone()
        # A missing row is indeterminate; indeterminate reads as cold.
        any_cold = bool(row[0]) if row else True

    if any_cold:
        return DailyMode.BACKFILL

    return DailyMode.STEADY_STATE


def _run_steady_state_cycle(
    *,
    report: CycleReport,
    symbol_list: list[str],
    pool: ConnectionPool,
    http: httpx.Client,
    settings: Settings,
    should_continue: Callable[[], bool] | None,
    t0: datetime,
) -> CycleReport:
    """Fetch one bulk /eod-bulk-last-day/US call and route bars to each symbol.

    Populates ``report`` in place and returns it, so counts the caller already
    set (the un-actionable buckets) survive. Returning a fresh report here is
    what dropped them (912 review F001).
    """
    if should_continue is not None and not should_continue():
        return report

    url = (
        f"{_EODHD_BASE}/eod-bulk-last-day/US"
        f"?api_token={settings.eodhd_api_key}&fmt=json"
    )
    try:
        response = eodhd_get(http, url, CallType.BULK_EOD)
    except (httpx.HTTPError, httpx.TimeoutException) as exc:
        _logger.warning(
            "run_daily_cycle[STEADY_STATE]: bulk EOD call failed (retries exhausted): %s",
            exc,
        )
        for sym in symbol_list:
            report.symbol_outcomes[sym] = str(LastAttemptOutcome.TRANSIENT_FAILURE)
            report.transient_failure_count += 1
        return report
    except Exception:
        _logger.exception("run_daily_cycle[STEADY_STATE]: bulk EOD call failed unexpectedly")
        for sym in symbol_list:
            report.symbol_outcomes[sym] = str(LastAttemptOutcome.TRANSIENT_FAILURE)
            report.transient_failure_count += 1
        return report

    try:
        all_bars: list[dict] = response.json()
    except Exception:
        _logger.warning("run_daily_cycle[STEADY_STATE]: failed to parse bulk EOD response")
        all_bars = []

    # Build a symbol → bars mapping (EODHD uses "code" field, e.g. "AAPL.US").
    bars_by_symbol: dict[str, list[dict]] = {}
    for bar in all_bars:
        code: str = bar.get("code", "")
        sym = code.split(".")[0] if "." in code else code
        bars_by_symbol.setdefault(sym, []).append(bar)

    for sym in symbol_list:
        if should_continue is not None and not should_continue():
            break

        sym_bars = bars_by_symbol.get(sym, [])
        try:
            with pool.connection() as conn:
                target_end = _last_completed_session(conn, sym)

            if target_end is None:
                _logger.warning(
                    "run_daily_cycle[STEADY_STATE]: no trading sessions for %s — skipping", sym
                )
                report.symbol_outcomes[sym] = str(LastAttemptOutcome.TRANSIENT_FAILURE)
                report.transient_failure_count += 1
                continue

            target_start = datetime(DAILY_HISTORY_FLOOR.year, DAILY_HISTORY_FLOOR.month, DAILY_HISTORY_FLOOR.day, tzinfo=_UTC)
            outcome = (
                LastAttemptOutcome.SUCCESS if sym_bars else LastAttemptOutcome.EMPTY
            )
            fetch_status = outcome_to_fetch_status(outcome)

            with pool.connection() as conn:
                with conn.transaction():
                    with advisory_lock(
                            conn, sym, _DAILY_GRANULARITY,
                            timeout=DAEMON_LOCK_TIMEOUT,
                        ):
                        if sym_bars:
                            _insert_daily_bars(conn, sym, sym_bars)
                            _update_first_data_date(conn, sym, sym_bars)
                            _update_delisted_date_if_needed(conn, sym, sym_bars)
                        update_data_gaps(
                            conn, sym, _DAILY_GRANULARITY, target_start, target_end,
                            fetch_status, force_reset_terminal=False, outcome=outcome,
                        )

            report.symbol_outcomes[sym] = str(outcome)
            if outcome == LastAttemptOutcome.SUCCESS:
                report.success_count += 1
            else:
                report.empty_count += 1

        except psycopg.errors.LockNotAvailable:
            _logger.warning(
                "run_daily_cycle[STEADY_STATE]: advisory lock timeout for %s — skipping", sym
            )
            report.symbol_outcomes[sym] = str(LastAttemptOutcome.TRANSIENT_FAILURE)
            report.transient_failure_count += 1
        except Exception:
            _logger.exception(
                "run_daily_cycle[STEADY_STATE]: transient failure for %s", sym
            )
            report.symbol_outcomes[sym] = str(LastAttemptOutcome.TRANSIENT_FAILURE)
            report.transient_failure_count += 1

    return report


def _process_daily_symbol(
    symbol: str,
    *,
    pool: ConnectionPool,
    http: httpx.Client,
    settings: Settings,
    via: FetchEntryPoint,
) -> LastAttemptOutcome:
    try:
        return _do_daily_symbol(
            symbol, pool=pool, http=http, settings=settings, via=via
        )
    except QuotaWaitAborted:
        # Shutdown, not a failure — must reach the cycle loop, so it cannot
        # fall through to the except Exception below.
        raise
    except ProviderResponseError as exc:
        # Unexpected 4xx from EODHD — skip this symbol rather than crashing
        # the cycle. Log at ERROR so it surfaces for investigation.
        _logger.error(
            "ProviderResponseError for %s daily — skipping: %s via=%s",
            symbol,
            exc,
            via,
        )
        return LastAttemptOutcome.TRANSIENT_FAILURE
    except psycopg.errors.LockNotAvailable:
        _logger.warning(
            "Advisory lock timeout for %s daily — skipping via=%s", symbol, via
        )
        return LastAttemptOutcome.TRANSIENT_FAILURE
    except PoolTimeout:
        _logger.warning(
            "DB pool timeout for %s daily — DB unreachable, skipping via=%s",
            symbol,
            via,
        )
        return LastAttemptOutcome.TRANSIENT_FAILURE
    except (httpx.HTTPError, httpx.TimeoutException) as exc:
        _logger.warning(
            "HTTP transient failure for %s daily (retries exhausted): %s via=%s",
            symbol, exc, via,
        )
        return LastAttemptOutcome.TRANSIENT_FAILURE
    except Exception:
        _logger.exception("Transient failure for %s daily via=%s", symbol, via)
        return LastAttemptOutcome.TRANSIENT_FAILURE


def _do_daily_symbol(
    symbol: str,
    *,
    pool: ConnectionPool,
    http: httpx.Client,
    settings: Settings,
    via: FetchEntryPoint,
    force_reset_terminal: bool = False,
    window: tuple[date, date] | None = None,
) -> LastAttemptOutcome:
    with pool.connection() as conn:
        target_end = _last_completed_session(conn, symbol)
    if target_end is None:
        # No trading sessions available — can't determine window; skip symbol.
        _logger.warning(
            "No trading sessions found for %s — skipping via=%s", symbol, via
        )
        return LastAttemptOutcome.TRANSIENT_FAILURE

    if window is not None:
        target_start = datetime(window[0].year, window[0].month, window[0].day, tzinfo=_UTC)
        # Clamp window end to last completed session.
        window_end = datetime(window[1].year, window[1].month, window[1].day, tzinfo=_UTC)
        target_end = min(target_end, window_end)
    else:
        target_start = datetime(DAILY_HISTORY_FLOOR.year, DAILY_HISTORY_FLOOR.month, DAILY_HISTORY_FLOOR.day, tzinfo=_UTC)

    # Happy-path via marker (slice 165) — mirrors _do_minute_symbol; without
    # it a successful daily fetch emits no line identifying its entry point.
    _logger.info(
        "daily fetch: %s window=[%s → %s] via=%s",
        symbol,
        target_start.date(),
        target_end.date(),
        via,
    )

    url = (
        f"{_EODHD_BASE}/eod/{_normalise(symbol)}"
        f"?api_token={settings.eodhd_api_key}&fmt=json"
    )
    response = eodhd_get(http, url, CallType.EOD)
    outcome = classify_outcome(response, target_start, target_end)
    fetch_status = outcome_to_fetch_status(outcome)

    bars: list[dict] = []
    if outcome not in (LastAttemptOutcome.TRANSIENT_FAILURE, LastAttemptOutcome.EMPTY):
        try:
            bars = response.json()
        except Exception:
            bars = []

    with pool.connection() as conn:
        with conn.transaction():
            with advisory_lock(
                conn, symbol, _DAILY_GRANULARITY, timeout=DAEMON_LOCK_TIMEOUT
            ):
                if bars:
                    _insert_daily_bars(conn, symbol, bars)
                    _update_first_data_date(conn, symbol, bars)
                    _update_delisted_date_if_needed(conn, symbol, bars)

                update_data_gaps(
                    conn, symbol, _DAILY_GRANULARITY, target_start, target_end,
                    fetch_status, force_reset_terminal=force_reset_terminal, outcome=outcome,
                )

    return outcome


def run_daily_refetch(
    symbol: str,
    *,
    from_date: date | None = None,
    to_date: date | None = None,
) -> CycleReport:
    """Re-fetch daily bars for a symbol in the given window.

    Resets terminal gap rows (PROVIDER_HOLE / RETRY_EXHAUSTED) to UNKNOWN
    before re-attempting via force_reset_terminal=True. Runs coalesce after
    the fetch. Intended as an operator escape valve; runs outside daemon quota.

    Args:
        symbol:    Instrument ticker.
        from_date: Start of window; defaults to symbol's first_data_date (1970-01-01 fallback).
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
            # Resolve from_date default from instruments.first_data_date.
            if from_date is None:
                with pool.connection() as conn:
                    from_date = _first_data_date(conn, symbol)

            # Resolve to_date default from last completed trading session.
            if to_date is None:
                with pool.connection() as conn:
                    last_session = _last_completed_session(conn, symbol)
                to_date = last_session.date() if last_session is not None else date.today()

            window = (from_date, to_date)
            outcome = _do_daily_symbol(
                symbol,
                pool=pool,
                http=http,
                settings=settings,
                force_reset_terminal=True,
                window=window,
                via=FetchEntryPoint.REFETCH,
            )
            report.symbol_outcomes[symbol] = str(outcome)
            if outcome == LastAttemptOutcome.SUCCESS:
                report.success_count += 1
            elif outcome == LastAttemptOutcome.PARTIAL:
                report.partial_count += 1
            elif outcome == LastAttemptOutcome.EMPTY:
                report.empty_count += 1
            else:
                report.transient_failure_count += 1

            # coalesce_data_gaps runs after _do_daily_symbol returns, not inside it,
            # so the normal daemon cycle (which calls _do_daily_symbol directly) is unchanged.
            with pool.connection() as conn:
                with conn.transaction():
                    coalesce_data_gaps(conn, symbol, _DAILY_GRANULARITY)

    report.wall_clock_seconds = (datetime.now(_UTC) - t0).total_seconds()
    return report


def _last_completed_session(
    conn: psycopg.Connection, symbol: str
) -> datetime | None:
    """Return the session_open_utc of the last completed trading session for symbol's calendar."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT MAX(ts.session_open_utc)
              FROM trading_sessions ts
              JOIN instruments i ON i.trading_calendar_id = ts.calendar_id
             WHERE i.symbol = %s
               AND ts.session_open_utc < NOW()
            """,
            (symbol,),
        )
        row = cur.fetchone()
    if row is None or row[0] is None:
        return None
    return row[0]


def _insert_daily_bars(conn: psycopg.Connection, symbol: str, bars: list[dict]) -> None:
    """Bulk-insert daily bars via COPY."""
    rows: list[tuple] = []
    for bar in bars:
        try:
            bar_date = date.fromisoformat(bar["date"])
            bar_ts = datetime(bar_date.year, bar_date.month, bar_date.day, tzinfo=_UTC)
            rows.append((
                bar_ts, symbol,
                Decimal(str(bar.get("open", 0))),
                Decimal(str(bar.get("high", 0))),
                Decimal(str(bar.get("low", 0))),
                Decimal(str(bar.get("close", 0))),
                int(bar.get("volume") or 0),
            ))
        except (KeyError, ValueError):
            _logger.warning("Skipping malformed bar for %s: %r", symbol, bar)

    if not rows:
        return

    with conn.cursor() as cur:
        cur.execute("""
            CREATE TEMP TABLE IF NOT EXISTS _daily_stage (
                time    TIMESTAMPTZ NOT NULL,
                symbol  TEXT        NOT NULL,
                open    NUMERIC,
                high    NUMERIC,
                low     NUMERIC,
                close   NUMERIC,
                volume  BIGINT
            ) ON COMMIT DROP
        """)
        with cur.copy(
            "COPY _daily_stage (time, symbol, open, high, low, close, volume) FROM STDIN"
        ) as copy:
            for row in rows:
                copy.write_row(row)
        cur.execute("""
            INSERT INTO daily_ohlcv (time, symbol, open, high, low, close, volume)
            SELECT time, symbol, open, high, low, close, volume FROM _daily_stage
            ON CONFLICT (symbol, time) DO NOTHING
        """)


def _update_first_data_date(conn: psycopg.Connection, symbol: str, bars: list[dict]) -> None:
    dates = [bar["date"] for bar in bars if "date" in bar]
    if not dates:
        return
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE instruments SET first_data_date = %s WHERE symbol = %s AND first_data_date IS NULL",
            (date.fromisoformat(min(dates)), symbol),
        )


def _update_delisted_date_if_needed(conn: psycopg.Connection, symbol: str, bars: list[dict]) -> None:
    with conn.cursor() as cur:
        cur.execute("SELECT delisted_at_eodhd FROM instruments WHERE symbol = %s", (symbol,))
        row = cur.fetchone()
    if row is None or not row[0]:
        return
    dates = [bar["date"] for bar in bars if "date" in bar]
    if not dates:
        return
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE instruments SET delisted_date = %s WHERE symbol = %s AND delisted_date IS NULL",
            (date.fromisoformat(max(dates)), symbol),
        )


def _first_data_date(conn: psycopg.Connection, symbol: str) -> date:
    """Return instruments.first_data_date for symbol, or epoch date if NULL."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT first_data_date FROM instruments WHERE symbol = %s",
            (symbol,),
        )
        row = cur.fetchone()
    if row is None or row[0] is None:
        return DAILY_HISTORY_FLOOR
    return row[0]


def _normalise(symbol: str) -> str:
    return symbol if "." in symbol else f"{symbol}.US"
