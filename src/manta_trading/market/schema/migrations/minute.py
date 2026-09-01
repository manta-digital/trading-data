"""Schema migration definitions for the minute (TimescaleDB) database track.

Each entry is a dict with ``id``, ``description``, and ``sql`` keys.
SQL is idempotent (IF NOT EXISTS, ON CONFLICT DO NOTHING, DO $$ guards).
"""

from __future__ import annotations

import os
from datetime import date, timedelta
from typing import Any

from manta_trading.constants import (
    COVERAGE_BUCKET_INTERVAL,
    DAILY_COVERAGE_REFRESH_END_OFFSET,
    DAILY_COVERAGE_REFRESH_SCHEDULE_INTERVAL,
    DAILY_COVERAGE_REFRESH_START_OFFSET,
    DAILY_COVERAGE_VIEW,
    DAILY_HISTORY_MONTHS,
    DAILY_OHLCV_CHUNK_INTERVAL,
    DAILY_OHLCV_TABLE,
    DAILY_STALENESS_THRESHOLD,
    GRANULARITY_SOURCE,
    LATE_BAR_GRACE_PERIOD,
    MINUTE_CAGG_CHUNK_INTERVAL,
    MINUTE_CAGG_COMPRESS_AFTER,
    MINUTE_CAGG_GRANULARITIES,
    MINUTE_CAGG_REFRESH_SCHEDULE_INTERVAL,
    DAILY_MONTHLY_REFRESH_START_OFFSET,
    MINUTE_CAGG_REFRESH_START_OFFSET,
    MINUTE_COVERAGE_REFRESH_END_OFFSET,
    MINUTE_COVERAGE_REFRESH_SCHEDULE_INTERVAL,
    MINUTE_COVERAGE_REFRESH_START_OFFSET,
    MINUTE_COVERAGE_VIEW,
    MINUTE_OHLCV_CHUNK_INTERVAL,
    MINUTE_STALENESS_THRESHOLD,
    TRADING_SESSIONS_EXTENSION_YEARS,
)
from manta_trading.data.acquisition.state import LastAttemptOutcome
from manta_trading.data.quality.fetch_status import FetchStatus
from manta_trading.data.universe.eodhd_classification import EodhdType
from manta_trading.market.schema.seed_calendar import (
    NASDAQ_CALENDAR,
    NYSE_CALENDAR,
    generate_calendar_insert_sql,
    generate_holidays,
    generate_holidays_insert_sql,
)


def _minute_chunk_interval_sql() -> str:
    """Render MINUTE_OHLCV_CHUNK_INTERVAL as a SQL INTERVAL literal.

    Seconds-based so any timedelta renders exactly; keeps migrations 001c and
    043 derived from the one constant (slice 166)."""
    return f"INTERVAL '{int(MINUTE_OHLCV_CHUNK_INTERVAL.total_seconds())} seconds'"


def _daily_chunk_interval_sql() -> str:
    """Render DAILY_OHLCV_CHUNK_INTERVAL as a SQL INTERVAL literal.

    Keeps the create-hypertable migration (023) and migration 050 derived from
    the one constant (slice 170), so a cold start and a migrated database land
    on the same interval."""
    return f"INTERVAL '{int(DAILY_OHLCV_CHUNK_INTERVAL.total_seconds())} seconds'"


# The four minute-cagg view names migrations 044/045 target — resolved from the
# canonical constants so the views cannot drift from the granularity→source
# mapping (slice 163).
_MINUTE_CAGG_VIEWS: tuple[str, ...] = tuple(
    GRANULARITY_SOURCE[g] for g in MINUTE_CAGG_GRANULARITIES
)


def _minute_cagg_views_sql_array() -> str:
    """Render the four minute-cagg view names as a SQL text[] array literal.

    Used by migrations 044/045 to iterate the caggs inside a DO-block that
    resolves each mat hypertable by *view name* from the catalog (never by
    ``mat_N`` literal, which is assignment-order-dependent)."""
    quoted = ", ".join(f"'{v}'" for v in _MINUTE_CAGG_VIEWS)
    return f"ARRAY[{quoted}]"


def _interval_seconds_sql(td: timedelta) -> str:
    """Render a timedelta as a seconds-based SQL INTERVAL literal (exact)."""
    return f"INTERVAL '{int(td.total_seconds())} seconds'"


#: Every minute cagg refresh policy's start_offset, rendered once (035, 053).
_MINUTE_REFRESH_START_SQL = _interval_seconds_sql(MINUTE_CAGG_REFRESH_START_OFFSET)
#: daily_monthly_ohlcv refresh start_offset, rendered once (035, 054).
_DAILY_MONTHLY_REFRESH_START_SQL = _interval_seconds_sql(
    DAILY_MONTHLY_REFRESH_START_OFFSET
)


def _eodhd_type_check_sql() -> str:
    """Render the IN (...) clause for instruments.eodhd_type CHECK constraint
    from EodhdType enum, so the migration and the enum cannot drift."""
    quoted = ", ".join(f"'{t.value}'" for t in sorted(EodhdType, key=lambda e: e.value))
    return f"eodhd_type IN ({quoted})"


# Historic CoverageGapStatus values — inlined here after coverage/ package was
# deleted in slice 142. Migration 012 SQL is frozen history; these values must
# not change. Do not import from manta_trading.data.coverage (package gone).
_COVERAGE_GAP_STATUS_UNKNOWN = "unknown"
_COVERAGE_GAP_STATUS_PROVIDER_CONFIRMED_UNFILLABLE = "provider_confirmed_unfillable"
_COVERAGE_GAP_STATUS_RETRY_PENDING = "retry_pending"
_COVERAGE_GAP_STATUS_RESOLVED = "resolved"
# Sorted alphabetically to match the original _coverage_status_check_sql output.
_COVERAGE_STATUS_SORTED = sorted(
    [
        _COVERAGE_GAP_STATUS_PROVIDER_CONFIRMED_UNFILLABLE,
        _COVERAGE_GAP_STATUS_RESOLVED,
        _COVERAGE_GAP_STATUS_RETRY_PENDING,
        _COVERAGE_GAP_STATUS_UNKNOWN,
    ]
)


def _coverage_status_check_sql() -> str:
    """Render the IN (...) clause for the historic coverage_gaps CHECK constraint.

    The coverage/ package was deleted in slice 142. Values are inlined above.
    Migration 012 SQL is frozen history — do not modify these values.
    """
    quoted = ", ".join(f"'{v}'" for v in _COVERAGE_STATUS_SORTED)
    return f"resolution_status IN ({quoted})"


def _fetch_status_check_sql() -> str:
    """Render the IN (...) clause for data_gaps.fetch_status CHECK from FetchStatus enum.

    Values are sorted alphabetically for deterministic migration text.
    """
    quoted = ", ".join(
        f"'{v.value}'" for v in sorted(FetchStatus, key=lambda s: s.value)
    )
    return f"fetch_status IN ({quoted})"


def _outcome_check_sql() -> str:
    """Render the IN (...) clause for acquisition_state.last_attempt_outcome CHECK.

    Values are sorted alphabetically for deterministic migration text.
    """
    quoted = ", ".join(
        f"'{v.value}'" for v in sorted(LastAttemptOutcome, key=lambda s: s.value)
    )
    return f"last_attempt_outcome IN ({quoted})"


def _interval_literal(td: timedelta) -> str:
    """Convert a timedelta to a Postgres interval string.

    Handles the two cases used by this codebase:
    - whole days (no seconds component) → ``'N days'``
    - whole minutes (seconds divisible by 60, <1 day) → ``'N minutes'``
    """
    total_seconds = int(td.total_seconds())
    if td.seconds == 0 and td.microseconds == 0:
        return f"{td.days} days"
    minutes, remainder = divmod(total_seconds, 60)
    if remainder == 0:
        return f"{minutes} minutes"
    raise ValueError(
        f"Cannot convert {td!r} to a Postgres interval literal: "
        "only whole-day and whole-minute timedeltas are supported."
    )


def _composite_lag_literal(td: timedelta) -> str:
    """Render a days+minutes duration for human prose (slice 169 D5).

    Distinct from :func:`_interval_literal`, which renders values that become
    real SQL ``INTERVAL`` literals and therefore must stay in a single unit.
    This one is for the ``COMMENT ON VIEW`` text only, where a coverage lag of
    one bucket plus a schedule interval would otherwise render as
    ``525720 minutes`` — arithmetically right and useless to the operator the
    comment exists for.
    """
    days, seconds = td.days, td.seconds
    minutes = seconds // 60
    if days and minutes:
        return f"{days} days {minutes} minutes"
    if days:
        return f"{days} days"
    return f"{minutes} minutes"


_SEED_START_YEAR = 2020
_SEED_END_YEAR = 2026


def _build_seed_sql(calendar: dict, calendar_id: str) -> str:
    """Build combined INSERT SQL for a calendar and its holidays."""
    cal_sql = generate_calendar_insert_sql(calendar)
    holidays = generate_holidays(calendar_id, _SEED_START_YEAR, _SEED_END_YEAR)
    hol_sql = generate_holidays_insert_sql(holidays)
    return f"{cal_sql}\n{hol_sql}"


def _history_horizon_disjunct() -> str:
    """Build the third disjunct of the symbols_x_granularity WHERE clause.

    If DAILY_HISTORY_MONTHS is None (unbounded), returns 'TRUE' so the
    filter degenerates to no upper bound. Otherwise returns an interval
    expression cutting off long-dead delistings.
    """
    if DAILY_HISTORY_MONTHS is None:
        return "TRUE"
    return f"i.delisted_date >= NOW() - INTERVAL '{DAILY_HISTORY_MONTHS} months'"


# Pre-render interval literals from constants at module load time so the
# migration SQL text is deterministic and does not contain Python f-string
# placeholders at runtime.
_LATE_BAR_GRACE_LITERAL = _interval_literal(LATE_BAR_GRACE_PERIOD)
_DAILY_STALENESS_LITERAL = _interval_literal(DAILY_STALENESS_THRESHOLD)
_MINUTE_STALENESS_LITERAL = _interval_literal(MINUTE_STALENESS_THRESHOLD)
_HISTORY_HORIZON_DISJUNCT = _history_horizon_disjunct()


