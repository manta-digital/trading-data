"""``mt data health`` — one read-only answer to "does anything need a human?"

Slice 919. Every check judges a measured value against a named threshold in
``constants.py`` and reports one line; any failing check makes the command
exit non-zero, so under the ``mt-health.timer`` unit a breach is a *failed
unit* — visible in ``mt-run status`` and ``systemctl --failed`` — instead of a
number somebody has to remember to read. The checks are the ones an operator
was doing by hand on 2026-08-31, each of which had a silent failure behind it:

- raw data freshness (newest ``minute_ohlcv`` / ``daily_ohlcv`` bar) — the
  minute freeze of issue #19 was invisible to ``data status`` health;
- continuous-aggregate freshness (slice 168's verdicts) — the 24-day 5m/15m
  freeze of issue #20 sat behind policies reporting Success;
- EODHD quota headroom — a starved night parks symbols (issue #19);
- Kalshi phase recency — a stalled pass shows here within three hours.

The measurement functions are pure (values in, verdict out) so the rules are
unit-tested without a database; ``gather`` is the only I/O.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import psycopg
import typer
from psycopg import sql

from manta_trading.cli.output import print_error, print_result
from manta_trading.constants import (
    DAILY_OHLCV_TABLE,
    HEALTH_DAILY_RAW_STALE_AFTER,
    HEALTH_EODHD_QUOTA_HEADROOM_MIN,
    HEALTH_EODHD_USER_ENDPOINT,
    HEALTH_KALSHI_PHASE_STALE_AFTER,
    HEALTH_MINUTE_RAW_STALE_AFTER,
    MINUTE_OHLCV_TABLE,
)
from manta_trading.data.kalshi.constants import DB_CONNECT_TIMEOUT_SECONDS
from manta_trading.data.kalshi.status import read_candle_status, read_catalog_status
from manta_trading.data.kalshi.trade_status import read_trade_status
from manta_trading.data.maintenance.status_coverage import check_coverage_freshness
from manta_trading.market.maintenance.cagg_freshness import assert_cagg_fresh

#: Exit codes: 0 every check passed; 1 at least one failed; 2 could not run.
EXIT_HEALTHY = 0
EXIT_UNHEALTHY = 1
EXIT_UNAVAILABLE = 2

#: The seven OHLCV continuous aggregates, in ``mt data caggs`` order. Coverage
#: caggs are judged through ``check_coverage_freshness`` (their source is not
#: in ``GRANULARITY_SOURCE``).
OHLCV_CAGG_VIEWS: tuple[str, ...] = (
    "minute_5min_ohlcv",
    "minute_15min_ohlcv",
    "minute_hourly_ohlcv",
    "minute_4hour_ohlcv",
    "daily_weekly_ohlcv",
    "daily_monthly_ohlcv",
    "daily_quarterly_ohlcv",
)


@dataclass(frozen=True)
class HealthCheck:
    """One verdict: what was checked, whether it passed, and the number."""

    name: str
    ok: bool
    detail: str


def _ago(now: datetime, then: datetime) -> str:
    delta = now - then
    hours = delta.total_seconds() / 3600
    return f"{hours / 24:.1f} d ago" if hours >= 48 else f"{hours:.1f} h ago"


# ---------------------------------------------------------------------------
# Pure rules
# ---------------------------------------------------------------------------


def check_raw_freshness(
    name: str, newest: datetime | None, *, now: datetime, threshold: timedelta
) -> HealthCheck:
    """Newest raw bar must be within ``threshold`` of now."""
    if newest is None:
        return HealthCheck(name, False, "no rows")
    ok = now - newest <= threshold
    return HealthCheck(
        name,
        ok,
        f"newest bar {newest:%Y-%m-%d %H:%M} UTC ({_ago(now, newest)}); "
        f"limit {threshold.days} d",
    )


def check_cagg(view_name: str, is_fresh: bool, detail: str) -> HealthCheck:
    """A slice-168 freshness verdict, one per cagg."""
    return HealthCheck(f"cagg {view_name}", is_fresh, detail)


def check_quota(
    used: int, daily_limit: int, extra: int, *, headroom_min: int
) -> HealthCheck:
    """Remaining EODHD requests today must clear the headroom floor."""
    remaining = daily_limit - used + extra
    return HealthCheck(
        "eodhd quota",
        remaining >= headroom_min,
        f"{remaining:,} remaining ({used:,}/{daily_limit:,} used, extra {extra:,}); "
        f"floor {headroom_min:,}",
    )


def check_phase_recency(
    name: str, last_at: datetime | None, *, now: datetime, threshold: timedelta
) -> HealthCheck:
    """A Kalshi phase must have completed within ``threshold``."""
    if last_at is None:
        return HealthCheck(name, False, "never completed")
    ok = now - last_at <= threshold
    hours = threshold.total_seconds() / 3600
    return HealthCheck(
        name, ok, f"last completed {_ago(now, last_at)}; limit {hours:.0f} h"
    )


# ---------------------------------------------------------------------------
# I/O
# ---------------------------------------------------------------------------


def _newest(conn: psycopg.Connection[Any], table: str) -> datetime | None:
    query = sql.SQL("SELECT max(time) FROM {}").format(sql.Identifier(table))
    row = conn.execute(query).fetchone()
    return row[0] if row else None


def fetch_quota(http: httpx.Client, api_key: str) -> tuple[int, int, int]:
    """(used, daily_limit, extra) from EODHD's account endpoint."""
    response = http.get(
        HEALTH_EODHD_USER_ENDPOINT, params={"api_token": api_key, "fmt": "json"}
    )
    response.raise_for_status()
    body = response.json()
    return (
        int(body["apiRequests"]),
        int(body["dailyRateLimit"]),
        int(body["extraLimit"]),
    )


