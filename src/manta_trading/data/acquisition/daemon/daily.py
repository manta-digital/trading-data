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
from manta_trading.constants import DAEMON_LOCK_TIMEOUT, DAILY_HISTORY_FLOOR, DailyMode
from manta_trading.data.acquisition.quota import CallType
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


@dataclass
class CycleReport:
    """Summary of one run_daily_cycle or run_minute_cycle pass."""

    success_count: int = 0
    partial_count: int = 0
    empty_count: int = 0
    transient_failure_count: int = 0
    wall_clock_seconds: float = 0.0
    symbol_outcomes: dict[str, str] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return (
            self.success_count
            + self.partial_count
            + self.empty_count
            + self.transient_failure_count
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

    with ConnectionPool(settings.timescale_db_url, min_size=1, max_size=4) as pool:
        with httpx.Client(timeout=_REQUEST_TIMEOUT) as http:
            if symbols is not None:
                symbol_list = symbols
            else:
                with pool.connection() as conn:
                    symbol_list = [
                        row.symbol
                        for row in iter_active_instruments(
                            conn, ordering="most_stale_first", granularity="daily"
                        )
                    ]

            # Determine mode: STEADY_STATE iff all symbols are caught up.
            with pool.connection() as conn:
                mode = _select_daily_mode(conn, symbol_list)

            _logger.info("run_daily_cycle: mode=%s scope=%d symbols", mode, len(symbol_list))

            if mode == DailyMode.STEADY_STATE:
                report = _run_steady_state_cycle(
                    symbol_list=symbol_list,
                    pool=pool,
                    http=http,
                    settings=settings,
                    should_continue=should_continue,
                    t0=t0,
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
                    outcome = _process_daily_symbol(
                        sym, pool=pool, http=http, settings=settings, via="cycle"
                    )
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
    - zero rows in daily_ohlcv (never fetched — no gaps seeded yet either)

    A symbol with only terminal gaps (PROVIDER_HOLE / RETRY_EXHAUSTED) but
    no bars is cold and needs a per-symbol /eod attempt, not a bulk call.
    """
    if not symbol_list:
        return DailyMode.STEADY_STATE

    with conn.cursor() as cur:
        # Any UNKNOWN gaps → backfill.
        cur.execute(
            "SELECT COUNT(*) FROM data_gaps "
            "WHERE granularity = 'daily' AND symbol = ANY(%s) "
            "AND fetch_status = 'UNKNOWN'",
            (symbol_list,),
        )
        row = cur.fetchone()
        if row and int(row[0]) > 0:
            return DailyMode.BACKFILL

        # Any symbol with zero bars → backfill (cold symbol).
        cur.execute(
            "SELECT COUNT(DISTINCT symbol) FROM daily_ohlcv "
            "WHERE symbol = ANY(%s)",
            (symbol_list,),
        )
        row = cur.fetchone()
        symbols_with_bars = int(row[0]) if row else 0

    if symbols_with_bars < len(symbol_list):
        return DailyMode.BACKFILL

    return DailyMode.STEADY_STATE


def _run_steady_state_cycle(
    *,
    symbol_list: list[str],
    pool: ConnectionPool,
    http: httpx.Client,
    settings: Settings,
    should_continue: Callable[[], bool] | None,
    t0: datetime,
) -> CycleReport:
    """Fetch one bulk /eod-bulk-last-day/US call and route bars to each symbol."""
    report = CycleReport()

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
                    with advisory_lock(conn, sym, "daily", timeout=DAEMON_LOCK_TIMEOUT):
                        if sym_bars:
                            _insert_daily_bars(conn, sym, sym_bars)
                            _update_first_data_date(conn, sym, sym_bars)
                            _update_delisted_date_if_needed(conn, sym, sym_bars)
                        update_data_gaps(
                            conn, sym, "daily", target_start, target_end,
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
    via: str,
) -> LastAttemptOutcome:
    try:
        return _do_daily_symbol(
            symbol, pool=pool, http=http, settings=settings, via=via
        )
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
    via: str,
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
            with advisory_lock(conn, symbol, "daily", timeout=DAEMON_LOCK_TIMEOUT):
                if bars:
                    _insert_daily_bars(conn, symbol, bars)
                    _update_first_data_date(conn, symbol, bars)
                    _update_delisted_date_if_needed(conn, symbol, bars)

                update_data_gaps(
                    conn, symbol, "daily", target_start, target_end,
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

    with ConnectionPool(settings.timescale_db_url, min_size=1, max_size=2) as pool:
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
                via="refetch",
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
                    coalesce_data_gaps(conn, symbol, "daily")

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