def _build_data_status_view_sql(
    *,
    include_daily_branch: bool,
    include_trading_sessions_cte: bool = False,
    cagg_backed_bars_summary: bool = False,
) -> str:
    """Build the data_status view CREATE OR REPLACE statement.

    The ``daily_ohlcv`` hypertable is created in slice 143 (mirroring
    ``minute_ohlcv``). For slice 142 cold-starts that run before slice 143
    has been applied, we install a view variant whose ``bars_summary`` CTE
    omits the daily branch entirely — daily symbols still appear in
    ``data_status`` (via the CROSS JOIN in symbols_x_granularity) with
    ``bars_stored = 0``. Migration 021 picks the variant at apply-time
    by inspecting ``to_regclass('daily_ohlcv')``.

    When ``include_trading_sessions_cte=True`` (migration 028+), the view
    projects ``target_end_ts`` from ``trading_sessions`` via the
    ``exchange_completed_close`` CTE. When False (migrations 021/024),
    ``target_end_ts = NULL`` — the slice-142 stub.

    When ``cagg_backed_bars_summary=True`` (migration 048+, slice 167), the
    ``bars_summary`` CTE reads the coverage continuous aggregates instead of
    aggregating the raw hypertables per symbol. Nothing else about the view
    changes: same CTEs, same joins, same output columns in the same order (D2).
    The minute branch's timestamps become bucket-truncated as a consequence —
    documented on the view itself by migration 048's ``COMMENT ON VIEW`` — while
    the daily branch stays exact, because ``daily_coverage`` reads raw
    ``daily_ohlcv`` rather than a parent cagg.
    """
    if cagg_backed_bars_summary:
        # first_bucket/last_bucket are already per-(symbol, year) extremes, so
        # the outer MIN/MAX collapse them to per-symbol extremes; SUM(bars)
        # rolls the per-year counts up the same way.
        # SUM() over a bigint column yields numeric; cast back to BIGINT so the
        # bars_stored column keeps the exact type COUNT(*) produced. Without
        # this, CREATE OR REPLACE VIEW refuses the change outright ("cannot
        # change data type of view column") -- and the D2 contract would be
        # broken even if it did not.
        minute_coverage_branch = (
            "    SELECT ''minute''::TEXT AS granularity, symbol, "
            "           MIN(first_bucket)  AS first_bar_ts, "
            "           MAX(last_bucket)   AS last_bar_ts, "
            "           SUM(bars)::BIGINT  AS bars_stored "
            f"    FROM {MINUTE_COVERAGE_VIEW} GROUP BY symbol"
        )
        if include_daily_branch:
            bars_summary_cte = (
                "bars_summary AS ("
                "    SELECT ''daily''::TEXT  AS granularity, symbol, "
                "           MIN(first_bucket)  AS first_bar_ts, "
                "           MAX(last_bucket)   AS last_bar_ts, "
                "           SUM(bars)::BIGINT  AS bars_stored "
                f"    FROM {DAILY_COVERAGE_VIEW} GROUP BY symbol "
                "    UNION ALL "
                f"{minute_coverage_branch}"
                ")"
            )
        else:
            bars_summary_cte = f"bars_summary AS ({minute_coverage_branch})"
    elif include_daily_branch:
        bars_summary_cte = (
            "bars_summary AS ("
            "    SELECT ''daily''::TEXT  AS granularity, symbol, "
            "           MIN(time) AS first_bar_ts, MAX(time) AS last_bar_ts, "
            "           COUNT(*) AS bars_stored "
            "    FROM daily_ohlcv GROUP BY symbol "
            "    UNION ALL "
            "    SELECT ''minute''::TEXT AS granularity, symbol, "
            "           MIN(time) AS first_bar_ts, MAX(time) AS last_bar_ts, "
            "           COUNT(*) AS bars_stored "
            "    FROM minute_ohlcv GROUP BY symbol"
            ")"
        )
    else:
        bars_summary_cte = (
            "bars_summary AS ("
            "    SELECT ''minute''::TEXT AS granularity, symbol, "
            "           MIN(time) AS first_bar_ts, MAX(time) AS last_bar_ts, "
            "           COUNT(*) AS bars_stored "
            "    FROM minute_ohlcv GROUP BY symbol"
            ")"
        )

    if include_trading_sessions_cte:
        leading_cte = (
            "WITH exchange_completed_close AS ("
            "    SELECT calendar_id, "
            "           MAX(session_close_utc) AS completed_close_ts "
            "    FROM trading_sessions "
            f"   WHERE session_close_utc + INTERVAL ''{_LATE_BAR_GRACE_LITERAL}'' < NOW() "
            "    GROUP BY calendar_id"
            "), "
        )
        target_end_col = "ec.completed_close_ts AS target_end_ts, "
        calendar_join = (
            "LEFT JOIN exchange_completed_close ec "
            "       ON ec.calendar_id = s.trading_calendar_id;"
        )
    else:
        leading_cte = "WITH "
        target_end_col = "NULL::TIMESTAMPTZ AS target_end_ts, "
        calendar_join = ";"

    return (
        "CREATE OR REPLACE VIEW data_status AS "
        f"{leading_cte}"
        "symbols_x_granularity AS ("
        "    SELECT i.symbol, i.trading_calendar_id, "
        "           i.first_listing_date, i.first_data_date, g.granularity "
        "    FROM instruments i "
        "    CROSS JOIN (VALUES (''daily''), (''minute'')) AS g(granularity) "
        "    WHERE i.delisted_at_eodhd = FALSE "
        "       OR i.delisted_date IS NULL "
        f"       OR {_HISTORY_HORIZON_DISJUNCT}"
        "), "
        f"{bars_summary_cte}, "
        "gap_counts AS ("
        "    SELECT symbol, granularity, COUNT(*) AS gap_count, "
        "           BOOL_OR(fetch_status = ''RETRY_EXHAUSTED'') AS has_retry_exhausted "
        "    FROM data_gaps GROUP BY symbol, granularity"
        ") "
        "SELECT s.symbol, s.granularity, s.trading_calendar_id, "
        "       bs.first_bar_ts, bs.last_bar_ts, "
        "       COALESCE(bs.bars_stored, 0) AS bars_stored, "
        f"       {target_end_col}"
        "       COALESCE(s.first_listing_date, s.first_data_date) AS effective_start, "
        "       COALESCE(gc.gap_count, 0) AS gap_count, "
        "       COALESCE(gc.has_retry_exhausted, FALSE) AS has_retry_exhausted, "
        "       ast.last_attempt_ts, ast.last_attempt_outcome, "
        "       CASE "
        "           WHEN COALESCE(gc.has_retry_exhausted, FALSE) THEN ''FAILED'' "
        "           WHEN ast.last_attempt_ts IS NULL "
        "                OR ast.last_attempt_ts < NOW() - CASE s.granularity "
        f"                    WHEN ''daily''  THEN INTERVAL ''{_DAILY_STALENESS_LITERAL}'' "
        f"                    WHEN ''minute'' THEN INTERVAL ''{_MINUTE_STALENESS_LITERAL}'' "
        "                END THEN ''STALE'' "
        "           WHEN COALESCE(gc.gap_count, 0) > 0 THEN ''GAPS'' "
        "           ELSE ''OK'' "
        "       END AS health "
        "FROM symbols_x_granularity s "
        "LEFT JOIN bars_summary bs "
        "       ON bs.symbol = s.symbol AND bs.granularity = s.granularity "
        "LEFT JOIN gap_counts gc "
        "       ON gc.symbol = s.symbol AND gc.granularity = s.granularity "
        "LEFT JOIN acquisition_state ast "
        "       ON ast.symbol = s.symbol AND ast.granularity = s.granularity "
        f"{calendar_join}"
    )


# Slice 142/143 stub: target_end_ts = NULL (no trading_sessions table yet)
_DATA_STATUS_VIEW_WITH_DAILY = _build_data_status_view_sql(include_daily_branch=True)
_DATA_STATUS_VIEW_WITHOUT_DAILY = _build_data_status_view_sql(
    include_daily_branch=False
)

# Slice 144 rewrite: target_end_ts from exchange_completed_close CTE
_DATA_STATUS_VIEW_WITH_DAILY_TS = _build_data_status_view_sql(
    include_daily_branch=True, include_trading_sessions_cte=True
)
_DATA_STATUS_VIEW_WITHOUT_DAILY_TS = _build_data_status_view_sql(
    include_daily_branch=False, include_trading_sessions_cte=True
)


def _data_status_doc_comment() -> str:
    """Render the ``COMMENT ON VIEW data_status`` text for migration 048.

    Built from the coverage constants rather than literals, so the documented
    bounds cannot drift from the policies actually installed by 047. Criterion 4
    is verified by reading this back via ``obj_description``.

    Single-quoted for embedding in the migration's DO block, hence the doubled
    quotes throughout — same escaping convention as the view builder.

    **The CAGG LAG formula carries a bucket-width term (slice 169 D5).** Before
    169 this rendered the schedule intervals alone — "2 hours total" — which was
    wrong from slice 167 onward, independent of any width change: a refresh
    policy's window is truncated to whole buckets, so the *open* bucket is never
    re-materialized while it is open. The schedule interval bounds only how
    promptly *closed* buckets are rewritten. The real bound an operator running
    ``\\d+ data_status`` needs is therefore

        coverage lag  =  COVERAGE_BUCKET_INTERVAL + refresh schedule interval(s)

    Rendering the old formula promised hours against a production reality of
    months (both coverage caggs sat at 2025-12-26 on 2026-08-11 while raw ran to
    2026-08-07). This is the artifact 140-arch points operators at, so it is
    precisely the one that must not lie.
    """
    bucket_lag = _interval_literal(COVERAGE_BUCKET_INTERVAL)
    minute_lag = _composite_lag_literal(
        COVERAGE_BUCKET_INTERVAL
        + MINUTE_CAGG_REFRESH_SCHEDULE_INTERVAL
        + MINUTE_COVERAGE_REFRESH_SCHEDULE_INTERVAL
    )
    daily_lag = _composite_lag_literal(
        COVERAGE_BUCKET_INTERVAL + DAILY_COVERAGE_REFRESH_SCHEDULE_INTERVAL
    )
    return (
        "data_status: per-symbol acquisition health. "
        "bars_summary (first_bar_ts, last_bar_ts, bars_stored) derives from the "
        f"{MINUTE_COVERAGE_VIEW} and {DAILY_COVERAGE_VIEW} continuous "
        "aggregates as of slice 167, not from a per-symbol scan of the raw "
        "hypertables. Two documented bounds apply. "
        "(1) BUCKET TRUNCATION: minute first_bar_ts/last_bar_ts are truncated "
        "to the 4-hour parent-cagg bucket start, so they may read up to 4 hours "
        "earlier than the true first/last bar; daily timestamps are EXACT, "
        f"because {DAILY_COVERAGE_VIEW} reads raw daily_ohlcv rather than a "
        "parent cagg. bars_stored is exact on both branches. "
        "(2) CAGG LAG: coverage trails raw ingest by at most one coverage "
        f"bucket ({bucket_lag}) PLUS the refresh schedule interval(s). The "
        "bucket term dominates and is structural, not a scheduling delay: a "
        "refresh policy''s window is truncated to whole buckets, so the "
        "CURRENT (open) bucket is never re-materialized while it is open. The "
        "schedule interval bounds only how promptly CLOSED buckets are "
        "rewritten. For "
        f"{MINUTE_COVERAGE_VIEW} that is {bucket_lag} plus "
        f"{_interval_literal(MINUTE_CAGG_REFRESH_SCHEDULE_INTERVAL)} "
        "for the parent 4-hour cagg plus "
        f"{_interval_literal(MINUTE_COVERAGE_REFRESH_SCHEDULE_INTERVAL)} "
        f"for the coverage cagg ({minute_lag} total); for "
        f"{DAILY_COVERAGE_VIEW}, which reads raw and so has one hop, "
        f"{bucket_lag} plus "
        f"{_interval_literal(DAILY_COVERAGE_REFRESH_SCHEDULE_INTERVAL)} "
        f"({daily_lag} total). "
        "Refresh policies use start_offset "
        f"{_interval_literal(MINUTE_COVERAGE_REFRESH_START_OFFSET)} / end_offset "
        f"{_interval_literal(MINUTE_COVERAGE_REFRESH_END_OFFSET)} (minute) and "
        f"{_interval_literal(DAILY_COVERAGE_REFRESH_START_OFFSET)} / "
        f"{_interval_literal(DAILY_COVERAGE_REFRESH_END_OFFSET)} (daily). "
        "FRESHNESS IS NOT ASSERTED IN SQL: readers must go through "
        "data.maintenance.status_coverage, which calls assert_cagg_fresh on "
        "both coverage caggs and reports staleness to the operator. Querying "
        "this view directly bypasses that guard."
    )


# Slice 167 rewrite: bars_summary reads the coverage caggs (migration 048)
_DATA_STATUS_VIEW_WITH_DAILY_COVERAGE = _build_data_status_view_sql(
    include_daily_branch=True,
    include_trading_sessions_cte=True,
    cagg_backed_bars_summary=True,
)
_DATA_STATUS_VIEW_WITHOUT_DAILY_COVERAGE = _build_data_status_view_sql(
    include_daily_branch=False,
    include_trading_sessions_cte=True,
    cagg_backed_bars_summary=True,
)


