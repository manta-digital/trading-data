"""Minute data acquisition daemon — data_gaps-driven cycle (slice 145)."""

from __future__ import annotations

from collections.abc import Callable
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
    MINUTE_SEED_PROGRESS_LOG_INTERVAL,
    FetchEntryPoint,
)
from manta_trading.market.db_session import make_configure_connection
from manta_trading.data.acquisition.quota import CallType, QuotaWaitAborted
from manta_trading.data.acquisition.daemon.daily import (
    CycleReport,
    _last_completed_session,
    _normalise,
)
from manta_trading.data.quality.fetch_status import FetchStatus
from manta_trading.data.acquisition.outcomes import (
    ProviderResponseError,
    classify_outcome,
    outcome_to_fetch_status,
)
from manta_trading.data.acquisition.state import LastAttemptOutcome
from manta_trading.data.acquisition.symbols import iter_active_instruments

from manta_trading.data.gaps import (
    GapRow,
    coalesce_data_gaps,
    pick_most_recent_actionable_gap,
    update_data_gaps,
)
from manta_trading.data.gaps.minute_coverage import (
    build_minute_coverage_index,
    build_symbol_minute_coverage,
    compute_missing_minute_sessions,
)
from manta_trading.data.locking import advisory_lock
from manta_trading.logging import get_logger

_logger = get_logger(__name__)