def gather(
    conn: psycopg.Connection[Any],
    settings: Any,
    *,
    http: httpx.Client,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> list[HealthCheck]:
    """Run every check against live sources. The only function here with I/O."""
    at = now()
    checks: list[HealthCheck] = [
        check_raw_freshness(
            "minute data",
            _newest(conn, MINUTE_OHLCV_TABLE),
            now=at,
            threshold=HEALTH_MINUTE_RAW_STALE_AFTER,
        ),
        check_raw_freshness(
            "daily data",
            _newest(conn, DAILY_OHLCV_TABLE),
            now=at,
            threshold=HEALTH_DAILY_RAW_STALE_AFTER,
        ),
    ]
    for view in OHLCV_CAGG_VIEWS:
        verdict = assert_cagg_fresh(conn, view)
        checks.append(check_cagg(view, verdict.is_fresh, verdict.detail))
    for verdict in check_coverage_freshness(conn).verdicts:
        checks.append(check_cagg(verdict.view_name, verdict.is_fresh, verdict.detail))

    used, limit, extra = fetch_quota(http, settings.eodhd_api_key)
    checks.append(
        check_quota(used, limit, extra, headroom_min=HEALTH_EODHD_QUOTA_HEADROOM_MIN)
    )

    catalog = read_catalog_status(conn)
    rule = settings.collection_rule()
    candles = read_candle_status(conn, rule)
    trades = read_trade_status(conn, rule, settings.kalshi_trades_excluded_categories)
    for name, last_at in (
        ("kalshi catalog", catalog.last_full_sync_at if catalog else None),
        ("kalshi candles", candles.last_phase_at if candles else None),
        ("kalshi trades", trades.last_phase_at if trades else None),
    ):
        checks.append(
            check_phase_recency(
                name, last_at, now=at, threshold=HEALTH_KALSHI_PHASE_STALE_AFTER
            )
        )
    return checks


def render(checks: list[HealthCheck]) -> str:
    width = max(len(c.name) for c in checks)
    lines = [
        f"{'OK  ' if c.ok else 'FAIL'} {c.name:<{width}}  {c.detail}" for c in checks
    ]
    failing = sum(1 for c in checks if not c.ok)
    lines.append(
        "healthy"
        if failing == 0
        else f"UNHEALTHY: {failing} of {len(checks)} checks failing"
    )
    return "\n".join(lines)


def data_health(
    ctx: typer.Context,
    json_output: bool = typer.Option(False, "--json", help="Emit JSON."),
) -> None:
    """Check data freshness, cagg lag, EODHD quota, and Kalshi phase recency.

    Exit 0 when every check passes, 1 when any fails, 2 when the checks could
    not run (no database URL, unreachable database or provider).
    """
    settings = ctx.obj["settings"]
    if not settings.timescale_db_url or not settings.eodhd_api_key:
        print_error(
            "MT_TIMESCALE_DB_URL and MT_EODHD_API_KEY must be configured.",
            json_mode=json_output,
        )
        raise typer.Exit(EXIT_UNAVAILABLE)
    try:
        with (
            psycopg.connect(
                str(settings.timescale_db_url),
                connect_timeout=DB_CONNECT_TIMEOUT_SECONDS,
            ) as conn,
            httpx.Client(timeout=30.0) as http,
        ):
            checks = gather(conn, settings, http=http)
    except (psycopg.OperationalError, httpx.HTTPError) as exc:
        print_error(f"health check could not run: {exc}", json_mode=json_output)
        raise typer.Exit(EXIT_UNAVAILABLE) from exc

    healthy = all(c.ok for c in checks)
    if json_output:
        print_result(
            {"healthy": healthy, "checks": [asdict(c) for c in checks]}, json_mode=True
        )
    else:
        print_result(render(checks), json_mode=False)
    raise typer.Exit(EXIT_HEALTHY if healthy else EXIT_UNHEALTHY)