def _run_trading_sessions_population(conn: Any) -> None:
    """Python callable for migration 026.

    For each calendar in trading_calendars, fetch all holidays from
    trading_holidays and call populate_trading_sessions over
    [earliest_seeded_year, current_year + TRADING_SESSIONS_EXTENSION_YEARS].
    Upserts results via ON CONFLICT DO UPDATE.
    """
    from datetime import datetime

    from psycopg.rows import dict_row

    from manta_trading.data.base.session_population import populate_trading_sessions

    current_year = datetime.now().year
    end_year = current_year + TRADING_SESSIONS_EXTENSION_YEARS

    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT calendar_id, timezone, market_open, market_close "
            "FROM trading_calendars"
        )
        calendars = cur.fetchall()

    for cal_row in calendars:
        calendar_id: str = cal_row["calendar_id"]

        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                "SELECT holiday_date, market_status, early_close_time, late_open_time "
                "FROM trading_holidays WHERE calendar_id = %s",
                (calendar_id,),
            )
            holidays = cur.fetchall()

        # Determine earliest seeded year from trading_holidays, fall back to current.
        if holidays:
            start_year: int = min(h["holiday_date"].year for h in holidays)
        else:
            start_year = current_year

        calendars_row: dict[str, Any] = {
            "timezone": cal_row["timezone"],
            "market_open": cal_row["market_open"],
            "market_close": cal_row["market_close"],
        }
        holidays_rows: list[dict[str, Any]] = [
            {
                "holiday_date": h["holiday_date"],
                "market_status": h["market_status"],
                "early_close_time": h["early_close_time"],
                "late_open_time": h["late_open_time"],
            }
            for h in holidays
        ]

        rows = populate_trading_sessions(
            calendar_id,
            date(start_year, 1, 1),
            date(end_year, 12, 31),
            calendars_row,
            holidays_rows,
        )

        if not rows:
            continue

        with conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO trading_sessions
                    (calendar_id, session_date, session_open_utc, session_close_utc)
                VALUES (%(calendar_id)s, %(session_date)s,
                        %(session_open_utc)s, %(session_close_utc)s)
                ON CONFLICT (calendar_id, session_date) DO UPDATE
                    SET session_open_utc  = EXCLUDED.session_open_utc,
                        session_close_utc = EXCLUDED.session_close_utc
                """,
                rows,
            )


def _copy_splits_dividends_from_marketdb(conn: Any) -> None:
    """Python callable for migration 036.

    Copies splits and dividends rows from MarketDB into TimescaleDB.
    Skips silently if MT_MARKET_DB_URL is unset or MarketDB is unreachable
    (data may already be migrated or MarketDB may already be gone).
    Uses ON CONFLICT DO NOTHING so re-running is safe.
    """
    import psycopg  # local import — only needed at migration time

    from manta_trading.logging import get_logger

    _log = get_logger(__name__)

    market_db_url = os.environ.get("MT_MARKET_DB_URL", "")
    if not market_db_url:
        _log.warning(
            "migration 036: MT_MARKET_DB_URL not set — skipping splits/dividends copy"
        )
        return

    try:
        with psycopg.connect(market_db_url) as src:
            with src.cursor() as cur:
                cur.execute(
                    "SELECT symbol, ex_date, ratio_to, ratio_from, source, fetched_at FROM splits"
                )
                splits_rows = cur.fetchall()
                cur.execute(
                    "SELECT symbol, ex_date, amount, currency, source, fetched_at FROM dividends"
                )
                dividends_rows = cur.fetchall()
    except Exception:
        _log.warning(
            "migration 036: could not connect to MarketDB — skipping splits/dividends copy",
            exc_info=True,
        )
        return

    # psycopg3 exposes executemany() on Cursor only, not Connection
    # (psycopg2 did both). Use an explicit cursor.
    with conn.cursor() as cur:
        if splits_rows:
            cur.executemany(
                "INSERT INTO splits (symbol, ex_date, ratio_to, ratio_from, source, fetched_at) "
                "VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
                splits_rows,
            )
        if dividends_rows:
            cur.executemany(
                "INSERT INTO dividends (symbol, ex_date, amount, currency, source, fetched_at) "
                "VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
                dividends_rows,
            )
    _log.info(
        "migration 036: copied %d splits, %d dividends from MarketDB",
        len(splits_rows),
        len(dividends_rows),
    )


def _setup_and_backfill_compression(conn: Any) -> None:
    """Configure columnar compression on OHLCV hypertables and compress existing chunks.

    Requires autocommit connection (TimescaleDB policy management restriction).
    Three steps: enable compression settings, install 7-day policies, backfill
    all eligible existing chunks.
    """
    from manta_trading.logging import get_logger

    _log = get_logger(__name__)

    # Step 1: enable compression settings on both tables
    for table in ("minute_ohlcv", "daily_ohlcv"):
        conn.execute(
            f"ALTER TABLE {table} SET ("  # noqa: S608 — table is a literal, not user input
            "timescaledb.compress, "
            "timescaledb.compress_segmentby = 'symbol', "
            "timescaledb.compress_orderby = 'time DESC'"
            ")"
        )
        _log.info("compression enabled on %s", table)

    # Step 2: install compression policies (idempotent — check before add)
    with conn.cursor() as cur:
        for table in ("minute_ohlcv", "daily_ohlcv"):
            cur.execute(
                "SELECT 1 FROM timescaledb_information.jobs "
                "WHERE hypertable_name = %s "
                "  AND proc_name = 'policy_compression'",
                (table,),
            )
            if not cur.fetchone():
                conn.execute(
                    "SELECT add_compression_policy(%s, INTERVAL '7 days')",
                    (table,),
                )
                _log.info("compression policy installed on %s", table)
            else:
                _log.info("compression policy already exists on %s", table)

    # Step 3: backfill-compress all existing chunks older than 7 days
    with conn.cursor() as cur:
        cur.execute(
            "SELECT chunk_schema, chunk_name, hypertable_name "
            "FROM timescaledb_information.chunks "
            "WHERE hypertable_name IN ('minute_ohlcv', 'daily_ohlcv') "
            "  AND range_end < NOW() - INTERVAL '7 days' "
            "  AND is_compressed = false "
            "ORDER BY hypertable_name, range_start"
        )
        chunks = cur.fetchall()

    import psycopg.errors  # local import — only needed at migration time

    total = len(chunks)
    _log.info("backfill: %d chunk(s) to compress", total)
    skipped = 0
    for i, (schema, name, table) in enumerate(chunks, 1):
        try:
            conn.execute(
                "SELECT compress_chunk(%s)",
                (f"{schema}.{name}",),
            )
        except psycopg.errors.DuplicateObject:
            # TimescaleDB raises DuplicateObject when a chunk is already compressed.
            # This happens when compression was previously enabled ad-hoc before
            # this migration ran. Safe to skip — the chunk is already in the right state.
            skipped += 1
            continue
        if i % 50 == 0 or i == total:
            _log.info(
                "compressed %d/%d chunks (%s, %d skipped)", i, total, table, skipped
            )
    if total > 0:
        _log.info(
            "backfill complete: %d compressed, %d skipped (already compressed)",
            total - skipped,
            skipped,
        )


def _setup_minute_cagg_columnstore(conn: Any) -> None:
    """Enable columnstore compression + policy on the four minute caggs (slice 163).

    Requires an autocommit connection (TimescaleDB policy management
    restriction). Operates on each cagg by **view name** via the
    continuous-aggregate-native API (``ALTER MATERIALIZED VIEW`` +
    ``add_columnstore_policy``) — never touches ``mat_N`` mat-hypertable
    literals. ``segmentby = symbol``; ``orderby`` defaults to the cagg's
    ``time_bucket`` column (design D3). ``compress_after`` is
    ``MINUTE_CAGG_COMPRESS_AFTER`` (> the refresh policy's 1-day ``start_offset``
    so the policy never compresses inside the actively-refreshed head).

    Unlike migration 042, performs **no backfill-compress** of existing chunks:
    the existing wrong-interval (~1.67-day) cagg chunks are dropped by ``mt data
    caggs repair``, and that sweep compresses each freshly-refreshed 70-day
    chunk itself (compress-behind-frontier). Idempotent: re-enabling columnstore
    and re-adding an existing policy are no-ops.
    """
    from manta_trading.logging import get_logger

    _log = get_logger(__name__)
    # Must be a typed INTERVAL literal: add_columnstore_policy's `after` argument
    # is interpolated directly into the CALL, so a bare "7 days" (what
    # _interval_literal renders, for contexts that write the INTERVAL keyword
    # separately) is a syntax error.
    compress_after = _interval_seconds_sql(MINUTE_CAGG_COMPRESS_AFTER)

    # Confirm all four caggs exist before mutating any (fail-fast, not partial).
    with conn.cursor() as cur:
        cur.execute(
            "SELECT view_name FROM timescaledb_information.continuous_aggregates "
            "WHERE view_name = ANY(%s)",
            (list(_MINUTE_CAGG_VIEWS),),
        )
        found = {row[0] for row in cur.fetchall()}
    missing = [v for v in _MINUTE_CAGG_VIEWS if v not in found]
    if missing:
        raise RuntimeError(
            "045_minute_cagg_columnstore: expected minute caggs not found in "
            f"catalog: {missing} — apply the cagg-creation migrations first"
        )

    for view in _MINUTE_CAGG_VIEWS:
        # Step 1: enable columnstore on the cagg, segmented by symbol. ALTER ...
        # SET is idempotent (re-setting the same options is a no-op).
        conn.execute(
            f"ALTER MATERIALIZED VIEW {view} SET ("  # noqa: S608 — view is a fixed catalog name
            "timescaledb.enable_columnstore = true, "
            "timescaledb.segmentby = 'symbol'"
            ")"
        )
        _log.info("columnstore enabled on %s", view)

        # Step 2: add the columnstore policy (idempotent — check before add;
        # add_columnstore_policy is a procedure invoked with CALL).
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM timescaledb_information.jobs "
                "WHERE hypertable_name = %s "
                "  AND proc_name = 'policy_compression'",
                (view,),
            )
            if cur.fetchone():
                _log.info("columnstore policy already exists on %s", view)
                continue
        conn.execute(
            f"CALL add_columnstore_policy('{view}', "  # noqa: S608 — view is a fixed catalog name
            f"after => {compress_after})"
        )
        _log.info("columnstore policy installed on %s (%s)", view, compress_after)


MINUTE_MIGRATIONS: list[dict[str, Any]] = [
    {
        "id": "001_schema_migrations",
        "description": "Create schema_migrations tracking table",
        "sql": """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                migration_id VARCHAR(64) PRIMARY KEY,
                applied_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                description TEXT
            );
        """,
    },
    # Position-critical front-of-list block (001a/b/c/d). Replaces the
    # deleted timescale_init.py module (slice 156). Each is idempotent so
    # existing DBs treat them as no-ops. Do not reorder.
    {
        "id": "001a_create_timescaledb_extension",
        "description": (
            "Create the TimescaleDB extension (slice 156). Migration-chain "
            "replacement for timescale_init.create_timescaledb_extension. "
            "Runs in autocommit because CREATE EXTENSION cannot execute "
            "inside a transaction block."
        ),
        "requires_autocommit": True,
        "sql": "CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE",
    },
    {
        "id": "001b_create_minute_ohlcv",
        "description": (
            "Create minute_ohlcv table with column shape from "
            "timescale_init.create_minute_ohlcv_table (slice 156)."
        ),
        "sql": """
            CREATE TABLE IF NOT EXISTS minute_ohlcv (
                time       TIMESTAMPTZ NOT NULL,
                symbol     TEXT NOT NULL,
                open       NUMERIC(12, 4) NOT NULL,
                high       NUMERIC(12, 4) NOT NULL,
                low        NUMERIC(12, 4) NOT NULL,
                close      NUMERIC(12, 4) NOT NULL,
                volume     BIGINT NOT NULL,
                created_at TIMESTAMPTZ DEFAULT NOW()
            );
        """,
    },
    {
        "id": "001c_create_minute_ohlcv_hypertable",
        "description": (
            "Convert minute_ohlcv into a TimescaleDB hypertable (slice 156). "
            "Chunk interval derives from MINUTE_OHLCV_CHUNK_INTERVAL (slice "
            "166; originally a hardcoded 4 hours, which produced the 25k-chunk "
            "planning pathology). Idempotent via if_not_exists => TRUE."
        ),
        "sql": f"""
            SELECT create_hypertable(
                'minute_ohlcv',
                'time',
                chunk_time_interval => {_minute_chunk_interval_sql()},
                if_not_exists       => TRUE
            );
        """,
    },
    {
        "id": "001d_create_minute_ohlcv_indexes",
        "description": (
            "Create the two non-auto indexes on minute_ohlcv that "
            "timescale_init.create_indexes used to install (slice 156). "
            "minute_ohlcv_time_idx is created automatically by "
            "create_hypertable (skip). ux_minute_ohlcv_symbol_time is "
            "created later by 011_dedup_minute_ohlcv (skip)."
        ),
        "sql": """
            CREATE INDEX IF NOT EXISTS ix_minute_ohlcv_symbol_time
                ON minute_ohlcv (symbol, time DESC);
            CREATE INDEX IF NOT EXISTS ix_minute_ohlcv_time_symbol
                ON minute_ohlcv (time DESC, symbol);
        """,
    },
    {
        "id": "002_instruments",
        "description": "Create instruments table",
        "sql": """
            CREATE TABLE IF NOT EXISTS instruments (
                instrument_id        BIGSERIAL PRIMARY KEY,
                canonical_id         VARCHAR(64) NOT NULL UNIQUE,
                symbol               VARCHAR(32) NOT NULL,
                asset_class          VARCHAR(32) NOT NULL,
                venue                VARCHAR(32) NOT NULL,
                currency             VARCHAR(8)  NOT NULL DEFAULT 'USD',
                tick_size            NUMERIC(18, 8),
                lot_size             NUMERIC(18, 8),
                trading_calendar_id  VARCHAR(32),
                adjustment_policy    VARCHAR(32),
                active               BOOLEAN NOT NULL DEFAULT TRUE,
                metadata             JSONB DEFAULT '{}',
                created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );

            CREATE INDEX IF NOT EXISTS idx_instruments_symbol
                ON instruments (symbol);
            CREATE INDEX IF NOT EXISTS idx_instruments_asset_class_venue
                ON instruments (asset_class, venue);
            CREATE INDEX IF NOT EXISTS idx_instruments_active
                ON instruments (active) WHERE active = TRUE;
        """,
    },
    {
        "id": "003_provider_symbol_mapping",
        "description": "Create provider_symbol_mapping table",
        "sql": """
            CREATE TABLE IF NOT EXISTS provider_symbol_mapping (
                mapping_id       BIGSERIAL PRIMARY KEY,
                instrument_id    BIGINT NOT NULL
                    REFERENCES instruments(instrument_id),
                provider         VARCHAR(32) NOT NULL,
                provider_symbol  VARCHAR(64) NOT NULL,
                valid_from       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                valid_to         TIMESTAMPTZ,
                created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );

            CREATE UNIQUE INDEX IF NOT EXISTS idx_provider_symbol_active
                ON provider_symbol_mapping (provider, provider_symbol)
                WHERE valid_to IS NULL;
            CREATE INDEX IF NOT EXISTS idx_provider_mapping_instrument
                ON provider_symbol_mapping (instrument_id);
        """,
    },
    {
        "id": "004_trading_calendars",
        "description": "Create trading_calendars table",
        "sql": """
            CREATE TABLE IF NOT EXISTS trading_calendars (
                calendar_id      VARCHAR(32) PRIMARY KEY,
                exchange_name    VARCHAR(128) NOT NULL,
                timezone         VARCHAR(64)  NOT NULL,
                market_open      TIME NOT NULL,
                market_close     TIME NOT NULL,
                extended_open    TIME,
                extended_close   TIME,
                has_extended_hours BOOLEAN NOT NULL DEFAULT FALSE,
                created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
        """,
    },
    {
        "id": "005_trading_holidays",
        "description": "Create trading_holidays table",
        "sql": """
            CREATE TABLE IF NOT EXISTS trading_holidays (
                holiday_id       BIGSERIAL PRIMARY KEY,
                calendar_id      VARCHAR(32) NOT NULL
                    REFERENCES trading_calendars(calendar_id),
                holiday_date     DATE NOT NULL,
                holiday_name     VARCHAR(128) NOT NULL,
                market_status    VARCHAR(32) NOT NULL DEFAULT 'closed',
                early_close_time TIME,
                late_open_time   TIME,
                created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );

            CREATE UNIQUE INDEX IF NOT EXISTS idx_trading_holidays_calendar_date
                ON trading_holidays (calendar_id, holiday_date);
            CREATE INDEX IF NOT EXISTS idx_trading_holidays_date
                ON trading_holidays (holiday_date);
        """,
    },
    {
        "id": "006_minute_ohlcv_instrument_id",
        "description": "Add nullable instrument_id column to minute_ohlcv",
        "sql": """
            ALTER TABLE minute_ohlcv
                ADD COLUMN IF NOT EXISTS instrument_id BIGINT;
        """,
    },
    {
        "id": "007_seed_nyse_calendar",
        "description": "Seed NYSE calendar and holidays (2020-2026)",
        "sql": _build_seed_sql(NYSE_CALENDAR, "NYSE"),
    },
    {
        "id": "008_seed_nasdaq_calendar",
        "description": "Seed NASDAQ calendar and holidays (2020-2026)",
        "sql": _build_seed_sql(NASDAQ_CALENDAR, "NASDAQ"),
    },
    {
        "id": "009_instruments_calendar_fk",
        "description": "Add FK from instruments.trading_calendar_id to trading_calendars",
        "sql": """
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM information_schema.table_constraints
                    WHERE constraint_name = 'fk_instruments_calendar'
                      AND table_name = 'instruments'
                ) THEN
                    ALTER TABLE instruments
                        ADD CONSTRAINT fk_instruments_calendar
                        FOREIGN KEY (trading_calendar_id)
                        REFERENCES trading_calendars(calendar_id);
                END IF;
            END $$;
        """,
    },
    {
        "id": "010_adjusted_columns",
        "description": "Add adjusted OHLC, k_factor, adjusted_at columns to minute_ohlcv",
        "sql": """
            ALTER TABLE minute_ohlcv
                ADD COLUMN IF NOT EXISTS adj_open    NUMERIC(20, 8),
                ADD COLUMN IF NOT EXISTS adj_high    NUMERIC(20, 8),
                ADD COLUMN IF NOT EXISTS adj_low     NUMERIC(20, 8),
                ADD COLUMN IF NOT EXISTS adj_close   NUMERIC(20, 8),
                ADD COLUMN IF NOT EXISTS k_factor    NUMERIC(20, 12),
                ADD COLUMN IF NOT EXISTS adjusted_at TIMESTAMPTZ;
        """,
    },
    {
        "id": "011_minute_ohlcv_unique_symbol_time",
        "description": (
            "Dedup minute_ohlcv keeping latest created_at per (symbol, time); "
            "add UNIQUE index on (symbol, time)"
        ),
        # Two-step: (1) delete all but the most-recently-inserted row for
        # each (symbol, time) pair — semantics match the future
        # ON CONFLICT (symbol, time) DO UPDATE writer behaviour;
        # (2) create a unique index that includes the partitioning column.
        # TimescaleDB requires the partitioning column be part of any
        # unique index on a hypertable. The index is created idempotently
        # so re-runs are safe even after the dedup has already removed
        # all duplicates.
        "sql": """
            WITH ranked AS (
                SELECT ctid,
                       ROW_NUMBER() OVER (
                           PARTITION BY symbol, time
                           ORDER BY created_at DESC, ctid DESC
                       ) AS rn
                FROM minute_ohlcv
            )
            DELETE FROM minute_ohlcv m
            USING ranked r
            WHERE m.ctid = r.ctid AND r.rn > 1;

            CREATE UNIQUE INDEX IF NOT EXISTS ux_minute_ohlcv_symbol_time
                ON minute_ohlcv (symbol, time);
        """,
    },
    {
        "id": "012_coverage_gaps",
        "description": (
            "Create coverage_gaps table for empirical provider-data-gap "
            "tracking (slice 128). Lives alongside acquisition_state on the "
            "TimescaleDB host per the architecture's centralization principle."
        ),
        "sql": f"""
            CREATE TABLE IF NOT EXISTS coverage_gaps (
                symbol            TEXT NOT NULL,
                gap_start         TIMESTAMPTZ NOT NULL,
                gap_end           TIMESTAMPTZ NOT NULL,
                source            TEXT NOT NULL,
                detected_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                resolution_status TEXT NOT NULL DEFAULT '{_COVERAGE_GAP_STATUS_UNKNOWN}',
                notes             TEXT,
                PRIMARY KEY (symbol, gap_start, source),
                CONSTRAINT coverage_gaps_resolution_status_check
                    CHECK ({_coverage_status_check_sql()}),
                CONSTRAINT coverage_gaps_range_check
                    CHECK (gap_end >= gap_start)
            );

            CREATE INDEX IF NOT EXISTS idx_coverage_gaps_symbol
                ON coverage_gaps (symbol);
            CREATE INDEX IF NOT EXISTS idx_coverage_gaps_status
                ON coverage_gaps (resolution_status);
        """,
    },
    {
        "id": "013_backfill_state",
        "description": (
            "Create backfill_state table for universe-iteration cursor and "
            "daily-quota tracking used by `mt data minute backfill` "
            "(slice 128)."
        ),
        "sql": """
            CREATE TABLE IF NOT EXISTS backfill_state (
                universe                  TEXT PRIMARY KEY,
                cursor_symbol             TEXT,
                since_date                DATE NOT NULL,
                started_at                TIMESTAMPTZ NOT NULL,
                last_progress_at          TIMESTAMPTZ,
                daily_calls_used          INTEGER NOT NULL DEFAULT 0,
                daily_calls_window_start  TIMESTAMPTZ NOT NULL
            );
        """,
    },
    {
        "id": "014_nvda_inaugural_gap",
        "description": (
            "Seed coverage_gaps with the EODHD-acknowledged NVDA gap "
            "2024-06-07 → 2024-07-25. Inaugural row documenting a known "
            "provider-confirmed unfillable gap (slice 128)."
        ),
        "sql": f"""
            INSERT INTO coverage_gaps
                (symbol, gap_start, gap_end, source,
                 detected_at, resolution_status, notes)
            VALUES (
                'NVDA',
                '2024-06-07T23:59:00Z',
                '2024-07-25T08:00:00Z',
                'eodhd',
                NOW(),
                '{_COVERAGE_GAP_STATUS_PROVIDER_CONFIRMED_UNFILLABLE}',
                'EODHD support 2026-04-27: missing from sources, unfillable. '
                'Verified by probe.'
            )
            ON CONFLICT (symbol, gap_start, source) DO NOTHING;
        """,
    },
    {
        "id": "015_instruments_lifecycle_columns",
        "description": (
            "Add lifecycle and EODHD classification columns to instruments "
            "(slice 141). Columns are nullable initially; NOT NULL constraints "
            "are tightened by migration 016 after the rebuild populates them."
        ),
        "sql": """
            ALTER TABLE instruments
                ADD COLUMN IF NOT EXISTS first_listing_date  DATE,
                ADD COLUMN IF NOT EXISTS first_data_date     DATE,
                ADD COLUMN IF NOT EXISTS delisted_date       DATE,
                ADD COLUMN IF NOT EXISTS eodhd_type          TEXT,
                ADD COLUMN IF NOT EXISTS eodhd_exchange      TEXT,
                ADD COLUMN IF NOT EXISTS delisted_at_eodhd   BOOLEAN NOT NULL DEFAULT FALSE;
        """,
    },
    {
        "id": "016_instruments_eodhd_type_not_null",
        "description": (
            "Tighten eodhd_type to NOT NULL and add CHECK constraint derived from "
            "EodhdType enum; same for eodhd_exchange. Applied after the rebuild has "
            "populated all rows and orphans are deleted (orchestrator sequencing, D1/D3)."
        ),
        "sql": f"""
            ALTER TABLE instruments
                ALTER COLUMN eodhd_type    SET NOT NULL,
                ALTER COLUMN eodhd_exchange SET NOT NULL;

            DO $$ BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_constraint
                    WHERE conname = 'instruments_eodhd_type_check'
                ) THEN
                    ALTER TABLE instruments
                        ADD CONSTRAINT instruments_eodhd_type_check
                        CHECK ({_eodhd_type_check_sql()});
                END IF;

                -- Note: deviation from LLD D3. The LLD assumed EODHD's bulk
                -- symbol-list returns Exchange='US' for all US equities. In
                -- practice it returns the authoritative venue per row
                -- (NASDAQ, NYSE, NYSE ARCA, BATS, PINK, OTCQB, NMFQS, etc.).
                -- We drop the strict CHECK to accept any non-empty string;
                -- eodhd_exchange is raw provider data, not classification.
            END $$;
        """,
    },
    {
        "id": "017_instruments_drop_active",
        "description": (
            "Drop the instruments.active column (slice 141, D2). Applied after "
            "consumer code is updated to use the lifecycle predicate. "
            "Active status is now derived from delisted_at_eodhd = FALSE AND delisted_date IS NULL."
        ),
        "sql": """
            ALTER TABLE instruments DROP COLUMN IF EXISTS active;
        """,
    },
    {
        "id": "018_data_gaps",
        "description": (
            "Create data_gaps table for tracking unfilled bar windows "
            "(slice 142). PK is (symbol, granularity, gap_start, gap_end) "
            "to allow multiple disjoint gaps per symbol."
        ),
        "sql": f"""
            CREATE TABLE IF NOT EXISTS data_gaps (
                symbol           TEXT        NOT NULL,
                granularity      TEXT        NOT NULL,
                gap_start        TIMESTAMPTZ NOT NULL,
                gap_end          TIMESTAMPTZ NOT NULL,
                fetch_status     TEXT        NOT NULL,
                last_attempt_ts  TIMESTAMPTZ,
                attempt_count    INTEGER     NOT NULL DEFAULT 0,
                CONSTRAINT data_gaps_pkey
                    PRIMARY KEY (symbol, granularity, gap_start, gap_end),
                CONSTRAINT data_gaps_fetch_status_check
                    CHECK ({_fetch_status_check_sql()}),
                CONSTRAINT data_gaps_granularity_check
                    CHECK (granularity IN ('daily', 'minute')),
                CONSTRAINT data_gaps_range_check
                    CHECK (gap_end >= gap_start),
                CONSTRAINT data_gaps_attempt_count_check
                    CHECK (attempt_count >= 0)
            );

            CREATE INDEX IF NOT EXISTS idx_data_gaps_symbol_granularity
                ON data_gaps (symbol, granularity);
            CREATE INDEX IF NOT EXISTS idx_data_gaps_fetch_status
                ON data_gaps (fetch_status);
        """,
    },
    # Position-critical: 038 must run before 019_slim_acquisition_state.
    # Do not reorder this list alphabetically by id — runner iterates list order,
    # not numeric id sort. Slice 152 deleted the original CREATE TABLE for
    # acquisition_state; slice 156 restores it as 038 inserted here so a fresh
    # DB reaches 019's ALTER TABLE without UndefinedTable. Idempotent on
    # existing DBs (IF NOT EXISTS).
    {
        "id": "038_create_acquisition_state",
        "description": (
            "Restore acquisition_state CREATE that slice 152's demolition "
            "removed (issue #16). Idempotent CREATE TABLE IF NOT EXISTS using "
            "the post-030 column shape; on existing DBs this is a no-op."
        ),
        "sql": """
            CREATE TABLE IF NOT EXISTS acquisition_state (
                symbol               TEXT NOT NULL,
                granularity          TEXT NOT NULL,
                provider             TEXT NOT NULL,
                last_attempt_ts      TIMESTAMPTZ,
                updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                last_attempt_outcome TEXT,
                CONSTRAINT acquisition_state_last_attempt_outcome_check CHECK (
                    last_attempt_outcome IS NULL
                    OR last_attempt_outcome = ANY (
                        ARRAY['empty', 'partial', 'success', 'transient_failure']
                    )
                ),
                PRIMARY KEY (symbol, granularity, provider)
            );
        """,
    },
    {
        "id": "019_slim_acquisition_state",
        "description": (
            "Slim acquisition_state: drop AV-era columns "
            "(last_success_ts, retry_count, error_message, run_id, status); "
            "add last_attempt_outcome and last_adjusted_ca_snapshot_id (slice 142)."
        ),
        "sql": """
            ALTER TABLE acquisition_state
                DROP COLUMN IF EXISTS last_success_ts,
                DROP COLUMN IF EXISTS retry_count,
                DROP COLUMN IF EXISTS error_message,
                DROP COLUMN IF EXISTS run_id,
                DROP COLUMN IF EXISTS status,
                ADD COLUMN IF NOT EXISTS last_attempt_outcome         TEXT,
                ADD COLUMN IF NOT EXISTS last_adjusted_ca_snapshot_id TEXT;
        """,
    },
    {
        "id": "020_drop_coverage_gaps",
        "description": (
            "Drop the coverage_gaps table (slice 142). The NVDA seed row "
            "from migration 014 goes with the table; slice 144's first "
            "fetch attempt over that window will re-derive it."
        ),
        "sql": """
            DROP TABLE IF EXISTS coverage_gaps;
        """,
    },
    {
        "id": "021_data_status_view",
        "description": (
            "Create data_status view — per-(symbol, granularity) health summary "
            "joining instruments, bars, gaps, and acquisition_state (slice 142). "
            "Two slice-142 deviations from arch (documented in slice design): "
            "(1) bars_summary CTE branches at apply-time on the presence of "
            "daily_ohlcv (slice 143 creates that hypertable; until then daily "
            "symbols show bars_stored = 0), and (2) target_end_ts is NULL — the "
            "exchange_completed_close CTE the arch describes requires a "
            "session-materialized trading_calendar table that does not exist. "
            "Slice 144 will pick between materializing trading_sessions or a "
            "fuller SQL replacement. Health rules do not depend on target_end_ts."
        ),
        "sql": f"""
            DO $$ BEGIN
                IF to_regclass('public.daily_ohlcv') IS NOT NULL THEN
                    EXECUTE '{_DATA_STATUS_VIEW_WITH_DAILY}';
                ELSE
                    EXECUTE '{_DATA_STATUS_VIEW_WITHOUT_DAILY}';
                END IF;
            END $$;
        """,
    },
    {
        "id": "022_acquisition_state_outcome_check",
        "description": (
            "Add CHECK constraint on acquisition_state.last_attempt_outcome "
            "derived from LastAttemptOutcome enum. NULL is permitted for rows "
            "not yet updated by the daemon (slice 142)."
        ),
        "sql": f"""
            DO $$ BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_constraint
                    WHERE conname = 'acquisition_state_last_attempt_outcome_check'
                ) THEN
                    ALTER TABLE acquisition_state
                        ADD CONSTRAINT acquisition_state_last_attempt_outcome_check
                        CHECK (last_attempt_outcome IS NULL
                            OR {_outcome_check_sql()});
                END IF;
            END $$;
        """,
    },
    {
        "id": "023_daily_ohlcv",
        "description": (
            "Create daily_ohlcv hypertable mirroring minute_ohlcv schema "
            "(slice 143). chunk_time_interval = DAILY_OHLCV_CHUNK_INTERVAL "
            "(70 days, slice 170 — was 7 days at creation, which produced "
            "3,371 chunks on production). Unique (symbol, time) index plus "
            "supporting indexes for symbol-range and time-range scans."
        ),
        "sql": f"""
            CREATE TABLE IF NOT EXISTS daily_ohlcv (
                time         TIMESTAMPTZ     NOT NULL,
                symbol       TEXT            NOT NULL,
                open         NUMERIC(12, 4)  NOT NULL,
                high         NUMERIC(12, 4)  NOT NULL,
                low          NUMERIC(12, 4)  NOT NULL,
                close        NUMERIC(12, 4)  NOT NULL,
                volume       BIGINT          NOT NULL,
                adj_open     NUMERIC(20, 8),
                adj_high     NUMERIC(20, 8),
                adj_low      NUMERIC(20, 8),
                adj_close    NUMERIC(20, 8),
                k_factor     NUMERIC(20, 12),
                adjusted_at  TIMESTAMPTZ,
                created_at   TIMESTAMPTZ DEFAULT NOW()
            );

            SELECT create_hypertable('daily_ohlcv', 'time',
                                     chunk_time_interval =>
                                         {_daily_chunk_interval_sql()},
                                     if_not_exists => TRUE);

            CREATE UNIQUE INDEX IF NOT EXISTS ux_daily_ohlcv_symbol_time
                ON daily_ohlcv (symbol, time);
            CREATE INDEX IF NOT EXISTS ix_daily_ohlcv_symbol_time
                ON daily_ohlcv (symbol, time DESC);
            CREATE INDEX IF NOT EXISTS ix_daily_ohlcv_time_symbol
                ON daily_ohlcv (time DESC, symbol);
        """,
    },
    {
        "id": "024_data_status_view_refresh",
        "description": (
            "Re-execute the data_status view DO-block after daily_ohlcv exists "
            "(slice 143). On DBs that ran migration 021 before 023, this flips "
            "the view to the with-daily branch. On fresh DBs it is a safe no-op "
            "redo — CREATE OR REPLACE is idempotent."
        ),
        # Body is bit-identical to migration 021. We import the pre-rendered
        # constants to keep the SQL definition in exactly one place.
        "sql": f"""
            DO $$ BEGIN
                IF to_regclass('public.daily_ohlcv') IS NOT NULL THEN
                    EXECUTE '{_DATA_STATUS_VIEW_WITH_DAILY}';
                ELSE
                    EXECUTE '{_DATA_STATUS_VIEW_WITHOUT_DAILY}';
                END IF;
            END $$;
        """,
    },
    {
        "id": "025_trading_sessions_table",
        "description": (
            "Create trading_sessions table materializing per-calendar trading "
            "day open/close UTC bounds (slice 144). One row per trading day; "
            "absence = non-trading. PK on (calendar_id, session_date); index "
            "on (calendar_id, session_close_utc) for CTE max-scan."
        ),
        "sql": """
            CREATE TABLE IF NOT EXISTS trading_sessions (
                calendar_id        VARCHAR(32)  NOT NULL
                    REFERENCES trading_calendars(calendar_id),
                session_date       DATE         NOT NULL,
                session_open_utc   TIMESTAMPTZ  NOT NULL,
                session_close_utc  TIMESTAMPTZ  NOT NULL,
                PRIMARY KEY (calendar_id, session_date)
            );

            CREATE INDEX IF NOT EXISTS idx_trading_sessions_close
                ON trading_sessions (calendar_id, session_close_utc);
        """,
    },
    {
        "id": "026_trading_sessions_initial_population",
        "description": (
            "Populate trading_sessions for all calendars from earliest seeded "
            "holiday year through current_year + TRADING_SESSIONS_EXTENSION_YEARS "
            "(slice 144). Idempotent upsert via ON CONFLICT DO UPDATE."
        ),
        # No SQL — population is done by the Python callable which calls
        # populate_trading_sessions for each calendar.
        "python_fn": _run_trading_sessions_population,
    },
    {
        "id": "028_data_status_view_target_end_ts",
        "description": (
            "Rewrite data_status view to project target_end_ts from "
            "trading_sessions via exchange_completed_close CTE (slice 144). "
            "Branches on to_regclass('trading_sessions'): if table exists, "
            "install new view; otherwise leave slice-142 stub in place."
        ),
        "sql": f"""
            DO $$ BEGIN
                IF to_regclass('public.trading_sessions') IS NOT NULL THEN
                    IF to_regclass('public.daily_ohlcv') IS NOT NULL THEN
                        EXECUTE '{_DATA_STATUS_VIEW_WITH_DAILY_TS}';
                    ELSE
                        EXECUTE '{_DATA_STATUS_VIEW_WITHOUT_DAILY_TS}';
                    END IF;
                ELSE
                    IF to_regclass('public.daily_ohlcv') IS NOT NULL THEN
                        EXECUTE '{_DATA_STATUS_VIEW_WITH_DAILY}';
                    ELSE
                        EXECUTE '{_DATA_STATUS_VIEW_WITHOUT_DAILY}';
                    END IF;
                END IF;
            END $$;
        """,
    },
    # -------------------------------------------------------------------------
    # Slice 152: adjusted-on-read consolidation
    # -------------------------------------------------------------------------
    {
        "id": "029_splits_dividends_timescale",
        "description": (
            "Create splits and dividends tables in TimescaleDB, mirroring the "
            "MarketDB schema (slice 152). Idempotent — IF NOT EXISTS."
        ),
        "sql": """
            CREATE TABLE IF NOT EXISTS splits (
                symbol     TEXT            NOT NULL,
                ex_date    DATE            NOT NULL,
                ratio_to   NUMERIC(20, 8)  NOT NULL,
                ratio_from NUMERIC(20, 8)  NOT NULL,
                source     TEXT            NOT NULL DEFAULT 'eodhd',
                fetched_at TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
                PRIMARY KEY (symbol, ex_date)
            );

            CREATE TABLE IF NOT EXISTS dividends (
                symbol     TEXT            NOT NULL,
                ex_date    DATE            NOT NULL,
                amount     NUMERIC(20, 8)  NOT NULL,
                currency   TEXT            NOT NULL DEFAULT 'USD',
                source     TEXT            NOT NULL DEFAULT 'eodhd',
                fetched_at TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
                PRIMARY KEY (symbol, ex_date)
            );
        """,
    },
    {
        "id": "030_drop_adj_columns_daily_ohlcv",
        "description": (
            "Drop adjusted-on-write columns from daily_ohlcv and "
            "last_adjusted_ca_snapshot_id from acquisition_state (slice 152). "
            "Idempotent — DROP COLUMN IF EXISTS."
        ),
        "sql": """
            ALTER TABLE daily_ohlcv
                DROP COLUMN IF EXISTS adj_open,
                DROP COLUMN IF EXISTS adj_high,
                DROP COLUMN IF EXISTS adj_low,
                DROP COLUMN IF EXISTS adj_close,
                DROP COLUMN IF EXISTS k_factor,
                DROP COLUMN IF EXISTS adjusted_at;

            ALTER TABLE acquisition_state
                DROP COLUMN IF EXISTS last_adjusted_ca_snapshot_id;
        """,
    },
    {
        "id": "031_drop_adj_columns_minute_ohlcv",
        "description": (
            "Drop adjusted-on-write columns from minute_ohlcv (slice 152). "
            "Idempotent — DROP COLUMN IF EXISTS."
        ),
        "sql": """
            ALTER TABLE minute_ohlcv
                DROP COLUMN IF EXISTS adj_open,
                DROP COLUMN IF EXISTS adj_high,
                DROP COLUMN IF EXISTS adj_low,
                DROP COLUMN IF EXISTS adj_close,
                DROP COLUMN IF EXISTS k_factor,
                DROP COLUMN IF EXISTS adjusted_at;
        """,
    },
    {
        "id": "032_drop_legacy_minute_caggs",
        "description": (
            "Drop all 11 legacy minute continuous aggregates (slice 152). "
            "CASCADE removes associated refresh-policy jobs. Idempotent — "
            "IF EXISTS."
        ),
        "sql": """
            DROP MATERIALIZED VIEW IF EXISTS minute_5min_ohlcv     CASCADE;
            DROP MATERIALIZED VIEW IF EXISTS minute_5min_ohlcv_v2  CASCADE;
            DROP MATERIALIZED VIEW IF EXISTS minute_15min_ohlcv    CASCADE;
            DROP MATERIALIZED VIEW IF EXISTS minute_15min_ohlcv_v2 CASCADE;
            DROP MATERIALIZED VIEW IF EXISTS minute_hourly_ohlcv   CASCADE;
            DROP MATERIALIZED VIEW IF EXISTS minute_hourly_ohlcv_v2 CASCADE;
            DROP MATERIALIZED VIEW IF EXISTS minute_4hour_ohlcv    CASCADE;
            DROP MATERIALIZED VIEW IF EXISTS minute_4hour_ohlcv_v2 CASCADE;
            DROP MATERIALIZED VIEW IF EXISTS minute_daily_ohlcv    CASCADE;
            DROP MATERIALIZED VIEW IF EXISTS minute_weekly_ohlcv   CASCADE;
            DROP MATERIALIZED VIEW IF EXISTS minute_monthly_ohlcv  CASCADE;
        """,
    },
    {
        "id": "033_create_minute_caggs",
        "description": (
            "Create 4 raw-projection minute continuous aggregates over "
            "minute_ohlcv (slice 152): 5min, 15min, 1h, 4h. "
            "Each view is a separate execute() call — Timescale forbids "
            "multiple continuous-aggregate DDL statements in one call."
        ),
        # Each CREATE MATERIALIZED VIEW must be its own execute() call.
        # Multi-statement SQL causes psycopg3 to wrap in an implicit transaction,
        # which Timescale rejects for continuous-aggregate DDL.
        "requires_autocommit": True,
        "python_fn": lambda conn: [
            conn.execute(sql)
            for sql in [
                """
                CREATE MATERIALIZED VIEW IF NOT EXISTS minute_5min_ohlcv
                WITH (timescaledb.continuous) AS
                SELECT
                    time_bucket('5 minutes', time) AS time_bucket,
                    symbol,
                    FIRST(open,  time) AS open,
                    MAX(high)          AS high,
                    MIN(low)           AS low,
                    LAST(close,  time) AS close,
                    SUM(volume)        AS volume,
                    COUNT(*)           AS minute_count
                FROM minute_ohlcv
                GROUP BY time_bucket, symbol
                """,
                """
                CREATE MATERIALIZED VIEW IF NOT EXISTS minute_15min_ohlcv
                WITH (timescaledb.continuous) AS
                SELECT
                    time_bucket('15 minutes', time) AS time_bucket,
                    symbol,
                    FIRST(open,  time) AS open,
                    MAX(high)          AS high,
                    MIN(low)           AS low,
                    LAST(close,  time) AS close,
                    SUM(volume)        AS volume,
                    COUNT(*)           AS minute_count
                FROM minute_ohlcv
                GROUP BY time_bucket, symbol
                """,
                """
                CREATE MATERIALIZED VIEW IF NOT EXISTS minute_hourly_ohlcv
                WITH (timescaledb.continuous) AS
                SELECT
                    time_bucket('1 hour', time) AS time_bucket,
                    symbol,
                    FIRST(open,  time) AS open,
                    MAX(high)          AS high,
                    MIN(low)           AS low,
                    LAST(close,  time) AS close,
                    SUM(volume)        AS volume,
                    COUNT(*)           AS minute_count
                FROM minute_ohlcv
                GROUP BY time_bucket, symbol
                """,
                """
                CREATE MATERIALIZED VIEW IF NOT EXISTS minute_4hour_ohlcv
                WITH (timescaledb.continuous) AS
                SELECT
                    time_bucket('4 hours', time) AS time_bucket,
                    symbol,
                    FIRST(open,  time) AS open,
                    MAX(high)          AS high,
                    MIN(low)           AS low,
                    LAST(close,  time) AS close,
                    SUM(volume)        AS volume,
                    COUNT(*)           AS minute_count
                FROM minute_ohlcv
                GROUP BY time_bucket, symbol
                """,
            ]
        ],
    },
    {
        "id": "034_create_daily_caggs",
        "description": (
            "Create 3 raw-projection daily continuous aggregates over "
            "daily_ohlcv (slice 152): weekly, monthly, quarterly. "
            "Each view is a separate execute() call."
        ),
        "requires_autocommit": True,
        "python_fn": lambda conn: [
            conn.execute(sql)
            for sql in [
                """
                CREATE MATERIALIZED VIEW IF NOT EXISTS daily_weekly_ohlcv
                WITH (timescaledb.continuous) AS
                SELECT
                    time_bucket('1 week', time) AS time_bucket,
                    symbol,
                    FIRST(open,  time) AS open,
                    MAX(high)          AS high,
                    MIN(low)           AS low,
                    LAST(close,  time) AS close,
                    SUM(volume)        AS volume,
                    COUNT(*)           AS day_count
                FROM daily_ohlcv
                GROUP BY time_bucket, symbol
                """,
                """
                CREATE MATERIALIZED VIEW IF NOT EXISTS daily_monthly_ohlcv
                WITH (timescaledb.continuous) AS
                SELECT
                    time_bucket('1 month', time) AS time_bucket,
                    symbol,
                    FIRST(open,  time) AS open,
                    MAX(high)          AS high,
                    MIN(low)           AS low,
                    LAST(close,  time) AS close,
                    SUM(volume)        AS volume,
                    COUNT(*)           AS day_count
                FROM daily_ohlcv
                GROUP BY time_bucket, symbol
                """,
                """
                CREATE MATERIALIZED VIEW IF NOT EXISTS daily_quarterly_ohlcv
                WITH (timescaledb.continuous) AS
                SELECT
                    time_bucket('3 months', time) AS time_bucket,
                    symbol,
                    FIRST(open,  time) AS open,
                    MAX(high)          AS high,
                    MIN(low)           AS low,
                    LAST(close,  time) AS close,
                    SUM(volume)        AS volume,
                    COUNT(*)           AS day_count
                FROM daily_ohlcv
                GROUP BY time_bucket, symbol
                """,
            ]
        ],
    },
    {
        "id": "035_cagg_refresh_policies",
        "description": (
            "Install refresh policies for all 7 caggs created in 033/034 "
            "(slice 152). Idempotent — policies are added inside a DO block "
            "that checks for existing jobs first."
        ),
        "sql": f"""
            DO $$ BEGIN
                -- Check uses hypertable_name (available across TimescaleDB versions).
                IF NOT EXISTS (
                    SELECT 1 FROM timescaledb_information.jobs
                    WHERE hypertable_name = 'minute_5min_ohlcv'
                      AND proc_name = 'policy_refresh_continuous_aggregate'
                ) THEN
                    PERFORM add_continuous_aggregate_policy('minute_5min_ohlcv',
                        start_offset  => {_MINUTE_REFRESH_START_SQL},
                        end_offset    => INTERVAL '5 minutes',
                        schedule_interval => INTERVAL '5 minutes');
                END IF;

                IF NOT EXISTS (
                    SELECT 1 FROM timescaledb_information.jobs
                    WHERE hypertable_name = 'minute_15min_ohlcv'
                      AND proc_name = 'policy_refresh_continuous_aggregate'
                ) THEN
                    PERFORM add_continuous_aggregate_policy('minute_15min_ohlcv',
                        start_offset  => {_MINUTE_REFRESH_START_SQL},
                        end_offset    => INTERVAL '15 minutes',
                        schedule_interval => INTERVAL '15 minutes');
                END IF;

                IF NOT EXISTS (
                    SELECT 1 FROM timescaledb_information.jobs
                    WHERE hypertable_name = 'minute_hourly_ohlcv'
                      AND proc_name = 'policy_refresh_continuous_aggregate'
                ) THEN
                    PERFORM add_continuous_aggregate_policy('minute_hourly_ohlcv',
                        start_offset  => {_MINUTE_REFRESH_START_SQL},
                        end_offset    => INTERVAL '1 hour',
                        schedule_interval => INTERVAL '1 hour');
                END IF;

                IF NOT EXISTS (
                    SELECT 1 FROM timescaledb_information.jobs
                    WHERE hypertable_name = 'minute_4hour_ohlcv'
                      AND proc_name = 'policy_refresh_continuous_aggregate'
                ) THEN
                    PERFORM add_continuous_aggregate_policy('minute_4hour_ohlcv',
                        start_offset  => {_MINUTE_REFRESH_START_SQL},
                        end_offset    => INTERVAL '4 hours',
                        schedule_interval => INTERVAL '1 hour');
                END IF;

                IF NOT EXISTS (
                    SELECT 1 FROM timescaledb_information.jobs
                    WHERE hypertable_name = 'daily_weekly_ohlcv'
                      AND proc_name = 'policy_refresh_continuous_aggregate'
                ) THEN
                    PERFORM add_continuous_aggregate_policy('daily_weekly_ohlcv',
                        start_offset  => INTERVAL '21 days',
                        end_offset    => INTERVAL '7 days',
                        schedule_interval => INTERVAL '1 day');
                END IF;

                IF NOT EXISTS (
                    SELECT 1 FROM timescaledb_information.jobs
                    WHERE hypertable_name = 'daily_monthly_ohlcv'
                      AND proc_name = 'policy_refresh_continuous_aggregate'
                ) THEN
                    PERFORM add_continuous_aggregate_policy('daily_monthly_ohlcv',
                        start_offset  => {_DAILY_MONTHLY_REFRESH_START_SQL},
                        end_offset    => INTERVAL '30 days',
                        schedule_interval => INTERVAL '1 day');
                END IF;

                IF NOT EXISTS (
                    SELECT 1 FROM timescaledb_information.jobs
                    WHERE hypertable_name = 'daily_quarterly_ohlcv'
                      AND proc_name = 'policy_refresh_continuous_aggregate'
                ) THEN
                    PERFORM add_continuous_aggregate_policy('daily_quarterly_ohlcv',
                        start_offset  => INTERVAL '270 days',
                        end_offset    => INTERVAL '90 days',
                        schedule_interval => INTERVAL '1 day');
                END IF;
            END $$;
        """,
    },
    {
        "id": "036_copy_splits_dividends_from_marketdb",
        "description": (
            "One-shot copy of splits and dividends rows from MarketDB into "
            "TimescaleDB (slice 152). Skips if MT_MARKET_DB_URL is unset or "
            "unreachable. ON CONFLICT DO NOTHING ensures idempotency."
        ),
        "python_fn": _copy_splits_dividends_from_marketdb,
    },
    {
        "id": "037_widen_minute_cagg_refresh_offsets",
        "description": (
            "Widen 5m and 15m cagg refresh policy start_offset from 2h to "
            "1 day. The 2h window only catches bars written within the last "
            "2 hours; bars older than that (late arrivals, daemon resumes "
            "after pause) never get materialized by the policy. 1 day is "
            "comfortable headroom while keeping each policy run cheap "
            "(<1s on production-shaped data). Idempotent: checks the "
            "current config before mutating."
        ),
        "sql": """
            DO $$
            DECLARE
                cur_start interval;
            BEGIN
                -- 5m cagg
                SELECT (config->>'start_offset')::interval INTO cur_start
                FROM timescaledb_information.jobs
                WHERE hypertable_name = 'minute_5min_ohlcv'
                  AND proc_name = 'policy_refresh_continuous_aggregate';

                IF cur_start IS NOT NULL AND cur_start < INTERVAL '1 day' THEN
                    PERFORM remove_continuous_aggregate_policy('minute_5min_ohlcv');
                    PERFORM add_continuous_aggregate_policy('minute_5min_ohlcv',
                        start_offset  => INTERVAL '1 day',
                        end_offset    => INTERVAL '5 minutes',
                        schedule_interval => INTERVAL '5 minutes');
                END IF;

                -- 15m cagg
                SELECT (config->>'start_offset')::interval INTO cur_start
                FROM timescaledb_information.jobs
                WHERE hypertable_name = 'minute_15min_ohlcv'
                  AND proc_name = 'policy_refresh_continuous_aggregate';

                IF cur_start IS NOT NULL AND cur_start < INTERVAL '1 day' THEN
                    PERFORM remove_continuous_aggregate_policy('minute_15min_ohlcv');
                    PERFORM add_continuous_aggregate_policy('minute_15min_ohlcv',
                        start_offset  => INTERVAL '1 day',
                        end_offset    => INTERVAL '15 minutes',
                        schedule_interval => INTERVAL '15 minutes');
                END IF;
            END $$;
        """,
    },
    {
        "id": "039_create_daemon_heartbeat",
        "description": (
            "Create daemon_heartbeat table (slice 156 follow-up). "
            "The original CREATE was never folded into the migration "
            "chain; trading_test had it from a prior ad-hoc creation. "
            "Required by HeartbeatStore (one row per daemon identity, "
            "upserted on every beat). Idempotent — CREATE TABLE IF NOT "
            "EXISTS — so existing DBs treat this as a no-op."
        ),
        "sql": """
            CREATE TABLE IF NOT EXISTS daemon_heartbeat (
                daemon_id      TEXT NOT NULL PRIMARY KEY,
                status         TEXT NOT NULL,
                started_at     TIMESTAMPTZ NOT NULL,
                last_beat_at   TIMESTAMPTZ NOT NULL,
                current_symbol TEXT,
                cycle_count    INTEGER NOT NULL DEFAULT 0,
                pid            INTEGER,
                hostname       TEXT
            );
        """,
    },
    {
        "id": "040_drop_preferred_stock",
        "description": (
            "Remove Preferred Stock from instruments: drop CHECK constraint, "
            "delete preferred rows, re-add tightened CHECK derived from EodhdType."
        ),
        "sql": f"""
            ALTER TABLE instruments
                DROP CONSTRAINT IF EXISTS instruments_eodhd_type_check;

            DELETE FROM instruments
            WHERE eodhd_type = 'Preferred Stock';

            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_constraint
                    WHERE conname = 'instruments_eodhd_type_check'
                ) THEN
                    ALTER TABLE instruments
                        ADD CONSTRAINT instruments_eodhd_type_check
                        CHECK ({_eodhd_type_check_sql()});
                END IF;
            END $$;
        """,
    },
    {
        "id": "041_create_universe_members",
        "description": (
            "Create universe_members table for point-in-time index constituent "
            "tracking (slice 161). Stores daily snapshots of SP500/R2000/NASDAQ-100 "
            "membership with added_date/removed_date lifecycle columns. "
            "To revert manually: DROP TABLE universe_members;"
        ),
        "sql": """
            CREATE TABLE IF NOT EXISTS universe_members (
                universe_name TEXT NOT NULL,
                symbol        TEXT NOT NULL,
                added_date    DATE NOT NULL,
                removed_date  DATE,
                PRIMARY KEY (universe_name, symbol, added_date)
            );
            CREATE INDEX IF NOT EXISTS idx_universe_members_active
                ON universe_members (universe_name, symbol)
                WHERE removed_date IS NULL;
        """,
    },
    {
        "id": "042_enable_columnar_compression",
        "description": (
            "Enable TimescaleDB columnar compression on minute_ohlcv and "
            "daily_ohlcv (slice 160). Segmentby=symbol, orderby=time DESC. "
            "compress_after=7 days policy. Backfill-compresses all existing "
            "chunks older than 7 days. Idempotent."
        ),
        "requires_autocommit": True,
        "python_fn": _setup_and_backfill_compression,
    },
    {
        "id": "043_minute_chunk_interval_7d",
        "description": (
            "Set minute_ohlcv chunk_time_interval to MINUTE_OHLCV_CHUNK_INTERVAL "
            "(7 days, slice 166). Affects FUTURE chunks only; existing 4-hour "
            "chunks are rewritten by the slice 166 `mt data rechunk` maintenance "
            "run. Idempotent — re-applying the same interval is a no-op. "
            "To revert manually: SELECT set_chunk_time_interval('minute_ohlcv', "
            "INTERVAL '4 hours');"
        ),
        "sql": f"""
            SELECT set_chunk_time_interval('minute_ohlcv', {_minute_chunk_interval_sql()});
        """,
    },
    {
        "id": "044_minute_cagg_chunk_interval_70d",
        "description": (
            "Set chunk_time_interval to MINUTE_CAGG_CHUNK_INTERVAL (70 days, "
            "slice 163) on the four minute caggs' materialized hypertables, "
            "resolved by continuous-aggregate VIEW NAME (never mat_N literals). "
            "Affects FUTURE chunks only; the existing ~1.67-day chunks are "
            "rewritten by the `mt data caggs repair` maintenance sweep. "
            "Idempotent — re-applying the same interval is a no-op. Cold-start "
            "no-op: TimescaleDB sizes a new cagg's mat hypertable at 10x the "
            "source interval, and post-043 minute_ohlcv is 7 days -> 70 days "
            "automatic. To revert manually, per cagg: "
            "SELECT set_chunk_time_interval('minute_5min_ohlcv', "
            "INTERVAL '1 day 16 hours')."
        ),
        "sql": f"""
            DO $$
            DECLARE
                cagg TEXT;
            BEGIN
                FOREACH cagg IN ARRAY {_minute_cagg_views_sql_array()}
                LOOP
                    PERFORM set_chunk_time_interval(
                        cagg::regclass,
                        {_interval_seconds_sql(MINUTE_CAGG_CHUNK_INTERVAL)}
                    );
                END LOOP;
            END $$;
        """,
    },
    {
        "id": "045_minute_cagg_columnstore",
        "description": (
            "Enable TimescaleDB columnstore compression + policy on the four "
            "minute caggs (slice 163), by VIEW NAME (enable_columnstore + "
            "segmentby=symbol; orderby defaults to time_bucket). "
            "compress_after=MINUTE_CAGG_COMPRESS_AFTER (7 days, > the refresh "
            "policy's 1-day start_offset). Unlike migration 042, NO "
            "backfill-compress: existing wrong-interval chunks are dropped by "
            "`mt data caggs repair`, which compresses each fresh 70-day chunk "
            "behind the sweep frontier. Idempotent. To revert manually, per "
            "cagg: CALL remove_columnstore_policy('minute_5min_ohlcv'); "
            "ALTER MATERIALIZED VIEW minute_5min_ohlcv "
            "SET (timescaledb.enable_columnstore = false);"
        ),
        "requires_autocommit": True,
        "python_fn": _setup_minute_cagg_columnstore,
    },
    {
        "id": "046_create_coverage_caggs",
        "description": (
            "Create the two coverage continuous aggregates backing "
            "data_status.bars_summary (slice 167): minute_coverage over the "
            "minute_4hour_ohlcv cagg (HIERARCHICAL) and daily_coverage over "
            "raw daily_ohlcv. Both bucket at COVERAGE_BUCKET_INTERVAL (1 year), "
            "so bars_summary groups ~15k rows instead of scanning 4.4B raw "
            "minute rows. "
            "ASYMMETRY, deliberate: minute_coverage's source is a 4-hour cagg, "
            "so its first_bucket/last_bucket are truncated to the parent's "
            "4-hour bucket start; daily_coverage reads the raw hypertable, so "
            "its first_ts/last_ts are EXACT. Slice 167 D3/D7 documents this, "
            "the view doc comment states it, and the equivalence tests assert "
            "the stricter condition on the daily branch. "
            "Each view is a separate execute() call — Timescale forbids "
            "multiple continuous-aggregate DDL statements in one call."
        ),
        # Same constraint as 033/034: one CREATE MATERIALIZED VIEW per
        # execute(), outside an implicit transaction.
        "requires_autocommit": True,
        "python_fn": lambda conn: [
            conn.execute(sql)
            for sql in [
                # The bucket column is named time_bucket, matching all seven
                # pre-167 caggs. This is load-bearing, not cosmetic: slice 168's
                # _cagg_max probes max(time_bucket) by name, so a differently
                # named column makes assert_cagg_fresh fail its probe and report
                # PROBE_FAILED -- a permanently stale verdict on a healthy cagg.
                f"""
                CREATE MATERIALIZED VIEW IF NOT EXISTS {MINUTE_COVERAGE_VIEW}
                WITH (timescaledb.continuous) AS
                SELECT
                    time_bucket(
                        {_interval_seconds_sql(COVERAGE_BUCKET_INTERVAL)},
                        time_bucket
                    ) AS time_bucket,
                    symbol,
                    SUM(minute_count) AS bars,
                    MIN(time_bucket)  AS first_bucket,
                    MAX(time_bucket)  AS last_bucket
                FROM minute_4hour_ohlcv
                GROUP BY 1, symbol
                """,
                f"""
                CREATE MATERIALIZED VIEW IF NOT EXISTS {DAILY_COVERAGE_VIEW}
                WITH (timescaledb.continuous) AS
                SELECT
                    time_bucket(
                        {_interval_seconds_sql(COVERAGE_BUCKET_INTERVAL)},
                        time
                    ) AS time_bucket,
                    symbol,
                    COUNT(*)  AS bars,
                    MIN(time) AS first_bucket,
                    MAX(time) AS last_bucket
                FROM daily_ohlcv
                GROUP BY 1, symbol
                """,
            ]
        ],
    },
    {
        "id": "047_coverage_cagg_refresh_policies",
        "description": (
            "Install refresh policies for the two coverage caggs (slice 167 "
            "D4). Offsets come from constants measured against the parent "
            "policies on prod, never literals: minute_coverage's start_offset "
            "must exceed the parent minute_4hour_ohlcv policy's entire refresh "
            "window (1 day) with margin, because a too-narrow start_offset on "
            "a hierarchical cagg strands older history permanently — the exact "
            "failure slice 163 had to repair. Idempotent — policies are added "
            "inside a DO block that checks for existing jobs first, matching "
            "035/037."
        ),
        "sql": f"""
            DO $$ BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM timescaledb_information.jobs
                    WHERE hypertable_name = '{MINUTE_COVERAGE_VIEW}'
                      AND proc_name = 'policy_refresh_continuous_aggregate'
                ) THEN
                    PERFORM add_continuous_aggregate_policy(
                        '{MINUTE_COVERAGE_VIEW}',
                        start_offset =>
                            {
            _interval_seconds_sql(MINUTE_COVERAGE_REFRESH_START_OFFSET)
        },
                        end_offset =>
                            {_interval_seconds_sql(MINUTE_COVERAGE_REFRESH_END_OFFSET)},
                        schedule_interval =>
                            {
            _interval_seconds_sql(MINUTE_COVERAGE_REFRESH_SCHEDULE_INTERVAL)
        });
                END IF;

                IF NOT EXISTS (
                    SELECT 1 FROM timescaledb_information.jobs
                    WHERE hypertable_name = '{DAILY_COVERAGE_VIEW}'
                      AND proc_name = 'policy_refresh_continuous_aggregate'
                ) THEN
                    PERFORM add_continuous_aggregate_policy(
                        '{DAILY_COVERAGE_VIEW}',
                        start_offset =>
                            {
            _interval_seconds_sql(DAILY_COVERAGE_REFRESH_START_OFFSET)
        },
                        end_offset =>
                            {_interval_seconds_sql(DAILY_COVERAGE_REFRESH_END_OFFSET)},
                        schedule_interval =>
                            {
            _interval_seconds_sql(DAILY_COVERAGE_REFRESH_SCHEDULE_INTERVAL)
        });
                END IF;
            END $$;
        """,
    },
    {
        "id": "048_data_status_cagg_backed",
        "description": (
            "Rewrite data_status so bars_summary reads the coverage caggs "
            "created in 046 instead of scanning raw minute_ohlcv/daily_ohlcv "
            "per symbol (slice 167). Everything outside the bars_summary CTE "
            "is byte-identical to the 028 variant — same CTEs, joins, health "
            "CASE, and output columns in the same order (D2). "
            "Branches on to_regclass the same way 028 does, so cold-start and "
            "existing DBs converge on one definition. Also attaches the D3 doc "
            "comment stating both documented bounds (bucket truncation and "
            "cagg lag) with the intervals rendered from the constants."
        ),
        "sql": f"""
            DO $$ BEGIN
                IF to_regclass('public.daily_ohlcv') IS NOT NULL THEN
                    EXECUTE '{_DATA_STATUS_VIEW_WITH_DAILY_COVERAGE}';
                ELSE
                    EXECUTE '{_DATA_STATUS_VIEW_WITHOUT_DAILY_COVERAGE}';
                END IF;

                EXECUTE 'COMMENT ON VIEW data_status IS '
                     || quote_literal('{_data_status_doc_comment()}');
            END $$;
        """,
    },
    {
        "id": "049_coverage_cagg_bucket_column_rename",
        "description": (
            "Rename the coverage caggs' bucket column yr_bucket -> time_bucket "
            "(slice 167 defect fix). Slice 168's assert_cagg_fresh probes "
            "max(time_bucket) by column NAME, so a cagg whose bucket column is "
            "named anything else fails the probe and returns PROBE_FAILED — a "
            "permanently stale verdict on a perfectly healthy cagg, which "
            "would make the D3a guard useless exactly where this slice needs "
            "it. All seven pre-167 caggs already use time_bucket; this aligns "
            "046's two with that convention. "
            "Corrective rather than folded into 046 because 046 shipped to "
            "production before the defect was found, and CREATE MATERIALIZED "
            "VIEW IF NOT EXISTS will not revise an existing cagg. Cheap: a "
            "catalog rename, no re-materialization. Idempotent — each rename "
            "is guarded on the old column still being present. data_status "
            "does not reference this column (it reads first_bucket/"
            "last_bucket), so the view needs no rebuild."
        ),
        "sql": f"""
            DO $$
            DECLARE
                cov TEXT;
            BEGIN
                FOREACH cov IN ARRAY ARRAY[
                    '{MINUTE_COVERAGE_VIEW}', '{DAILY_COVERAGE_VIEW}'
                ]
                LOOP
                    IF EXISTS (
                        SELECT 1 FROM information_schema.columns
                        WHERE table_name = cov AND column_name = 'yr_bucket'
                    ) THEN
                        EXECUTE format(
                            'ALTER MATERIALIZED VIEW %I RENAME COLUMN '
                            'yr_bucket TO time_bucket', cov
                        );
                    END IF;
                END LOOP;
            END $$;
        """,
    },
    {
        "id": "050_daily_chunk_interval_70d",
        "description": (
            "Set daily_ohlcv chunk_time_interval to DAILY_OHLCV_CHUNK_INTERVAL "
            "(70 days, slice 170). Affects FUTURE chunks only; the existing "
            "7-day chunks are rewritten by `mt data rechunk --table daily`. "
            "The 7-day interval set at creation (023) was never revisited and "
            "produced 3,371 chunks over ~34.7 M rows — the same over-chunking "
            "pathology migration 043 fixed for minute_ohlcv. 70 = 10 x 7, so "
            "existing chunks nest exactly inside the new epoch-aligned grid. "
            "Idempotent — re-applying the same interval is a no-op. Cold-start "
            "no-op: migration 023 now creates the hypertable at this interval "
            "directly. To revert manually: SELECT set_chunk_time_interval("
            "'daily_ohlcv', INTERVAL '7 days');"
        ),
        "sql": f"""
            SELECT set_chunk_time_interval(
                '{DAILY_OHLCV_TABLE}', {_daily_chunk_interval_sql()}
            );
        """,
    },
    {
        "id": "051_coverage_cagg_bucket_narrowing",
        "description": (
            "Drop and recreate both coverage caggs at the narrowed "
            f"COVERAGE_BUCKET_INTERVAL ({_interval_literal(COVERAGE_BUCKET_INTERVAL)}, "
            "slice 169), replacing 046's 1-year width. "
            "WHY A REBUILD: a cagg's time_bucket width is compiled into its view "
            "definition and TimescaleDB has no re-bucket operation, so the width "
            "cannot be ALTERed — the views must be dropped and re-materialized. "
            "WHY NARROWER: a refresh policy's window is truncated to whole "
            "buckets and only re-materializes buckets FULLY CONTAINED in it, so "
            "the open bucket is never written while open. At a 1-year width that "
            "made both hourly policies successful no-ops for 205 consecutive "
            "runs — measured on prod 2026-08-11, both caggs sat at 2025-12-26 "
            "while raw ran to 2026-08-07. Narrowing does not make the engine "
            "refresh an open bucket (nothing does); it bounds how much that "
            "limitation can hide, from up to a year to one bucket width. "
            "ORDERING IS MANDATORY: data_status depends on both caggs, so it is "
            "dropped FIRST and re-installed LAST from 048's unchanged builder. "
            "CASCADE is forbidden — it would silently drop data_status and break "
            "mt data status, /api/v1/status, and /api/v1/health against a "
            "missing relation. "
            "Runs requires_autocommit with NO transactional rollback, so every "
            "statement is IF EXISTS / IF NOT EXISTS and re-running converges "
            "from any point. "
            "This migration creates the caggs EMPTY (WITH NO DATA); "
            "full-history materialization is a separate operational step, not "
            "part of the migration chain — a cold-start database has no history "
            "to materialize and should not pay for the machinery."
        ),
        # Same constraint as 033/034/046: one CREATE MATERIALIZED VIEW per
        # execute(), outside an implicit transaction.
        "requires_autocommit": True,
        "python_fn": lambda conn: [
            conn.execute(sql)
            for sql in [
                # (1) data_status holds a hard relation dependency on both
                # caggs (048's bars_summary CTE selects from them), so it must
                # go first. Without this the DROP below raises "cannot drop ...
                # because other objects depend on it" partway through a
                # non-transactional migration, leaving one cagg dropped and one
                # intact.
                "DROP VIEW IF EXISTS data_status",
                # (2) No CASCADE. See the description.
                f"DROP MATERIALIZED VIEW IF EXISTS {MINUTE_COVERAGE_VIEW}",
                f"DROP MATERIALIZED VIEW IF EXISTS {DAILY_COVERAGE_VIEW}",
                # Column names/types/aliases are identical to 046 (with 049's
                # time_bucket rename already applied), so every downstream query
                # binds unchanged — the payoff of 167 D2's column contract.
                # WITH NO DATA: materialization is the operational step.
                f"""
                CREATE MATERIALIZED VIEW IF NOT EXISTS {MINUTE_COVERAGE_VIEW}
                WITH (timescaledb.continuous) AS
                SELECT
                    time_bucket(
                        {_interval_seconds_sql(COVERAGE_BUCKET_INTERVAL)},
                        time_bucket
                    ) AS time_bucket,
                    symbol,
                    SUM(minute_count) AS bars,
                    MIN(time_bucket)  AS first_bucket,
                    MAX(time_bucket)  AS last_bucket
                FROM minute_4hour_ohlcv
                GROUP BY 1, symbol
                WITH NO DATA
                """,
                f"""
                CREATE MATERIALIZED VIEW IF NOT EXISTS {DAILY_COVERAGE_VIEW}
                WITH (timescaledb.continuous) AS
                SELECT
                    time_bucket(
                        {_interval_seconds_sql(COVERAGE_BUCKET_INTERVAL)},
                        time
                    ) AS time_bucket,
                    symbol,
                    COUNT(*)  AS bars,
                    MIN(time) AS first_bucket,
                    MAX(time) AS last_bucket
                FROM daily_ohlcv
                GROUP BY 1, symbol
                WITH NO DATA
                """,
                # (3) Re-install data_status from 048's definition, unchanged,
                # and re-attach the corrected doc comment. Both are re-executed
                # here rather than rewritten: the view is a pure function of its
                # migration, so this is restoration, not revision.
                f"""
                DO $$ BEGIN
                    IF to_regclass('public.daily_ohlcv') IS NOT NULL THEN
                        EXECUTE '{_DATA_STATUS_VIEW_WITH_DAILY_COVERAGE}';
                    ELSE
                        EXECUTE '{_DATA_STATUS_VIEW_WITHOUT_DAILY_COVERAGE}';
                    END IF;

                    EXECUTE 'COMMENT ON VIEW data_status IS '
                         || quote_literal('{_data_status_doc_comment()}');
                END $$;
                """,
            ]
        ],
    },
    {
        "id": "052_coverage_cagg_refresh_policies_narrowed",
        "description": (
            "Reinstall both coverage caggs' refresh policies after 051's "
            "drop/recreate (slice 169). Dropping a cagg drops its policies with "
            "it, so this is a reinstall rather than an alteration. "
            "start_offset is re-derived at the new width (D4a): the previous "
            "750 days was FORCED by the 1-year bucket's 731-day engine floor, "
            "not independently motivated. Measured policy cost with real "
            "invalidations shows steady-state (head-only) refresh is flat across "
            "a 47x window increase — 0.058 s at 16 days vs 0.072 s at 750 — so "
            "the window is chosen for how far back a deep backfill can self-heal "
            "rather than to minimise per-run work. end_offset and "
            "schedule_interval are unchanged from 047. "
            "Also re-renders COMMENT ON VIEW data_status. 051 step (3) is the "
            "primary install path for that comment — view and comment are "
            "attached together so they can never observably disagree — and this "
            "is a belt-and-braces idempotency guard for the case where 051 "
            "already applied and 052 runs later. Both call the same function, so "
            "this is a no-op in the happy path, not a second source of truth. "
            "Idempotent: policies are added inside a DO block that checks for an "
            "existing job first, matching 047/035/037."
        ),
        "sql": f"""
            DO $$ BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM timescaledb_information.jobs
                    WHERE hypertable_name = '{MINUTE_COVERAGE_VIEW}'
                      AND proc_name = 'policy_refresh_continuous_aggregate'
                ) THEN
                    PERFORM add_continuous_aggregate_policy(
                        '{MINUTE_COVERAGE_VIEW}',
                        start_offset =>
                            {
            _interval_seconds_sql(MINUTE_COVERAGE_REFRESH_START_OFFSET)
        },
                        end_offset =>
                            {_interval_seconds_sql(MINUTE_COVERAGE_REFRESH_END_OFFSET)},
                        schedule_interval =>
                            {
            _interval_seconds_sql(MINUTE_COVERAGE_REFRESH_SCHEDULE_INTERVAL)
        });
                END IF;

                IF NOT EXISTS (
                    SELECT 1 FROM timescaledb_information.jobs
                    WHERE hypertable_name = '{DAILY_COVERAGE_VIEW}'
                      AND proc_name = 'policy_refresh_continuous_aggregate'
                ) THEN
                    PERFORM add_continuous_aggregate_policy(
                        '{DAILY_COVERAGE_VIEW}',
                        start_offset =>
                            {
            _interval_seconds_sql(DAILY_COVERAGE_REFRESH_START_OFFSET)
        },
                        end_offset =>
                            {_interval_seconds_sql(DAILY_COVERAGE_REFRESH_END_OFFSET)},
                        schedule_interval =>
                            {
            _interval_seconds_sql(DAILY_COVERAGE_REFRESH_SCHEDULE_INTERVAL)
        });
                END IF;

                EXECUTE 'COMMENT ON VIEW data_status IS '
                     || quote_literal('{_data_status_doc_comment()}');
            END $$;
        """,
    },
    {
        "id": "053_minute_cagg_refresh_offsets_from_constant",
        "description": (
            "Set every minute cagg refresh policy's start_offset to "
            "MINUTE_CAGG_REFRESH_START_OFFSET (issue #20). 037 widened 5m/15m "
            "from 2h to 1 day, but a restore replay of 035 recreated the "
            "policies at 2h after 037 had run and the forward-only ledger never "
            "re-fired it; the 5m/15m caggs then froze for 24 days with the "
            "policies reporting Success (their window never contained the "
            "nightly, hours-old bars). Guarded: alters only a policy whose "
            "start_offset differs from the constant, so it is safe to re-run "
            "and converges any ledger that replayed 035. 035 now renders the "
            "same constant, so a future replay cannot regress it."
        ),
        "sql": f"""
            DO $$
            DECLARE
                v_view  text;
                v_end   interval;
                v_sched interval;
                cur_start interval;
            BEGIN
                FOR v_view, v_end, v_sched IN
                    SELECT * FROM (VALUES
                        ('minute_5min_ohlcv',
                            INTERVAL '5 minutes', INTERVAL '5 minutes'),
                        ('minute_15min_ohlcv',
                            INTERVAL '15 minutes', INTERVAL '15 minutes'),
                        ('minute_hourly_ohlcv',
                            INTERVAL '1 hour', INTERVAL '1 hour'),
                        ('minute_4hour_ohlcv',
                            INTERVAL '4 hours', INTERVAL '1 hour')
                    ) AS t(view_name, end_off, sched)
                LOOP
                    SELECT (config->>'start_offset')::interval INTO cur_start
                    FROM timescaledb_information.jobs
                    WHERE hypertable_name = v_view
                      AND proc_name = 'policy_refresh_continuous_aggregate';

                    IF cur_start IS NOT NULL
                       AND cur_start <> {_MINUTE_REFRESH_START_SQL} THEN
                        PERFORM remove_continuous_aggregate_policy(v_view);
                        PERFORM add_continuous_aggregate_policy(v_view,
                            start_offset      => {_MINUTE_REFRESH_START_SQL},
                            end_offset        => v_end,
                            schedule_interval => v_sched);
                    END IF;
                END LOOP;
            END $$;
        """,
    },
    {
        "id": "054_daily_monthly_refresh_window",
        "description": (
            "Widen daily_monthly_ohlcv's refresh start_offset to "
            "DAILY_MONTHLY_REFRESH_START_OFFSET (issue #20): the 90-day value "
            "left a 60-day window, narrower than two 31-day month buckets, so "
            "the policy failed with SQLSTATE 22023 on month boundaries. "
            "Guarded: alters only a policy whose start_offset differs from "
            "the constant; 035 renders the same constant."
        ),
        "sql": f"""
            DO $$
            DECLARE
                cur_start interval;
            BEGIN
                SELECT (config->>'start_offset')::interval INTO cur_start
                FROM timescaledb_information.jobs
                WHERE hypertable_name = 'daily_monthly_ohlcv'
                  AND proc_name = 'policy_refresh_continuous_aggregate';

                IF cur_start IS NOT NULL
                   AND cur_start <> {_DAILY_MONTHLY_REFRESH_START_SQL} THEN
                    PERFORM remove_continuous_aggregate_policy('daily_monthly_ohlcv');
                    PERFORM add_continuous_aggregate_policy('daily_monthly_ohlcv',
                        start_offset      => {_DAILY_MONTHLY_REFRESH_START_SQL},
                        end_offset        => INTERVAL '30 days',
                        schedule_interval => INTERVAL '1 day');
                END IF;
            END $$;
        """,
    },
]