_UTC = timezone.utc
_EODHD_BASE = "https://eodhd.com/api"
_REQUEST_TIMEOUT = httpx.Timeout(connect=10.0, read=60.0, write=10.0, pool=5.0)
_PROVIDER_MAX_CHUNK_DAYS: int = 120


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

            symbols_scanned = 0
            gaps_seeded_total = 0
            for sym in symbol_list:
                if should_continue is not None and not should_continue():
                    _logger.info(
                        "run_minute_cycle: should_continue=False — exiting "
                        "between symbols (processed=%d, remaining=%d)",
                        report.total,
                        len(symbol_list) - report.total,
                    )
                    break
                try:
                    outcome, cs, ce, n_chunks, gaps_seeded = _process_minute_symbol(
                        sym,
                        pool=pool,
                        http=http,
                        settings=settings,
                        coverage_index=coverage_index,
                        via=FetchEntryPoint.CYCLE,
                        should_continue=should_continue,
                    )
                except QuotaWaitAborted:
                    _logger.info(
                        "run_minute_cycle: quota wait aborted by shutdown — "
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
                    on_symbol(sym, str(outcome), cs, ce, n_chunks)

                symbols_scanned += 1
                gaps_seeded_total += gaps_seeded
                if symbols_scanned % MINUTE_SEED_PROGRESS_LOG_INTERVAL == 0:
                    _logger.info(
                        "minute seed: %d/%d symbols scanned, %d gap rows seeded",
                        symbols_scanned,
                        len(symbol_list),
                        gaps_seeded_total,
                    )

            _logger.info(
                "minute seed: complete — %d symbols, %d gap rows seeded",
                symbols_scanned,
                gaps_seeded_total,
            )

    report.wall_clock_seconds = (datetime.now(_UTC) - t0).total_seconds()
    return report


def _process_minute_symbol(
    symbol: str,
    *,
    pool: ConnectionPool,
    http: httpx.Client,
    settings: Settings,
    via: FetchEntryPoint,
    coverage_index: dict[str, set[date]] | None = None,
    should_continue: Callable[[], bool] | None = None,
) -> tuple[LastAttemptOutcome, datetime | None, datetime | None, int, int]:
    try:
        return _do_minute_symbol(
            symbol,
            pool=pool,
            http=http,
            settings=settings,
            coverage_index=coverage_index,
            via=via,
            should_continue=should_continue,
        )
    except QuotaWaitAborted:
        # Shutdown, not a failure — must reach the cycle loop, so it cannot
        # fall through to the except Exception below.
        raise
    except ProviderResponseError as exc:
        # Non-404 4xx from EODHD — unexpected but skip this symbol rather than
        # crashing the entire cycle. Log at ERROR so it surfaces for investigation.
        _logger.error(
            "ProviderResponseError for %s minute — skipping: %s via=%s",
            symbol,
            exc,
            via,
        )
        return LastAttemptOutcome.TRANSIENT_FAILURE, None, None, 0, 0
    except psycopg.errors.LockNotAvailable:
        _logger.warning(
            "Advisory lock timeout for %s minute — skipping via=%s", symbol, via
        )
        return LastAttemptOutcome.TRANSIENT_FAILURE, None, None, 0, 0
    except PoolTimeout:
        _logger.warning(
            "DB pool timeout for %s minute — DB unreachable, skipping via=%s",
            symbol,
            via,
        )
        return LastAttemptOutcome.TRANSIENT_FAILURE, None, None, 0, 0
    except (httpx.HTTPError, httpx.TimeoutException) as exc:
        _logger.warning(
            "HTTP transient failure for %s minute (retries exhausted): %s via=%s",
            symbol,
            exc,
            via,
        )
        return LastAttemptOutcome.TRANSIENT_FAILURE, None, None, 0, 0
    except Exception:
        _logger.exception("Transient failure for %s minute via=%s", symbol, via)
        return LastAttemptOutcome.TRANSIENT_FAILURE, None, None, 0, 0


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
) -> tuple[LastAttemptOutcome, datetime | None, datetime | None, int, int]:
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
        if _needs_full_seed:
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
                precomputed_ranges = compute_missing_minute_sessions(
                    conn, symbol, coverage_index, seed_from, target_end
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
                chunk_conn, symbol, "minute", history_start, target_end
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
            f"&from={int(chunk_start.timestamp())}&to={int(chunk_end.timestamp())}"
        )
        response = eodhd_get(http, url, CallType.INTRADAY)
        outcome = classify_outcome(response, chunk_start, chunk_end)

        bars: list[dict] = []
        if outcome not in (
            LastAttemptOutcome.TRANSIENT_FAILURE,
            LastAttemptOutcome.EMPTY,
        ):
            try:
                bars = response.json()
            except Exception:
                bars = []

        # Trailing-weekend / trailing-holiday tolerance: classify_outcome
        # marks the response PARTIAL whenever the latest bar's date is
        # before chunk_end's date. For minute granularity, chunk_end is
        # set from gap.gap_end which is anchored to UTC midnight of the
        # current calendar day, so a Sunday chunk_end will *never* be
        # reached by EODHD (which only returns weekday session bars).
        # Without this override the chunk re-fetches forever, never
        # adding bars (all duplicates), with attempt_count climbing.
        # If we got bars and the latest one is within a small calendar
        # tolerance of chunk_end, accept it as SUCCESS — the
        # unreachable trailing window is non-trading time, not data
        # the provider is withholding.
        if outcome == LastAttemptOutcome.PARTIAL and bars:
            latest = _latest_bar_dt(bars)
            if latest is not None and (chunk_end.date() - latest.date()).days <= 4:
                outcome = LastAttemptOutcome.SUCCESS

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
                    _record_minute_attempt(chunk_conn, symbol, outcome)

    with pool.connection() as chunk_conn:
        with chunk_conn.transaction():
            coalesce_data_gaps(chunk_conn, symbol, "minute")

    # Display outcome: first chunk (most recent window) is the meaningful signal.
    # last_outcome (oldest chunk) is often empty for pre-IPO periods.
    display_outcome = (
        first_chunk_outcome if first_chunk_outcome is not None else last_outcome
    )
    return display_outcome, first_chunk_end, last_chunk_end, chunk_count, gaps_seeded


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
            outcome, _, __, ___, ____ = _do_minute_symbol(
                symbol,
                pool=pool,
                http=http,
                settings=settings,
                force_reset_terminal=True,
                window=window,
                coverage_index=coverage_index,
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

    report.wall_clock_seconds = (datetime.now(_UTC) - t0).total_seconds()
    return report


def _latest_bar_dt(bars: list[dict]) -> datetime | None:
    """Return the latest UTC datetime found in EODHD minute bars, or None.

    Mirrors the timestamp parsing in _insert_minute_bars but skips the rest.
    Used by the daemon to decide when a PARTIAL outcome is really SUCCESS
    against a chunk_end that lands on a non-trading day.
    """
    latest: datetime | None = None
    for bar in bars:
        try:
            ts_epoch = bar.get("timestamp")
            if ts_epoch is not None:
                ts = datetime.fromtimestamp(int(ts_epoch), tz=_UTC)
            else:
                ts = datetime.fromisoformat(bar.get("datetime", "")).replace(
                    tzinfo=_UTC
                )
        except (KeyError, ValueError, TypeError):
            continue
        if latest is None or ts > latest:
            latest = ts
    return latest


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
